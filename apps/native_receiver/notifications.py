from __future__ import annotations

import threading
import time
import tkinter as tk
from collections.abc import Callable
from typing import Any

from apps.native_receiver.constants import (
    COLORS,
    FONTS,
    TOAST_FADE_INTERVAL_MS,
    TOAST_FADE_STEPS,
    TOAST_VISIBLE_MS,
)


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
        message_label.grid(row=1, column=1, sticky="ew", padx=(10, 8), pady=(2, 8))
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
        close_button.grid(row=0, column=2, sticky="ne", padx=(0, 4), pady=4)
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
