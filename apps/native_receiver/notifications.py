"""Loading indicators, connection overlays, notices, and error feedback."""
from __future__ import annotations

import threading
import time
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

from apps.native_receiver.constants import (
    COLORS,
    CONNECTION_FAILED_NOTICE,
    CONNECTION_FAILED_SPECTRUM_NOTICE,
    CONNECTION_OVERLAY_BG,
    CONNECTION_OVERLAY_BUSY_LABELS,
    CONNECTION_OVERLAY_OPACITY,
    FONTS,
    TOAST_FADE_INTERVAL_MS,
    TOAST_FADE_STEPS,
    TOAST_VISIBLE_MS,
)
from apps.native_receiver.helpers import short_error


class NotificationsMixin:
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
            wraplength=240,
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
        message_label.grid(row=1, column=1, columnspan=3, sticky="ew",
                           padx=(10, 8), pady=(2, 8))

        def copy_message() -> None:
            try:
                self.clipboard_clear()
                self.clipboard_append(f"{title}\n{message}")
                copy_button.configure(text="Copied")
            except tk.TclError:
                copy_button.configure(text="Retry")

        copy_button = tk.Button(
            frame, text="Copy", command=copy_message, width=6,
            bg=COLORS["panel_2"], fg=COLORS["text_secondary"],
            activebackground=COLORS["panel_3"], activeforeground=COLORS["text"],
            font=FONTS["small"], relief="flat", bd=0, padx=4, pady=2,
        )
        copy_button.grid(row=0, column=2, sticky="ne", padx=(0, 2), pady=4)
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
        close_button.grid(row=0, column=3, sticky="ne", padx=(0, 4), pady=4)
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

    def _show_error(self, exc: BaseException) -> None:
        self.status.set("error")
        if hasattr(self, "engine_status"):
            self._set_engine_status("error")
        self._ui_log("ui error", error=short_error(exc, 180))
        messagebox.showerror("Error", str(exc))
