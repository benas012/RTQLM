from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

import numpy as np

from apps.native_receiver.constants import (
    COLORS,
    CONNECTION_FAILED_SPECTRUM_NOTICE,
    CONNECTION_OVERLAY_BUSY_LABELS,
    DIMENSIONS,
    SPECTRUM_MIN_INTERVAL_MS,
)
from apps.native_receiver.helpers import (
    compute_spectrum,
    draw_spectrum_line,
    format_frequency,
    format_readout,
    time_monotonic_ns,
)
from apps.native_receiver.nullifier_ui import NullifierMonitorPanel


class SpectrumUiMixin:
    def _build_spectrum_panel(self, main: ttk.Frame) -> None:
        self.analysis_notebook = ttk.Notebook(main, style="Analysis.TNotebook", height=290)
        self.analysis_notebook.grid(row=8, column=0, sticky="nsew")
        spectrum_tab = ttk.Frame(self.analysis_notebook, style="Surface.TFrame")
        spectrum_tab.columnconfigure(0, weight=1)
        spectrum_tab.rowconfigure(1, weight=1)
        self.analysis_notebook.add(spectrum_tab, text="Spectrum analyzer")
        self.nullifier_panel = NullifierMonitorPanel(self.analysis_notebook, self)
        self.analysis_notebook.add(self.nullifier_panel, text="Mode analysis")
        self.analysis_notebook.select(self.nullifier_panel)
        self.analysis_notebook.bind("<<NotebookTabChanged>>", self._analysis_tab_changed)
        spectrum_controls = ttk.Frame(spectrum_tab, style="Toolbar.TFrame", padding=(12, 8))
        spectrum_controls.grid(row=0, column=0, sticky="ew")
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
            width=36,
            anchor="e",
        ).grid(row=0, column=6, sticky="e")
        channel_box.bind("<<ComboboxSelected>>", lambda _event: self._redraw_latest_spectrum())
        fft_box.bind("<<ComboboxSelected>>", lambda _event: self._redraw_latest_spectrum())
        scale_box.bind("<<ComboboxSelected>>", lambda _event: self._redraw_latest_spectrum())

        self.spectrum_canvas = tk.Canvas(
            spectrum_tab,
            height=140,
            bg=COLORS["bg_deep"],
            highlightthickness=1,
            highlightbackground=COLORS["grid"],
            relief="flat",
        )
        self.spectrum_canvas.grid(row=1, column=0, sticky="nsew")
        self.spectrum_canvas.bind("<Configure>", self._resize_spectrum_plot)
        self._set_spectrum_notice("Waiting for spectrum data")

    def _analysis_tab_changed(self, _event: tk.Event | None) -> None:
        nullifiers_visible = self.analysis_notebook.select() == str(self.nullifier_panel)
        self.plot_split.paneconfigure(
            self.analysis_surface, minsize=410 if nullifiers_visible else 240
        )
        self.minsize(
            DIMENSIONS["min_width"], 740 if nullifiers_visible else DIMENSIONS["min_height"]
        )

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
            f"Peak: {format_frequency(float(spectrum['peak_hz'])):>11} / "
            f"{format_readout(float(spectrum['peak_db']), width=7, decimals=1, signed=True)} dBFS"
        )

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
