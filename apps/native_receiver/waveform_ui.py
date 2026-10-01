from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from apps.native_receiver.constants import (
    COLORS,
    SIGNAL_RESOLUTION_POINTS,
    TIME_SPANS_MS,
)


class WaveformUiMixin:
    def _build_waveform_panel(self, main: ttk.Frame) -> None:
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
        self._build_time_vertical_controls()
        self._build_channel_display_controls()
        self._build_trigger_controls()

    def _build_time_vertical_controls(self) -> None:
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

    def _build_channel_display_controls(self) -> None:
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

    def _build_trigger_controls(self) -> None:
        self.trigger_control_group = ttk.Frame(self.signal_controls, style="ControlGroup.TFrame")
        ttk.Label(self.trigger_control_group, text="TRIGGER", style="GroupLabel.TLabel").pack(
            side="left", padx=(0, 8)
        )
        ttk.Checkbutton(
            self.trigger_control_group,
            text="On",
            variable=self.trigger_enabled,
            command=self.apply_trigger_settings,
            style="Toolbar.TCheckbutton",
        ).pack(side="left", padx=(0, 7))
        trigger_channel_box = ttk.Combobox(
            self.trigger_control_group,
            textvariable=self.trigger_channel,
            values=("CH1", "CH2"),
            state="readonly",
            width=5,
        )
        trigger_channel_box.pack(side="left", padx=(0, 6))
        trigger_edge_box = ttk.Combobox(
            self.trigger_control_group,
            textvariable=self.trigger_edge,
            values=("Rising", "Falling"),
            state="readonly",
            width=8,
        )
        trigger_edge_box.pack(side="left", padx=(0, 6))
        ttk.Label(self.trigger_control_group, text="Level", style="Toolbar.TLabel").pack(
            side="left"
        )
        trigger_level_entry = ttk.Entry(
            self.trigger_control_group, textvariable=self.trigger_level, width=8
        )
        trigger_level_entry.pack(side="left", padx=(5, 3))
        ttk.Label(self.trigger_control_group, text="V", style="Toolbar.TLabel").pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(
            self.trigger_control_group,
            text="Apply",
            style="Compact.TButton",
            command=self.apply_trigger_settings,
        ).pack(side="left", padx=(0, 8))
        self.test_output_button = ttk.Button(
            self.trigger_control_group,
            textvariable=self.test_output_label,
            style="Compact.TButton",
            command=self.toggle_test_output,
        )
        self.test_output_button.pack(side="left", padx=(0, 8))
        ttk.Label(
            self.trigger_control_group,
            textvariable=self.trigger_status,
            style="Toolbar.TLabel",
            width=27,
        ).pack(side="left")
        trigger_channel_box.bind(
            "<<ComboboxSelected>>", lambda _event: self.apply_trigger_settings()
        )
        trigger_edge_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_trigger_settings())
        trigger_level_entry.bind("<Return>", lambda _event: self.apply_trigger_settings())
        trigger_level_entry.bind("<FocusOut>", lambda _event: self.apply_trigger_settings())
        self.waveform_more_button = ttk.Button(
            self.signal_controls,
            text="Vertical & channel controls ▾",
            style="Compact.TButton",
            command=self._toggle_waveform_advanced,
        )
