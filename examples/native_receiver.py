from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    import numpy as np
    import websockets
except ImportError as exc:  # pragma: no cover - user-facing startup guard
    raise SystemExit("Missing dependency. Run this with `uv run`.") from exc

from quantum_light_engine.post_processing import (
    ClusterProcessingConfig,
    ClusterProcessingResult,
    RealtimeClusterProcessor,
    config_from_dict,
    config_to_dict,
)

HEADER = struct.Struct("<4sBBHQQQQdIIIIQQ")
HEADER_SIZE = 80
MAGIC = b"RPA1"
MAX_POINTS = 800
# About 34 seconds at 488 kS/s; two int16 channels use at most 64 MiB.
# Plotting still reads only the selected display resolution from the ring.
HISTORY_MAX_SAMPLES = 16_777_216
TIME_ZOOM_MIN = 1 / 4096
DEFAULT_TIME_SPAN = "One batch"
TIME_SPANS_MS = {
    "One batch": None,
    "100 ms": 100,
    "500 ms": 500,
    "1 s": 1000,
    "5 s": 5000,
    "10 s": 10000,
    "30 s": 30000,
}
DEFAULT_SIGNAL_RESOLUTION = "Medium"
SIGNAL_RESOLUTION_POINTS = {
    "Low": 400,
    "Medium": MAX_POINTS,
    "High": 1600,
    "Ultra": 3200,
}
SIGNAL_PLOT_TOP_PAD = 14
ADC_COUNTS_PER_VOLT = 32_768.0
UI_FRAME_INTERVAL_MS = 16
WIDE_VIEW_FAST_REFRESH_SECONDS = 8.0
WIDE_VIEW_AUTO_INTERVAL_MS = 500
STATS_MIN_INTERVAL_MS = 200
SPECTRUM_MIN_INTERVAL_MS = 180
ENGINE_READY_TIMEOUT_SECONDS = 10.0
ENGINE_HEALTH_TIMEOUT_SECONDS = 1.0
ENGINE_CONNECT_TIMEOUT_SECONDS = 5.0
REDPITAYA_CONNECT_TIMEOUT_SECONDS = 30.0
REDPITAYA_START_TIMEOUT_SECONDS = 8.0
ENGINE_PING_INTERVAL_MS = 3000
ENGINE_PROCESS_TITLE = "RPs Server"
TOAST_VISIBLE_MS = 5000
TOAST_FADE_INTERVAL_MS = 50
TOAST_FADE_STEPS = 10
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = WORKSPACE_ROOT / "data" / "native_receiver_settings.json"
CLUSTER_CONFIG_PATH = WORKSPACE_ROOT / "data" / "cluster_processing.json"
SIGNAL_SCRIPT_FEATURE_ENV = "QL_NATIVE_RECEIVER_SIGNAL_SCRIPT"
DISCONNECTED_STATES = {"", "-", "disconnected"}
STREAMING_STATES = {"streaming", "reconnecting", "starting"}
READY_STATES = {"ready"}
UI_LOGGER = logging.getLogger("quantum_light_engine.native_ui")
CONNECTION_FAILURE_BUSY_LABELS = frozenset({"Starting Red Pitaya acquisition"})
CONNECTION_OVERLAY_BUSY_LABELS = frozenset(
    {"Opening sample stream", "Starting Red Pitaya acquisition"}
)
CONNECTION_OVERLAY_BG = "#10141b"
CONNECTION_OVERLAY_OPACITY = 0.70
CONNECTION_FAILED_NOTICE = "Red Pitaya is not connected to the RTQLM acquisition engine"
CONNECTION_FAILED_SPECTRUM_NOTICE = "Spectrum unavailable until Red Pitaya is connected"
DEFAULT_SIGNAL_SCRIPT = """\
# Available:
#   signals: dict[str, numpy.ndarray]
#   np: numpy
#
# Stable IDs include:
#   signals["selected.ch1"]
#   signals["selected.ch2"]
#   signals["<device_slug>.ch1"]
#   signals["<device_slug>.ch2"]
#
# Return one of:
#   array
#   (ch1, ch2)
#   {"ch1": array, "ch2": array}

def process(signals, np):
    # Example: show CH1 minus CH2 if both are available.
    ch1 = signals.get("selected.ch1")
    ch2 = signals.get("selected.ch2")
    if ch1 is not None and ch2 is not None:
        n = min(len(ch1), len(ch2))
        return {"ch1": ch1[:n] - ch2[:n], "ch2": ch2[:n]}
    return {"ch1": ch1}
"""
# Semantic visual tokens.  Tk widgets that cannot consume ttk styles still use
# this single palette so the instrument remains coherent and easy to tune.
COLORS = {
    "bg": "#10151d",
    "bg_deep": "#0b0f15",
    "sidebar": "#0d1219",
    "surface": "#151b24",
    "surface_elevated": "#1a222d",
    "surface_active": "#222d3b",
    "hover": "#202a36",
    "selected": "#1d3557",
    "border_subtle": "#273241",
    "border_strong": "#3a485c",
    "text": "#e8edf5",
    "text_secondary": "#a5b0c0",
    "text_disabled": "#687586",
    "accent": "#4b8df8",
    "accent_hover": "#6ca2fb",
    "success": "#36c275",
    "warning": "#e7ae45",
    "danger": "#f06f83",
    "ch1": "#43c97b",
    "ch2": "#45c6e8",
    "plot_grid": "#202a37",
    "plot_axis": "#425168",
    "plot_annotation": "#b6c0cf",
}
# Compatibility aliases keep rendering and focused tests stable while all new
# UI work uses the semantic names above.
COLORS.update(
    {
        "panel": COLORS["surface"],
        "panel_2": COLORS["surface_elevated"],
        "panel_3": COLORS["surface_active"],
        "muted": COLORS["text_secondary"],
        "grid": COLORS["border_subtle"],
        "axis": COLORS["plot_axis"],
        "warn": COLORS["warning"],
        "accent_hover": COLORS["accent_hover"],
    }
)
SPACING = {"xxs": 4, "xs": 8, "sm": 12, "md": 16, "lg": 24, "xl": 32}
FONTS = {
    "small": ("Segoe UI", 8),
    "body": ("Segoe UI", 9),
    "body_bold": ("Segoe UI", 9, "bold"),
    "section": ("Segoe UI", 10, "bold"),
    "title": ("Segoe UI", 15, "bold"),
    "mono": ("Cascadia Mono", 9),
    "mono_bold": ("Cascadia Mono", 9, "bold"),
}
DIMENSIONS = {
    "sidebar_width": 272,
    "control_height": 30,
    "plot_border": 1,
    "min_width": 960,
    "min_height": 640,
}


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    host: str
    state: str
    channel_1_enabled: bool
    channel_2_enabled: bool
    decimation: int
    block_size_bytes: int


@dataclass(frozen=True)
class SampleFrame:
    channel_mask: int
    sequence: int
    sample_rate_hz: float
    samples_per_channel: int
    flags: int
    fpga_lost_ch1: int
    fpga_lost_ch2: int
    channel_1: memoryview | None
    channel_2: memoryview | None


class SampleHistory:
    """Bounded raw-sample ring; callers hold frame_lock for writes and snapshots."""

    def __init__(self, capacity: int = HISTORY_MAX_SAMPLES) -> None:
        self.capacity = capacity
        self.clear()

    def clear(self) -> None:
        self.latest: SampleFrame | None = None
        self.size = 0
        self.position = 0
        self.channels: dict[int, np.ndarray[Any, Any]] = {}

    def frozen_copy(self) -> SampleHistory:
        frozen = SampleHistory(max(self.size, 1))
        frozen.latest = self.latest
        frozen.size = self.size
        start = (self.position - self.size) % self.capacity
        first = min(self.size, self.capacity - start)
        for channel, buffer in self.channels.items():
            data = np.empty(self.size, dtype=np.int16)
            data[:first] = buffer[start : start + first]
            data[first:] = buffer[: self.size - first]
            frozen.channels[channel] = data
        return frozen

    def append(self, frame: SampleFrame) -> None:
        previous = self.latest
        if previous is not None and (
            frame.sequence != previous.sequence + 1
            or frame.channel_mask != previous.channel_mask
            or frame.sample_rate_hz != previous.sample_rate_hz
            or frame.samples_per_channel != previous.samples_per_channel
        ):
            self.clear()
        count = min(frame.samples_per_channel, self.capacity)
        first = min(count, self.capacity - self.position)
        for channel, samples in ((1, frame.channel_1), (2, frame.channel_2)):
            if samples is None:
                continue
            if channel not in self.channels:
                self.channels[channel] = np.empty(self.capacity, dtype=np.int16)
            buffer = self.channels[channel]
            data = samples_to_numpy(samples)[-count:]
            buffer[self.position : self.position + first] = data[:first]
            buffer[: count - first] = data[first:]
        self.position = (self.position + count) % self.capacity
        self.size = min(self.size + count, self.capacity)
        self.latest = frame

    def snapshot(self, requested_samples: int) -> SampleFrame | None:
        frame = self.latest
        if frame is None:
            return None
        count = min(requested_samples, self.size)
        if count <= frame.samples_per_channel:
            return frame
        start = (self.position - count) % self.capacity
        first = min(count, self.capacity - start)
        result: dict[int, memoryview] = {}
        for channel, buffer in self.channels.items():
            # Snapshot ownership prevents the socket overwriting samples during drawing.
            data = np.empty(count, dtype=np.int16)
            data[:first] = buffer[start : start + first]
            data[first:] = buffer[: count - first]
            data.flags.writeable = False
            result[channel] = memoryview(data)
        return replace(
            frame,
            samples_per_channel=count,
            channel_1=result.get(1),
            channel_2=result.get(2),
        )

    def plot_channels(
        self, requested_samples: int, point_budget: int = MAX_POINTS
    ) -> tuple[Any, Any, float, int] | None:
        if self.latest is None:
            return None
        count = min(requested_samples, self.size)
        limit = max(2, point_budget)
        indices = np.linspace(0, count - 1, min(count, limit), dtype=np.intp)
        indices = (self.position - count + indices) % self.capacity
        first = self.channels[1][indices] if 1 in self.channels else None
        second = self.channels[2][indices] if 2 in self.channels else None
        return first, second, self.latest.sample_rate_hz, count


@dataclass(frozen=True)
class AppSettings:
    api_base: str = "http://127.0.0.1:8080"
    launch_engine_on_start: bool = True


class ApiError(RuntimeError):
    pass


def configure_ui_logging() -> None:
    if not UI_LOGGER.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [native-ui] %(message)s"))
        UI_LOGGER.addHandler(handler)
    UI_LOGGER.setLevel(logging.INFO)
    UI_LOGGER.propagate = False


class SignalScriptDialog(tk.Toplevel):
    def __init__(self, parent: ReceiverApp) -> None:
        super().__init__(parent)
        self.parent = parent
        self.title("Python signal processing")
        self.configure(bg=COLORS["bg"])
        self.geometry("860x620")
        self.minsize(700, 480)
        self.transient(parent)

        self._build()
        self._load_script()
        self._refresh_sources()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-Return>", lambda _event: self._apply())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        position_dialog(self, parent)
        self.editor.focus_set()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=12, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Python signal processing", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="Transform live samples before plotting. Apply with Ctrl+Enter.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Button(header, text="Refresh signal IDs", command=self._refresh_sources).grid(
            row=0, column=1, rowspan=2, padx=(8, 0)
        )

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        left = ttk.Frame(body, style="Panel.TFrame", padding=10)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="Available signal IDs", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self.sources = tk.Listbox(
            left,
            bg=COLORS["panel_2"],
            fg=COLORS["text"],
            selectbackground=COLORS["accent"],
            selectforeground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["grid"],
            activestyle="none",
            width=28,
            font=("Consolas", 10),
        )
        self.sources.grid(row=1, column=0, sticky="nsew")
        ttk.Label(
            left,
            text="Use IDs as strings:\nsignals['selected.ch1']",
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        body.add(left, weight=1)

        right = ttk.Frame(body, style="Panel.TFrame", padding=10)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.editor = tk.Text(
            right,
            bg="#080b10",
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["grid"],
            font=("Consolas", 10),
            undo=True,
            wrap="none",
        )
        self.editor.grid(row=0, column=0, sticky="nsew")
        body.add(right, weight=4)

        footer = ttk.Frame(self, padding=(12, 0, 12, 12), style="Panel.TFrame")
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.parent.script_status, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(footer, text="Disable", command=self._disable).grid(row=0, column=1)
        ttk.Button(footer, text="Apply", style="Accent.TButton", command=self._apply).grid(
            row=0, column=2, padx=(8, 0)
        )

    def _load_script(self) -> None:
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", self.parent.signal_script)

    def _refresh_sources(self) -> None:
        self.sources.delete(0, tk.END)
        for source_id in self.parent.available_signal_ids():
            self.sources.insert(tk.END, source_id)

    def _apply(self) -> None:
        code = self.editor.get("1.0", tk.END).strip()
        if not code:
            self._disable()
            return
        try:
            self.parent.set_signal_script(code)
        except Exception as exc:
            messagebox.showerror("Script error", str(exc), parent=self)

    def _disable(self) -> None:
        self.parent.clear_signal_script()


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event: tk.Event[Any]) -> None:
        if self.tip is not None:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(
            self.tip,
            text=self.text,
            style="Tooltip.TLabel",
            padding=(10, 7),
            justify=tk.LEFT,
        )
        label.grid(row=0, column=0)

    def _hide(self, _event: tk.Event[Any]) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


def position_dialog(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    """Center a dialog on its owner and keep it fully inside the visible desktop."""
    dialog.update_idletasks()
    width = dialog.winfo_width()
    height = dialog.winfo_height()
    x = parent.winfo_rootx() + max((parent.winfo_width() - width) // 2, 0)
    y = parent.winfo_rooty() + max((parent.winfo_height() - height) // 2, 0)
    screen_width = dialog.winfo_screenwidth()
    screen_height = dialog.winfo_screenheight()
    x = min(max(x, 0), max(screen_width - width, 0))
    y = min(max(y, 0), max(screen_height - height, 0))
    dialog.geometry(f"+{x}+{y}")


class AddRedPitayaDialog(tk.Toplevel):
    def __init__(self, parent: ReceiverApp) -> None:
        super().__init__(parent)
        self.parent = parent
        self.result: dict[str, Any] | None = None
        self.title("Add Red Pitaya")
        self.configure(bg=COLORS["bg"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.name = tk.StringVar(value="Red Pitaya")
        self.host = tk.StringVar()
        self.channel_1 = tk.BooleanVar(value=True)
        self.channel_2 = tk.BooleanVar(value=True)

        self._build()
        self.bind("<Return>", lambda _event: self._submit())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        self._center(parent)
        self.host_entry.focus_set()

    def _build(self) -> None:
        shell = ttk.Frame(self, padding=20, style="Panel.TFrame")
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(1, weight=1)

        ttk.Label(shell, text="Add Red Pitaya", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            shell,
            text="Register a board with the RTQLM acquisition engine.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 14))

        ttk.Label(shell, text="Name", style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=6
        )
        ttk.Entry(shell, textvariable=self.name, width=34).grid(
            row=2, column=1, sticky="ew", pady=6
        )

        ttk.Label(shell, text="Host / IP", style="Muted.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 12), pady=6
        )
        self.host_entry = ttk.Entry(shell, textvariable=self.host, width=34)
        self.host_entry.grid(row=3, column=1, sticky="ew", pady=6)

        ttk.Label(shell, text="Channels", style="Muted.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 12), pady=6
        )
        channels = ttk.Frame(shell, style="Panel.TFrame")
        channels.grid(row=4, column=1, sticky="w", pady=6)
        ttk.Checkbutton(channels, text="CH1", variable=self.channel_1).grid(
            row=0, column=0, padx=(0, 12)
        )
        ttk.Checkbutton(channels, text="CH2", variable=self.channel_2).grid(row=0, column=1)

        actions = ttk.Frame(shell, style="Panel.TFrame")
        actions.grid(row=5, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(actions, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Add device", style="Accent.TButton", command=self._submit).grid(
            row=0, column=1
        )

    def _submit(self) -> None:
        host = self.host.get().strip()
        if not host:
            messagebox.showerror("Missing host", "Enter a Red Pitaya host or IP.", parent=self)
            return
        if not self.channel_1.get() and not self.channel_2.get():
            messagebox.showerror("Missing channel", "Enable at least CH1 or CH2.", parent=self)
            return
        self.result = {
            "name": self.name.get().strip() or host,
            "host": host,
            "channel1Enabled": self.channel_1.get(),
            "channel2Enabled": self.channel_2.get(),
        }
        self.destroy()

    def _center(self, parent: tk.Tk) -> None:
        position_dialog(self, parent)


class EngineSettingsDialog(tk.Toplevel):
    def __init__(self, parent: ReceiverApp) -> None:
        super().__init__(parent)
        self.parent = parent
        self.title("Engine Settings")
        self.configure(bg=COLORS["bg"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.api_base = tk.StringVar(value=parent.api_base.get())
        self.launch_on_start = tk.BooleanVar(value=parent.launch_engine_on_start.get())

        self._build()
        self.bind("<Return>", lambda _event: self._save())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        self._center(parent)
        self.api_entry.focus_set()

    def _build(self) -> None:
        shell = ttk.Frame(self, padding=20, style="Panel.TFrame")
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(1, weight=1)

        ttk.Label(shell, text="Acquisition engine", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            shell,
            text="Configure the local RTQLM control endpoint and startup behavior.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 14))

        ttk.Label(shell, text="API base", style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=6
        )
        self.api_entry = ttk.Entry(shell, textvariable=self.api_base, width=42)
        self.api_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)

        ttk.Label(shell, text="Startup", style="Muted.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 12), pady=6
        )
        ttk.Checkbutton(
            shell,
            text="Launch engine on app start",
            variable=self.launch_on_start,
        ).grid(row=3, column=1, columnspan=2, sticky="w", pady=6)

        actions = ttk.Frame(shell, style="Panel.TFrame")
        actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Start engine", command=self._start_engine).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(actions, text="Stop engine", command=self._stop_engine).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(actions, text="Cancel", command=self.destroy).grid(row=0, column=2, padx=(24, 8))
        ttk.Button(actions, text="Save", style="Accent.TButton", command=self._save).grid(
            row=0, column=3
        )

    def _apply_to_parent(self) -> None:
        self.parent.api_base.set(self.api_base.get().strip().rstrip("/"))
        self.parent.launch_engine_on_start.set(self.launch_on_start.get())
        self.parent.save_settings()

    def _save(self) -> None:
        self._apply_to_parent()
        self.destroy()

    def _start_engine(self) -> None:
        self._apply_to_parent()
        self.parent.ensure_engine_running()

    def _stop_engine(self) -> None:
        self.parent.stop_engine()

    def _center(self, parent: tk.Tk) -> None:
        position_dialog(self, parent)


class ReceiverApp(tk.Tk):
    def __init__(self) -> None:
        configure_ui_logging()
        super().__init__()
        self.title("RTQLM · Native receiver")
        self.geometry("1180x760")
        self.minsize(DIMENSIONS["min_width"], DIMENSIONS["min_height"])

        self.settings = load_settings()
        self._configure_style()
        self.ui_colors = COLORS
        self.ui_fonts = FONTS
        self.api_base = tk.StringVar(value=self.settings.api_base)
        self.launch_engine_on_start = tk.BooleanVar(value=self.settings.launch_engine_on_start)
        self.status = tk.StringVar(value="disconnected")
        self.engine_status = tk.StringVar(value="UNKNOWN")
        self.selected = tk.StringVar(value="-")
        self.frames = tk.StringVar(value="0")
        self.sequence = tk.StringVar(value="-")
        self.sample_rate = tk.StringVar(value="-")
        self.samples = tk.StringVar(value="-")
        self.mask = tk.StringVar(value="-")
        self.ch1_range = tk.StringVar(value="-")
        self.ch2_range = tk.StringVar(value="-")
        self.lost1 = tk.StringVar(value="0")
        self.lost2 = tk.StringVar(value="0")
        self.lost_pair = tk.StringVar(value="0 / 0")
        self.flags = tk.StringVar(value="0")
        self.script_status = tk.StringVar(value="signal script disabled")
        self.activity = tk.StringVar(value="")
        self.loading_message = tk.StringVar(value="Ready")
        self.spectrum_channel = tk.StringVar(value="CH1")
        self.spectrum_fft_size = tk.StringVar(value="4096")
        self.spectrum_scale = tk.StringVar(value="dBFS")
        self.spectrum_peak = tk.StringVar(value="Peak: -")
        self.timebase = tk.StringVar(value="- ms/div")
        self.time_zoom_label = tk.StringVar(value="1.0x")
        self.time_window_label = tk.StringVar(value="Full capture")
        self.time_span_choice = tk.StringVar(value=DEFAULT_TIME_SPAN)
        self.display_mode = tk.StringVar(value="Auto")
        self.signal_resolution = tk.StringVar(value=DEFAULT_SIGNAL_RESOLUTION)
        self.pause_label = tk.StringVar(value="Pause")
        self.display_detail = tk.StringVar(value="Live")
        self.diagnostics_expanded = tk.BooleanVar(value=False)
        self.waveform_advanced_expanded = tk.BooleanVar(value=False)
        self.device_empty_message = tk.StringVar(value="No Red Pitayas registered")
        self.connection_summary = tk.StringVar(value="No device selected")
        self.display_paused = False
        self.paused_frame: SampleFrame | None = None
        self.paused_history: SampleHistory | None = None
        self.pending_time_span_choice = True
        self.signal_padding_samples = 0
        self.last_display_monotonic_ns = 0
        self.cursor_status = tk.StringVar(value="Cursor off")
        self.y_axis_range_label = tk.StringVar(value="Y range ±1.00 V")
        self.channel_1_visible = tk.BooleanVar(value=True)
        self.channel_2_visible = tk.BooleanVar(value=True)
        self.channel_1_offset_divisions = tk.DoubleVar(value=0.0)
        self.channel_2_offset_divisions = tk.DoubleVar(value=0.0)
        self.channel_1_offset_label = tk.StringVar(value="CH1 0.0 div")
        self.channel_2_offset_label = tk.StringVar(value="CH2 0.0 div")
        self.acquisition_decimation = tk.StringVar(value="64")
        self.acquisition_block_size = tk.StringVar(value="16384")
        self.acquisition_rate = tk.StringVar(value="Sample rate: 1.95 MHz")
        self.acquisition_batch = tk.StringVar(value="Batch: -")
        self.signal_scripts_enabled = signal_script_feature_enabled()
        self.signal_script = DEFAULT_SIGNAL_SCRIPT
        self.signal_processor: Callable[[dict[str, np.ndarray[Any, Any]], Any], Any] | None = None
        self.cluster_processor = RealtimeClusterProcessor(load_cluster_config())
        self.cluster_lock = threading.Lock()
        self.latest_cluster_result: ClusterProcessingResult | None = None
        self.cluster_error: str | None = None
        self.cluster_last_sequence: int | None = None
        self.cluster_dialog: tk.Toplevel | None = None

        self.devices: list[Device] = []
        self.selected_device: Device | None = None
        self.open_device_ids: list[str] = []
        self.tab_hitboxes: list[tuple[float, float, str]] = []
        self.socket_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.frame_count = 0
        self.socket_frame_count = 0
        self.rendered_frame_count = 0
        self.latest_frame: SampleFrame | None = None
        self.sample_history = SampleHistory()
        self.frame_lock = threading.Lock()
        self.last_render_monotonic_ns = 0
        self.last_stats_monotonic_ns = 0
        self.grid_items: list[int] = []
        self.trace_items: list[int] = []
        self.spectrum_grid_items: list[int] = []
        self.spectrum_trace_items: list[int] = []
        self.signal_notice_items: list[int] = []
        self.spectrum_notice_items: list[int] = []
        self.axis_y_abs = 1.0
        self.y_axis_fit_pending = True
        self.y_axis_dirty = False
        self.axis_sample_rate_hz = 0.0
        self.axis_samples_per_channel = 0
        self.time_zoom = 1.0
        self.time_pan = 0.0
        self.cursor_enabled = False
        self.spectrum_axis_sample_rate_hz = 0.0
        self.spectrum_axis_scale = self.spectrum_scale.get()
        self.last_spectrum_monotonic_ns = 0
        self.last_frame_log_monotonic_ns = 0
        self.loading_active = False
        self.loading_label = ""
        self.spinner_running = False
        self.spinner_index = 0
        self.spinner_token = 0
        self.engine_status_dot: tk.Canvas | None = None
        self.engine_status_dot_item: int | None = None
        self.toast_frames: list[tk.Toplevel] = []
        self.connection_failure_active = False
        self.connection_overlay: tk.Toplevel | None = None
        self.connection_overlay_canvas: tk.Canvas | None = None
        self.connection_overlay_title = tk.StringVar(value="")
        self.connection_overlay_detail = tk.StringVar(value="")
        self.connection_overlay_bg_rect: int | None = None
        self.connection_overlay_spinner_track: int | None = None
        self.connection_overlay_spinner_arc: int | None = None
        self.connection_overlay_title_item: int | None = None
        self.connection_overlay_detail_item: int | None = None
        self.engine_ping_in_flight = False
        self.signal_resize_after_id: str | None = None
        self.spectrum_resize_after_id: str | None = None
        self.control_layout_after_id: str | None = None
        self._last_control_layout = ""
        self.closing = False
        self.engine_process: subprocess.Popen[str] | None = None

        self._build_menu()
        self._build()
        self._bind_shortcuts()
        self._set_engine_status("UNKNOWN")
        self._ui_log("app opened", api_base=self.api_base.get())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(UI_FRAME_INTERVAL_MS, self._drain_events)
        self.after(1000, self._ping_engine_status_async)
        if self.launch_engine_on_start.get():
            self.after(100, self.ensure_engine_running)
            self.after(2500, self.refresh_devices)
        else:
            self.refresh_devices()

    def _configure_style(self) -> None:
        self.configure(bg=COLORS["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["surface"])
        style.configure("Sidebar.TFrame", background=COLORS["sidebar"])
        style.configure("Surface.TFrame", background=COLORS["surface"])
        style.configure("Toolbar.TFrame", background=COLORS["surface_elevated"])
        style.configure("Metric.TFrame", background=COLORS["surface_elevated"])
        style.configure("Status.TFrame", background=COLORS["surface"])
        style.configure("ControlGroup.TFrame", background=COLORS["surface_elevated"])
        style.configure("Diagnostics.TFrame", background=COLORS["bg"])
        style.configure(
            "TLabelframe",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border_subtle"],
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background=COLORS["surface"],
            foreground=COLORS["text_secondary"],
            font=FONTS["body_bold"],
        )
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure(
            "Muted.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text_secondary"],
            font=FONTS["body"],
        )
        style.configure(
            "SidebarMuted.TLabel",
            background=COLORS["sidebar"],
            foreground=COLORS["text_secondary"],
            font=FONTS["body"],
        )
        style.configure(
            "Tooltip.TLabel",
            background=COLORS["panel_3"],
            foreground=COLORS["text"],
            bordercolor=COLORS["grid"],
            relief="solid",
            font=FONTS["body"],
        )
        style.configure(
            "SidebarTitle.TLabel",
            background=COLORS["sidebar"],
            foreground=COLORS["text"],
            font=FONTS["title"],
        )
        style.configure(
            "Title.TLabel",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            font=("Segoe UI", 12, "bold"),
        )
        style.configure(
            "Section.TLabel",
            background=COLORS["panel_2"],
            foreground=COLORS["text"],
            font=FONTS["section"],
        )
        style.configure(
            "Toolbar.TLabel",
            background=COLORS["panel_2"],
            foreground=COLORS["text_secondary"],
            font=FONTS["body"],
        )
        style.configure(
            "Value.TLabel",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            font=FONTS["mono"],
        )
        style.configure(
            "MetricValue.TLabel",
            background=COLORS["panel_2"],
            foreground=COLORS["text"],
            font=FONTS["mono_bold"],
        )
        style.configure(
            "StatusLabel.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text_secondary"],
            font=FONTS["small"],
        )
        style.configure(
            "StatusValue.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["mono_bold"],
        )
        style.configure(
            "GroupLabel.TLabel",
            background=COLORS["surface_elevated"],
            foreground=COLORS["text_secondary"],
            font=FONTS["body_bold"],
        )
        style.configure(
            "TEntry",
            fieldbackground=COLORS["panel_2"],
            readonlybackground=COLORS["panel_2"],
            foreground=COLORS["text"],
            bordercolor=COLORS["grid"],
            insertcolor=COLORS["text"],
        )
        style.map(
            "TEntry",
            fieldbackground=[
                ("readonly", COLORS["panel_2"]),
                ("focus", COLORS["panel_2"]),
                ("disabled", COLORS["panel"]),
            ],
            foreground=[("readonly", COLORS["text"]), ("disabled", COLORS["muted"])],
            bordercolor=[("focus", COLORS["accent"]), ("readonly", COLORS["grid"])],
        )
        style.configure(
            "TButton",
            background=COLORS["surface_elevated"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border_subtle"],
            focuscolor=COLORS["accent"],
            focusthickness=1,
            padding=(10, 5),
        )
        style.map(
            "TButton",
            background=[
                ("disabled", COLORS["surface"]),
                ("active", COLORS["hover"]),
                ("pressed", COLORS["surface_active"]),
            ],
            foreground=[("disabled", COLORS["text_disabled"])],
            bordercolor=[("focus", COLORS["accent"]), ("active", COLORS["border_strong"])],
        )
        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground="#ffffff",
            bordercolor=COLORS["accent"],
        )
        style.map(
            "Accent.TButton",
            background=[
                ("disabled", COLORS["surface_active"]),
                ("active", COLORS["accent_hover"]),
                ("pressed", COLORS["accent"]),
            ],
            foreground=[("disabled", COLORS["text_disabled"])],
        )
        style.configure(
            "Danger.TButton",
            background=COLORS["surface_elevated"],
            foreground=COLORS["danger"],
            bordercolor=COLORS["border_subtle"],
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#3a2029"), ("pressed", "#46232e")],
            bordercolor=[("focus", COLORS["danger"]), ("active", COLORS["danger"])],
        )
        style.configure(
            "Active.TButton",
            background=COLORS["selected"],
            foreground=COLORS["text"],
            bordercolor=COLORS["accent"],
        )
        style.map(
            "Active.TButton",
            background=[("active", COLORS["selected"]), ("pressed", COLORS["surface_active"])],
        )
        style.configure("Compact.TButton", padding=(8, 4))
        style.configure(
            "TCheckbutton",
            background=COLORS["bg"],
            foreground=COLORS["text"],
            indicatorcolor=COLORS["panel_2"],
        )
        style.configure(
            "Toolbar.TCheckbutton",
            background=COLORS["surface_elevated"],
            foreground=COLORS["text"],
            indicatorcolor=COLORS["surface_active"],
            padding=(4, 2),
        )
        style.map(
            "Toolbar.TCheckbutton",
            indicatorcolor=[("selected", COLORS["accent"]), ("disabled", COLORS["surface"])],
            foreground=[("disabled", COLORS["text_disabled"])],
        )
        style.configure("CH1.Toolbar.TCheckbutton", foreground=COLORS["ch1"])
        style.configure("CH2.Toolbar.TCheckbutton", foreground=COLORS["ch2"])
        style.configure(
            "TCombobox",
            fieldbackground=COLORS["panel_2"],
            background=COLORS["panel_2"],
            foreground=COLORS["text"],
            bordercolor=COLORS["grid"],
            arrowcolor=COLORS["text"],
            selectbackground=COLORS["panel_2"],
            selectforeground=COLORS["text"],
            insertcolor=COLORS["text"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[
                ("readonly", COLORS["panel_2"]),
                ("focus", COLORS["panel_2"]),
                ("disabled", COLORS["panel"]),
            ],
            background=[
                ("readonly", COLORS["panel_2"]),
                ("active", COLORS["panel_3"]),
                ("disabled", COLORS["panel"]),
            ],
            foreground=[("readonly", COLORS["text"]), ("disabled", COLORS["muted"])],
            selectbackground=[
                ("readonly", COLORS["panel_2"]),
                ("focus", COLORS["panel_2"]),
            ],
            selectforeground=[("readonly", COLORS["text"]), ("focus", COLORS["text"])],
            bordercolor=[("focus", COLORS["accent"]), ("readonly", COLORS["grid"])],
            arrowcolor=[
                ("disabled", COLORS["muted"]),
                ("active", "#ffffff"),
                ("readonly", COLORS["text"]),
            ],
        )
        self.option_add("*TCombobox*Listbox.background", COLORS["panel_2"])
        self.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style.configure(
            "Device.Treeview",
            background=COLORS["sidebar"],
            fieldbackground=COLORS["sidebar"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border_subtle"],
            rowheight=52,
            font=FONTS["body"],
        )
        style.map(
            "Device.Treeview",
            background=[("selected", COLORS["selected"])],
            foreground=[("selected", COLORS["text"])],
            bordercolor=[("focus", COLORS["accent"])],
        )
        style.configure(
            "Device.Treeview.Heading",
            background=COLORS["sidebar"],
            foreground=COLORS["text_secondary"],
            relief="flat",
            font=FONTS["small"],
        )
        style.configure("TSeparator", background=COLORS["border_subtle"])

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        self.config(menu=menu)

        engine_menu = tk.Menu(menu, tearoff=False)
        engine_menu.add_command(
            label="Start acquisition engine", command=self.ensure_engine_running
        )
        engine_menu.add_command(label="Stop acquisition engine", command=self.stop_engine)
        engine_menu.add_separator()
        engine_menu.add_checkbutton(
            label="Launch engine on app start",
            variable=self.launch_engine_on_start,
            command=self.save_settings,
        )
        engine_menu.add_command(label="Engine settings…", command=self.open_engine_settings)
        menu.add_cascade(label="Engine", menu=engine_menu)

        self.redpitaya_menu = tk.Menu(menu, tearoff=False)
        menu.add_cascade(label="Red Pitayas", menu=self.redpitaya_menu)
        self._populate_redpitaya_menu()

        signal_menu = tk.Menu(menu, tearoff=False)
        signal_menu.add_command(
            label="Python signal editor…",
            command=self.open_signal_editor,
            state=tk.NORMAL if self._signal_scripting_enabled() else tk.DISABLED,
        )
        signal_menu.add_command(label="Disable signal script", command=self.clear_signal_script)
        signal_menu.add_separator()
        signal_menu.add_command(label="1D cluster nullifiers…", command=self.open_cluster_monitor)
        menu.add_cascade(label="Signal", menu=signal_menu)

        view_menu = tk.Menu(menu, tearoff=False)
        view_menu.add_command(label="Redraw plots", command=self._redraw_all_grids)
        menu.add_cascade(label="View", menu=view_menu)

    def _build(self) -> None:
        self.columnconfigure(0, minsize=DIMENSIONS["sidebar_width"])
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, style="Sidebar.TFrame", padding=(16, 14, 14, 14))
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.rowconfigure(4, weight=1)
        sidebar.columnconfigure(0, weight=1)

        brand = ttk.Frame(sidebar, style="Sidebar.TFrame")
        brand.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        brand.columnconfigure(0, weight=1)
        ttk.Label(brand, text="RTQLM", style="SidebarTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(brand, text="Quantum-light receiver", style="SidebarMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(1, 0)
        )

        engine_card = tk.Frame(
            sidebar,
            bg=COLORS["surface_elevated"],
            highlightbackground=COLORS["border_subtle"],
            highlightthickness=1,
            padx=10,
            pady=8,
        )
        engine_card.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        engine_card.columnconfigure(1, weight=1)
        self.engine_status_dot = tk.Canvas(
            engine_card,
            width=12,
            height=12,
            bg=COLORS["surface_elevated"],
            highlightthickness=0,
            bd=0,
        )
        self.engine_status_dot.grid(row=0, column=0, rowspan=2, padx=(0, 8))
        self.engine_status_dot_item = self.engine_status_dot.create_oval(
            2, 2, 10, 10, fill=COLORS["danger"], outline=COLORS["danger"]
        )
        tk.Label(
            engine_card,
            text="Acquisition engine",
            bg=COLORS["surface_elevated"],
            fg=COLORS["text_secondary"],
            font=FONTS["small"],
            anchor="w",
        ).grid(row=0, column=1, sticky="ew")
        tk.Label(
            engine_card,
            textvariable=self.engine_status,
            bg=COLORS["surface_elevated"],
            fg=COLORS["text"],
            font=FONTS["body_bold"],
            anchor="w",
        ).grid(row=1, column=1, sticky="ew")

        device_heading = ttk.Frame(sidebar, style="Sidebar.TFrame")
        device_heading.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        device_heading.columnconfigure(0, weight=1)
        ttk.Label(device_heading, text="RED PITAYAS", style="SidebarMuted.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(
            device_heading,
            text="Refresh",
            style="Compact.TButton",
            command=self.refresh_devices,
        ).grid(row=0, column=1, padx=(4, 0))
        ttk.Button(
            device_heading,
            text="Add device",
            style="Compact.TButton",
            command=self.add_device,
        ).grid(row=0, column=2, padx=(4, 0))

        ttk.Label(
            sidebar,
            textvariable=self.activity,
            style="SidebarMuted.TLabel",
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", pady=(0, 6))

        self.device_list = ttk.Treeview(
            sidebar,
            columns=("state", "channels"),
            show="tree headings",
            selectmode="browse",
            style="Device.Treeview",
            height=5,
        )
        self.device_list.heading("#0", text="Device", anchor="w")
        self.device_list.heading("state", text="State", anchor="w")
        self.device_list.heading("channels", text="Inputs", anchor="w")
        self.device_list.column("#0", width=118, minwidth=92, stretch=True)
        self.device_list.column("state", width=70, minwidth=62, stretch=False)
        self.device_list.column("channels", width=60, minwidth=54, stretch=False)
        self.device_list.grid(row=4, column=0, sticky="nsew")
        self.device_list.bind("<<TreeviewSelect>>", self._select_from_list)
        self.device_list.bind("<Double-Button-1>", lambda _event: self.connect_selected())

        side_actions = ttk.Frame(sidebar, style="Sidebar.TFrame")
        side_actions.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        side_actions.columnconfigure(0, weight=1)
        side_actions.columnconfigure(1, weight=1)
        self.connect_button = ttk.Button(
            side_actions,
            text="Connect",
            style="Accent.TButton",
            command=self.connect_selected,
            state=tk.DISABLED,
        )
        self.connect_button.grid(row=0, column=0, columnspan=2, sticky="ew")
        ToolTip(
            self.connect_button,
            "Select a Red Pitaya first. A streaming board attaches directly to the live display.",
        )
        self.disconnect_button = ttk.Button(
            side_actions,
            text="Disconnect device",
            command=self.disconnect_selected,
        )
        self.disconnect_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.disconnect_button.grid_remove()
        self.channel_1_button = ttk.Button(
            side_actions,
            text="Acq CH1",
            style="Compact.TButton",
            command=lambda: self.toggle_channel(1),
        )
        self.channel_1_button.grid(row=2, column=0, sticky="ew", padx=(0, 3), pady=(8, 0))
        self.channel_2_button = ttk.Button(
            side_actions,
            text="Acq CH2",
            style="Compact.TButton",
            command=lambda: self.toggle_channel(2),
        )
        self.channel_2_button.grid(row=2, column=1, sticky="ew", padx=(3, 0), pady=(8, 0))
        self.forget_button = ttk.Button(
            side_actions,
            text="Forget device",
            style="Danger.TButton",
            command=self.forget_selected,
        )
        self.forget_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        ttk.Separator(sidebar).grid(row=6, column=0, sticky="ew", pady=14)
        acquisition_panel = ttk.Frame(sidebar, style="Sidebar.TFrame")
        acquisition_panel.grid(row=7, column=0, sticky="ew")
        acquisition_panel.columnconfigure(1, weight=1)
        ttk.Label(acquisition_panel, text="ACQUISITION", style="SidebarMuted.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        decimation_label = ttk.Label(
            acquisition_panel, text="Decimation (÷)", style="SidebarMuted.TLabel"
        )
        decimation_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(0, 6))
        decimation_box = ttk.Combobox(
            acquisition_panel,
            textvariable=self.acquisition_decimation,
            values=(
                "1",
                "2",
                "4",
                "8",
                "16",
                "32",
                "64",
                "128",
                "256",
                "512",
                "1024",
                "2048",
                "4096",
                "8192",
                "65536",
            ),
            state="readonly",
            width=8,
        )
        decimation_box.grid(row=1, column=1, sticky="ew", pady=(0, 6))
        decimation_box.bind("<<ComboboxSelected>>", lambda _event: self._update_acquisition_rate())
        block_label = ttk.Label(acquisition_panel, text="Block size", style="SidebarMuted.TLabel")
        block_label.grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(0, 6))
        block_box = ttk.Combobox(
            acquisition_panel,
            textvariable=self.acquisition_block_size,
            values=("2048", "4096", "8192", "16384", "32768", "65536", "131072", "262144"),
            state="readonly",
            width=8,
        )
        block_box.grid(row=2, column=1, sticky="ew", pady=(0, 6))
        block_box.bind("<<ComboboxSelected>>", lambda _event: self._update_acquisition_rate())
        ttk.Label(
            acquisition_panel, textvariable=self.acquisition_rate, style="SidebarMuted.TLabel"
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(
            acquisition_panel, textvariable=self.acquisition_batch, style="SidebarMuted.TLabel"
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(1, 8))
        low_delay_button = ttk.Button(
            acquisition_panel,
            text="Low delay",
            style="Compact.TButton",
            command=self.apply_fast_acquisition,
        )
        low_delay_button.grid(row=5, column=0, sticky="ew", padx=(0, 4))
        stable_button = ttk.Button(
            acquisition_panel,
            text="2CH stable",
            style="Compact.TButton",
            command=self.apply_stable_two_channel,
        )
        stable_button.grid(row=5, column=1, sticky="ew", padx=(4, 0))
        self.apply_acquisition_button = ttk.Button(
            acquisition_panel, text="Apply settings", command=self.apply_acquisition_settings
        )
        self.apply_acquisition_button.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ToolTip(
            decimation_label,
            "Divides the 125 MHz ADC clock. Lower numbers sample faster and create more data.",
        )
        ToolTip(
            decimation_box,
            "Sample rate is 125 MHz divided by this value.",
        )
        ToolTip(
            block_label,
            "How many bytes the board sends per batch. Smaller feels quicker; larger is steadier.",
        )
        ToolTip(
            block_box,
            "For two channels, each batch is split between CH1 and CH2.",
        )
        ToolTip(low_delay_button, "Chooses a small batch at the current decimation.")
        ToolTip(stable_button, "Uses 1024 decimation and 32768-byte blocks for two-channel tests.")

        main = ttk.Frame(self, style="Surface.TFrame")
        self.main_surface = main
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(5, weight=7)
        main.rowconfigure(8, weight=3)

        workspace_header = ttk.Frame(main, style="Surface.TFrame", padding=(14, 10, 14, 8))
        workspace_header.grid(row=0, column=0, sticky="ew")
        workspace_header.columnconfigure(1, weight=1)
        ttk.Label(workspace_header, text="Acquisition workspace", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            workspace_header,
            textvariable=self.connection_summary,
            style="Muted.TLabel",
            anchor="e",
        ).grid(row=0, column=1, sticky="e")

        self.tab_bar = tk.Canvas(
            main,
            height=34,
            bg=COLORS["bg"],
            highlightthickness=0,
            bd=0,
        )
        self.tab_bar.grid(row=1, column=0, sticky="ew")
        self.tab_bar.bind("<Configure>", lambda _event: self._draw_tabs())
        self.tab_bar.bind("<Button-1>", self._select_tab_at)

        status_strip = ttk.Frame(main, style="Status.TFrame", padding=(14, 8))
        status_strip.grid(row=2, column=0, sticky="ew")
        for column in range(5):
            status_strip.columnconfigure(column, weight=1)
        self._compact_stat(status_strip, "Acquisition", self.status, 0)
        self._compact_stat(status_strip, "Selected device", self.selected, 1)
        self._compact_stat(status_strip, "Sample rate", self.sample_rate, 2)
        self._compact_stat(status_strip, "Lost samples CH1 / CH2", self.lost_pair, 3)
        self.diagnostics_button = ttk.Button(
            status_strip,
            text="Diagnostics ▾",
            style="Compact.TButton",
            command=self._toggle_diagnostics,
        )
        self.diagnostics_button.grid(row=0, column=4, rowspan=2, sticky="e", padx=(12, 0))
        ttk.Label(
            status_strip,
            textvariable=self.loading_message,
            style="StatusLabel.TLabel",
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(5, 0))

        self.diagnostics_panel = ttk.Frame(main, style="Diagnostics.TFrame", padding=(14, 6))
        for column in range(4):
            self.diagnostics_panel.columnconfigure(column, weight=1)
        self._compact_stat(self.diagnostics_panel, "Rendered frames", self.frames, 0, surface="bg")
        self._compact_stat(self.diagnostics_panel, "Sequence", self.sequence, 1, surface="bg")
        self._compact_stat(
            self.diagnostics_panel, "Samples / channel", self.samples, 2, surface="bg"
        )
        self._compact_stat(self.diagnostics_panel, "Protocol flags", self.flags, 3, surface="bg")

        signal_header = ttk.Frame(main, style="Toolbar.TFrame", padding=(12, 8))
        signal_header.grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 0))
        signal_header.columnconfigure(1, weight=1)
        ttk.Label(signal_header, text="Live waveform", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        legend = ttk.Frame(signal_header, style="Toolbar.TFrame")
        legend.grid(row=0, column=1, sticky="e")
        ttk.Checkbutton(
            legend,
            text="CH1",
            variable=self.channel_1_visible,
            command=self._redraw_latest_signal,
            style="CH1.Toolbar.TCheckbutton",
        ).grid(row=0, column=0, padx=(0, 10))
        ttk.Checkbutton(
            legend,
            text="CH2",
            variable=self.channel_2_visible,
            command=self._redraw_latest_signal,
            style="CH2.Toolbar.TCheckbutton",
        ).grid(row=0, column=1, padx=(0, 16))
        ttk.Label(legend, textvariable=self.ch1_range, width=25, style="Toolbar.TLabel").grid(
            row=0, column=2, padx=(0, 12)
        )
        ttk.Label(legend, textvariable=self.ch2_range, width=25, style="Toolbar.TLabel").grid(
            row=0, column=3
        )

        self.canvas = tk.Canvas(
            main,
            bg=COLORS["bg_deep"],
            highlightthickness=1,
            highlightbackground=COLORS["grid"],
            relief="flat",
        )
        self.canvas.grid(row=5, column=0, sticky="nsew", padx=12)
        self.canvas.bind("<Configure>", self._resize_signal_plot)
        self._set_signal_notice("No live signal\nSelect a Red Pitaya and connect to begin")

        self.signal_controls = ttk.Frame(main, style="Toolbar.TFrame", padding=(10, 8))
        self.signal_controls.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.signal_controls.columnconfigure((0, 1), weight=1)

        self.time_control_group = ttk.Frame(self.signal_controls, style="ControlGroup.TFrame")
        ttk.Label(self.time_control_group, text="TIME", style="GroupLabel.TLabel").pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            self.time_control_group,
            text="Zoom out",
            style="Compact.TButton",
            command=lambda: self.zoom_time(0.5),
        ).pack(side="left", padx=2)
        ttk.Button(
            self.time_control_group,
            text="Zoom in",
            style="Compact.TButton",
            command=lambda: self.zoom_time(2.0),
        ).pack(side="left", padx=2)
        ttk.Button(
            self.time_control_group,
            text="◀",
            width=3,
            style="Compact.TButton",
            command=lambda: self.pan_time(-0.12),
        ).pack(side="left", padx=(6, 2))
        ttk.Button(
            self.time_control_group,
            text="▶",
            width=3,
            style="Compact.TButton",
            command=lambda: self.pan_time(0.12),
        ).pack(side="left", padx=2)
        ttk.Button(
            self.time_control_group, text="Fit", style="Compact.TButton", command=self.fit_time
        ).pack(side="left", padx=(6, 6))
        ttk.Label(
            self.time_control_group, textvariable=self.timebase, style="Toolbar.TLabel", width=14
        ).pack(side="left")

        self.vertical_control_group = ttk.Frame(self.signal_controls, style="ControlGroup.TFrame")
        ttk.Label(self.vertical_control_group, text="VERTICAL", style="GroupLabel.TLabel").pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            self.vertical_control_group,
            text="Zoom in",
            style="Compact.TButton",
            command=lambda: self.zoom_y_axis(0.5),
        ).pack(side="left", padx=2)
        ttk.Button(
            self.vertical_control_group,
            text="Zoom out",
            style="Compact.TButton",
            command=lambda: self.zoom_y_axis(2.0),
        ).pack(side="left", padx=2)
        ttk.Button(
            self.vertical_control_group,
            text="Fit",
            style="Compact.TButton",
            command=self.fit_y_axis,
        ).pack(side="left", padx=(6, 6))
        ttk.Label(
            self.vertical_control_group,
            textvariable=self.y_axis_range_label,
            style="Toolbar.TLabel",
            width=18,
        ).pack(side="left")

        self.channel_control_group = ttk.Frame(self.signal_controls, style="ControlGroup.TFrame")
        ttk.Label(self.channel_control_group, text="CHANNELS", style="GroupLabel.TLabel").pack(
            side="left", padx=(0, 8)
        )
        self.cursor_button = ttk.Button(
            self.channel_control_group,
            text="Cursor",
            style="Compact.TButton",
            command=self.toggle_cursor,
        )
        self.cursor_button.pack(side="left", padx=(0, 6))
        ttk.Button(
            self.channel_control_group,
            text="CH1 -",
            width=6,
            style="Compact.TButton",
            command=lambda: self.nudge_channel_offset(1, -0.25),
        ).pack(side="left", padx=2)
        ttk.Button(
            self.channel_control_group,
            text="CH1 +",
            width=6,
            style="Compact.TButton",
            command=lambda: self.nudge_channel_offset(1, 0.25),
        ).pack(side="left", padx=2)
        ttk.Button(
            self.channel_control_group,
            text="CH2 -",
            width=6,
            style="Compact.TButton",
            command=lambda: self.nudge_channel_offset(2, -0.25),
        ).pack(side="left", padx=(6, 2))
        ttk.Button(
            self.channel_control_group,
            text="CH2 +",
            width=6,
            style="Compact.TButton",
            command=lambda: self.nudge_channel_offset(2, 0.25),
        ).pack(side="left", padx=2)
        ttk.Button(
            self.channel_control_group,
            text="Reset offsets",
            style="Compact.TButton",
            command=self.reset_channel_offsets,
        ).pack(side="left", padx=(6, 0))

        self.display_control_group = ttk.Frame(self.signal_controls, style="ControlGroup.TFrame")
        ttk.Label(self.display_control_group, text="DISPLAY", style="GroupLabel.TLabel").pack(
            side="left", padx=(0, 8)
        )
        ttk.Label(self.display_control_group, text="Span", style="Toolbar.TLabel").pack(side="left")
        span_box = ttk.Combobox(
            self.display_control_group,
            textvariable=self.time_span_choice,
            values=tuple(TIME_SPANS_MS),
            state="readonly",
            width=10,
        )
        span_box.pack(side="left", padx=(5, 10))
        span_box.bind("<<ComboboxSelected>>", lambda _event: self.select_time_span())
        speed_box = ttk.Combobox(
            self.display_control_group,
            textvariable=self.display_mode,
            values=("Auto", "Live", "Slow"),
            state="readonly",
            width=7,
        )
        speed_box.pack(side="left", padx=(0, 8))
        speed_box.bind("<<ComboboxSelected>>", lambda _event: self._update_display_detail())
        resolution_box = ttk.Combobox(
            self.display_control_group,
            textvariable=self.signal_resolution,
            values=tuple(SIGNAL_RESOLUTION_POINTS),
            state="readonly",
            width=7,
        )
        resolution_box.pack(side="left", padx=(0, 8))
        resolution_box.bind("<<ComboboxSelected>>", lambda _event: self._redraw_latest_signal())
        self.pause_button = ttk.Button(
            self.display_control_group,
            textvariable=self.pause_label,
            style="Compact.TButton",
            command=self.toggle_display_pause,
        )
        self.pause_button.pack(side="left", padx=(0, 8))
        ttk.Label(
            self.display_control_group,
            textvariable=self.display_detail,
            style="Toolbar.TLabel",
            width=22,
        ).pack(side="left")
        self.waveform_more_button = ttk.Button(
            self.signal_controls,
            text="Vertical & channel controls ▾",
            style="Compact.TButton",
            command=self._toggle_waveform_advanced,
        )
        main.bind("<Configure>", self._on_main_configure, add="+")

        spectrum_controls = ttk.Frame(main, style="Toolbar.TFrame", padding=(12, 8))
        spectrum_controls.grid(row=7, column=0, sticky="ew", padx=12)
        spectrum_controls.columnconfigure(6, weight=1)
        ttk.Label(spectrum_controls, text="Spectrum analyzer", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 14)
        )
        ttk.Label(spectrum_controls, text="Channel", style="Toolbar.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        channel_box = ttk.Combobox(
            spectrum_controls,
            textvariable=self.spectrum_channel,
            values=("CH1", "CH2"),
            state="readonly",
            width=7,
        )
        channel_box.grid(row=0, column=2, sticky="w", padx=(5, 12))
        ttk.Label(spectrum_controls, text="FFT", style="Toolbar.TLabel").grid(
            row=0, column=3, sticky="w"
        )
        fft_box = ttk.Combobox(
            spectrum_controls,
            textvariable=self.spectrum_fft_size,
            values=("1024", "2048", "4096", "8192", "16384"),
            state="readonly",
            width=8,
        )
        fft_box.grid(row=0, column=4, sticky="w", padx=(5, 12))
        scale_box = ttk.Combobox(
            spectrum_controls,
            textvariable=self.spectrum_scale,
            values=("dBFS", "Linear"),
            state="readonly",
            width=9,
        )
        scale_box.grid(row=0, column=5, sticky="w", padx=(0, 12))
        self.python_signal_button = ttk.Button(
            spectrum_controls,
            text="Python signal",
            style="Compact.TButton",
            command=self.open_signal_editor,
            state=tk.NORMAL if self._signal_scripting_enabled() else tk.DISABLED,
        )
        self.python_signal_button.grid(row=1, column=1, columnspan=2, sticky="w", pady=(7, 0))
        self.cluster_tools_button = ttk.Button(
            spectrum_controls,
            text="Cluster nullifiers",
            style="Compact.TButton",
            command=self.open_cluster_monitor,
        )
        self.cluster_tools_button.grid(
            row=1, column=3, columnspan=2, sticky="w", pady=(7, 0), padx=(8, 0)
        )
        self.advanced_tools_label = ttk.Label(
            spectrum_controls, text="Advanced", style="Toolbar.TLabel"
        )
        self.advanced_tools_label.grid(row=1, column=0, sticky="w", pady=(7, 0))
        ttk.Label(
            spectrum_controls,
            textvariable=self.spectrum_peak,
            style="MetricValue.TLabel",
        ).grid(row=0, column=6, sticky="e")
        channel_box.bind("<<ComboboxSelected>>", lambda _event: self._redraw_latest_spectrum())
        fft_box.bind("<<ComboboxSelected>>", lambda _event: self._redraw_latest_spectrum())
        scale_box.bind("<<ComboboxSelected>>", lambda _event: self._redraw_latest_spectrum())

        self.spectrum_canvas = tk.Canvas(
            main,
            height=140,
            bg=COLORS["bg_deep"],
            highlightthickness=1,
            highlightbackground=COLORS["grid"],
            relief="flat",
        )
        self.spectrum_canvas.grid(row=8, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.spectrum_canvas.bind("<Configure>", self._resize_spectrum_plot)
        self._set_spectrum_notice("Waiting for spectrum data")
        self._build_connection_overlay(main)
        self._draw_tabs()
        self._update_time_controls()
        self._update_connect_controls()
        self._layout_waveform_controls(900)

    @staticmethod
    def _compact_stat(
        parent: tk.Misc,
        title: str,
        value: tk.StringVar,
        column: int,
        *,
        surface: str = "surface",
    ) -> None:
        background = COLORS[surface]
        frame = tk.Frame(parent, bg=background, padx=2, pady=1)
        frame.grid(row=0, column=column, sticky="ew", padx=(0, 12))
        tk.Label(
            frame,
            text=title,
            bg=background,
            fg=COLORS["text_secondary"],
            font=FONTS["small"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            frame,
            textvariable=value,
            bg=background,
            fg=COLORS["text"],
            font=FONTS["mono_bold"],
            anchor="w",
            width=15,
        ).grid(row=1, column=0, sticky="ew")

    def _toggle_diagnostics(self) -> None:
        expanded = not self.diagnostics_expanded.get()
        self.diagnostics_expanded.set(expanded)
        if expanded:
            self.diagnostics_panel.grid(row=3, column=0, sticky="ew")
            self.diagnostics_button.configure(text="Diagnostics ▴", style="Active.TButton")
        else:
            self.diagnostics_panel.grid_remove()
            self.diagnostics_button.configure(text="Diagnostics ▾", style="Compact.TButton")

    def _on_main_configure(self, event: tk.Event[Any]) -> None:
        if event.widget is not self.main_surface:
            return
        if self.control_layout_after_id is not None:
            try:
                self.after_cancel(self.control_layout_after_id)
            except tk.TclError:
                pass
        width = event.width
        self.control_layout_after_id = self.after_idle(
            lambda: self._layout_waveform_controls(width)
        )

    def _layout_waveform_controls(self, width: int) -> None:
        self.control_layout_after_id = None
        # At the default window width, keep expert controls one click away so
        # the waveform remains dominant. Larger lab displays reveal them inline.
        layout = "wide" if width >= 1040 else "compact"
        expanded = self.waveform_advanced_expanded.get()
        layout_key = f"{layout}:{expanded}"
        if layout_key == self._last_control_layout:
            return
        self._last_control_layout = layout_key
        groups = (
            self.time_control_group,
            self.vertical_control_group,
            self.channel_control_group,
            self.display_control_group,
        )
        for group in groups:
            group.grid_forget()
        self.waveform_more_button.grid_forget()
        if layout == "wide":
            self.time_control_group.grid(row=0, column=0, sticky="w", padx=(0, 12))
            self.vertical_control_group.grid(row=0, column=1, sticky="e")
            self.channel_control_group.grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 0))
            self.display_control_group.grid(row=2, column=0, columnspan=2, sticky="w", pady=(7, 0))
        else:
            self.time_control_group.grid(row=0, column=0, columnspan=2, sticky="w")
            self.display_control_group.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
            self.waveform_more_button.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
            if expanded:
                self.vertical_control_group.grid(
                    row=3, column=0, columnspan=2, sticky="w", pady=(6, 0)
                )
                self.channel_control_group.grid(
                    row=4, column=0, columnspan=2, sticky="w", pady=(6, 0)
                )
        show_spectrum_advanced = layout == "wide"
        for widget in (
            self.advanced_tools_label,
            self.python_signal_button,
            self.cluster_tools_button,
        ):
            if show_spectrum_advanced:
                widget.grid()
            else:
                widget.grid_remove()

    def _toggle_waveform_advanced(self) -> None:
        expanded = not self.waveform_advanced_expanded.get()
        self.waveform_advanced_expanded.set(expanded)
        self.waveform_more_button.configure(
            text=f"Vertical & channel controls {'▴' if expanded else '▾'}",
            style="Active.TButton" if expanded else "Compact.TButton",
        )
        self._last_control_layout = ""
        self._layout_waveform_controls(self.main_surface.winfo_width())

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-r>", lambda event: self._run_shortcut(event, self.refresh_devices))
        self.bind(
            "<Control-Return>", lambda event: self._run_shortcut(event, self.connect_selected)
        )
        self.bind("<space>", lambda event: self._run_shortcut(event, self.toggle_display_pause))
        self.bind("<Key-c>", lambda event: self._run_shortcut(event, self.toggle_cursor))
        self.bind("<Key-f>", lambda event: self._run_shortcut(event, self.fit_time))

    def _run_shortcut(self, _event: tk.Event[Any], action: Callable[[], None]) -> str | None:
        focused = self.focus_get()
        if isinstance(focused, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, tk.Listbox)):
            return None
        action()
        return "break"

    @staticmethod
    def _stat(parent: ttk.Frame, title: str, value: tk.StringVar, row: int, column: int) -> None:
        frame = ttk.Frame(parent, padding=7, style="Metric.TFrame")
        frame.grid(row=row, column=column, sticky="ew", padx=2, pady=2)
        ttk.Label(frame, text=title, style="Toolbar.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=value, style="MetricValue.TLabel").grid(
            row=1, column=0, sticky="w"
        )

    def _engine_stat(self, parent: ttk.Frame, row: int, column: int) -> None:
        frame = ttk.Frame(parent, padding=7, style="Metric.TFrame")
        frame.grid(row=row, column=column, sticky="ew", padx=2, pady=2)
        ttk.Label(frame, text="Acquisition engine", style="Toolbar.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        self.engine_status_dot = tk.Canvas(
            frame,
            width=13,
            height=13,
            bg=COLORS["panel_2"],
            highlightthickness=0,
            bd=0,
        )
        self.engine_status_dot.grid(row=1, column=0, sticky="w", padx=(0, 5))
        self.engine_status_dot_item = self.engine_status_dot.create_oval(
            2, 2, 11, 11, fill=COLORS["danger"], outline=COLORS["danger"]
        )
        ttk.Label(frame, textvariable=self.engine_status, style="MetricValue.TLabel").grid(
            row=1, column=1, sticky="w"
        )

    def _build_connection_overlay(self, parent: ttk.Frame) -> None:
        overlay = tk.Toplevel(self)
        overlay.withdraw()
        overlay.overrideredirect(True)
        overlay.transient(self)
        overlay.configure(bg=CONNECTION_OVERLAY_BG)
        try:
            overlay.attributes("-alpha", CONNECTION_OVERLAY_OPACITY)
        except tk.TclError:
            pass

        canvas = tk.Canvas(
            overlay,
            bg=CONNECTION_OVERLAY_BG,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(fill="both", expand=True)
        bg_rect = canvas.create_rectangle(
            0,
            0,
            1,
            1,
            fill=CONNECTION_OVERLAY_BG,
            outline=CONNECTION_OVERLAY_BG,
        )
        track = canvas.create_oval(0, 0, 1, 1, outline=COLORS["grid"], width=7)
        arc = canvas.create_arc(
            0,
            0,
            1,
            1,
            start=90,
            extent=95,
            style=tk.ARC,
            outline=COLORS["accent"],
            width=7,
        )
        title = canvas.create_text(
            0,
            0,
            text="",
            fill=COLORS["text"],
            font=("Segoe UI", 20, "bold"),
            anchor="center",
        )
        detail = canvas.create_text(
            0,
            0,
            text="",
            fill=COLORS["muted"],
            font=("Segoe UI", 11),
            anchor="center",
        )
        canvas.bind(
            "<Configure>",
            lambda event: self._layout_connection_overlay(event.width, event.height),
        )
        self.connection_overlay = overlay
        self.connection_overlay_canvas = canvas
        self.connection_overlay_bg_rect = bg_rect
        self.connection_overlay_spinner_track = track
        self.connection_overlay_spinner_arc = arc
        self.connection_overlay_title_item = title
        self.connection_overlay_detail_item = detail

    def _layout_connection_overlay(self, width: int, height: int) -> None:
        canvas = self.connection_overlay_canvas
        if canvas is None:
            return
        center_x = width / 2
        center_y = height / 2
        spinner_size = 76
        spinner_top = center_y - 92
        spinner_left = center_x - spinner_size / 2
        spinner_right = center_x + spinner_size / 2
        spinner_bottom = spinner_top + spinner_size
        if self.connection_overlay_bg_rect is not None:
            canvas.coords(
                self.connection_overlay_bg_rect,
                -2,
                -2,
                width + 2,
                height + 2,
            )
        if self.connection_overlay_spinner_track is not None:
            canvas.coords(
                self.connection_overlay_spinner_track,
                spinner_left,
                spinner_top,
                spinner_right,
                spinner_bottom,
            )
        if self.connection_overlay_spinner_arc is not None:
            canvas.coords(
                self.connection_overlay_spinner_arc,
                spinner_left,
                spinner_top,
                spinner_right,
                spinner_bottom,
            )
        if self.connection_overlay_title_item is not None:
            canvas.coords(self.connection_overlay_title_item, center_x, center_y)
        if self.connection_overlay_detail_item is not None:
            canvas.coords(
                self.connection_overlay_detail_item,
                center_x,
                center_y + 36,
            )

    def _position_connection_overlay(self) -> None:
        overlay = self.connection_overlay
        if overlay is None or not overlay.winfo_exists():
            return
        target = getattr(self, "main_surface", None)
        if target is None:
            return
        self.update_idletasks()
        width = max(target.winfo_width(), 1)
        height = max(target.winfo_height(), 1)
        x = target.winfo_rootx()
        y = target.winfo_rooty()
        overlay.geometry(f"{width}x{height}+{x}+{y}")
        self._layout_connection_overlay(width, height)

    def _set_engine_status(self, status: str) -> None:
        normalized = status.strip().lower()
        display = {
            "ok": "OK",
            "checking": "Checking",
            "starting": "Starting",
            "stopping": "Stopping",
            "stopped": "Stopped",
            "offline": "Offline",
            "exited": "Exited",
            "timeout": "Timed out",
            "error": "Error",
            "unknown": "Unknown",
            "": "Unknown",
        }.get(normalized, normalized.capitalize())
        if normalized == "ok":
            color = COLORS["ch1"]
        elif normalized in {"checking", "starting", "stopping"}:
            color = COLORS["warning"]
        elif normalized in {"unknown", "stopped", "offline", ""}:
            color = COLORS["text_disabled"]
        else:
            color = COLORS["danger"]
        self.engine_status.set(display)
        dot = getattr(self, "engine_status_dot", None)
        dot_item = getattr(self, "engine_status_dot_item", None)
        if dot is not None and dot_item is not None:
            dot.itemconfigure(
                dot_item,
                fill=color,
                outline=color,
            )
        self._ui_log("engine status updated", status=display)

    def _ui_log(self, message: str, **fields: Any) -> None:
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        UI_LOGGER.info("%s%s", message, f" {details}" if details else "")

    def _populate_redpitaya_menu(self) -> None:
        self.redpitaya_menu.delete(0, tk.END)
        self.redpitaya_menu.add_command(label="Refresh Red Pitayas", command=self.refresh_devices)
        self.redpitaya_menu.add_command(label="Add device…", command=self.add_device)
        self.redpitaya_menu.add_separator()

        if self.devices:
            for device in self.devices:
                marker = (
                    "*" if self.selected_device and self.selected_device.id == device.id else " "
                )
                state = device.state or "-"
                self.redpitaya_menu.add_command(
                    label=f"{marker} {device.name} ({state})",
                    command=lambda device_id=device.id: self._select_device_by_id(device_id),
                )
        else:
            self.redpitaya_menu.add_command(label="No Red Pitayas registered", state=tk.DISABLED)

        self.redpitaya_menu.add_separator()
        selection_state = tk.NORMAL if self.selected_device is not None else tk.DISABLED
        self.redpitaya_menu.add_command(
            label="Connect",
            command=self.connect_selected,
            state=selection_state,
        )
        self.redpitaya_menu.add_command(label="Disconnect device", command=self.disconnect_selected)
        self.redpitaya_menu.add_command(label="Forget device…", command=self.forget_selected)
        self.redpitaya_menu.add_separator()
        self.redpitaya_menu.add_command(
            label="Toggle CH1 acquisition", command=lambda: self.toggle_channel(1)
        )
        self.redpitaya_menu.add_command(
            label="Toggle CH2 acquisition", command=lambda: self.toggle_channel(2)
        )

    def _device_by_id(self, device_id: str) -> Device | None:
        return next((device for device in self.devices if device.id == device_id), None)

    def _open_device_tab(self, device_id: str) -> None:
        if device_id not in self.open_device_ids:
            self.open_device_ids.append(device_id)
        self._draw_tabs()

    def _draw_tabs(self) -> None:
        if not hasattr(self, "tab_bar"):
            return
        self.tab_bar.delete("all")
        self.tab_hitboxes.clear()
        height = max(self.tab_bar.winfo_height(), 34)
        bottom = height - 1
        top = 0
        x = 0.0
        slant = 14.0
        open_devices = [self._device_by_id(device_id) for device_id in self.open_device_ids]
        open_devices = [device for device in open_devices if device is not None]

        if not open_devices:
            width = 232
            self.tab_bar.create_polygon(
                0,
                bottom,
                0,
                top,
                width - slant,
                top,
                width,
                bottom,
                fill=COLORS["panel_2"],
                outline=COLORS["grid"],
            )
            self.tab_bar.create_text(
                14,
                height / 2,
                text="No Red Pitaya tab open",
                fill=COLORS["muted"],
                anchor="w",
                font=("Segoe UI", 9, "bold"),
            )
            return

        self.open_device_ids = [device.id for device in open_devices]
        for index, device in enumerate(open_devices):
            selected = self.selected_device is not None and device.id == self.selected_device.id
            width = min(max(132, 62 + len(device.name) * 7), 210)
            fill = COLORS["panel"] if selected else COLORS["panel_2"]
            outline = COLORS["accent"] if selected else COLORS["grid"]
            if index == 0:
                points = (x, bottom, x, top, x + width - slant, top, x + width, bottom)
            else:
                points = (
                    x,
                    bottom,
                    x + slant,
                    top,
                    x + width - slant,
                    top,
                    x + width,
                    bottom,
                )
            self.tab_bar.create_polygon(*points, fill=fill, outline=outline)
            self.tab_bar.create_text(
                x + (12 if index == 0 else 22),
                height / 2,
                text=self._tab_label(device),
                fill=COLORS["text"] if selected else COLORS["muted"],
                anchor="w",
                font=("Segoe UI", 9, "bold"),
            )
            self.tab_hitboxes.append((x, x + width, device.id))
            x += width - 8

    def _tab_label(self, device: Device) -> str:
        channels = "CH1" if device.channel_1_enabled else ""
        if device.channel_2_enabled:
            channels = f"{channels}/CH2" if channels else "CH2"
        return f"{device.name}  {channels or '-'}"

    def _select_tab_at(self, event: tk.Event[tk.Canvas]) -> None:
        for start_x, end_x, device_id in self.tab_hitboxes:
            if start_x <= event.x <= end_x:
                self._select_device_by_id(device_id)
                break

    def zoom_time(self, factor: float) -> None:
        if "time_span_choice" in self.__dict__:
            self.time_span_choice.set("Custom")
        self.pending_time_span_choice = False
        previous_zoom = self.time_zoom
        frame = self._view_frame()
        minimum_zoom = TIME_ZOOM_MIN
        if frame is not None:
            minimum_zoom = max(
                minimum_zoom, min(1.0, frame.samples_per_channel / HISTORY_MAX_SAMPLES)
            )
        self.time_zoom = min(max(self.time_zoom * factor, minimum_zoom), 128.0)
        if self.time_zoom <= 1.0:
            self.time_pan = 0.0
        else:
            previous_visible = min(1.0 / previous_zoom, 1.0)
            next_visible = 1.0 / self.time_zoom
            center = self.time_pan * max(1.0 - previous_visible, 0.0) + previous_visible / 2
            self.time_pan = (center - next_visible / 2) / max(1.0 - next_visible, 1e-9)
            self.time_pan = min(max(self.time_pan, 0.0), 1.0)
        self._update_time_controls()
        self._redraw_latest_signal()

    def _view_frame(self) -> SampleFrame | None:
        if self.__dict__.get("display_paused", False):
            return self.paused_frame
        with self.frame_lock:
            return self.latest_frame

    def _view_history(self) -> SampleHistory:
        if self.__dict__.get("display_paused", False) and self.paused_history is not None:
            return self.paused_history
        return self.sample_history

    def select_time_span(self) -> None:
        choice = self.time_span_choice.get()
        duration = TIME_SPANS_MS.get(choice)
        if duration is None:
            self.pending_time_span_choice = False
            self.fit_time()
            return
        frame = self._view_frame()
        if frame is None:
            self.status.set("Connect to a sample stream before choosing a time span")
            self.pending_time_span_choice = True
            return
        self._apply_time_span_to_frame(choice, frame)
        self.time_span_choice.set(choice)
        self.pending_time_span_choice = False

    def _apply_time_span_to_frame(self, choice: str, frame: SampleFrame) -> bool:
        duration = TIME_SPANS_MS.get(choice)
        if duration is None or frame.sample_rate_hz <= 0:
            return False
        zoom = frame.samples_per_channel / (frame.sample_rate_hz * duration / 1000)
        self.zoom_time(zoom / self.time_zoom)
        self.time_span_choice.set(choice)
        return True

    def _signal_plot_points(self) -> int:
        resolution = self.__dict__.get("signal_resolution")
        choice = resolution.get() if resolution is not None else DEFAULT_SIGNAL_RESOLUTION
        return SIGNAL_RESOLUTION_POINTS.get(
            choice, SIGNAL_RESOLUTION_POINTS[DEFAULT_SIGNAL_RESOLUTION]
        )

    def _display_interval_ms(self) -> int:
        mode = self.__dict__.get("display_mode")
        zoom = self.__dict__.get("time_zoom", 1.0)
        visible_seconds = None
        frame = self._view_frame()
        sample_rate_hz = getattr(frame, "sample_rate_hz", 0)
        samples_per_channel = getattr(frame, "samples_per_channel", 0)
        if sample_rate_hz > 0 and samples_per_channel > 0:
            visible_samples = min(
                HISTORY_MAX_SAMPLES,
                max(2, int(samples_per_channel / max(zoom, TIME_ZOOM_MIN))),
            )
            visible_seconds = visible_samples / sample_rate_hz
        return display_interval_ms(
            mode.get() if mode is not None else "Auto",
            zoom,
            visible_seconds,
        )

    def _update_display_detail(self) -> None:
        if self.display_paused:
            text = "Paused · acquisition continues"
        else:
            interval = self._display_interval_ms()
            text = "Live" if interval <= UI_FRAME_INTERVAL_MS else f"Updates every {interval} ms"
            if self.signal_padding_samples and self.axis_sample_rate_hz > 0:
                missing = self.signal_padding_samples / self.axis_sample_rate_hz
                text += f" · collecting {missing:.1f} s more"
        set_if_changed(self.display_detail, text)

    def toggle_display_pause(self) -> None:
        if self.display_paused:
            self.display_paused = False
            self.paused_frame = None
            self.paused_history = None
            self.pause_label.set("Pause")
            self.last_display_monotonic_ns = 0
            self._redraw_latest_signal()
            self._redraw_latest_spectrum()
        else:
            with self.frame_lock:
                if self.latest_frame is None:
                    return
                self.paused_frame = self.latest_frame
                self.paused_history = self.sample_history.frozen_copy()
            self.display_paused = True
            self.pause_label.set("Resume live")
            self._redraw_latest_signal()
            self._redraw_latest_spectrum()
        if "pause_button" in self.__dict__:
            self.pause_button.configure(
                style="Active.TButton" if self.display_paused else "Compact.TButton"
            )
        self._update_display_detail()

    def _reset_display_pause(self) -> None:
        self.display_paused = False
        self.paused_frame = None
        self.paused_history = None
        self.pause_label.set("Pause")
        if "pause_button" in self.__dict__:
            self.pause_button.configure(style="Compact.TButton")
        self.signal_padding_samples = 0
        self.last_display_monotonic_ns = 0
        self._update_display_detail()

    def pan_time(self, amount: float) -> None:
        if self.time_zoom <= 1.0:
            self.time_pan = 0.0
        else:
            self.time_pan = min(max(self.time_pan + amount, 0.0), 1.0)
        self._update_time_controls()
        self._redraw_latest_signal()

    def fit_time(self) -> None:
        if "time_span_choice" in self.__dict__:
            self.time_span_choice.set("One batch")
        self.pending_time_span_choice = False
        self.time_zoom = 1.0
        self.time_pan = 0.0
        self._update_time_controls()
        self._redraw_latest_signal()

    def zoom_y_axis(self, factor: float) -> None:
        self.axis_y_abs = min(max(self.axis_y_abs * factor, 1e-9), 1e12)
        self.y_axis_fit_pending = False
        self.y_axis_dirty = True
        self._update_y_axis_range_label()
        self.status.set(f"vertical range set to ±{format_voltage(self.axis_y_abs)}")
        self._ui_log("signal vertical range changed", range_abs=self.axis_y_abs)
        self._redraw_latest_signal()

    def fit_y_axis(self) -> None:
        self.y_axis_fit_pending = True
        self._redraw_latest_signal()

    def _update_y_axis_range_label(self) -> None:
        self.y_axis_range_label.set(f"Y range ±{format_voltage(self.axis_y_abs)}")

    def _fit_y_axis_to_channels(self, channel_1: Any, channel_2: Any) -> None:
        """Fit the selected traces with a small margin, only when explicitly requested."""
        max_abs = max_sample_abs(channel_1, channel_2)
        largest_offset = max(
            abs(self.channel_1_offset_divisions.get()) if channel_1 is not None else 0.0,
            abs(self.channel_2_offset_divisions.get()) if channel_2 is not None else 0.0,
        )
        # Eight grid divisions span the complete ± range.  Preserve 10% breathing
        # room after accounting for a channel's user-selected vertical offset.
        usable_half_range = max(0.9 - largest_offset / 4, 0.05)
        self.axis_y_abs = max(max_abs / usable_half_range, 1e-9)
        self.y_axis_fit_pending = False
        self.y_axis_dirty = True
        self._update_y_axis_range_label()
        self.status.set(f"vertical range fitted to ±{format_voltage(self.axis_y_abs)}")
        self._ui_log("signal vertical range fitted", range_abs=self.axis_y_abs)

    def toggle_cursor(self) -> None:
        self.cursor_enabled = not self.cursor_enabled
        if "cursor_button" in self.__dict__:
            self.cursor_button.configure(
                text="Cursor on" if self.cursor_enabled else "Cursor",
                style="Active.TButton" if self.cursor_enabled else "Compact.TButton",
            )
        self._update_time_controls()
        self._redraw_latest_signal()

    def nudge_channel_offset(self, channel: int, divisions: float) -> None:
        variable = (
            self.channel_1_offset_divisions if channel == 1 else self.channel_2_offset_divisions
        )
        next_value = min(max(variable.get() + divisions, -3.0), 3.0)
        variable.set(next_value)
        self._update_channel_offset_labels()
        self.status.set(f"CH{channel} vertical offset {next_value:+.1f} div")
        self._ui_log("channel vertical offset changed", channel=channel, divisions=next_value)
        self._redraw_latest_signal()

    def reset_channel_offsets(self) -> None:
        self.channel_1_offset_divisions.set(0.0)
        self.channel_2_offset_divisions.set(0.0)
        self._update_channel_offset_labels()
        self.status.set("channel offsets reset")
        self._ui_log("channel vertical offsets reset")
        self._redraw_latest_signal()

    def _update_channel_offset_labels(self) -> None:
        self.channel_1_offset_label.set(f"CH1 {self.channel_1_offset_divisions.get():+.1f} div")
        self.channel_2_offset_label.set(f"CH2 {self.channel_2_offset_divisions.get():+.1f} div")

    def _channel_y_offset(self, channel: int, plot_height: float) -> float:
        divisions = (
            self.channel_1_offset_divisions.get()
            if channel == 1
            else self.channel_2_offset_divisions.get()
        )
        return plot_height / 2 - divisions * plot_height / 8

    def _redraw_latest_signal(self) -> None:
        frame = self._view_frame()
        if frame is None:
            self._redraw_grid()
            return
        try:
            channel_1, channel_2, rate, count = self._channels_for_signal(frame)
            self._draw_frame(
                adc_counts_to_volts(channel_1),
                adc_counts_to_volts(channel_2),
                rate,
                count,
            )
        except Exception as exc:
            self.signal_processor = None
            self.script_status.set(f"signal script disabled: {exc}")
            self.status.set("signal script error")

    def _frame_for_signal(self, frame: SampleFrame) -> SampleFrame:
        if self.time_zoom >= 1.0:
            return frame
        with self.frame_lock:
            history = self._view_history().snapshot(int(frame.samples_per_channel / self.time_zoom))
        return history or frame

    def _channels_for_signal(self, frame: SampleFrame) -> tuple[Any, Any, float, int]:
        self.signal_padding_samples = 0
        requested = min(HISTORY_MAX_SAMPLES, int(frame.samples_per_channel / self.time_zoom))
        if self.time_zoom < 1.0 and not self._signal_script_active():
            with self.frame_lock:
                channels = self._view_history().plot_channels(requested, self._signal_plot_points())
            if channels is not None:
                first, second, rate, available = channels
                self.signal_padding_samples = requested - available
                return first, second, rate, requested
        frame = self._frame_for_signal(frame)
        first, second = self._processed_channels(frame)
        if self.time_zoom < 1.0:
            self.signal_padding_samples = max(0, requested - frame.samples_per_channel)
            return first, second, frame.sample_rate_hz, requested
        return first, second, frame.sample_rate_hz, frame.samples_per_channel

    def _resize_signal_plot(self, _event: tk.Event[Any]) -> None:
        if self.signal_resize_after_id is not None:
            try:
                self.after_cancel(self.signal_resize_after_id)
            except tk.TclError:
                pass
        self.signal_resize_after_id = self.after_idle(self._finish_signal_resize)

    def _finish_signal_resize(self) -> None:
        self.signal_resize_after_id = None
        self._redraw_grid()
        self._redraw_latest_signal()

    def _resize_spectrum_plot(self, _event: tk.Event[Any]) -> None:
        if self.spectrum_resize_after_id is not None:
            try:
                self.after_cancel(self.spectrum_resize_after_id)
            except tk.TclError:
                pass
        self.spectrum_resize_after_id = self.after_idle(self._finish_spectrum_resize)

    def _finish_spectrum_resize(self) -> None:
        self.spectrum_resize_after_id = None
        self._redraw_spectrum_grid()
        self._redraw_latest_spectrum()

    def _window_bounds(self, sample_count: int) -> tuple[int, int]:
        if sample_count <= 0:
            return 0, 0
        visible = max(int(sample_count / self.time_zoom), 2)
        visible = min(visible, sample_count)
        span = sample_count - visible
        start = round(span * self.time_pan) if span > 0 else 0
        return start, start + visible

    def _window_channel(self, samples: Any) -> Any:
        if samples is None or len(samples) < 2:
            return samples
        start, end = self._window_bounds(len(samples))
        return samples[start:end]

    def _update_time_controls(
        self,
        sample_rate_hz: float | None = None,
        total_samples: int | None = None,
        start_index: int | None = None,
        end_index: int | None = None,
    ) -> None:
        set_if_changed(self.time_zoom_label, f"{self.time_zoom:.3g}x")
        set_if_changed(self.cursor_status, "Cursor center" if self.cursor_enabled else "Cursor off")
        rate = sample_rate_hz or self.axis_sample_rate_hz
        total = total_samples or self.axis_samples_per_channel
        if start_index is None or end_index is None:
            start_index, end_index = self._window_bounds(total)
        visible = max(end_index - start_index, 0)
        if rate > 0 and visible > 0:
            ms_per_div = visible / rate * 1000 / 10
            start_ms = start_index / rate * 1000
            end_ms = end_index / rate * 1000
            set_if_changed(self.timebase, f"{ms_per_div:.3g} ms/div")
            set_if_changed(self.time_window_label, f"{start_ms:.3g}-{end_ms:.3g} ms")
        else:
            set_if_changed(self.timebase, "- ms/div")
            set_if_changed(self.time_window_label, "Full capture")

    def toggle_display_channel(self, channel: int) -> None:
        variable = self.channel_1_visible if channel == 1 else self.channel_2_visible
        variable.set(not variable.get())
        visible = variable.get()
        acquired = True
        if self.selected_device is not None:
            acquired = (
                self.selected_device.channel_1_enabled
                if channel == 1
                else self.selected_device.channel_2_enabled
            )
        if visible and not acquired:
            self.status.set(f"CH{channel} display on, acquisition is off")
        else:
            self.status.set(f"CH{channel} {'shown' if visible else 'hidden'} in display")
        self._ui_log("display channel toggled", channel=channel, visible=visible)
        self._redraw_latest_signal()

    def _sync_acquisition_controls(self, device: Device) -> None:
        self.acquisition_decimation.set(str(device.decimation))
        self.acquisition_block_size.set(str(device.block_size_bytes))
        self._update_acquisition_rate()

    def _update_acquisition_rate(self) -> None:
        try:
            decimation = max(int(self.acquisition_decimation.get()), 1)
            block_size = max(int(self.acquisition_block_size.get()), 1)
        except ValueError:
            self.acquisition_rate.set("Sample rate: -")
            self.acquisition_batch.set("Batch: -")
            return
        channel_count = acquisition_channel_count(self.__dict__.get("selected_device"))
        sample_rate = 125_000_000 / decimation
        batch_seconds = acquisition_batch_seconds(block_size, channel_count, sample_rate)
        self.acquisition_rate.set(f"Sample rate: {format_frequency(sample_rate)}")
        self.acquisition_batch.set(f"Batch: {format_duration(batch_seconds)} · {channel_count} ch")

    def _is_stream_active(self, runtime_state: str | None = None) -> bool:
        socket_alive = self.socket_thread is not None and self.socket_thread.is_alive()
        backend_active = str(runtime_state or "").lower() not in DISCONNECTED_STATES
        return socket_alive or backend_active

    def apply_fast_acquisition(self) -> None:
        self._ui_log("smooth acquisition preset clicked")
        # Improve delivery cadence without increasing the sample rate/bandwidth.
        block_size = smooth_acquisition_block_size(int(self.acquisition_decimation.get()))
        self.acquisition_block_size.set(str(block_size))
        self._update_acquisition_rate()
        self.apply_acquisition_settings()

    def apply_stable_two_channel(self) -> None:
        self._ui_log("stable two-channel preset clicked")
        self.acquisition_decimation.set("1024")
        self.acquisition_block_size.set("32768")
        self._update_acquisition_rate()
        self.apply_acquisition_settings()

    def apply_acquisition_settings(self) -> None:
        if self.selected_device is None:
            messagebox.showerror("No device", "Select a Red Pitaya first.")
            return
        device = self.selected_device
        try:
            decimation = int(self.acquisition_decimation.get())
            block_size = int(self.acquisition_block_size.get())
        except ValueError:
            messagebox.showerror("Invalid speed", "Use numeric acquisition settings.")
            return

        if decimation < 1 or decimation > 65_536:
            messagebox.showerror("Invalid decimation", "Decimation must be from 1 to 65536.")
            return
        if block_size < 2_048 or block_size > 2_097_152:
            messagebox.showerror("Invalid block size", "Block size must be 2048 to 2097152 bytes.")
            return

        api_base = self._api_base()
        self._ui_log(
            "apply acquisition clicked",
            device_id=device.id,
            decimation=decimation,
            block_size=block_size,
        )

        def work() -> tuple[str, list[Device]]:
            profile = self._api_for_base(api_base, "GET", f"/api/v1/devices/{device.id}")
            runtime_state = str((profile.get("runtime") or {}).get("state") or device.state)
            body = editable_profile_payload(profile)
            body["decimation"] = decimation
            body["blockSizeBytes"] = block_size

            if self._is_stream_active(runtime_state):
                raise ApiError(
                    "Stop acquisition with Disconnect before changing acquisition speed."
                )
            self._require_backend_disconnected(device.id, runtime_state, api_base=api_base)

            updated = self._api_for_base(api_base, "PUT", f"/api/v1/devices/{device.id}", body)
            return str(updated["id"]), self._load_devices(api_base)

        def apply(result: tuple[str, list[Device]]) -> None:
            updated_id, devices = result
            self._clear_live_plot()
            self._apply_loaded_devices(devices)
            self._select_device_by_id(updated_id)
            self.status.set("acquisition speed updated")
            self._update_acquisition_rate()

        self._run_busy("Applying acquisition settings", work, apply)

    def refresh_devices(self) -> None:
        self.status.set("loading devices")
        api_base = self._api_base()
        self._ui_log("refresh devices clicked", api_base=api_base)
        self._run_busy(
            "Refreshing Red Pitayas",
            lambda: self._load_devices(api_base),
            self._apply_loaded_devices,
        )

    def save_settings(self) -> None:
        settings = AppSettings(
            api_base=self._api_base(),
            launch_engine_on_start=self.launch_engine_on_start.get(),
        )
        save_settings(settings)
        self.status.set("settings saved")

    def open_engine_settings(self) -> None:
        EngineSettingsDialog(self)

    def open_signal_editor(self) -> None:
        if not self._signal_scripting_enabled():
            messagebox.showinfo(
                "Signal scripting disabled",
                (
                    f"Python signal scripting is disabled. Set {SIGNAL_SCRIPT_FEATURE_ENV}=1 "
                    "before launching the native receiver to enable it."
                ),
                parent=self,
            )
            return
        SignalScriptDialog(self)

    def _signal_scripting_enabled(self) -> bool:
        return bool(getattr(self, "signal_scripts_enabled", signal_script_feature_enabled()))

    def _signal_script_active(self) -> bool:
        return (
            self._signal_scripting_enabled() and getattr(self, "signal_processor", None) is not None
        )

    def available_signal_ids(self) -> list[str]:
        ids = ["selected.ch1", "selected.ch2"]
        for device in self.devices:
            slug = device_slug(device)
            if device.channel_1_enabled:
                ids.append(f"{slug}.ch1")
                ids.append(f"{device.id}.ch1")
            if device.channel_2_enabled:
                ids.append(f"{slug}.ch2")
                ids.append(f"{device.id}.ch2")
        return sorted(dict.fromkeys(ids))

    def set_signal_script(self, code: str) -> None:
        if not self._signal_scripting_enabled():
            raise ValueError(
                f"Python signal scripting is disabled. Set {SIGNAL_SCRIPT_FEATURE_ENV}=1 "
                "before launching the native receiver to enable it."
            )
        namespace: dict[str, Any] = {"np": np}
        exec(code, {"__builtins__": safe_builtins()}, namespace)
        processor = namespace.get("process")
        if not callable(processor):
            raise ValueError("Script must define a callable process(signals, np).")
        self.signal_script = code
        self.signal_processor = processor
        self.script_status.set("signal script enabled")
        self.status.set("signal script enabled")

    def clear_signal_script(self) -> None:
        self.signal_processor = None
        self.script_status.set("signal script disabled")
        self.status.set("signal script disabled")

    def open_cluster_monitor(self) -> None:
        if self.cluster_dialog is not None and self.cluster_dialog.winfo_exists():
            self.cluster_dialog.lift()
            self.cluster_dialog.focus_force()
            return
        try:
            from examples.cluster_monitor import ClusterMonitorDialog
        except ModuleNotFoundError:  # pragma: no cover - direct script execution
            from cluster_monitor import ClusterMonitorDialog  # type: ignore[no-redef]

        self.cluster_dialog = ClusterMonitorDialog(self)

    def apply_cluster_config(self, config: ClusterProcessingConfig) -> None:
        with self.cluster_lock:
            self.cluster_processor.configure(config)
            self.latest_cluster_result = None
            self.cluster_error = None
            self.cluster_last_sequence = None
        save_cluster_config(config)

    def start_cluster_calibration(self) -> None:
        with self.cluster_lock:
            self.cluster_processor.start_calibration()
            self.latest_cluster_result = None
            self.cluster_error = None
            self.cluster_last_sequence = None

    def finish_cluster_calibration(self) -> dict[str, float]:
        with self.cluster_lock:
            coefficients = self.cluster_processor.finish_calibration()
            self.latest_cluster_result = None
            self.cluster_error = None
            return dict(coefficients)

    def clear_cluster_calibration(self) -> None:
        with self.cluster_lock:
            self.cluster_processor.clear_calibration()
            self.latest_cluster_result = None
            self.cluster_error = None

    def cluster_snapshot(self) -> tuple[ClusterProcessingResult | None, str | None]:
        with self.cluster_lock:
            return self.latest_cluster_result, self.cluster_error

    def ensure_engine_running(self) -> None:
        self._ui_log("start engine requested")
        self.save_settings()
        self._set_engine_status("checking")
        if self.engine_process and self.engine_process.poll() is None:
            self.status.set("engine starting")
            self._wait_for_engine_ready_async()
            return

        api_base = self._api_base()
        self._run_busy(
            "Checking engine",
            lambda: self._engine_is_running_for_base(api_base),
            self._handle_engine_check,
        )

    def _handle_engine_check(self, running: bool) -> None:
        if running:
            self._set_engine_status("ok")
            self.status.set("engine already running")
            self.activity.set("")
            self._ui_log("engine already running")
            return
        self._set_engine_status("starting")
        self._launch_engine_process()

    def _launch_engine_process(self) -> None:
        try:
            env = os.environ.copy()
            default_module_path = (
                Path(env.get("LOCALAPPDATA", ""))
                / "RedPitaya"
                / "rpsa-client-2.07-48"
                / "python_lib"
            )
            if "QL_STREAMING_MODULE_PATH" not in env and default_module_path.exists():
                env["QL_STREAMING_MODULE_PATH"] = str(default_module_path)

            env["RTQML_ENGINE_PROCESS_NAME"] = ENGINE_PROCESS_TITLE
            command = self._engine_command()
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.engine_process = subprocess.Popen(
                command,
                cwd=WORKSPACE_ROOT,
                env=env,
                creationflags=flags,
                text=True,
            )
            self.status.set("engine starting")
            self._set_engine_status("starting")
            self._wait_for_engine_ready_async()
        except Exception as exc:
            self._set_engine_status("error")
            self._stop_spinner()
            self._show_error(exc)

    def _engine_command(self) -> list[str]:
        launcher = WORKSPACE_ROOT / ".venv" / "Scripts" / "quantum-light-engine.exe"
        if launcher.exists():
            return [str(launcher), "run", "--host", "127.0.0.1", "--port", "8080"]
        return [
            sys.executable,
            "-c",
            (
                f"RTQML_ENGINE_PROCESS_NAME={ENGINE_PROCESS_TITLE!r}; "
                "from quantum_light_engine.cli import main; "
                "raise SystemExit(main(['run','--host','127.0.0.1','--port','8080']))"
            ),
        ]

    def stop_engine(self) -> None:
        self._ui_log("stop engine requested")
        if self.engine_process and self.engine_process.poll() is None:
            process = self.engine_process
            self.status.set("stopping acquisition engine")
            self._set_engine_status("stopping")

            def work() -> subprocess.Popen[Any]:
                terminate_process_tree(process)
                return process

            def apply(stopped_process: subprocess.Popen[Any]) -> None:
                if self.engine_process is stopped_process:
                    self.engine_process = None
                self.status.set("engine stopped")
                self._set_engine_status("stopped")

            self._run_busy("Stopping acquisition engine", work, apply)
            return
        self.status.set("engine not launched by this app")
        self._set_engine_status("unknown")

    def _wait_for_engine_ready_async(self) -> None:
        api_base = self._api_base()
        delay_seconds = 0.5
        attempts = max(1, int(ENGINE_READY_TIMEOUT_SECONDS / delay_seconds))
        self._run_busy(
            "Launching engine",
            lambda: self._wait_engine_ready(
                api_base, attempts=attempts, delay_seconds=delay_seconds
            ),
            self._handle_engine_ready,
        )

    def _handle_engine_ready(self, running: bool) -> None:
        if running:
            self._set_engine_status("ok")
            self.status.set("engine running")
            self.refresh_devices()
            return
        self._set_engine_status("timeout")
        self.status.set("engine launch timeout")

    def _ping_engine_status_async(self) -> None:
        if getattr(self, "closing", False):
            return
        if self.engine_ping_in_flight:
            self.after(ENGINE_PING_INTERVAL_MS, self._ping_engine_status_async)
            return

        api_base = self._api_base()
        self.engine_ping_in_flight = True

        def runner() -> None:
            started = time.monotonic()
            running = self._engine_is_running_for_base(api_base)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self.events.put(("engine_ping", (running, elapsed_ms)))

        threading.Thread(target=runner, daemon=True).start()
        self.after(ENGINE_PING_INTERVAL_MS, self._ping_engine_status_async)

    def _handle_engine_ping(self, running: bool, elapsed_ms: int) -> None:
        self.engine_ping_in_flight = False
        if running:
            self._set_engine_status("ok")
            return

        if self.engine_process is not None and self.engine_process.poll() is not None:
            self.engine_process = None
            self._set_engine_status("exited")
            self.status.set("engine exited")
        else:
            self._set_engine_status("offline")
        self._ui_log("engine ping failed", elapsed_ms=elapsed_ms)

    def _start_spinner(self, label: str) -> None:
        self._ui_log("loading started", label=label)
        self.spinner_token += 1
        token = self.spinner_token
        self.spinner_running = True
        self.loading_active = True
        self.loading_label = label
        self.spinner_index = 0
        self._update_connect_controls()
        if label in CONNECTION_OVERLAY_BUSY_LABELS:
            self._show_connection_overlay("Connecting to Red Pitaya", label)
        else:
            self._set_signal_notice(label)
            self._set_spectrum_notice(label)
        self.update_idletasks()
        self._spin(label, token)

    def _spin(self, label: str, token: int) -> None:
        if not self.spinner_running or token != self.spinner_token:
            return
        frames = "|/-\\"
        message = f"{frames[self.spinner_index % len(frames)]} {label}..."
        self.activity.set(message)
        self.loading_message.set(message)
        if label in CONNECTION_OVERLAY_BUSY_LABELS:
            self._update_connection_overlay_spinner(message)
        else:
            self._set_signal_notice(message)
            self._set_spectrum_notice(message)
        self.spinner_index += 1
        self.after(100, lambda: self._spin(label, token))

    def _stop_spinner(self, *, restore_notices: bool = True) -> None:
        if self.loading_label:
            self._ui_log("loading stopped", label=self.loading_label)
        self.spinner_running = False
        self.spinner_token += 1
        self.loading_active = False
        self.loading_label = ""
        self.activity.set("")
        self.loading_message.set("Ready")
        self._update_connect_controls()
        self._hide_connection_overlay()
        if not restore_notices:
            return
        with self.frame_lock:
            has_frame = self.latest_frame is not None
        if not has_frame:
            self._restore_idle_notices()
        else:
            self._clear_signal_notice()
            self._clear_spectrum_notice()

    def _show_connection_overlay(self, title: str, detail: str) -> None:
        self.connection_overlay_title.set(title)
        self.connection_overlay_detail.set(detail)
        overlay = self.connection_overlay
        canvas = self.connection_overlay_canvas
        if overlay is None or canvas is None:
            return
        overlay.configure(bg=CONNECTION_OVERLAY_BG)
        canvas.configure(bg=CONNECTION_OVERLAY_BG)
        try:
            overlay.attributes("-alpha", CONNECTION_OVERLAY_OPACITY)
        except tk.TclError:
            pass
        if self.connection_overlay_bg_rect is not None:
            canvas.itemconfigure(
                self.connection_overlay_bg_rect,
                fill=CONNECTION_OVERLAY_BG,
                outline=CONNECTION_OVERLAY_BG,
            )
        if self.connection_overlay_title_item is not None:
            canvas.itemconfigure(
                self.connection_overlay_title_item,
                text=title,
            )
        if self.connection_overlay_detail_item is not None:
            canvas.itemconfigure(
                self.connection_overlay_detail_item,
                text=detail,
            )
        self._position_connection_overlay()
        overlay.deiconify()
        overlay.lift(self)

    def _update_connection_overlay_spinner(self, detail: str) -> None:
        self.connection_overlay_detail.set(detail)
        canvas = self.connection_overlay_canvas
        if canvas is None or self.connection_overlay_spinner_arc is None:
            return
        self._position_connection_overlay()
        if self.connection_overlay_detail_item is not None:
            canvas.itemconfigure(self.connection_overlay_detail_item, text=detail)
        canvas.itemconfigure(
            self.connection_overlay_spinner_arc,
            start=(90 - self.spinner_index * 24) % 360,
        )

    def _hide_connection_overlay(self) -> None:
        if self.connection_overlay is not None and self.connection_overlay.winfo_exists():
            self.connection_overlay.withdraw()

    def _restore_idle_notices(self) -> None:
        if self.connection_failure_active:
            self._set_connection_failed_notice()
            return
        self._set_signal_notice("Waiting for live samples")
        self._set_spectrum_notice("Waiting for spectrum data")

    def _set_connection_failed_notice(self) -> None:
        self.connection_failure_active = True
        self._set_signal_notice(CONNECTION_FAILED_NOTICE)
        self._set_spectrum_notice(CONNECTION_FAILED_SPECTRUM_NOTICE)

    def _run_busy(
        self,
        label: str,
        work: Callable[[], Any],
        callback: Callable[[Any], None] | None = None,
    ) -> None:
        self._ui_log("background job queued", label=label)
        self._start_spinner(label)

        def runner() -> None:
            started = time.monotonic()
            try:
                result = work()
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self.events.put(("busy_success", (label, callback, result, elapsed_ms)))
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self.events.put(("busy_error", (label, exc, elapsed_ms)))

        threading.Thread(target=runner, daemon=True).start()

    def _set_signal_notice(self, text: str) -> None:
        if "canvas" not in self.__dict__:
            return
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        if self.signal_notice_items:
            self.canvas.coords(self.signal_notice_items[0], width / 2, height / 2)
            self.canvas.itemconfigure(self.signal_notice_items[0], text=text, state="normal")
        else:
            self.signal_notice_items.append(
                self.canvas.create_text(
                    width / 2,
                    height / 2,
                    text=text,
                    fill=COLORS["text_secondary"],
                    font=FONTS["section"],
                    justify="center",
                    anchor="center",
                )
            )

    def _clear_signal_notice(self) -> None:
        if "canvas" not in self.__dict__:
            return
        for item in self.signal_notice_items:
            self.canvas.delete(item)
        self.signal_notice_items.clear()

    def _set_spectrum_notice(self, text: str) -> None:
        if "spectrum_canvas" not in self.__dict__:
            return
        width = max(self.spectrum_canvas.winfo_width(), 1)
        height = max(self.spectrum_canvas.winfo_height(), 1)
        if self.spectrum_notice_items:
            self.spectrum_canvas.coords(self.spectrum_notice_items[0], width / 2, height / 2)
            self.spectrum_canvas.itemconfigure(
                self.spectrum_notice_items[0], text=text, state="normal"
            )
        else:
            self.spectrum_notice_items.append(
                self.spectrum_canvas.create_text(
                    width / 2,
                    height / 2,
                    text=text,
                    fill=COLORS["text_secondary"],
                    font=FONTS["body_bold"],
                    justify="center",
                    anchor="center",
                )
            )

    def _clear_spectrum_notice(self) -> None:
        if "spectrum_canvas" not in self.__dict__:
            return
        for item in self.spectrum_notice_items:
            self.spectrum_canvas.delete(item)
        self.spectrum_notice_items.clear()

    def _show_toast(
        self,
        title: str,
        message: str,
        *,
        level: str = "error",
        duration_ms: int = TOAST_VISIBLE_MS,
    ) -> None:
        if not hasattr(self, "toast_frames"):
            self._ui_log("toast skipped", title=title, toast_message=message)
            return

        color = COLORS["danger"] if level == "error" else COLORS["accent"]
        toast = tk.Toplevel(self)
        toast.withdraw()
        toast.overrideredirect(True)
        toast.transient(self)
        try:
            toast.attributes("-alpha", 1.0)
        except tk.TclError:
            pass
        frame = tk.Frame(
            toast,
            bg=COLORS["panel_2"],
            highlightbackground=color,
            highlightthickness=1,
            bd=0,
        )
        frame.columnconfigure(1, weight=1)
        stripe = tk.Frame(frame, bg=color, width=4)
        stripe.grid(row=0, column=0, rowspan=2, sticky="nsw")
        title_label = tk.Label(
            frame,
            text=title,
            bg=COLORS["panel_2"],
            fg=COLORS["text"],
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        title_label.grid(row=0, column=1, sticky="ew", padx=(10, 8), pady=(8, 0))
        message_label = tk.Label(
            frame,
            text=message,
            bg=COLORS["panel_2"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=340,
        )
        message_label.grid(row=1, column=1, sticky="ew", padx=(10, 8), pady=(2, 8))
        close_button = tk.Button(
            frame,
            text="x",
            command=lambda toast=toast: self._dismiss_toast(toast),
            bg=COLORS["panel_2"],
            fg=COLORS["muted"],
            activebackground=COLORS["panel_3"],
            activeforeground=COLORS["text"],
            relief="flat",
            bd=0,
            padx=6,
            pady=2,
        )
        close_button.grid(row=0, column=2, sticky="ne", padx=(0, 4), pady=4)
        frame.pack(fill="both", expand=True)
        self.toast_frames.append(toast)
        while len(self.toast_frames) > 4:
            old_toast = self.toast_frames.pop(0)
            old_toast.destroy()
        self._position_toasts()
        toast.deiconify()
        toast.lift()
        self._ui_log("toast shown", title=title, toast_message=message)
        self.after(duration_ms, lambda toast=toast: self._fade_toast(toast))

    def _fade_toast(self, toast: tk.Toplevel, step: int = 0) -> None:
        try:
            if not toast.winfo_exists():
                return
        except tk.TclError:
            return
        if step >= TOAST_FADE_STEPS:
            self._dismiss_toast(toast)
            return

        progress = (step + 1) / TOAST_FADE_STEPS
        try:
            toast.attributes("-alpha", max(1.0 - progress, 0.0))
        except tk.TclError:
            self._dismiss_toast(toast)
            return
        self.after(
            TOAST_FADE_INTERVAL_MS,
            lambda current=toast, next_step=step + 1: self._fade_toast(current, next_step),
        )

    def _dismiss_toast(self, toast: tk.Toplevel) -> None:
        if toast in self.toast_frames:
            self.toast_frames.remove(toast)
        try:
            if toast.winfo_exists():
                toast.destroy()
        except tk.TclError:
            pass
        self._position_toasts()

    def _position_toasts(self) -> None:
        for index, toast in enumerate(self.toast_frames):
            try:
                self.update_idletasks()
                toast.update_idletasks()
                width = 390
                height = max(toast.winfo_reqheight(), 74)
                x = self.winfo_rootx() + self.winfo_width() - width - 18
                y = self.winfo_rooty() + 54 + index * (height + 10)
                toast.geometry(f"{width}x{height}+{x}+{y}")
            except tk.TclError:
                continue

    def _engine_is_running(self) -> bool:
        return self._engine_is_running_for_base(self._api_base())

    def _engine_is_running_for_base(self, api_base: str) -> bool:
        try:
            request = Request(f"{api_base.rstrip('/')}/health/live", method="GET")
            with urlopen(request, timeout=ENGINE_HEALTH_TIMEOUT_SECONDS) as response:
                running: bool = response.status == 200
                response.read()
                return running
        except Exception:
            return False

    def _wait_engine_ready(self, api_base: str, *, attempts: int, delay_seconds: float) -> bool:
        for _attempt in range(attempts):
            if self._engine_is_running_for_base(api_base):
                return True
            time.sleep(delay_seconds)
        return False

    def _load_devices(self, api_base: str) -> list[Device]:
        payload = self._api_for_base(api_base, "GET", "/api/v1/devices")
        return [
            Device(
                id=item["id"],
                name=item["name"],
                host=item.get("host") or "",
                state=(item.get("runtime") or {}).get("state", "-"),
                channel_1_enabled=bool(item.get("channel1Enabled")),
                channel_2_enabled=bool(item.get("channel2Enabled")),
                decimation=int(item.get("decimation") or 256),
                block_size_bytes=int(item.get("blockSizeBytes") or 131_072),
            )
            for item in payload
            if item.get("source") == "redpitaya"
        ]

    def _apply_loaded_devices(self, devices: list[Device]) -> None:
        self.devices = devices
        self._render_devices()
        socket_alive = self.socket_thread is not None and self.socket_thread.is_alive()
        self._set_engine_status("ok")
        self.status.set("connected" if socket_alive else "disconnected")
        self._ui_log("device list updated", count=len(devices), socket_alive=socket_alive)

    def _render_devices(self) -> None:
        selected_id = self.selected_device.id if self.selected_device is not None else None
        for item in self.device_list.get_children():
            self.device_list.delete(item)
        self.device_list.tag_configure("streaming", foreground=COLORS["success"])
        self.device_list.tag_configure("warning", foreground=COLORS["warning"])
        self.device_list.tag_configure("offline", foreground=COLORS["text_secondary"])
        self.device_list.tag_configure("empty", foreground=COLORS["text_disabled"])
        for device in self.devices:
            state_label, tag = self._device_state_label(device.state)
            channels = (
                "/".join(
                    label
                    for label, enabled in (
                        ("CH1", device.channel_1_enabled),
                        ("CH2", device.channel_2_enabled),
                    )
                    if enabled
                )
                or "None"
            )
            self.device_list.insert(
                "",
                tk.END,
                iid=device.id,
                text=f"{device.name}\n{device.host}",
                values=(state_label, channels),
                tags=(tag,),
            )
        if not self.devices:
            self.device_list.insert(
                "",
                tk.END,
                iid="__empty__",
                text="No devices registered\nUse Add device to begin",
                values=("", ""),
                tags=("empty",),
            )
            self.selected_device = None
            self.selected.set("-")
        elif selected_id and (selected := self._device_by_id(selected_id)) is not None:
            self.selected_device = selected
            self.device_list.selection_set(selected_id)
            self.device_list.see(selected_id)
        if hasattr(self, "redpitaya_menu"):
            self._populate_redpitaya_menu()
        self._update_connect_controls()
        self._draw_tabs()

    @staticmethod
    def _device_state_label(state: str) -> tuple[str, str]:
        normalized = str(state or "").lower()
        if normalized == "streaming":
            return "● Streaming", "streaming"
        if normalized == "ready":
            return "● Ready", "streaming"
        if normalized in {"starting", "connecting", "reconnecting"}:
            return f"◐ {normalized.capitalize()}", "warning"
        if normalized in DISCONNECTED_STATES:
            return "○ Offline", "offline"
        return f"! {normalized.capitalize() or 'Unknown'}", "warning"

    def _select_from_list(self, _event: tk.Event[ttk.Treeview]) -> None:
        selection = self.device_list.selection()
        if not selection:
            return
        device_id = selection[0]
        if device_id == "__empty__":
            self.device_list.selection_remove(device_id)
            return
        selected = self._device_by_id(device_id)
        if selected is None:
            return
        self.selected_device = selected
        self.selected.set(f"{self.selected_device.name} ({self.selected_device.host})")
        self._sync_acquisition_controls(self.selected_device)
        self._populate_redpitaya_menu()
        self._update_connect_controls()
        self._draw_tabs()

    def _select_device_by_id(self, device_id: str) -> None:
        for device in self.devices:
            if device.id == device_id:
                self.device_list.selection_set(device_id)
                self.device_list.see(device_id)
                self.selected_device = device
                self.selected.set(f"{device.name} ({device.host})")
                self._sync_acquisition_controls(device)
                self._populate_redpitaya_menu()
                self._update_connect_controls()
                self._draw_tabs()
                return

    def _update_connect_controls(self) -> None:
        device = self.selected_device
        enabled = device is not None and not self.__dict__.get("loading_active", False)
        socket_alive = self.socket_thread is not None and self.socket_thread.is_alive()
        runtime_state = str(device.state or "").lower() if device is not None else ""
        connected = device is not None and (
            runtime_state not in DISCONNECTED_STATES or socket_alive
        )
        if "connect_button" in self.__dict__:
            button_text = (
                "Attach stream" if runtime_state == "streaming" and not socket_alive else "Connect"
            )
            self.connect_button.configure(
                text=button_text, state=tk.NORMAL if enabled and not socket_alive else tk.DISABLED
            )
        if "disconnect_button" in self.__dict__:
            if connected:
                self.disconnect_button.grid()
                self.disconnect_button.configure(
                    state=tk.NORMAL if not self.loading_active else tk.DISABLED
                )
            else:
                self.disconnect_button.grid_remove()
        for name in (
            "channel_1_button",
            "channel_2_button",
            "forget_button",
            "apply_acquisition_button",
        ):
            button = self.__dict__.get(name)
            if button is not None:
                button.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        if "connection_summary" in self.__dict__:
            if device is None:
                summary = "No device selected"
            else:
                state_label, _tag = self._device_state_label(device.state)
                summary = f"{device.name} · {state_label.lstrip('●○◐! ')}"
            set_if_changed(self.connection_summary, summary)

    @staticmethod
    def _device_channel_label(device: Device) -> str:
        labels = [
            f"CH1 {'ON' if device.channel_1_enabled else 'OFF'}",
            f"CH2 {'ON' if device.channel_2_enabled else 'OFF'}",
        ]
        return " / ".join(labels)

    def add_device(self) -> None:
        dialog = AddRedPitayaDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        body = {
            "name": dialog.result["name"],
            "source": "redpitaya",
            "host": dialog.result["host"],
            "enabled": True,
            "isDefault": False,
            "channel1Enabled": dialog.result["channel1Enabled"],
            "channel2Enabled": dialog.result["channel2Enabled"],
            "decimation": 64,
            "blockSizeBytes": 16384,
            "adcBufferSizeBytes": 4194304,
            "streamingModulePath": None,
            "simulatorSamplesPerPacket": 4096,
            "simulatorSeed": 1,
        }

        api_base = self._api_base()
        self._ui_log("add Red Pitaya submitted", host=dialog.result["host"])

        def work() -> tuple[str, list[Device]]:
            created = self._api_for_base(api_base, "POST", "/api/v1/devices", body)
            return str(created["id"]), self._load_devices(api_base)

        def apply(result: tuple[str, list[Device]]) -> None:
            created_id, devices = result
            self._apply_loaded_devices(devices)
            self._select_device_by_id(created_id)
            self.status.set("Red Pitaya added")

        self._run_busy("Adding Red Pitaya", work, apply)

    def forget_selected(self) -> None:
        if self.selected_device is None:
            messagebox.showerror("No device", "Select a Red Pitaya first.")
            return
        device = self.selected_device
        confirmed = messagebox.askyesno(
            "Forget Red Pitaya",
            f"Forget '{device.name}' at {device.host}?\n\n"
            "This removes the saved profile. It does not modify the Red Pitaya.",
            parent=self,
        )
        if not confirmed:
            return
        device_id = device.id
        api_base = self._api_base()
        self._ui_log("forget Red Pitaya confirmed", device_id=device_id, host=device.host)
        if self.socket_thread and self.socket_thread.is_alive():
            self.disconnect_socket()

        def work() -> list[Device]:
            if str(device.state or "").lower() not in DISCONNECTED_STATES:
                self._api_for_base(api_base, "POST", f"/api/v1/devices/{device_id}/disconnect")
            self._api_for_base(api_base, "DELETE", f"/api/v1/devices/{device_id}")
            return self._load_devices(api_base)

        def apply(devices: list[Device]) -> None:
            self._apply_loaded_devices(devices)
            self.selected_device = None
            self.selected.set("-")
            self._clear_live_plot()
            self.status.set("device forgotten")

        self._run_busy("Forgetting Red Pitaya", work, apply)

    def toggle_channel(self, channel: int) -> None:
        if self.selected_device is None:
            messagebox.showerror("No device", "Select a Red Pitaya first.")
            return
        device = self.selected_device
        api_base = self._api_base()
        label = f"Updating CH{channel} acquisition"
        self._ui_log("acquisition channel toggle clicked", device_id=device.id, channel=channel)

        def work() -> tuple[str, bool, list[Device]]:
            profile = self._api_for_base(api_base, "GET", f"/api/v1/devices/{device.id}")
            runtime_state = str((profile.get("runtime") or {}).get("state") or device.state)
            body = editable_profile_payload(profile)
            key = "channel1Enabled" if channel == 1 else "channel2Enabled"
            body[key] = not bool(body[key])
            if not body["channel1Enabled"] and not body["channel2Enabled"]:
                raise ApiError("At least one channel must stay enabled.")

            if self._is_stream_active(runtime_state):
                raise ApiError(
                    f"Stop acquisition with Disconnect before changing CH{channel} acquisition.",
                )
            self._require_backend_disconnected(device.id, runtime_state, api_base=api_base)

            updated = self._api_for_base(api_base, "PUT", f"/api/v1/devices/{device.id}", body)
            return str(updated["id"]), bool(body[key]), self._load_devices(api_base)

        def apply(result: tuple[str, bool, list[Device]]) -> None:
            updated_id, enabled, devices = result
            self._clear_live_plot()
            self._apply_loaded_devices(devices)
            self._select_device_by_id(updated_id)
            self.status.set(f"CH{channel} {'enabled' if enabled else 'disabled'}")

        self._run_busy(label, work, apply)

    def connect_selected(self) -> None:
        if self.selected_device is None:
            messagebox.showerror("No device", "Select a Red Pitaya first.")
            return
        if self.socket_thread and self.socket_thread.is_alive():
            return

        device_id = self.selected_device.id
        api_base = self._api_base()
        self._ui_log(
            "connect clicked",
            device_id=device_id,
            cached_state=self.selected_device.state,
        )
        self.connection_failure_active = False
        self.status.set("attaching sample socket")
        self._open_device_tab(device_id)
        self._start_socket(device_id)

        cached_state = str(self.selected_device.state or "").lower()
        if cached_state in STREAMING_STATES:
            return

        def work() -> tuple[str, list[Device]]:
            status = self._api_for_base(
                api_base,
                "GET",
                f"/api/v1/devices/{device_id}/status",
                timeout_seconds=ENGINE_CONNECT_TIMEOUT_SECONDS,
            )
            runtime_state = str(status.get("state") or "").lower()
            if runtime_state in STREAMING_STATES:
                return device_id, self._load_devices(api_base)

            if runtime_state not in READY_STATES:
                self.events.put(("status", "connecting device"))
                try:
                    self._api_for_base(
                        api_base,
                        "POST",
                        f"/api/v1/devices/{device_id}/connect",
                        timeout_seconds=REDPITAYA_CONNECT_TIMEOUT_SECONDS,
                    )
                except Exception as exc:
                    raise ApiError(
                        "Connect failed. The receiver reached the acquisition engine, but it "
                        "could not open the Red Pitaya streaming client: "
                        f"{short_error(exc, 180)}"
                    ) from exc

            self.events.put(("status", "starting device"))
            try:
                self._api_for_base(
                    api_base,
                    "POST",
                    f"/api/v1/devices/{device_id}/start",
                    timeout_seconds=REDPITAYA_START_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                raise ApiError(
                    "Start acquisition failed. The engine connected to the selected profile but "
                    f"could not start streaming: {short_error(exc, 180)}"
                ) from exc
            return device_id, self._load_devices(api_base)

        def apply(result: tuple[str, list[Device]]) -> None:
            connected_id, devices = result
            self._open_device_tab(connected_id)
            self._apply_loaded_devices(devices)

        self._run_busy("Starting Red Pitaya acquisition", work, apply)

    def disconnect_socket(self) -> None:
        self.stop_event.set()
        self.status.set("disconnecting socket")
        self._ui_log("socket disconnect requested")

    def disconnect_selected(self) -> None:
        if self.selected_device is None:
            messagebox.showerror("No device", "Select a Red Pitaya first.")
            return
        device_id = self.selected_device.id
        api_base = self._api_base()
        self._ui_log("disconnect clicked", device_id=device_id)
        self.disconnect_socket()

        def work() -> list[Device]:
            self._api_for_base(api_base, "POST", f"/api/v1/devices/{device_id}/disconnect")
            return self._load_devices(api_base)

        def apply(devices: list[Device]) -> None:
            self._clear_live_plot()
            self._apply_loaded_devices(devices)
            self._select_device_by_id(device_id)
            self.status.set("disconnected")

        self._run_busy("Disconnecting Red Pitaya", work, apply)

    def _require_backend_disconnected(
        self, device_id: str, runtime_state: str | None, *, api_base: str | None = None
    ) -> None:
        def request(method: str, path: str) -> Any:
            if api_base is None:
                return self._api(method, path)
            return self._api_for_base(api_base, method, path)

        if str(runtime_state or "").lower() not in DISCONNECTED_STATES:
            try:
                request("POST", f"/api/v1/devices/{device_id}/disconnect")
            except Exception as exc:
                raise ApiError(
                    "Cannot change channels while the backend stream is still connected. "
                    "Use Red Pitayas > Disconnect device, wait for state 'Offline', "
                    f"then toggle again. Disconnect failed: {short_error(exc)}"
                ) from exc
        status = request("GET", f"/api/v1/devices/{device_id}/status")
        state = str(status.get("state") or "").lower()
        if state not in DISCONNECTED_STATES:
            raise ApiError(
                f"Cannot change channels while backend state is '{state}'. "
                "Disconnect the device first."
            )

    def _best_effort_disconnect_device(self, device_id: str, runtime_state: str | None) -> None:
        if str(runtime_state or "").lower() in DISCONNECTED_STATES:
            return
        try:
            self._api("POST", f"/api/v1/devices/{device_id}/disconnect")
        except Exception as exc:
            self.status.set(f"disconnect warning: {short_error(exc)}")

    def _clear_live_plot(self) -> None:
        self._reset_display_pause()
        with self.frame_lock:
            self.latest_frame = None
            self.sample_history.clear()
            self.socket_frame_count = 0
        with self.cluster_lock:
            self.cluster_processor.reset_stream()
            self.latest_cluster_result = None
            self.cluster_error = None
            self.cluster_last_sequence = None
        self.rendered_frame_count = 0
        self.last_stats_monotonic_ns = 0
        self.frames.set("0")
        self.sequence.set("-")
        self.mask.set("-")
        self.ch1_range.set("-")
        self.ch2_range.set("-")
        self.lost1.set("0")
        self.lost2.set("0")
        self.lost_pair.set("0 / 0")
        for item in self.trace_items:
            self.canvas.delete(item)
        self.trace_items.clear()
        self.canvas.delete("signal_trace")
        for item in self.spectrum_trace_items:
            self.spectrum_canvas.delete(item)
        self.spectrum_trace_items.clear()
        self.spectrum_canvas.delete("spectrum_trace")
        self.spectrum_peak.set("Peak: -")
        self._update_time_controls()
        notice = (
            self.loading_label
            if self.loading_active and self.loading_label not in CONNECTION_OVERLAY_BUSY_LABELS
            else CONNECTION_FAILED_NOTICE
            if self.connection_failure_active
            else "Waiting for live samples"
        )
        self._set_signal_notice(notice)
        self._set_spectrum_notice(
            self.loading_label
            if self.loading_active and self.loading_label not in CONNECTION_OVERLAY_BUSY_LABELS
            else CONNECTION_FAILED_SPECTRUM_NOTICE
            if self.connection_failure_active
            else "Waiting for spectrum data"
        )

    def _start_socket(self, device_id: str) -> None:
        self._reset_display_pause()
        selected_span = self.time_span_choice.get()
        self.pending_time_span_choice = (
            selected_span in TIME_SPANS_MS and TIME_SPANS_MS[selected_span] is not None
        )
        self.stop_event.clear()
        self.frame_count = 0
        self.rendered_frame_count = 0
        with self.frame_lock:
            self.socket_frame_count = 0
            self.latest_frame = None
            self.sample_history.clear()
        with self.cluster_lock:
            self.cluster_processor.reset_stream()
            self.latest_cluster_result = None
            self.cluster_error = None
            self.cluster_last_sequence = None
        self.frames.set("0")
        url = self._websocket_url(device_id)
        self._ui_log("opening sample socket", device_id=device_id, url=url)
        self.socket_thread = threading.Thread(
            target=lambda: asyncio.run(self._socket_loop(url)), daemon=True
        )
        self.socket_thread.start()

    async def _socket_loop(self, url: str) -> None:
        release_timer = start_display_timer()
        try:
            UI_LOGGER.info("sample socket connecting url=%s", url)
            self.events.put(("status", "connecting socket"))
            async with websockets.connect(url, max_size=None) as websocket:
                UI_LOGGER.info("sample socket connected url=%s", url)
                self.events.put(("status", "connected"))
                while not self.stop_event.is_set():
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                    except TimeoutError:
                        continue
                    if isinstance(message, str):
                        continue
                    frame = parse_frame(message)
                    self._process_cluster_frame(frame)
                    with self.frame_lock:
                        self.socket_frame_count += 1
                        self.sample_history.append(frame)
                        self.latest_frame = frame
        except Exception as exc:
            if not self.stop_event.is_set():
                UI_LOGGER.warning("sample socket failed error=%s", short_error(exc, 180))
                UI_LOGGER.debug("sample socket traceback", exc_info=True)
                self.events.put(
                    (
                        "connection_error",
                        {
                            "title": "Connection failed",
                            "message": self._connection_failure_message(exc),
                        },
                    )
                )
        finally:
            release_timer()
            UI_LOGGER.info("sample socket closed url=%s", url)
            self.events.put(("socket_closed", None))

    def _process_cluster_frame(self, frame: SampleFrame) -> None:
        if frame.channel_1 is None or frame.channel_2 is None:
            with self.cluster_lock:
                self.cluster_error = "Cluster processing requires both CH1 and CH2"
                self.latest_cluster_result = None
            return
        channel_a = adc_counts_to_volts(frame.channel_1)
        channel_b = adc_counts_to_volts(frame.channel_2)
        if channel_a is None or channel_b is None:
            return
        with self.cluster_lock:
            sequence_gap = (
                self.cluster_last_sequence is not None
                and frame.sequence != self.cluster_last_sequence + 1
            )
            self.cluster_last_sequence = frame.sequence
            try:
                result = self.cluster_processor.feed(
                    channel_a,
                    channel_b,
                    frame.sample_rate_hz,
                    discontinuity=sequence_gap or bool(frame.flags & 0b11),
                )
                if result is not None:
                    self.latest_cluster_result = result
                self.cluster_error = None
            except ValueError as exc:
                self.latest_cluster_result = None
                self.cluster_error = f"Cluster processing unavailable: {short_error(exc, 180)}"

    @staticmethod
    def _connection_failure_message(exc: BaseException) -> str:
        detail = short_error(exc, 170)
        if not detail:
            detail = exc.__class__.__name__
        return f"Could not open the Red Pitaya sample stream. {detail}"

    def _show_connection_failure(self, title: str, message: str) -> None:
        self.status.set("RP not connected to engine")
        self._stop_spinner(restore_notices=False)
        self._set_connection_failed_notice()
        self._show_toast(title, message, level="error")

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                try:
                    self._handle_ui_event(event, payload)
                except Exception as exc:
                    UI_LOGGER.warning(
                        "ui event handler failed event=%s error=%s",
                        event,
                        short_error(exc, 180),
                    )
                    UI_LOGGER.debug("ui event handler traceback", exc_info=True)
                    self.status.set("ui event error")
        except queue.Empty:
            pass

        now = time_monotonic_ns()
        self.last_render_monotonic_ns = now
        with self.frame_lock:
            frame = self.latest_frame
            received_count = self.socket_frame_count
        interval = self._display_interval_ms()
        display_due = interval <= UI_FRAME_INTERVAL_MS or (
            now - self.__dict__.get("last_display_monotonic_ns", 0) >= interval * 1_000_000
        )
        if (
            frame is not None
            and received_count != self.rendered_frame_count
            and display_due
            and not self.__dict__.get("display_paused", False)
        ):
            self.last_display_monotonic_ns = now
            self.rendered_frame_count = received_count
            self._handle_frame(frame, received_count)

        # Schedule from the start of this refresh, so processing time doesn't
        # lengthen every frame. Still yield to Tk and never redraw stale samples.
        self.after(
            next_frame_delay_ms(self.last_render_monotonic_ns, time_monotonic_ns()),
            self._drain_events,
        )

    def _handle_ui_event(self, event: str, payload: Any) -> None:
        if event == "status":
            status = str(payload)
            self.status.set(status)
            self._ui_log("ui status event", status=status)
            if status == "connecting socket":
                if not self.loading_active:
                    self._start_spinner("Opening sample stream")
            elif status == "connected":
                self.connection_failure_active = False
                if self.loading_active and self.loading_label in CONNECTION_OVERLAY_BUSY_LABELS:
                    self._show_connection_overlay(
                        "Connected to RTQLM acquisition engine",
                        "Waiting for Red Pitaya samples",
                    )
                if self.loading_label == "Opening sample stream":
                    self._stop_spinner()
                self._set_signal_notice("Waiting for first samples")
                self._set_spectrum_notice("Waiting for first spectrum")
        elif event == "error":
            self.status.set("error")
            self._stop_spinner()
            messagebox.showerror("Receiver error", str(payload))
        elif event == "connection_error":
            title = str(payload.get("title", "Connection failed"))
            message = str(payload.get("message", "Could not connect to Red Pitaya."))
            self._show_connection_failure(title, message)
        elif event == "engine_ping":
            running, elapsed_ms = payload
            self._handle_engine_ping(bool(running), int(elapsed_ms))
        elif event == "socket_closed":
            if not self.connection_failure_active:
                self.status.set("disconnected")
            self._ui_log("socket closed event")
            self.stop_event.clear()
            self.socket_thread = None
            self._stop_spinner(restore_notices=not self.connection_failure_active)
        elif event == "busy_success":
            label, callback, result, elapsed_ms = payload
            self._ui_log("background job finished", label=label, elapsed_ms=elapsed_ms)
            try:
                if callback is not None:
                    callback(result)
            except Exception as exc:
                self._show_error(exc)
            finally:
                if self.loading_label == label:
                    self._stop_spinner()
        elif event == "busy_error":
            label, exc, elapsed_ms = payload
            self._ui_log(
                "background job failed",
                label=label,
                elapsed_ms=elapsed_ms,
                error=short_error(exc, 180),
            )
            if label in CONNECTION_FAILURE_BUSY_LABELS:
                self._show_connection_failure(
                    "Connection failed",
                    f"Could not connect to Red Pitaya. {short_error(exc, 180)}",
                )
            else:
                if self.loading_label == label:
                    self._stop_spinner()
                self._show_error(exc)

    def _handle_frame(self, frame: SampleFrame, received_count: int) -> None:
        self.connection_failure_active = False
        self.frame_count = received_count
        if self.__dict__.get("pending_time_span_choice", False):
            self.pending_time_span_choice = not self._apply_time_span_to_frame(
                self.time_span_choice.get(), frame
            )
        now = time_monotonic_ns()
        update_stats = now - self.last_stats_monotonic_ns >= STATS_MIN_INTERVAL_MS * 1_000_000
        if update_stats:
            self.last_stats_monotonic_ns = now
            for variable, value in (
                (self.frames, str(received_count)),
                (self.sequence, str(frame.sequence)),
                (self.sample_rate, f"{frame.sample_rate_hz:.2f} Hz"),
                (self.samples, str(frame.samples_per_channel)),
                (self.mask, f"0x{frame.channel_mask:x}"),
                (self.lost1, str(frame.fpga_lost_ch1)),
                (self.lost2, str(frame.fpga_lost_ch2)),
                (self.lost_pair, f"{frame.fpga_lost_ch1} / {frame.fpga_lost_ch2}"),
                (self.flags, str(frame.flags)),
            ):
                set_if_changed(variable, value)
        if now - self.last_frame_log_monotonic_ns >= 1_000_000_000:
            self.last_frame_log_monotonic_ns = now
            self._ui_log(
                "frame rendered",
                frames=received_count,
                sequence=frame.sequence,
                sample_rate=f"{frame.sample_rate_hz:.2f}",
                samples=frame.samples_per_channel,
                mask=f"0x{frame.channel_mask:x}",
            )

        try:
            channel_1, channel_2, rate, count = self._channels_for_signal(frame)
            channel_1_volts = adc_counts_to_volts(channel_1)
            channel_2_volts = adc_counts_to_volts(channel_2)
            if update_stats:
                # Raw readouts and FFT retain the full latest packet, even when
                # the history plot only copies the points needed on screen.
                range_1 = (
                    adc_counts_to_volts(frame.channel_1)
                    if not self._signal_script_active()
                    else channel_1_volts
                )
                range_2 = (
                    adc_counts_to_volts(frame.channel_2)
                    if not self._signal_script_active()
                    else channel_2_volts
                )
                set_if_changed(
                    self.ch1_range,
                    self._channel_range_label(
                        1,
                        range_1,
                        sample_range(range_1),
                        self.channel_1_visible.get(),
                    ),
                )
                set_if_changed(
                    self.ch2_range,
                    self._channel_range_label(
                        2,
                        range_2,
                        sample_range(range_2),
                        self.channel_2_visible.get(),
                    ),
                )
            self._draw_frame(channel_1_volts, channel_2_volts, rate, count)
            if not self._signal_script_active():
                self._draw_spectrum(frame.channel_1, frame.channel_2, frame.sample_rate_hz)
            else:
                self._draw_spectrum(channel_1, channel_2, rate)
        except Exception as exc:
            self.signal_processor = None
            self.script_status.set(f"signal script disabled: {exc}")
            self.status.set("signal script error")

    def _channel_range_label(
        self,
        channel: int,
        samples: Any,
        value_range: tuple[float, float] | None,
        visible: bool,
    ) -> str:
        if not visible:
            return f"CH{channel} hidden"
        if value_range is not None:
            return f"{format_voltage(value_range[0])} / {format_voltage(value_range[1])}"
        acquired = True
        if self.selected_device is not None:
            acquired = (
                self.selected_device.channel_1_enabled
                if channel == 1
                else self.selected_device.channel_2_enabled
            )
        if not acquired:
            return f"CH{channel} acquisition off"
        if samples is None:
            return f"CH{channel} no data"
        return "-"

    def _processed_channels(self, frame: SampleFrame) -> tuple[Any, Any]:
        if not self._signal_scripting_enabled():
            self.signal_processor = None
            return frame.channel_1, frame.channel_2
        if self.signal_processor is None:
            return frame.channel_1, frame.channel_2

        signals = self._signals_for_frame(frame)
        try:
            result = self.signal_processor(signals, np)
        except Exception as exc:
            self.signal_processor = None
            self.script_status.set(f"signal script disabled: {exc}")
            self.status.set("signal script error")
            return frame.channel_1, frame.channel_2

        if isinstance(result, dict):
            first, second = result.get("ch1"), result.get("ch2")
            return normalize_script_channel(
                first, frame.samples_per_channel
            ), normalize_script_channel(second, frame.samples_per_channel)
        if isinstance(result, tuple | list):
            first = result[0] if len(result) > 0 else None
            second = result[1] if len(result) > 1 else None
            return normalize_script_channel(
                first, frame.samples_per_channel
            ), normalize_script_channel(second, frame.samples_per_channel)
        return normalize_script_channel(result, frame.samples_per_channel), None

    def _redraw_latest_spectrum(self) -> None:
        frame = self._view_frame()
        if frame is None:
            self._redraw_spectrum_grid()
            return
        try:
            if self._signal_script_active():
                frame = self._frame_for_signal(frame)
            channel_1, channel_2 = self._processed_channels(frame)
            self._draw_spectrum(channel_1, channel_2, frame.sample_rate_hz, force=True)
        except Exception as exc:
            self.signal_processor = None
            self.script_status.set(f"signal script disabled: {exc}")
            self.status.set("signal script error")

    def _signals_for_frame(self, frame: SampleFrame) -> dict[str, np.ndarray[Any, Any]]:
        """Return raw ADC counts for optional signal-processing scripts."""
        signals: dict[str, np.ndarray[Any, Any]] = {}
        device = self.selected_device
        pairs = (("ch1", frame.channel_1), ("ch2", frame.channel_2))
        for channel_name, samples in pairs:
            if samples is None:
                continue
            array = samples_to_numpy(samples)
            signals[f"selected.{channel_name}"] = array
            if device is not None:
                signals[f"{device_slug(device)}.{channel_name}"] = array
                signals[f"{device.id}.{channel_name}"] = array
        return signals

    def _redraw_grid(self) -> None:
        for item in self.grid_items:
            self.canvas.delete(item)
        self.grid_items.clear()
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        left_pad = 56
        bottom_pad = 26
        plot_width = max(width - left_pad - 8, 1)
        plot_height = max(height - SIGNAL_PLOT_TOP_PAD - bottom_pad - 8, 1)
        plot_bottom = SIGNAL_PLOT_TOP_PAD + plot_height
        for index in range(11):
            x = left_pad + index * plot_width / 10
            self.grid_items.append(
                self.canvas.create_line(
                    x, SIGNAL_PLOT_TOP_PAD, x, plot_bottom, fill=COLORS["plot_grid"]
                )
            )
        for index in range(9):
            y = SIGNAL_PLOT_TOP_PAD + index * plot_height / 8
            self.grid_items.append(
                self.canvas.create_line(left_pad, y, width, y, fill=COLORS["plot_grid"])
            )
        self.grid_items.append(
            self.canvas.create_line(
                left_pad,
                SIGNAL_PLOT_TOP_PAD + plot_height / 2,
                width,
                SIGNAL_PLOT_TOP_PAD + plot_height / 2,
                fill=COLORS["plot_axis"],
            )
        )
        self._draw_axis_labels(left_pad, plot_width, plot_height, SIGNAL_PLOT_TOP_PAD)
        self.grid_items.append(
            self.canvas.create_text(left_pad + 12, 16, text="CH1", fill=COLORS["ch1"], anchor="w")
        )
        self.grid_items.append(
            self.canvas.create_text(left_pad + 56, 16, text="CH2", fill=COLORS["ch2"], anchor="w")
        )
        with self.frame_lock:
            has_frame = self.latest_frame is not None
        if self.loading_active and self.loading_label not in CONNECTION_OVERLAY_BUSY_LABELS:
            self._set_signal_notice(self.loading_label)
        elif self.connection_failure_active:
            self._set_signal_notice(CONNECTION_FAILED_NOTICE)
        elif not has_frame:
            self._set_signal_notice("No live signal\nSelect a Red Pitaya and connect to begin")

    def _redraw_all_grids(self) -> None:
        self._redraw_grid()
        self._redraw_spectrum_grid()

    def _draw_axis_labels(
        self, left_pad: int, plot_width: int, plot_height: int, plot_top: int
    ) -> None:
        y_values = (
            self.axis_y_abs,
            self.axis_y_abs / 2,
            0.0,
            -self.axis_y_abs / 2,
            -self.axis_y_abs,
        )
        for index, value in enumerate(y_values):
            y = plot_top + index * plot_height / 4
            self.grid_items.append(
                self.canvas.create_text(
                    left_pad - 8,
                    y,
                    text=format_voltage(value),
                    fill=COLORS["muted"],
                    anchor="e",
                    font=("Consolas", 8),
                )
            )

        duration_ms = 0.0
        if self.axis_sample_rate_hz > 0 and self.axis_samples_per_channel > 0:
            duration_ms = self.axis_samples_per_channel / self.axis_sample_rate_hz * 1000
        # Keep tick labels easy to scan.  Microseconds retain useful precision for
        # short captures without showing fractional values on the horizontal axis.
        time_factor = 1.0 if duration_ms >= 5.0 else 1000.0
        time_unit = "ms" if duration_ms >= 5.0 else "µs"
        for index in range(6):
            x = left_pad + index * plot_width / 5
            time_value = round(index * duration_ms / 5 * time_factor)
            self.grid_items.append(
                self.canvas.create_text(
                    x,
                    plot_top + plot_height + 15,
                    text=f"{time_value} {time_unit}",
                    fill=COLORS["muted"],
                    anchor="n",
                    font=("Consolas", 8),
                )
            )

    def _draw_frame(
        self, channel_1: Any, channel_2: Any, sample_rate_hz: float, samples_per_channel: int
    ) -> None:
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        self._clear_signal_notice()
        for item in self.trace_items:
            self.canvas.delete(item)
        self.trace_items.clear()

        plot_channel_1 = self._window_channel(channel_1) if self.channel_1_visible.get() else None
        plot_channel_2 = self._window_channel(channel_2) if self.channel_2_visible.get() else None
        start_index, end_index = self._window_bounds(samples_per_channel)
        visible_samples = max(end_index - start_index, 1)
        axis_changed = (
            sample_rate_hz != self.axis_sample_rate_hz
            or visible_samples != self.axis_samples_per_channel
            or self.y_axis_dirty
        )
        if self.y_axis_fit_pending:
            self._fit_y_axis_to_channels(plot_channel_1, plot_channel_2)
            axis_changed = True
        self.axis_sample_rate_hz = sample_rate_hz
        self.axis_samples_per_channel = visible_samples
        self._update_time_controls(sample_rate_hz, samples_per_channel, start_index, end_index)
        if not self.grid_items or axis_changed:
            self._redraw_grid()
        self.y_axis_dirty = False

        left_pad = 56
        bottom_pad = 26
        plot_width = max(width - left_pad - 8, 1)
        plot_height = max(height - SIGNAL_PLOT_TOP_PAD - bottom_pad - 8, 1)
        plot_bottom = SIGNAL_PLOT_TOP_PAD + plot_height
        y_scale = (plot_height * 0.5) / self.axis_y_abs
        plot_points = self._signal_plot_points()
        padding = min(self.signal_padding_samples, visible_samples)
        trace_left = left_pad + plot_width * padding / visible_samples
        trace_width = plot_width * (visible_samples - padding) / visible_samples
        draw_channel(
            self.canvas,
            plot_channel_1,
            COLORS["ch1"],
            SIGNAL_PLOT_TOP_PAD + self._channel_y_offset(1, plot_height),
            y_scale,
            trace_width,
            trace_left,
            plot_points,
            tag="signal_ch1",
        )
        draw_channel(
            self.canvas,
            plot_channel_2,
            COLORS["ch2"],
            SIGNAL_PLOT_TOP_PAD + self._channel_y_offset(2, plot_height),
            y_scale,
            trace_width,
            trace_left,
            plot_points,
            tag="signal_ch2",
        )
        if self.cursor_enabled:
            cursor_x = left_pad + plot_width / 2
            self.trace_items.append(
                self.canvas.create_line(
                    cursor_x,
                    SIGNAL_PLOT_TOP_PAD,
                    cursor_x,
                    plot_bottom,
                    fill=COLORS["warn"],
                    width=1,
                    dash=(4, 4),
                )
            )
            if sample_rate_hz > 0:
                cursor_ms = (start_index + visible_samples / 2) / sample_rate_hz * 1000
                readout = [f"t {cursor_ms:.4g} ms"]
                for label, values in (("CH1", plot_channel_1), ("CH2", plot_channel_2)):
                    if values is not None and len(values):
                        readout.append(f"{label} {format_voltage(float(values[len(values) // 2]))}")
                self.trace_items.append(
                    self.canvas.create_text(
                        cursor_x + 8,
                        18,
                        text="  ·  ".join(readout),
                        fill=COLORS["warn"],
                        anchor="w",
                        font=FONTS["mono"],
                    )
                )
                set_if_changed(self.cursor_status, "  ·  ".join(readout))
        self._draw_missing_channel_markers(channel_1, channel_2, left_pad, plot_width)
        self._update_display_detail()

    def _draw_missing_channel_markers(
        self, channel_1: Any, channel_2: Any, left_pad: int, plot_width: int
    ) -> None:
        missing: list[str] = []
        if self.channel_1_visible.get() and channel_1 is None:
            missing.append(self._missing_channel_text(1))
        if self.channel_2_visible.get() and channel_2 is None:
            missing.append(self._missing_channel_text(2))
        for index, text in enumerate(missing):
            self.trace_items.append(
                self.canvas.create_text(
                    left_pad + plot_width - 10,
                    18 + index * 18,
                    text=text,
                    fill=COLORS["warn"],
                    anchor="e",
                    font=("Segoe UI", 9, "bold"),
                )
            )

    def _missing_channel_text(self, channel: int) -> str:
        if self.selected_device is not None:
            acquired = (
                self.selected_device.channel_1_enabled
                if channel == 1
                else self.selected_device.channel_2_enabled
            )
            if not acquired:
                return f"CH{channel} acquisition off"
        return f"CH{channel} no samples"

    def _redraw_spectrum_grid(self) -> None:
        for item in self.spectrum_grid_items:
            self.spectrum_canvas.delete(item)
        self.spectrum_grid_items.clear()
        width = max(self.spectrum_canvas.winfo_width(), 1)
        height = max(self.spectrum_canvas.winfo_height(), 1)
        left_pad = 56
        bottom_pad = 24
        plot_width = max(width - left_pad - 8, 1)
        plot_height = max(height - bottom_pad - 8, 1)

        for index in range(11):
            x = left_pad + index * plot_width / 10
            self.spectrum_grid_items.append(
                self.spectrum_canvas.create_line(x, 0, x, plot_height, fill=COLORS["grid"])
            )
        for index in range(7):
            y = index * plot_height / 6
            self.spectrum_grid_items.append(
                self.spectrum_canvas.create_line(left_pad, y, width, y, fill=COLORS["grid"])
            )

        scale = self.spectrum_scale.get()
        top_label = "0 dBFS" if scale == "dBFS" else "max"
        bottom_label = "-120" if scale == "dBFS" else "0"
        self.spectrum_grid_items.append(
            self.spectrum_canvas.create_text(
                left_pad - 8,
                10,
                text=top_label,
                fill=COLORS["muted"],
                anchor="e",
                font=("Consolas", 8),
            )
        )
        self.spectrum_grid_items.append(
            self.spectrum_canvas.create_text(
                left_pad - 8,
                plot_height - 3,
                text=bottom_label,
                fill=COLORS["muted"],
                anchor="e",
                font=("Consolas", 8),
            )
        )
        self.spectrum_grid_items.append(
            self.spectrum_canvas.create_text(
                left_pad,
                plot_height + 13,
                text="0 Hz",
                fill=COLORS["muted"],
                anchor="n",
                font=("Consolas", 8),
            )
        )
        self.spectrum_grid_items.append(
            self.spectrum_canvas.create_text(
                left_pad + plot_width,
                plot_height + 13,
                text=format_frequency(self.spectrum_axis_sample_rate_hz / 2),
                fill=COLORS["muted"],
                anchor="n",
                font=("Consolas", 8),
            )
        )

        for item in self.spectrum_grid_items:
            self.spectrum_canvas.tag_lower(item)
        with self.frame_lock:
            has_frame = self.latest_frame is not None
        if self.loading_active and self.loading_label not in CONNECTION_OVERLAY_BUSY_LABELS:
            self._set_spectrum_notice(self.loading_label)
        elif self.connection_failure_active:
            self._set_spectrum_notice(CONNECTION_FAILED_SPECTRUM_NOTICE)
        elif not has_frame:
            self._set_spectrum_notice("Waiting for spectrum data")

    def _draw_spectrum(
        self,
        channel_1: Any,
        channel_2: Any,
        sample_rate_hz: float,
        *,
        force: bool = False,
    ) -> None:
        now = time_monotonic_ns()
        if (
            not force
            and now - self.last_spectrum_monotonic_ns < SPECTRUM_MIN_INTERVAL_MS * 1_000_000
        ):
            return
        self.last_spectrum_monotonic_ns = now

        for item in self.spectrum_trace_items:
            self.spectrum_canvas.delete(item)
        self.spectrum_trace_items.clear()
        self._clear_spectrum_notice()

        channel_name = self.spectrum_channel.get()
        samples = channel_2 if channel_name == "CH2" else channel_1
        color = COLORS["ch2"] if channel_name == "CH2" else COLORS["ch1"]
        axis_changed = (
            sample_rate_hz != self.spectrum_axis_sample_rate_hz
            or self.spectrum_scale.get() != self.spectrum_axis_scale
        )
        self.spectrum_axis_sample_rate_hz = sample_rate_hz
        self.spectrum_axis_scale = self.spectrum_scale.get()
        if not self.spectrum_grid_items or axis_changed:
            self._redraw_spectrum_grid()

        if samples is None or len(samples) < 2:
            self.spectrum_canvas.itemconfigure("spectrum_trace", state="hidden")
            self.spectrum_peak.set("Peak: -")
            self.spectrum_trace_items.append(
                self.spectrum_canvas.create_text(
                    68,
                    24,
                    text=f"{channel_name} is not enabled",
                    fill=COLORS["muted"],
                    anchor="w",
                )
            )
            return

        spectrum = compute_spectrum(samples, sample_rate_hz, int(self.spectrum_fft_size.get()))
        if spectrum is None:
            self.spectrum_canvas.itemconfigure("spectrum_trace", state="hidden")
            self.spectrum_peak.set("Peak: -")
            return

        width = max(self.spectrum_canvas.winfo_width(), 1)
        height = max(self.spectrum_canvas.winfo_height(), 1)
        left_pad = 56
        bottom_pad = 24
        plot_width = max(width - left_pad - 8, 1)
        plot_height = max(height - bottom_pad - 8, 1)
        use_db = self.spectrum_scale.get() == "dBFS"
        magnitudes = spectrum["magnitudes"]

        if use_db:
            values = 20 * np.log10(np.maximum(magnitudes, 1e-12))
            normalized = np.clip((values + 120.0) / 120.0, 0.0, 1.0)
        else:
            maximum = max(float(np.max(magnitudes)), 1e-12)
            normalized = np.clip(magnitudes / maximum, 0.0, 1.0)

        draw_spectrum_line(
            self.spectrum_canvas,
            normalized,
            color,
            plot_width,
            plot_height,
            left_pad,
        )
        self.spectrum_peak.set(
            f"Peak: {format_frequency(float(spectrum['peak_hz']))} / "
            f"{float(spectrum['peak_db']):.1f} dBFS"
        )

    def _api(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 10,
    ) -> Any:
        return self._api_for_base(
            self._api_base(), method, path, body, timeout_seconds=timeout_seconds
        )

    def _api_for_base(
        self,
        api_base: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 10,
    ) -> Any:
        started = time.monotonic()
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            f"{api_base.rstrip('/')}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                UI_LOGGER.info(
                    "api %s %s -> %s elapsed_ms=%s",
                    method,
                    path,
                    response.status,
                    elapsed_ms,
                )
                if response.status == 204:
                    return None
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            detail = exc.reason
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail = payload.get("detail", detail)
            except Exception:
                pass
            UI_LOGGER.info(
                "api %s %s -> %s elapsed_ms=%s error=%s",
                method,
                path,
                exc.code,
                elapsed_ms,
                detail,
            )
            raise ApiError(str(detail)) from exc
        except TimeoutError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            detail = f"Timed out after {timeout_seconds:g}s"
            UI_LOGGER.info(
                "api %s %s timeout elapsed_ms=%s error=%s",
                method,
                path,
                elapsed_ms,
                detail,
            )
            raise ApiError(detail) from exc
        except URLError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            UI_LOGGER.info(
                "api %s %s failed elapsed_ms=%s error=%s",
                method,
                path,
                elapsed_ms,
                exc.reason,
            )
            raise ApiError(str(exc.reason)) from exc

    def _api_base(self) -> str:
        return self.api_base.get().strip().rstrip("/")

    def _websocket_url(self, device_id: str) -> str:
        parsed = urlparse(self._api_base())
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}/ws/v1/devices/{device_id}/samples"

    def _show_error(self, exc: BaseException) -> None:
        self.status.set("error")
        if hasattr(self, "engine_status"):
            self._set_engine_status("error")
        self._ui_log("ui error", error=short_error(exc, 180))
        messagebox.showerror("Error", str(exc))

    def _on_close(self) -> None:
        self._ui_log("app closing")
        self.closing = True
        self.stop_event.set()
        self._stop_spinner()
        try:
            for callback_id in self.tk.call("after", "info"):
                try:
                    self.after_cancel(callback_id)
                except tk.TclError:
                    pass
        except tk.TclError:
            pass
        for toast in [*self.toast_frames]:
            self._dismiss_toast(toast)
        if self.connection_overlay is not None and self.connection_overlay.winfo_exists():
            self.connection_overlay.destroy()
        if self.engine_process and self.engine_process.poll() is None:
            terminate_process_tree(self.engine_process)
        self.destroy()


def load_settings() -> AppSettings:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return AppSettings(
            api_base=str(data.get("api_base") or AppSettings.api_base),
            launch_engine_on_start=bool(
                data.get("launch_engine_on_start", AppSettings.launch_engine_on_start)
            ),
        )
    except FileNotFoundError:
        return AppSettings()
    except Exception:
        return AppSettings()


def load_cluster_config() -> ClusterProcessingConfig:
    try:
        data = json.loads(CLUSTER_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("cluster processing configuration must be an object")
        return config_from_dict(data)
    except FileNotFoundError:
        return ClusterProcessingConfig()
    except Exception as exc:
        UI_LOGGER.warning("invalid cluster processing configuration: %s", short_error(exc))
        return ClusterProcessingConfig()


def save_cluster_config(config: ClusterProcessingConfig) -> None:
    CLUSTER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CLUSTER_CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(config_to_dict(config), indent=2), encoding="utf-8")
    temporary.replace(CLUSTER_CONFIG_PATH)


def signal_script_feature_enabled() -> bool:
    return os.environ.get(SIGNAL_SCRIPT_FEATURE_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def save_settings(settings: AppSettings) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(
            {
                "api_base": settings.api_base,
                "launch_engine_on_start": settings.launch_engine_on_start,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def editable_profile_payload(profile: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "name",
        "source",
        "host",
        "enabled",
        "isDefault",
        "channel1Enabled",
        "channel2Enabled",
        "decimation",
        "blockSizeBytes",
        "adcBufferSizeBytes",
        "streamingModulePath",
        "simulatorSamplesPerPacket",
        "simulatorSeed",
    )
    return {key: profile[key] for key in keys}


def short_error(exc: BaseException, limit: int = 90) -> str:
    message = " ".join(str(exc).split())
    if len(message) <= limit:
        return message
    return f"{message[: limit - 1]}…"


def blend_hex_color(start: str, end: str, progress: float) -> str:
    bounded = min(max(progress, 0.0), 1.0)
    start_rgb = tuple(int(start[index : index + 2], 16) for index in (1, 3, 5))
    end_rgb = tuple(int(end[index : index + 2], 16) for index in (1, 3, 5))
    values = [
        round(start_value + (end_value - start_value) * bounded)
        for start_value, end_value in zip(start_rgb, end_rgb, strict=True)
    ]
    return "#{:02x}{:02x}{:02x}".format(*values)


def safe_builtins() -> dict[str, Any]:
    return {
        "abs": abs,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "round": round,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
    }


def device_slug(device: Device) -> str:
    raw = f"{device.name or 'redpitaya'}_{device.host or device.id}".lower()
    chars = [char if char.isalnum() else "_" for char in raw]
    compact = "_".join(part for part in "".join(chars).split("_") if part)
    return compact or device.id


def samples_to_numpy(samples: Any) -> np.ndarray[Any, Any]:
    if isinstance(samples, np.ndarray):
        return samples
    return np.asarray(samples)


def normalize_script_channel(value: Any, length: int) -> np.ndarray[Any, Any] | None:
    if value is None:
        return None
    array = np.asarray(value)
    if array.ndim == 0 or array.size == 1:
        scalar = float(array.reshape(-1)[0])
        return np.full(max(length, 2), scalar, dtype=np.float64)
    return np.nan_to_num(array.reshape(-1).astype(np.float64, copy=False))


def parse_frame(message: bytes) -> SampleFrame:
    if len(message) < HEADER_SIZE:
        raise ValueError(f"message too short: {len(message)} bytes")
    values = HEADER.unpack_from(message)
    magic = values[0]
    protocol_version = values[1]
    channel_mask = values[2]
    header_size = values[3]
    if magic != MAGIC or protocol_version != 1 or header_size != HEADER_SIZE:
        raise ValueError(
            f"unsupported frame: magic={magic!r}, version={protocol_version}, header={header_size}"
        )

    sequence = int(values[5])
    sample_rate_hz = float(values[8])
    samples_per_channel = int(values[10])
    payload_size = int(values[11])
    flags = int(values[12])
    fpga_lost_ch1 = int(values[13])
    fpga_lost_ch2 = int(values[14])

    payload = memoryview(message)[HEADER_SIZE : HEADER_SIZE + payload_size]
    samples = payload.cast("h")
    if sys.byteorder != "little":
        raise RuntimeError("native receiver currently expects a little-endian host")

    offset = 0
    channel_1 = None
    channel_2 = None
    if channel_mask & 1:
        channel_1 = samples[offset : offset + samples_per_channel]
        offset += samples_per_channel
    if channel_mask & 2:
        channel_2 = samples[offset : offset + samples_per_channel]

    return SampleFrame(
        channel_mask=channel_mask,
        sequence=sequence,
        sample_rate_hz=sample_rate_hz,
        samples_per_channel=samples_per_channel,
        flags=flags,
        fpga_lost_ch1=fpga_lost_ch1,
        fpga_lost_ch2=fpga_lost_ch2,
        channel_1=channel_1,
        channel_2=channel_2,
    )


def sample_range(samples: Any) -> tuple[float, float] | None:
    if samples is None or len(samples) == 0:
        return None
    data = samples_to_numpy(samples)
    return float(np.min(data)), float(np.max(data))


def set_if_changed(variable: tk.StringVar, value: str) -> None:
    # Setting even identical text makes Tk schedule label redraws and layout work.
    if variable.get() != value:
        variable.set(value)


def adc_counts_to_volts(samples: Any) -> np.ndarray[Any, Any] | None:
    """Convert signed stream samples to volts for the standard ±1 V STEMlab input."""
    if samples is None:
        return None
    return samples_to_numpy(samples).astype(np.float64, copy=False) / ADC_COUNTS_PER_VOLT


def max_sample_abs(*channels: Any) -> float:
    maximum = 0.0
    for channel in channels:
        if channel is None:
            continue
        for value in channel[:: max(len(channel) // MAX_POINTS, 1)]:
            maximum = max(maximum, abs(float(value)))
    return maximum


def time_monotonic_ns() -> int:
    return time.monotonic_ns()


def next_frame_delay_ms(last_refresh_ns: int, now_ns: int) -> int:
    remaining_ns = last_refresh_ns + UI_FRAME_INTERVAL_MS * 1_000_000 - now_ns
    return max(1, (remaining_ns + 999_999) // 1_000_000)


def display_interval_ms(mode: str, zoom: float, visible_seconds: float | None = None) -> int:
    if mode == "Live":
        return UI_FRAME_INTERVAL_MS
    if mode == "Slow":
        return 500
    interval = round(UI_FRAME_INTERVAL_MS / max(zoom, TIME_ZOOM_MIN) ** 0.5)
    if visible_seconds is not None and visible_seconds >= WIDE_VIEW_FAST_REFRESH_SECONDS:
        interval = min(interval, WIDE_VIEW_AUTO_INTERVAL_MS)
    return max(UI_FRAME_INTERVAL_MS, min(1000, interval))


def start_display_timer() -> Callable[[], None]:
    """Improve Windows Tk timing only for the lifetime of an open sample stream."""
    if sys.platform == "win32":
        try:
            winmm = ctypes.WinDLL("winmm")
            if winmm.timeBeginPeriod(1) == 0:
                return lambda: winmm.timeEndPeriod(1)
        except OSError:
            pass
    return lambda: None


def terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
            return
        except Exception:
            pass
    process.terminate()


def smooth_acquisition_block_size(decimation: int) -> int:
    """Choose a supported block for frequent delivery at the existing sample rate."""
    if not 1 <= decimation <= 65_536:
        raise ValueError("Decimation must be from 1 to 65536.")
    bytes_per_refresh = max(1, int((125_000_000 / decimation) * 2 / 60))
    return min(2_097_152, max(2048, 1 << (bytes_per_refresh.bit_length() - 1)))


def acquisition_channel_count(device: Device | None) -> int:
    if device is None:
        return 2
    return max(int(device.channel_1_enabled) + int(device.channel_2_enabled), 1)


def acquisition_batch_seconds(
    block_size_bytes: int, channel_count: int, sample_rate_hz: float
) -> float:
    if block_size_bytes <= 0 or channel_count <= 0 or sample_rate_hz <= 0:
        return 0.0
    samples_per_channel = block_size_bytes / 2 / channel_count
    return samples_per_channel / sample_rate_hz


def format_duration(seconds: float) -> str:
    if not np.isfinite(seconds) or seconds <= 0:
        return "-"
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.0f} us"
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.2f} s"


def format_voltage(value: float) -> str:
    return f"{value:.3g} V"


def format_frequency(value: float) -> str:
    if not np.isfinite(value):
        return "-"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f} MHz"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.2f} kHz"
    return f"{value:.1f} Hz"


def compute_spectrum(
    samples: Any, sample_rate_hz: float, requested_size: int
) -> dict[str, Any] | None:
    if samples is None or len(samples) < 16:
        return None

    limit = min(max(requested_size, 16), len(samples))
    fft_size = 1 << (limit.bit_length() - 1)
    if fft_size < 16:
        return None

    data = samples_to_numpy(samples)[-fft_size:].astype(np.float64, copy=False)
    data = data - float(np.mean(data))
    window = np.hanning(fft_size)
    window_sum = max(float(np.sum(window)), 1e-12)
    bins = np.fft.rfft(data * window)
    magnitudes = np.abs(bins) * 2.0 / (window_sum * 32768.0)
    if len(magnitudes) > 0:
        magnitudes[0] *= 0.5

    if len(magnitudes) > 1:
        peak_index = int(np.argmax(magnitudes[1:]) + 1)
    else:
        peak_index = 0
    peak_magnitude = max(float(magnitudes[peak_index]), 1e-12)
    return {
        "magnitudes": magnitudes,
        "peak_hz": peak_index * sample_rate_hz / fft_size,
        "peak_db": 20.0 * np.log10(peak_magnitude),
    }


def draw_channel(
    canvas: tk.Canvas,
    samples: Any,
    color: str,
    y_offset: float,
    y_scale: float,
    width: int,
    x_offset: int = 0,
    point_budget: int = MAX_POINTS,
    *,
    tag: str = "",
) -> int | None:
    if samples is None or len(samples) < 2:
        if tag:
            canvas.itemconfigure(tag, state="hidden")
        return None
    indices = np.linspace(0, len(samples) - 1, min(len(samples), point_budget), dtype=np.intp)
    data = samples_to_numpy(samples)[indices]
    points = np.empty((len(data), 2), dtype=np.float64)
    points[:, 0] = x_offset + indices * (width / (len(samples) - 1))
    points[:, 1] = y_offset - data * y_scale
    return update_trace_line(canvas, points.ravel().tolist(), color, tag, "signal_trace")


def update_trace_line(
    canvas: tk.Canvas, points: list[float], color: str, tag: str, group: str
) -> int:
    items = canvas.find_withtag(tag) if tag else ()
    if items:
        item = int(items[0])
        canvas.coords(item, *points)
        canvas.itemconfigure(item, fill=color, state="normal")
        canvas.tag_raise(item)
        return item
    # Wide polylines are expensive to rasterize with Windows GDI, particularly
    # for two dense/noisy channels. Keep native one-pixel strokes and reuse them.
    return int(canvas.create_line(*points, fill=color, width=1, tags=(tag, group)))


def draw_spectrum_line(
    canvas: tk.Canvas,
    values: np.ndarray[Any, Any],
    color: str,
    width: int,
    height: int,
    x_offset: int,
) -> int | None:
    if len(values) < 2:
        return None
    indices = np.linspace(0, len(values) - 1, min(len(values), MAX_POINTS), dtype=np.intp)
    points = np.empty((len(indices), 2), dtype=np.float64)
    points[:, 0] = x_offset + indices * (width / (len(values) - 1))
    points[:, 1] = (1.0 - values[indices]) * height
    return update_trace_line(
        canvas, points.ravel().tolist(), color, "spectrum_trace", "spectrum_trace"
    )


if __name__ == "__main__":
    app = ReceiverApp()
    app.mainloop()
