from __future__ import annotations

import tkinter as tk
from typing import Any

import numpy as np

from apps.native_receiver.constants import (
    ADC_COUNTS_PER_VOLT,
    HISTORY_MAX_SAMPLES,
    TRIGGER_HYSTERESIS_VOLTS,
    TRIGGER_PRE_SAMPLES_FRACTION,
)
from apps.native_receiver.helpers import (
    adc_counts_to_volts,
    find_trigger_crossing,
    set_if_changed,
)
from apps.native_receiver.protocol import SampleFrame


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
                        TRIGGER_HYSTERESIS_VOLTS * ADC_COUNTS_PER_VOLT,
                    )
                    if np.isfinite(trigger_level)
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

    def _resize_spectrum_plot(self, _event: tk.Event[Any]) -> None:
        if self.spectrum_resize_after_id is not None:
            try:
                self.after_cancel(self.spectrum_resize_after_id)
            except tk.TclError:
                pass
        self.spectrum_resize_after_id = self.after_idle(self._finish_spectrum_resize)

    def _finish_spectrum_resize(self) -> None:
        self.spectrum_resize_after_id = None
        self._redraw_spectrum_grid()
        self._redraw_latest_spectrum()

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
