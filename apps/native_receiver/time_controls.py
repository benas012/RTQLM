from __future__ import annotations

from typing import Any

import numpy as np

from apps.native_receiver.constants import (
    DEFAULT_SIGNAL_RESOLUTION,
    HISTORY_MAX_SAMPLES,
    SIGNAL_RESOLUTION_POINTS,
    TIME_SPANS_MS,
    TIME_ZOOM_MIN,
    UI_FRAME_INTERVAL_MS,
)
from apps.native_receiver.helpers import (
    display_interval_ms,
    format_voltage,
    max_sample_abs,
    set_if_changed,
)
from apps.native_receiver.protocol import SampleFrame, SampleHistory


class TimeControlsMixin:
    def zoom_time(self, factor: float) -> None:
        if "time_span_choice" in self.__dict__:
            self.time_span_choice.set("Custom")
        self.pending_time_span_choice = False
        previous_zoom = self.time_zoom
        frame = self._view_frame()
        minimum_zoom = TIME_ZOOM_MIN
        if frame is not None:
            minimum_zoom = max(
                minimum_zoom, min(1.0, frame.samples_per_channel / HISTORY_MAX_SAMPLES)
            )
        self.time_zoom = min(max(self.time_zoom * factor, minimum_zoom), 128.0)
        if self.time_zoom <= 1.0:
            self.time_pan = 0.0
        else:
            previous_visible = min(1.0 / previous_zoom, 1.0)
            next_visible = 1.0 / self.time_zoom
            center = self.time_pan * max(1.0 - previous_visible, 0.0) + previous_visible / 2
            self.time_pan = (center - next_visible / 2) / max(1.0 - next_visible, 1e-9)
            self.time_pan = min(max(self.time_pan, 0.0), 1.0)
        self._update_time_controls()
        self._redraw_latest_signal()

    def _view_frame(self) -> SampleFrame | None:
        if self.__dict__.get("display_paused", False):
            return self.paused_frame
        with self.frame_lock:
            return self.latest_frame

    def _view_history(self) -> SampleHistory:
        if self.__dict__.get("display_paused", False) and self.paused_history is not None:
            return self.paused_history
        return self.sample_history

    def select_time_span(self) -> None:
        choice = self.time_span_choice.get()
        duration = TIME_SPANS_MS.get(choice)
        if duration is None:
            self.pending_time_span_choice = False
            self.fit_time()
            return
        frame = self._view_frame()
        if frame is None:
            self.status.set("Connect to a sample stream before choosing a time span")
            self.pending_time_span_choice = True
            return
        self._apply_time_span_to_frame(choice, frame)
        self.time_span_choice.set(choice)
        self.pending_time_span_choice = False

    def _apply_time_span_to_frame(self, choice: str, frame: SampleFrame) -> bool:
        duration = TIME_SPANS_MS.get(choice)
        if duration is None or frame.sample_rate_hz <= 0:
            return False
        zoom = frame.samples_per_channel / (frame.sample_rate_hz * duration / 1000)
        self.zoom_time(zoom / self.time_zoom)
        self.time_span_choice.set(choice)
        return True

    def _signal_plot_points(self) -> int:
        resolution = self.__dict__.get("signal_resolution")
        choice = resolution.get() if resolution is not None else DEFAULT_SIGNAL_RESOLUTION
        return SIGNAL_RESOLUTION_POINTS.get(
            choice, SIGNAL_RESOLUTION_POINTS[DEFAULT_SIGNAL_RESOLUTION]
        )

    def _display_interval_ms(self) -> int:
        mode = self.__dict__.get("display_mode")
        zoom = self.__dict__.get("time_zoom", 1.0)
        visible_seconds = None
        frame = self._view_frame()
        sample_rate_hz = getattr(frame, "sample_rate_hz", 0)
        samples_per_channel = getattr(frame, "samples_per_channel", 0)
        if sample_rate_hz > 0 and samples_per_channel > 0:
            visible_samples = min(
                HISTORY_MAX_SAMPLES,
                max(2, int(samples_per_channel / max(zoom, TIME_ZOOM_MIN))),
            )
            visible_seconds = visible_samples / sample_rate_hz
        return display_interval_ms(
            mode.get() if mode is not None else "Auto",
            zoom,
            visible_seconds,
        )

    def _update_display_detail(self) -> None:
        if self.display_paused:
            text = "Paused · acquisition continues"
        else:
            interval = self._display_interval_ms()
            text = "Live" if interval <= UI_FRAME_INTERVAL_MS else f"Updates every {interval} ms"
            if self.signal_padding_samples and self.axis_sample_rate_hz > 0:
                missing = self.signal_padding_samples / self.axis_sample_rate_hz
                text += f" · collecting {missing:.1f} s more"
        set_if_changed(self.display_detail, text)

    def toggle_display_pause(self) -> None:
        if self.display_paused:
            self.display_paused = False
            self.paused_frame = None
            self.paused_history = None
            self.pause_label.set("Pause")
            self.last_display_monotonic_ns = 0
            self._redraw_latest_signal()
            self._redraw_latest_spectrum()
        else:
            with self.frame_lock:
                if self.latest_frame is None:
                    return
                self.paused_frame = self.latest_frame
                self.paused_history = self.sample_history.frozen_copy()
            self.display_paused = True
            self.pause_label.set("Resume live")
            self._redraw_latest_signal()
            self._redraw_latest_spectrum()
        if "pause_button" in self.__dict__:
            self.pause_button.configure(
                style="Active.TButton" if self.display_paused else "Compact.TButton"
            )
        self._update_display_detail()

    def _reset_display_pause(self) -> None:
        self.display_paused = False
        self.paused_frame = None
        self.paused_history = None
        self.pause_label.set("Pause")
        if "pause_button" in self.__dict__:
            self.pause_button.configure(style="Compact.TButton")
        self.signal_padding_samples = 0
        self.last_display_monotonic_ns = 0
        self._update_display_detail()

    def pan_time(self, amount: float) -> None:
        if self.time_zoom <= 1.0:
            self.time_pan = 0.0
        else:
            self.time_pan = min(max(self.time_pan + amount, 0.0), 1.0)
        self._update_time_controls()
        self._redraw_latest_signal()

    def fit_time(self) -> None:
        if "time_span_choice" in self.__dict__:
            self.time_span_choice.set("One batch")
        self.pending_time_span_choice = False
        self.time_zoom = 1.0
        self.time_pan = 0.0
        self._update_time_controls()
        self._redraw_latest_signal()

    def zoom_y_axis(self, factor: float) -> None:
        self.axis_y_abs = min(max(self.axis_y_abs * factor, 1e-9), 1e12)
        self.y_axis_fit_pending = False
        self.y_axis_dirty = True
        self._update_y_axis_range_label()
        self.status.set(f"vertical range set to ±{format_voltage(self.axis_y_abs)}")
        self._ui_log("signal vertical range changed", range_abs=self.axis_y_abs)
        self._redraw_latest_signal()

    def fit_y_axis(self) -> None:
        self.y_axis_fit_pending = True
        self._redraw_latest_signal()

    def _update_y_axis_range_label(self) -> None:
        self.y_axis_range_label.set(f"Y range ±{format_voltage(self.axis_y_abs)}")

    def _fit_y_axis_to_channels(self, channel_1: Any, channel_2: Any) -> None:
        """Fit the selected traces with a small margin, only when explicitly requested."""
        max_abs = max_sample_abs(channel_1, channel_2)
        largest_offset = max(
            abs(self.channel_1_offset_divisions.get()) if channel_1 is not None else 0.0,
            abs(self.channel_2_offset_divisions.get()) if channel_2 is not None else 0.0,
        )
        # Eight grid divisions span the complete ± range.  Preserve 10% breathing
        # room after accounting for a channel's user-selected vertical offset.
        usable_half_range = max(0.9 - largest_offset / 4, 0.05)
        self.axis_y_abs = max(max_abs / usable_half_range, 1e-9)
        self.y_axis_fit_pending = False
        self.y_axis_dirty = True
        self._update_y_axis_range_label()
        self.status.set(f"vertical range fitted to ±{format_voltage(self.axis_y_abs)}")
        self._ui_log("signal vertical range fitted", range_abs=self.axis_y_abs)

    def toggle_cursor(self) -> None:
        self.cursor_enabled = not self.cursor_enabled
        if "cursor_button" in self.__dict__:
            self.cursor_button.configure(
                text="Cursor on" if self.cursor_enabled else "Cursor",
                style="Active.TButton" if self.cursor_enabled else "Compact.TButton",
            )
        self._update_time_controls()
        self._redraw_latest_signal()

    def apply_trigger_settings(self) -> None:
        self.trigger_capture_valid = False
        if not self.trigger_enabled.get():
            set_if_changed(self.trigger_status, "Trigger off")
            self.status.set("voltage trigger disabled")
            self._redraw_latest_signal()
            return
        try:
            level = float(self.trigger_level.get().strip())
        except ValueError:
            set_if_changed(self.trigger_status, "Enter a numeric voltage")
            return
        if not np.isfinite(level):
            set_if_changed(self.trigger_status, "Enter a finite voltage")
            return
        edge_symbol = "↑" if self.trigger_edge.get() == "Rising" else "↓"
        set_if_changed(
            self.trigger_status,
            f"Armed {self.trigger_channel.get()} {edge_symbol} {format_voltage(level)}",
        )
        self.status.set("voltage trigger armed")
        self._ui_log(
            "voltage trigger changed",
            channel=self.trigger_channel.get(),
            edge=self.trigger_edge.get().lower(),
            level_volts=level,
        )
        self._redraw_latest_signal()
