from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from apps.native_receiver.constants import (
    COLORS,
)


class WorkspaceUiMixin:
    def _build_workspace(self) -> None:
        main = ttk.Frame(self, style="Surface.TFrame")
        self.main_surface = main
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(5, weight=7)
        main.rowconfigure(8, weight=3)
        self._build_workspace_header(main)
        self._build_waveform_panel(main)
        self._build_spectrum_panel(main)
        main.bind("<Configure>", self._on_main_configure, add="+")
        self._build_connection_overlay(main)
        self._draw_tabs()
        self._update_time_controls()
        self._update_connect_controls()
        self._layout_waveform_controls(900)

    def _build_workspace_header(self, main: ttk.Frame) -> None:
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
