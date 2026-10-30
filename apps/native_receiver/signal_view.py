"""Waveform selection, history, triggers, and display controls."""
from __future__ import annotations

import tkinter as tk
from typing import Any

import numpy as np

from apps.native_receiver.constants import (
    ADC_COUNTS_PER_VOLT,
    DEFAULT_SIGNAL_RESOLUTION,
    HISTORY_MAX_SAMPLES,
    SIGNAL_RESOLUTION_POINTS,
    TIME_SPANS_MS,
    TIME_ZOOM_MIN,
    TRIGGER_HYSTERESIS_VOLTS,
    TRIGGER_PRE_SAMPLES_FRACTION,
    UI_FRAME_INTERVAL_MS,
)
from apps.native_receiver.helpers import (
    adc_counts_to_volts,
    display_interval_ms,
    find_trigger_crossing,
    format_voltage,
    max_sample_abs,
    set_if_changed,
)
from apps.native_receiver.protocol import SampleFrame, SampleHistory


class SignalViewMixin:
    def _redraw_latest_signal(self) -> None:
        frame = self._view_frame()
        if frame is None:
            self._redraw_grid()
            return
        try:
            channel_1, channel_2, rate, count = self._channels_for_signal(frame)
            self._draw_frame(
                adc_counts_to_volts(channel_1),
                adc_counts_to_volts(channel_2),
                rate,
                count,
            )
        except Exception as exc:
            self.signal_processor = None
            self.script_status.set(f"signal script disabled: {exc}")
            self.status.set("signal script error")

    def _frame_for_signal(self, frame: SampleFrame) -> SampleFrame:
        if self.time_zoom >= 1.0:
            return frame
        with self.frame_lock:
            history = self._view_history().snapshot(int(frame.samples_per_channel / self.time_zoom))
        return history or frame

    def _channels_for_signal(self, frame: SampleFrame) -> tuple[Any, Any, float, int]:
        self.signal_padding_samples = 0
        self.trigger_window_precomputed = False
        self.signal_sample_positions = None
        self.precomputed_trigger_index: float | None = None
        requested = min(HISTORY_MAX_SAMPLES, int(frame.samples_per_channel / self.time_zoom))
        self.signal_window_samples = requested
        trigger_variable = self.__dict__.get("trigger_enabled")
        trigger_active = trigger_variable is not None and trigger_variable.get()
        if trigger_active and not self._signal_script_active():
            search_samples = min(
                HISTORY_MAX_SAMPLES,
                requested + frame.samples_per_channel,
            )
            trigger_level_variable = self.__dict__.get("trigger_level")
            trigger_channel_variable = self.__dict__.get("trigger_channel")
            trigger_edge_variable = self.__dict__.get("trigger_edge")
            trigger_level = float("nan")
            hysteresis = self._trigger_hysteresis_volts()
            if trigger_level_variable is not None:
                try:
                    trigger_level = float(trigger_level_variable.get().strip())
                except ValueError:
                    pass
            with self.frame_lock:
                history = self._view_history()
                capture = (
                    history.triggered_plot_channels(
                        search_samples,
                        requested,
                        1 if trigger_channel_variable.get() == "CH1" else 2,
                        trigger_level * ADC_COUNTS_PER_VOLT,
                        trigger_edge_variable.get(),
                        TRIGGER_PRE_SAMPLES_FRACTION,
                        self._signal_plot_points(),
                        hysteresis * ADC_COUNTS_PER_VOLT,
                    )
                    if np.isfinite(trigger_level)
                    and np.isfinite(hysteresis) and hysteresis >= 0
                    and trigger_channel_variable is not None
                    and trigger_edge_variable is not None
                    else None
                )
                fallback_channels = (
                    None
                    if capture is not None
                    else history.plot_channels(search_samples, self._signal_plot_points())
                )
            if capture is not None:
                self.trigger_window_precomputed = True
                self.precomputed_trigger_index = capture.trigger_index
                self.signal_sample_positions = capture.sample_positions
                if capture.trigger_index is None:
                    edge_symbol = "↑" if self.trigger_edge.get() == "Rising" else "↓"
                    set_if_changed(
                        self.trigger_status,
                        f"Armed {self.trigger_channel.get()} {edge_symbol} · waiting",
                    )
                return (
                    capture.channel_1,
                    capture.channel_2,
                    capture.sample_rate_hz,
                    capture.samples_per_channel,
                )
            if fallback_channels is not None and fallback_channels[3] >= requested:
                return fallback_channels
        if self.time_zoom < 1.0 and not self._signal_script_active():
            with self.frame_lock:
                channels = self._view_history().plot_channels(requested, self._signal_plot_points())
            if channels is not None:
                first, second, rate, available = channels
                self.signal_padding_samples = requested - available
                return first, second, rate, requested
        frame = self._frame_for_signal(frame)
        first, second = self._processed_channels(frame)
        if self.time_zoom < 1.0:
            self.signal_padding_samples = max(0, requested - frame.samples_per_channel)
            return first, second, frame.sample_rate_hz, requested
        return first, second, frame.sample_rate_hz, frame.samples_per_channel

    def _trigger_hysteresis_volts(self) -> float:
        variable = self.__dict__.get("trigger_hysteresis")
        if variable is None:
            return TRIGGER_HYSTERESIS_VOLTS
        try:
            return float(variable.get().strip())
        except ValueError:
            return float("nan")

    def _resize_signal_plot(self, _event: tk.Event[Any]) -> None:
        if self.signal_resize_after_id is not None:
            try:
                self.after_cancel(self.signal_resize_after_id)
            except tk.TclError:
                pass
        self.signal_resize_after_id = self.after_idle(self._finish_signal_resize)

    def _finish_signal_resize(self) -> None:
        self.signal_resize_after_id = None
        self._redraw_grid()
        self._redraw_latest_signal()



    def _window_bounds(self, sample_count: int) -> tuple[int, int]:
        if sample_count <= 0:
            return 0, 0
        visible = max(int(sample_count / self.time_zoom), 2)
        visible = min(visible, sample_count)
        span = sample_count - visible
        start = round(span * self.time_pan) if span > 0 else 0
        return start, start + visible

    def _window_channel(self, samples: Any) -> Any:
        if samples is None or len(samples) < 2:
            return samples
        start, end = self._window_bounds(len(samples))
        return samples[start:end]

    def _window_channel_for_bounds(
        self,
        samples: Any,
        start_index: int,
        end_index: int,
        total_samples: int,
    ) -> Any:
        """Slice raw or display-decimated data using capture-relative bounds."""
        if samples is None or len(samples) < 2:
            return samples
        padding = min(max(self.signal_padding_samples, 0), total_samples)
        data_span = max(total_samples - padding, 0)
        overlap_start = max(start_index, padding)
        overlap_end = min(end_index, total_samples)
        if data_span <= 0 or overlap_end <= overlap_start:
            return samples[0:0]
        if len(samples) == data_span:
            return samples[overlap_start - padding : overlap_end - padding]
        source_start = int((overlap_start - padding) * len(samples) / data_span)
        source_end = int(np.ceil((overlap_end - padding) * len(samples) / data_span))
        source_start = min(max(source_start, 0), len(samples) - 1)
        source_end = min(max(source_end, source_start + 1), len(samples))
        return samples[source_start:source_end]

    def _trigger_window_bounds(
        self,
        channel_1: Any,
        channel_2: Any,
        sample_count: int,
    ) -> tuple[int, int, int | None]:
        nominal_start, nominal_end = self._window_bounds(sample_count)
        if not self.trigger_enabled.get():
            return nominal_start, nominal_end, None
        try:
            level = float(self.trigger_level.get().strip())
        except ValueError:
            set_if_changed(self.trigger_status, "Enter a numeric voltage")
            return nominal_start, nominal_end, None
        if not np.isfinite(level):
            set_if_changed(self.trigger_status, "Enter a finite voltage")
            return nominal_start, nominal_end, None
        hysteresis = self._trigger_hysteresis_volts()
        if not np.isfinite(hysteresis) or hysteresis < 0:
            set_if_changed(self.trigger_status, "Noise guard must be finite and nonnegative")
            return nominal_start, nominal_end, None

        source = channel_1 if self.trigger_channel.get() == "CH1" else channel_2
        if source is None or len(source) < 2:
            set_if_changed(self.trigger_status, f"Waiting for {self.trigger_channel.get()} data")
            return nominal_start, nominal_end, None

        configured_visible = self.__dict__.get("signal_window_samples", 0)
        visible = min(
            max(int(configured_visible), 2) if configured_visible else nominal_end - nominal_start,
            sample_count,
        )
        pre_trigger_samples = round((visible - 1) * TRIGGER_PRE_SAMPLES_FRACTION)
        post_trigger_samples = visible - 1 - pre_trigger_samples
        desired_logical_index = sample_count - 1 - post_trigger_samples
        padding = min(max(self.signal_padding_samples, 0), sample_count)
        data_span = max(sample_count - padding, 1)
        desired_source_index = round(
            (desired_logical_index - padding) * max(len(source) - 1, 1) / data_span
        )
        minimum_source_index = round(
            (pre_trigger_samples - padding) * max(len(source) - 1, 1) / data_span
        )
        logical_index: int | None = None
        if "sample_history" in self.__dict__ and "frame_lock" in self.__dict__:
            desired_data_index = desired_logical_index - padding
            minimum_data_index = pre_trigger_samples - padding
            if desired_data_index >= 1:
                channel_number = 1 if self.trigger_channel.get() == "CH1" else 2
                with self.frame_lock:
                    raw_index = self._view_history().find_channel_crossing_before(
                        channel_number,
                        data_span,
                        level * ADC_COUNTS_PER_VOLT,
                        self.trigger_edge.get(),
                        target_index=desired_data_index,
                        minimum_index=max(minimum_data_index, 1),
                        hysteresis=hysteresis * ADC_COUNTS_PER_VOLT,
                    )
                if raw_index is not None:
                    logical_index = padding + raw_index
        else:
            source_index = find_trigger_crossing(
                source,
                level,
                self.trigger_edge.get(),
                target_index=desired_source_index,
                minimum_index=max(minimum_source_index, 1),
                maximum_index=desired_source_index,
                hysteresis=hysteresis,
            )
            if source_index is not None:
                logical_index = padding + round(
                    source_index * max(data_span - 1, 0) / max(len(source) - 1, 1)
                )
        if logical_index is None:
            edge_symbol = "↑" if self.trigger_edge.get() == "Rising" else "↓"
            set_if_changed(
                self.trigger_status,
                f"Armed {self.trigger_channel.get()} {edge_symbol} · waiting",
            )
            return nominal_start, nominal_end, None

        max_start = max(sample_count - visible, 0)
        start = logical_index - pre_trigger_samples
        start = min(max(start, 0), max_start)
        return start, start + visible, logical_index

    def _update_time_controls(
        self,
        sample_rate_hz: float | None = None,
        total_samples: int | None = None,
        start_index: int | None = None,
        end_index: int | None = None,
    ) -> None:
        set_if_changed(self.time_zoom_label, f"{self.time_zoom:.3g}x")
        set_if_changed(self.cursor_status, "Cursor center" if self.cursor_enabled else "Cursor off")
        rate = sample_rate_hz or self.axis_sample_rate_hz
        total = total_samples or self.axis_samples_per_channel
        if start_index is None or end_index is None:
            start_index, end_index = self._window_bounds(total)
        visible = max(end_index - start_index, 0)
        if rate > 0 and visible > 0:
            ms_per_div = visible / rate * 1000 / 10
            start_ms = start_index / rate * 1000
            end_ms = end_index / rate * 1000
            set_if_changed(self.timebase, f"{ms_per_div:.3g} ms/div")
            set_if_changed(self.time_window_label, f"{start_ms:.3g}-{end_ms:.3g} ms")
        else:
            set_if_changed(self.timebase, "- ms/div")
            set_if_changed(self.time_window_label, "Full capture")

    def toggle_display_channel(self, channel: int) -> None:
        variable = self.channel_1_visible if channel == 1 else self.channel_2_visible
        variable.set(not variable.get())
        visible = variable.get()
        acquired = True
        if self.selected_device is not None:
            acquired = (
                self.selected_device.channel_1_enabled
                if channel == 1
                else self.selected_device.channel_2_enabled
            )
        if visible and not acquired:
            self.status.set(f"CH{channel} display on, acquisition is off")
        else:
            self.status.set(f"CH{channel} {'shown' if visible else 'hidden'} in display")
        self._ui_log("display channel toggled", channel=channel, visible=visible)
        self._redraw_latest_signal()

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
        hysteresis = self._trigger_hysteresis_volts()
        if not np.isfinite(hysteresis) or hysteresis < 0:
            set_if_changed(self.trigger_status, "Noise guard must be finite and nonnegative")
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
            hysteresis_volts=hysteresis,
        )
        self._redraw_latest_signal()
