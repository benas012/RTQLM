from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from apps.native_receiver.constants import (
    COLORS,
    CONNECTION_OVERLAY_BG,
    CONNECTION_OVERLAY_OPACITY,
)


class ConnectionOverlayMixin:
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
