from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from apps.native_receiver.constants import (
    COLORS,
    FONTS,
)
from apps.native_receiver.dialogs import (
    ToolTip,
)


class SidebarUiMixin:
    def _build_sidebar(self) -> None:
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
