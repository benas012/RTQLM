from __future__ import annotations

import logging
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any

from apps.native_receiver.constants import COLORS, UI_LOGGER

if TYPE_CHECKING:
    from apps.native_receiver.native_receiver import ReceiverApp


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
