from __future__ import annotations

from typing import Any

from apps.native_receiver.constants import (
    COLORS,
    CONNECTION_FAILED_NOTICE,
    CONNECTION_OVERLAY_BUSY_LABELS,
    FONTS,
    SIGNAL_PLOT_TOP_PAD,
)
from apps.native_receiver.helpers import (
    draw_channel,
    format_duration,
    format_voltage,
    set_if_changed,
    update_trace_line,
)

TRIGGER_LEVEL_TAG = "trigger_level"
TRIGGER_VERTICAL_TAG = "trigger_vertical"
TRIGGER_OVERLAY_TAG = "signal_overlay"


class WaveformRendererMixin:
    def _redraw_grid(self) -> None:
        for item in self.grid_items:
            self.canvas.delete(item)
        self.grid_items.clear()
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        left_pad = 56
        bottom_pad = 26
        plot_width = max(width - left_pad - 8, 1)
        plot_height = max(height - SIGNAL_PLOT_TOP_PAD - bottom_pad - 8, 1)
        plot_bottom = SIGNAL_PLOT_TOP_PAD + plot_height
        for index in range(11):
            x = left_pad + index * plot_width / 10
            self.grid_items.append(
                self.canvas.create_line(
                    x, SIGNAL_PLOT_TOP_PAD, x, plot_bottom, fill=COLORS["plot_grid"]
                )
            )
        for index in range(9):
            y = SIGNAL_PLOT_TOP_PAD + index * plot_height / 8
            self.grid_items.append(
                self.canvas.create_line(left_pad, y, width, y, fill=COLORS["plot_grid"])
            )
        self.grid_items.append(
            self.canvas.create_line(
                left_pad,
                SIGNAL_PLOT_TOP_PAD + plot_height / 2,
                width,
                SIGNAL_PLOT_TOP_PAD + plot_height / 2,
                fill=COLORS["plot_axis"],
            )
        )
        self._draw_axis_labels(left_pad, plot_width, plot_height, SIGNAL_PLOT_TOP_PAD)
        self.grid_items.append(
            self.canvas.create_text(left_pad + 12, 16, text="CH1", fill=COLORS["ch1"], anchor="w")
        )
        self.grid_items.append(
            self.canvas.create_text(left_pad + 56, 16, text="CH2", fill=COLORS["ch2"], anchor="w")
        )
        with self.frame_lock:
            has_frame = self.latest_frame is not None
        if self.loading_active and self.loading_label not in CONNECTION_OVERLAY_BUSY_LABELS:
            self._set_signal_notice(self.loading_label)
        elif self.connection_failure_active:
            self._set_signal_notice(CONNECTION_FAILED_NOTICE)
        elif not has_frame:
            self._set_signal_notice("No live signal\nSelect a Red Pitaya and connect to begin")

    def _redraw_all_grids(self) -> None:
        self._redraw_grid()
        self._redraw_spectrum_grid()

    def _draw_axis_labels(
        self, left_pad: int, plot_width: int, plot_height: int, plot_top: int
    ) -> None:
        y_values = (
            self.axis_y_abs,
            self.axis_y_abs / 2,
            0.0,
            -self.axis_y_abs / 2,
            -self.axis_y_abs,
        )
        for index, value in enumerate(y_values):
            y = plot_top + index * plot_height / 4
            self.grid_items.append(
                self.canvas.create_text(
                    left_pad - 8,
                    y,
                    text=format_voltage(value),
                    fill=COLORS["muted"],
                    anchor="e",
                    font=("Consolas", 8),
                )
            )

        duration_ms = 0.0
        if self.axis_sample_rate_hz > 0 and self.axis_samples_per_channel > 0:
            duration_ms = self.axis_samples_per_channel / self.axis_sample_rate_hz * 1000
        # Keep tick labels easy to scan.  Microseconds retain useful precision for
        # short captures without showing fractional values on the horizontal axis.
        time_factor = 1.0 if duration_ms >= 5.0 else 1000.0
        time_unit = "ms" if duration_ms >= 5.0 else "µs"
        for index in range(6):
            x = left_pad + index * plot_width / 5
            time_ms = index * duration_ms / 5
            if self.axis_trigger_offset_samples is not None and self.axis_sample_rate_hz > 0:
                time_ms -= self.axis_trigger_offset_samples / self.axis_sample_rate_hz * 1000
            time_value = round(time_ms * time_factor)
            self.grid_items.append(
                self.canvas.create_text(
                    x,
                    plot_top + plot_height + 15,
                    text=f"{time_value} {time_unit}",
                    fill=COLORS["muted"],
                    anchor="n",
                    font=("Consolas", 8),
                )
            )

    def _draw_frame(
        self, channel_1: Any, channel_2: Any, sample_rate_hz: float, samples_per_channel: int
    ) -> None:
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        if self.trigger_enabled.get() and self.__dict__.get("trigger_window_precomputed", False):
            start_index = 0
            end_index = samples_per_channel
            trigger_index = self.__dict__.get("precomputed_trigger_index")
        else:
            start_index, end_index, trigger_index = self._trigger_window_bounds(
                channel_1, channel_2, samples_per_channel
            )
        if self.trigger_enabled.get() and trigger_index is None:
            # Normal trigger mode: retain the last valid capture while armed.
            if not self.trigger_capture_valid:
                self._hide_trigger_lines()
                self._set_signal_notice(
                    f"Trigger armed\nWaiting for {self.trigger_channel.get()} "
                    f"{self.trigger_edge.get().lower()} edge"
                )
            return
        self.trigger_capture_valid = trigger_index is not None
        self._clear_signal_notice()
        for item in self.trace_items:
            self.canvas.delete(item)
        self.trace_items.clear()

        plot_channel_1 = (
            self._window_channel_for_bounds(channel_1, start_index, end_index, samples_per_channel)
            if self.channel_1_visible.get()
            else None
        )
        plot_channel_2 = (
            self._window_channel_for_bounds(channel_2, start_index, end_index, samples_per_channel)
            if self.channel_2_visible.get()
            else None
        )
        visible_samples = max(end_index - start_index, 1)
        trigger_offset_samples = trigger_index - start_index if trigger_index is not None else None
        axis_changed = (
            sample_rate_hz != self.axis_sample_rate_hz
            or visible_samples != self.axis_samples_per_channel
            or trigger_offset_samples != self.axis_trigger_offset_samples
            or self.y_axis_dirty
        )
        if self.y_axis_fit_pending:
            self._fit_y_axis_to_channels(plot_channel_1, plot_channel_2)
            axis_changed = True
        self.axis_sample_rate_hz = sample_rate_hz
        self.axis_samples_per_channel = visible_samples
        self.axis_trigger_offset_samples = trigger_offset_samples
        self._update_time_controls(sample_rate_hz, samples_per_channel, start_index, end_index)
        if not self.grid_items or axis_changed:
            self._redraw_grid()
        self.y_axis_dirty = False

        left_pad = 56
        bottom_pad = 26
        plot_width = max(width - left_pad - 8, 1)
        plot_height = max(height - SIGNAL_PLOT_TOP_PAD - bottom_pad - 8, 1)
        plot_bottom = SIGNAL_PLOT_TOP_PAD + plot_height
        y_scale = (plot_height * 0.5) / self.axis_y_abs
        plot_points = self._signal_plot_points()
        padding = min(max(self.signal_padding_samples - start_index, 0), visible_samples)
        trace_left = left_pad + plot_width * padding / visible_samples
        trace_width = plot_width * (visible_samples - padding) / visible_samples
        draw_channel(
            self.canvas,
            plot_channel_1,
            COLORS["ch1"],
            SIGNAL_PLOT_TOP_PAD + self._channel_y_offset(1, plot_height),
            y_scale,
            trace_width,
            trace_left,
            plot_points,
            tag="signal_ch1",
        )
        draw_channel(
            self.canvas,
            plot_channel_2,
            COLORS["ch2"],
            SIGNAL_PLOT_TOP_PAD + self._channel_y_offset(2, plot_height),
            y_scale,
            trace_width,
            trace_left,
            plot_points,
            tag="signal_ch2",
        )
        if trigger_index is not None:
            trigger_x = left_pad + (
                (trigger_index - start_index) * plot_width / max(visible_samples - 1, 1)
            )
            trigger_vertical = update_trace_line(
                self.canvas,
                [trigger_x, SIGNAL_PLOT_TOP_PAD, trigger_x, plot_bottom],
                COLORS["warning"],
                TRIGGER_VERTICAL_TAG,
                TRIGGER_OVERLAY_TAG,
            )
            self.canvas.itemconfigure(trigger_vertical, dash=(3, 3), state="normal")
            trigger_level = float(self.trigger_level.get().strip())
            trigger_channel_number = 1 if self.trigger_channel.get() == "CH1" else 2
            trigger_y = (
                SIGNAL_PLOT_TOP_PAD
                + self._channel_y_offset(trigger_channel_number, plot_height)
                - trigger_level * y_scale
            )
            if SIGNAL_PLOT_TOP_PAD <= trigger_y <= plot_bottom:
                trigger_level_line = update_trace_line(
                    self.canvas,
                    [left_pad, trigger_y, left_pad + plot_width, trigger_y],
                    COLORS["warning"],
                    TRIGGER_LEVEL_TAG,
                    TRIGGER_OVERLAY_TAG,
                )
                self.canvas.itemconfigure(trigger_level_line, dash=(3, 3), state="normal")
            else:
                self.canvas.itemconfigure(TRIGGER_LEVEL_TAG, state="hidden")
            edge_symbol = "↑" if self.trigger_edge.get() == "Rising" else "↓"
            visible_seconds = visible_samples / sample_rate_hz if sample_rate_hz > 0 else 0.0
            set_if_changed(
                self.trigger_status,
                f"Triggered {self.trigger_channel.get()} {edge_symbol} "
                f"{format_voltage(trigger_level)} · {format_duration(visible_seconds)}",
            )
        else:
            self._hide_trigger_lines()
        if self.cursor_enabled:
            cursor_x = left_pad + plot_width / 2
            self.trace_items.append(
                self.canvas.create_line(
                    cursor_x,
                    SIGNAL_PLOT_TOP_PAD,
                    cursor_x,
                    plot_bottom,
                    fill=COLORS["warn"],
                    width=1,
                    dash=(4, 4),
                )
            )
            if sample_rate_hz > 0:
                cursor_ms = (start_index + visible_samples / 2) / sample_rate_hz * 1000
                readout = [f"t {cursor_ms:.4g} ms"]
                for label, values in (("CH1", plot_channel_1), ("CH2", plot_channel_2)):
                    if values is not None and len(values):
                        readout.append(f"{label} {format_voltage(float(values[len(values) // 2]))}")
                self.trace_items.append(
                    self.canvas.create_text(
                        cursor_x + 8,
                        18,
                        text="  ·  ".join(readout),
                        fill=COLORS["warn"],
                        anchor="w",
                        font=FONTS["mono"],
                    )
                )
                set_if_changed(self.cursor_status, "  ·  ".join(readout))
        self._draw_missing_channel_markers(channel_1, channel_2, left_pad, plot_width)
        self._update_display_detail()

    def _hide_trigger_lines(self) -> None:
        self.canvas.itemconfigure(TRIGGER_VERTICAL_TAG, state="hidden")
        self.canvas.itemconfigure(TRIGGER_LEVEL_TAG, state="hidden")

    def _draw_missing_channel_markers(
        self, channel_1: Any, channel_2: Any, left_pad: int, plot_width: int
    ) -> None:
        missing: list[str] = []
        if self.channel_1_visible.get() and channel_1 is None:
            missing.append(self._missing_channel_text(1))
        if self.channel_2_visible.get() and channel_2 is None:
            missing.append(self._missing_channel_text(2))
        for index, text in enumerate(missing):
            self.trace_items.append(
                self.canvas.create_text(
                    left_pad + plot_width - 10,
                    18 + index * 18,
                    text=text,
                    fill=COLORS["warn"],
                    anchor="e",
                    font=("Segoe UI", 9, "bold"),
                )
            )

    def _missing_channel_text(self, channel: int) -> str:
        if self.selected_device is not None:
            acquired = (
                self.selected_device.channel_1_enabled
                if channel == 1
                else self.selected_device.channel_2_enabled
            )
            if not acquired:
                return f"CH{channel} acquisition off"
        return f"CH{channel} no samples"
