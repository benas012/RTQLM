"""Window layout, menus, sidebar, and workspace construction."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from apps.native_receiver.constants import (
    COLORS,
    FONTS,
)
from apps.native_receiver.dialogs import (
    ToolTip,
)


class LayoutMixin:
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
        signal_menu.add_command(
            label="Red Pitaya output signals…", command=self.open_output_signals
        )
        signal_menu.add_command(label="1D cluster nullifiers…", command=self.open_cluster_monitor)
        signal_menu.add_command(label="GPIO (E1) input…", command=self.open_gpio_input)
        menu.add_cascade(label="Signal", menu=signal_menu)

        view_menu = tk.Menu(menu, tearoff=False)
        view_menu.add_command(label="Redraw plots", command=self._redraw_all_grids)
        menu.add_cascade(label="View", menu=view_menu)

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
        layout_key = width >= 1040
        if layout_key == self._last_control_layout:
            return
        self._last_control_layout = layout_key
        self.display_control_group.grid(row=0, column=0, columnspan=2, sticky="w")
        groups = (self.time_control_group, self.vertical_control_group,
                  self.channel_control_group, self.trigger_control_group)
        for row, group in enumerate(groups, start=2):
            group.grid(row=row, column=0, sticky="w", pady=(14, 0))
        show_spectrum_advanced = width >= 1040
        for widget in (
            self.advanced_tools_label,
            self.python_signal_button,
            self.cluster_tools_button,
        ):
            if show_spectrum_advanced:
                widget.grid()
            else:
                widget.grid_remove()

    def _toggle_waveform_controls(self) -> None:
        if getattr(self, "waveform_controls_expanded", False):
            self._hide_waveform_controls()
            return
        self.waveform_controls_expanded = True
        self.display_settings_window.update_idletasks()
        x = max(0, min(self.winfo_rootx() + 40,
                       self.winfo_screenwidth() - self.display_settings_window.winfo_reqwidth()))
        y = max(0, min(self.winfo_rooty() + 140,
                       self.winfo_screenheight() - self.display_settings_window.winfo_reqheight()))
        self.display_settings_window.geometry(f"+{x}+{y}")
        self.display_settings_window.deiconify()
        self.display_settings_window.lift()

    def _hide_waveform_controls(self) -> None:
        self.waveform_controls_expanded = False
        self.display_settings_window.withdraw()

    def _constrain_plot_split(self) -> None:
        """Keep both plots usable when the containing window becomes smaller."""
        if not self.plot_split.winfo_exists():
            return
        minimum = int(self.plot_split.panecget(self.analysis_surface, "minsize"))
        top = self.plot_split.sash_coord(0)[1]
        maximum = self.plot_split.winfo_height() - minimum - 7
        target = min(max(top, 166), max(maximum, 166))
        if top != target:
            self.plot_split.sash_place(0, 0, target)

    def _on_plot_split_configure(self, _event: Any) -> None:
        self.after_idle(self._constrain_plot_split)

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

    def _build_sidebar(self) -> None:
        sidebar = ttk.Frame(self, style="Sidebar.TFrame", padding=(16, 20, 16, 16))
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
            text="↻",
            width=3,
            style="Compact.TButton",
            command=self.refresh_devices,
        ).grid(row=0, column=1, padx=(4, 0))
        ttk.Button(
            device_heading,
            text="+",
            width=3,
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
            displaycolumns=("state",),
            selectmode="browse",
            style="Device.Treeview",
            height=2,
        )
        self.device_list.heading("#0", text="Device", anchor="w")
        self.device_list.heading("state", text="State", anchor="w")
        self.device_list.heading("channels", text="Inputs", anchor="w")
        self.device_list.column("#0", width=148, minwidth=110, stretch=True)
        self.device_list.column("state", width=88, minwidth=80, stretch=False)
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
        self.connect_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ToolTip(
            self.connect_button,
            "Select a Red Pitaya first. A streaming board attaches directly to the live display.",
        )
        self.stop_stream_button = ttk.Button(
            side_actions, text="Stop streaming", command=self.stop_streaming,
            state=tk.DISABLED,
        )
        self.stop_stream_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ToolTip(self.stop_stream_button,
                "Stop acquisition on the Red Pitaya and keep the last trace on screen.")
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
        self.forget_button.grid_remove()

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

    def _build_workspace(self) -> None:
        main = ttk.Frame(self, style="Surface.TFrame")
        self.main_surface = main
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)
        self._build_workspace_header(main)
        self.plot_split = tk.PanedWindow(main, orient=tk.VERTICAL, bg=COLORS["bg"],
                                        sashwidth=7, sashrelief="flat", bd=0,
                                        opaqueresize=True)
        self.plot_split.grid(row=4, column=0, sticky="nsew", padx=16, pady=(4, 14))
        self.waveform_surface = ttk.Frame(self.plot_split, style="Surface.TFrame")
        self.waveform_surface.columnconfigure(0, weight=1)
        self.waveform_surface.rowconfigure(5, weight=1)
        self.analysis_surface = ttk.Frame(self.plot_split, style="Surface.TFrame")
        self.analysis_surface.columnconfigure(0, weight=1)
        self.analysis_surface.rowconfigure(8, weight=1)
        self.plot_split.add(self.waveform_surface, minsize=166, stretch="always")
        self.plot_split.add(self.analysis_surface, minsize=410, stretch="always")
        self._build_waveform_panel(self.waveform_surface)
        self._build_spectrum_panel(self.analysis_surface)
        self.plot_split.bind("<Configure>", self._on_plot_split_configure)
        main.bind("<Configure>", self._on_main_configure, add="+")
        self._build_connection_overlay(main)
        self._draw_tabs()
        self._update_time_controls()
        self._update_connect_controls()
        self._layout_waveform_controls(900)

    def _build_workspace_header(self, main: ttk.Frame) -> None:
        workspace_header = ttk.Frame(main, style="Surface.TFrame", padding=(18, 18, 18, 8))
        workspace_header.grid(row=0, column=0, sticky="ew")
        workspace_header.columnconfigure(1, weight=1)
        ttk.Label(workspace_header, text="Signal workspace", style="Title.TLabel").grid(
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
            height=30,
            bg=COLORS["surface"],
            highlightthickness=0,
            bd=0,
        )
        self.tab_bar.grid(row=1, column=0, sticky="ew", padx=18)
        self.tab_bar.bind("<Configure>", lambda _event: self._draw_tabs())
        self.tab_bar.bind("<Button-1>", self._select_tab_at)

        status_strip = ttk.Frame(main, style="Status.TFrame", padding=(18, 8))
        status_strip.grid(row=2, column=0, sticky="ew")
        for column in range(5):
            status_strip.columnconfigure(column, weight=1)
        self._compact_stat(status_strip, "ACQUISITION", self.acquisition_state, 0)
        self._compact_stat(status_strip, "SAMPLE RATE", self.sample_rate, 1)
        self._compact_stat(status_strip, "LOST SAMPLES · CH1 / CH2", self.lost_pair, 2)
        ttk.Button(status_strip, text="GPIO (E1)…", style="Compact.TButton",
                   command=self.open_gpio_input).grid(row=0, column=3, sticky="e")
        self.diagnostics_button = ttk.Button(
            status_strip,
            text="Diagnostics ▾",
            style="Compact.TButton",
            command=self._toggle_diagnostics,
        )
        self.diagnostics_button.grid(row=0, column=4, rowspan=2, sticky="e", padx=(12, 0))
        self.diagnostics_panel = ttk.Frame(main, style="Diagnostics.TFrame", padding=(14, 6))
        for column in range(4):
            self.diagnostics_panel.columnconfigure(column, weight=1)
        self._compact_stat(self.diagnostics_panel, "Rendered frames", self.frames, 0, surface="bg")
        self._compact_stat(self.diagnostics_panel, "Sequence", self.sequence, 1, surface="bg")
        self._compact_stat(
            self.diagnostics_panel, "Samples / channel", self.samples, 2, surface="bg"
        )
        self._compact_stat(self.diagnostics_panel, "Protocol flags", self.flags, 3, surface="bg")
        ttk.Label(self.diagnostics_panel, textvariable=self.status, style="Muted.TLabel",
                   wraplength=800).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(self.diagnostics_panel, textvariable=self.ch1_range,
                   style="Muted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(5, 0)
        )
        ttk.Label(self.diagnostics_panel, textvariable=self.ch2_range,
                   style="Muted.TLabel").grid(
            row=2, column=2, columnspan=2, sticky="w", pady=(5, 0)
        )
