from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from apps.native_receiver.constants import (
    COLORS,
    DIMENSIONS,
)
from apps.native_receiver.nullifier_ui import NullifierMonitorPanel


class SpectrumUiMixin:
    def _build_spectrum_panel(self, main: ttk.Frame) -> None:
        self.analysis_notebook = ttk.Notebook(main, style="Analysis.TNotebook", height=290)
        self.analysis_notebook.grid(row=8, column=0, sticky="nsew", padx=12, pady=(0, 12))
        spectrum_tab = ttk.Frame(self.analysis_notebook, style="Surface.TFrame")
        spectrum_tab.columnconfigure(0, weight=1)
        spectrum_tab.rowconfigure(1, weight=1)
        self.analysis_notebook.add(spectrum_tab, text="Spectrum analyzer")
        self.nullifier_panel = NullifierMonitorPanel(self.analysis_notebook, self)
        self.analysis_notebook.add(self.nullifier_panel, text="Nullifiers")
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

    def _analysis_tab_changed(self, _event: tk.Event) -> None:
        nullifiers_visible = self.analysis_notebook.select() == str(self.nullifier_panel)
        self.main_surface.rowconfigure(5, weight=5 if nullifiers_visible else 7)
        self.main_surface.rowconfigure(
            8, weight=5 if nullifiers_visible else 3, minsize=330 if nullifiers_visible else 0
        )
        self.minsize(
            DIMENSIONS["min_width"], 700 if nullifiers_visible else DIMENSIONS["min_height"]
        )
