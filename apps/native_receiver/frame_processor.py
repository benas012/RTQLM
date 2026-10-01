from __future__ import annotations

from typing import Any

import numpy as np

from apps.native_receiver.constants import (
    STATS_MIN_INTERVAL_MS,
)
from apps.native_receiver.helpers import (
    adc_counts_to_volts,
    device_slug,
    format_voltage,
    normalize_script_channel,
    sample_range,
    samples_to_numpy,
    set_if_changed,
    time_monotonic_ns,
)
from apps.native_receiver.protocol import SampleFrame


class FrameProcessorMixin:
    def _handle_frame(self, frame: SampleFrame, received_count: int) -> None:
        self.connection_failure_active = False
        self.frame_count = received_count
        if self.__dict__.get("pending_time_span_choice", False):
            self.pending_time_span_choice = not self._apply_time_span_to_frame(
                self.time_span_choice.get(), frame
            )
        now = time_monotonic_ns()
        update_stats = now - self.last_stats_monotonic_ns >= STATS_MIN_INTERVAL_MS * 1_000_000
        if update_stats:
            self.last_stats_monotonic_ns = now
            for variable, value in (
                (self.frames, str(received_count)),
                (self.sequence, str(frame.sequence)),
                (self.sample_rate, f"{frame.sample_rate_hz:.2f} Hz"),
                (self.samples, str(frame.samples_per_channel)),
                (self.mask, f"0x{frame.channel_mask:x}"),
                (self.lost1, str(frame.fpga_lost_ch1)),
                (self.lost2, str(frame.fpga_lost_ch2)),
                (self.lost_pair, f"{frame.fpga_lost_ch1} / {frame.fpga_lost_ch2}"),
                (self.flags, str(frame.flags)),
            ):
                set_if_changed(variable, value)
        if now - self.last_frame_log_monotonic_ns >= 1_000_000_000:
            self.last_frame_log_monotonic_ns = now
            self._ui_log(
                "frame rendered",
                frames=received_count,
                sequence=frame.sequence,
                sample_rate=f"{frame.sample_rate_hz:.2f}",
                samples=frame.samples_per_channel,
                mask=f"0x{frame.channel_mask:x}",
            )

        try:
            channel_1, channel_2, rate, count = self._channels_for_signal(frame)
            channel_1_volts = adc_counts_to_volts(channel_1)
            channel_2_volts = adc_counts_to_volts(channel_2)
            if update_stats:
                # Raw readouts and FFT retain the full latest packet, even when
                # the history plot only copies the points needed on screen.
                range_1 = (
                    adc_counts_to_volts(frame.channel_1)
                    if not self._signal_script_active()
                    else channel_1_volts
                )
                range_2 = (
                    adc_counts_to_volts(frame.channel_2)
                    if not self._signal_script_active()
                    else channel_2_volts
                )
                set_if_changed(
                    self.ch1_range,
                    self._channel_range_label(
                        1,
                        range_1,
                        sample_range(range_1),
                        self.channel_1_visible.get(),
                    ),
                )
                set_if_changed(
                    self.ch2_range,
                    self._channel_range_label(
                        2,
                        range_2,
                        sample_range(range_2),
                        self.channel_2_visible.get(),
                    ),
                )
            self._draw_frame(channel_1_volts, channel_2_volts, rate, count)
            if not self._signal_script_active():
                self._draw_spectrum(frame.channel_1, frame.channel_2, frame.sample_rate_hz)
            else:
                self._draw_spectrum(channel_1, channel_2, rate)
        except Exception as exc:
            self.signal_processor = None
            self.script_status.set(f"signal script disabled: {exc}")
            self.status.set("signal script error")

    def _channel_range_label(
        self,
        channel: int,
        samples: Any,
        value_range: tuple[float, float] | None,
        visible: bool,
    ) -> str:
        if not visible:
            return f"CH{channel} hidden"
        if value_range is not None:
            return f"{format_voltage(value_range[0])} / {format_voltage(value_range[1])}"
        acquired = True
        if self.selected_device is not None:
            acquired = (
                self.selected_device.channel_1_enabled
                if channel == 1
                else self.selected_device.channel_2_enabled
            )
        if not acquired:
            return f"CH{channel} acquisition off"
        if samples is None:
            return f"CH{channel} no data"
        return "-"

    def _processed_channels(self, frame: SampleFrame) -> tuple[Any, Any]:
        if not self._signal_scripting_enabled():
            self.signal_processor = None
            return frame.channel_1, frame.channel_2
        if self.signal_processor is None:
            return frame.channel_1, frame.channel_2

        signals = self._signals_for_frame(frame)
        try:
            result = self.signal_processor(signals, np)
        except Exception as exc:
            self.signal_processor = None
            self.script_status.set(f"signal script disabled: {exc}")
            self.status.set("signal script error")
            return frame.channel_1, frame.channel_2

        if isinstance(result, dict):
            first, second = result.get("ch1"), result.get("ch2")
            return normalize_script_channel(
                first, frame.samples_per_channel
            ), normalize_script_channel(second, frame.samples_per_channel)
        if isinstance(result, tuple | list):
            first = result[0] if len(result) > 0 else None
            second = result[1] if len(result) > 1 else None
            return normalize_script_channel(
                first, frame.samples_per_channel
            ), normalize_script_channel(second, frame.samples_per_channel)
        return normalize_script_channel(result, frame.samples_per_channel), None

    def _redraw_latest_spectrum(self) -> None:
        frame = self._view_frame()
        if frame is None:
            self._redraw_spectrum_grid()
            return
        try:
            if self._signal_script_active():
                frame = self._frame_for_signal(frame)
            channel_1, channel_2 = self._processed_channels(frame)
            self._draw_spectrum(channel_1, channel_2, frame.sample_rate_hz, force=True)
        except Exception as exc:
            self.signal_processor = None
            self.script_status.set(f"signal script disabled: {exc}")
            self.status.set("signal script error")

    def _signals_for_frame(self, frame: SampleFrame) -> dict[str, np.ndarray[Any, Any]]:
        """Return raw ADC counts for optional signal-processing scripts."""
        signals: dict[str, np.ndarray[Any, Any]] = {}
        device = self.selected_device
        pairs = (("ch1", frame.channel_1), ("ch2", frame.channel_2))
        for channel_name, samples in pairs:
            if samples is None:
                continue
            array = samples_to_numpy(samples)
            signals[f"selected.{channel_name}"] = array
            if device is not None:
                signals[f"{device_slug(device)}.{channel_name}"] = array
                signals[f"{device.id}.{channel_name}"] = array
        return signals
