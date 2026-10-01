from __future__ import annotations

from typing import Any

import numpy as np

from apps.native_receiver.constants import (
    COLORS,
    CONNECTION_FAILED_SPECTRUM_NOTICE,
    CONNECTION_OVERLAY_BUSY_LABELS,
    SPECTRUM_MIN_INTERVAL_MS,
)
from apps.native_receiver.helpers import (
    compute_spectrum,
    draw_spectrum_line,
    format_frequency,
    time_monotonic_ns,
)


class SpectrumRendererMixin:
    def _redraw_spectrum_grid(self) -> None:
        for item in self.spectrum_grid_items:
            self.spectrum_canvas.delete(item)
        self.spectrum_grid_items.clear()
        width = max(self.spectrum_canvas.winfo_width(), 1)
        height = max(self.spectrum_canvas.winfo_height(), 1)
        left_pad = 56
        bottom_pad = 24
        plot_width = max(width - left_pad - 8, 1)
        plot_height = max(height - bottom_pad - 8, 1)

        for index in range(11):
            x = left_pad + index * plot_width / 10
            self.spectrum_grid_items.append(
                self.spectrum_canvas.create_line(x, 0, x, plot_height, fill=COLORS["grid"])
            )
        for index in range(7):
            y = index * plot_height / 6
            self.spectrum_grid_items.append(
                self.spectrum_canvas.create_line(left_pad, y, width, y, fill=COLORS["grid"])
            )

        scale = self.spectrum_scale.get()
        top_label = "0 dBFS" if scale == "dBFS" else "max"
        bottom_label = "-120" if scale == "dBFS" else "0"
        self.spectrum_grid_items.append(
            self.spectrum_canvas.create_text(
                left_pad - 8,
                10,
                text=top_label,
                fill=COLORS["muted"],
                anchor="e",
                font=("Consolas", 8),
            )
        )
        self.spectrum_grid_items.append(
            self.spectrum_canvas.create_text(
                left_pad - 8,
                plot_height - 3,
                text=bottom_label,
                fill=COLORS["muted"],
                anchor="e",
                font=("Consolas", 8),
            )
        )
        self.spectrum_grid_items.append(
            self.spectrum_canvas.create_text(
                left_pad,
                plot_height + 13,
                text="0 Hz",
                fill=COLORS["muted"],
                anchor="n",
                font=("Consolas", 8),
            )
        )
        self.spectrum_grid_items.append(
            self.spectrum_canvas.create_text(
                left_pad + plot_width,
                plot_height + 13,
                text=format_frequency(self.spectrum_axis_sample_rate_hz / 2),
                fill=COLORS["muted"],
                anchor="n",
                font=("Consolas", 8),
            )
        )

        for item in self.spectrum_grid_items:
            self.spectrum_canvas.tag_lower(item)
        with self.frame_lock:
            has_frame = self.latest_frame is not None
        if self.loading_active and self.loading_label not in CONNECTION_OVERLAY_BUSY_LABELS:
            self._set_spectrum_notice(self.loading_label)
        elif self.connection_failure_active:
            self._set_spectrum_notice(CONNECTION_FAILED_SPECTRUM_NOTICE)
        elif not has_frame:
            self._set_spectrum_notice("Waiting for spectrum data")

    def _draw_spectrum(
        self,
        channel_1: Any,
        channel_2: Any,
        sample_rate_hz: float,
        *,
        force: bool = False,
    ) -> None:
        now = time_monotonic_ns()
        if (
            not force
            and now - self.last_spectrum_monotonic_ns < SPECTRUM_MIN_INTERVAL_MS * 1_000_000
        ):
            return
        self.last_spectrum_monotonic_ns = now

        for item in self.spectrum_trace_items:
            self.spectrum_canvas.delete(item)
        self.spectrum_trace_items.clear()
        self._clear_spectrum_notice()

        channel_name = self.spectrum_channel.get()
        samples = channel_2 if channel_name == "CH2" else channel_1
        color = COLORS["ch2"] if channel_name == "CH2" else COLORS["ch1"]
        axis_changed = (
            sample_rate_hz != self.spectrum_axis_sample_rate_hz
            or self.spectrum_scale.get() != self.spectrum_axis_scale
        )
        self.spectrum_axis_sample_rate_hz = sample_rate_hz
        self.spectrum_axis_scale = self.spectrum_scale.get()
        if not self.spectrum_grid_items or axis_changed:
            self._redraw_spectrum_grid()

        if samples is None or len(samples) < 2:
            self.spectrum_canvas.itemconfigure("spectrum_trace", state="hidden")
            self.spectrum_peak.set("Peak: -")
            self.spectrum_trace_items.append(
                self.spectrum_canvas.create_text(
                    68,
                    24,
                    text=f"{channel_name} is not enabled",
                    fill=COLORS["muted"],
                    anchor="w",
                )
            )
            return

        spectrum = compute_spectrum(samples, sample_rate_hz, int(self.spectrum_fft_size.get()))
        if spectrum is None:
            self.spectrum_canvas.itemconfigure("spectrum_trace", state="hidden")
            self.spectrum_peak.set("Peak: -")
            return

        width = max(self.spectrum_canvas.winfo_width(), 1)
        height = max(self.spectrum_canvas.winfo_height(), 1)
        left_pad = 56
        bottom_pad = 24
        plot_width = max(width - left_pad - 8, 1)
        plot_height = max(height - bottom_pad - 8, 1)
        use_db = self.spectrum_scale.get() == "dBFS"
        magnitudes = spectrum["magnitudes"]

        if use_db:
            values = 20 * np.log10(np.maximum(magnitudes, 1e-12))
            normalized = np.clip((values + 120.0) / 120.0, 0.0, 1.0)
        else:
            maximum = max(float(np.max(magnitudes)), 1e-12)
            normalized = np.clip(magnitudes / maximum, 0.0, 1.0)

        draw_spectrum_line(
            self.spectrum_canvas,
            normalized,
            color,
            plot_width,
            plot_height,
            left_pad,
        )
        self.spectrum_peak.set(
            f"Peak: {format_frequency(float(spectrum['peak_hz']))} / "
            f"{float(spectrum['peak_db']):.1f} dBFS"
        )
