from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from apps.native_receiver.constants import (
    COLORS,
    FONTS,
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
        signal_menu.add_command(label="1D cluster nullifiers…", command=self.open_cluster_monitor)
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
            self.trigger_control_group,
        )
        for group in groups:
            group.grid_forget()
        self.waveform_more_button.grid_forget()
        if layout == "wide":
            self.time_control_group.grid(row=0, column=0, sticky="w", padx=(0, 12))
            self.vertical_control_group.grid(row=0, column=1, sticky="e")
            self.channel_control_group.grid(row=1, column=0, sticky="w", pady=(7, 0))
            self.trigger_control_group.grid(row=1, column=1, sticky="e", pady=(7, 0))
            self.display_control_group.grid(row=2, column=0, columnspan=2, sticky="w", pady=(7, 0))
        else:
            self.time_control_group.grid(row=0, column=0, columnspan=2, sticky="w")
            self.display_control_group.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
            self.trigger_control_group.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
            self.waveform_more_button.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
            if expanded:
                self.vertical_control_group.grid(
                    row=4, column=0, columnspan=2, sticky="w", pady=(6, 0)
                )
                self.channel_control_group.grid(
                    row=5, column=0, columnspan=2, sticky="w", pady=(6, 0)
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
