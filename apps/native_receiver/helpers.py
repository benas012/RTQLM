from __future__ import annotations

import ctypes
import subprocess
import sys
import time
import tkinter as tk
from collections.abc import Callable
from typing import Any

import numpy as np

from apps.native_receiver.constants import (
    ADC_COUNTS_PER_VOLT,
    MAX_POINTS,
    TIME_ZOOM_MIN,
    UI_FRAME_INTERVAL_MS,
    WIDE_VIEW_AUTO_INTERVAL_MS,
    WIDE_VIEW_FAST_REFRESH_SECONDS,
)
from apps.native_receiver.protocol import Device
from apps.native_receiver.trigger_search import qualified_crossing


def editable_profile_payload(profile: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "name",
        "source",
        "host",
        "enabled",
        "isDefault",
        "channel1Enabled",
        "channel2Enabled",
        "decimation",
        "blockSizeBytes",
        "adcBufferSizeBytes",
        "streamingModulePath",
        "simulatorSamplesPerPacket",
        "simulatorSeed",
    )
    return {key: profile[key] for key in keys} | {
        key: profile[key] for key in ("acquisitionMode", "captureSamples") if key in profile
    }


def short_error(exc: BaseException, limit: int = 90) -> str:
    message = " ".join(str(exc).split())
    if len(message) <= limit:
        return message
    return f"{message[: limit - 1]}…"


def safe_builtins() -> dict[str, Any]:
    return {
        "abs": abs,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "round": round,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
    }


def device_slug(device: Device) -> str:
    raw = f"{device.name or 'redpitaya'}_{device.host or device.id}".lower()
    chars = [char if char.isalnum() else "_" for char in raw]
    compact = "_".join(part for part in "".join(chars).split("_") if part)
    return compact or device.id


def samples_to_numpy(samples: Any) -> np.ndarray[Any, Any]:
    if isinstance(samples, np.ndarray):
        return samples
    return np.asarray(samples)


def normalize_script_channel(value: Any, length: int) -> np.ndarray[Any, Any] | None:
    if value is None:
        return None
    array = np.asarray(value)
    if array.ndim == 0 or array.size == 1:
        scalar = float(array.reshape(-1)[0])
        return np.full(max(length, 2), scalar, dtype=np.float64)
    return np.nan_to_num(array.reshape(-1).astype(np.float64, copy=False))


def find_trigger_crossing(
    samples: Any,
    level: float,
    edge: str,
    *,
    target_index: int | None = None,
    minimum_index: int | None = None,
    maximum_index: int | None = None,
    hysteresis: float = 0.0,
) -> int | None:
    """Find the requested threshold edge nearest the preferred display position."""
    if samples is None or len(samples) < 2 or not np.isfinite(level):
        return None
    data = samples_to_numpy(samples).reshape(-1)
    before = data[:-1]
    after = data[1:]
    finite = np.isfinite(before) & np.isfinite(after)
    if edge.lower() == "falling":
        matches = finite & (before > level) & (after <= level)
    else:
        matches = finite & (before < level) & (after >= level)
    crossings = np.flatnonzero(matches) + 1
    if hysteresis > 0:
        lower = max(minimum_index or 1, 1)
        upper = min(maximum_index if maximum_index is not None else len(data) - 1,
                    len(data) - 1)
        return qualified_crossing(lambda start, end: data[start:end], len(data),
                                  level, edge, lower, upper, hysteresis)
    if minimum_index is not None:
        crossings = crossings[crossings >= minimum_index]
    if maximum_index is not None:
        crossings = crossings[crossings <= maximum_index]
    if len(crossings) == 0:
        return None
    if target_index is None:
        return int(crossings[-1])
    nearest = int(np.argmin(np.abs(crossings - target_index)))
    return int(crossings[nearest])


def sample_range(samples: Any) -> tuple[float, float] | None:
    if samples is None or len(samples) == 0:
        return None
    data = samples_to_numpy(samples)
    return float(np.min(data)), float(np.max(data))


def set_if_changed(variable: tk.StringVar, value: str) -> None:
    # Setting even identical text makes Tk schedule label redraws and layout work.
    if variable.get() != value:
        variable.set(value)


def adc_counts_to_volts(samples: Any) -> np.ndarray[Any, Any] | None:
    """Convert signed stream samples to volts for the standard ±1 V STEMlab input."""
    if samples is None:
        return None
    return samples_to_numpy(samples).astype(np.float64, copy=False) / ADC_COUNTS_PER_VOLT


def max_sample_abs(*channels: Any) -> float:
    maximum = 0.0
    for channel in channels:
        if channel is None:
            continue
        for value in channel[:: max(len(channel) // MAX_POINTS, 1)]:
            maximum = max(maximum, abs(float(value)))
    return maximum


def time_monotonic_ns() -> int:
    return time.monotonic_ns()


def next_frame_delay_ms(last_refresh_ns: int, now_ns: int) -> int:
    remaining_ns = last_refresh_ns + UI_FRAME_INTERVAL_MS * 1_000_000 - now_ns
    return max(1, (remaining_ns + 999_999) // 1_000_000)


def display_interval_ms(mode: str, zoom: float, visible_seconds: float | None = None) -> int:
    if mode == "Live":
        return UI_FRAME_INTERVAL_MS
    if mode == "Slow":
        return 500
    interval = round(UI_FRAME_INTERVAL_MS / max(zoom, TIME_ZOOM_MIN) ** 0.5)
    if visible_seconds is not None and visible_seconds >= WIDE_VIEW_FAST_REFRESH_SECONDS:
        interval = min(interval, WIDE_VIEW_AUTO_INTERVAL_MS)
    return max(UI_FRAME_INTERVAL_MS, min(1000, interval))


def start_display_timer() -> Callable[[], None]:
    """Improve Windows Tk timing only for the lifetime of an open sample stream."""
    if sys.platform == "win32":
        try:
            winmm = ctypes.WinDLL("winmm")
            if winmm.timeBeginPeriod(1) == 0:
                return lambda: winmm.timeEndPeriod(1)
        except OSError:
            pass
    return lambda: None


def terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
            return
        except Exception:
            pass
    process.terminate()


def smooth_acquisition_block_size(decimation: int) -> int:
    """Choose a supported block for frequent delivery at the existing sample rate."""
    if not 1 <= decimation <= 65_536:
        raise ValueError("Decimation must be from 1 to 65536.")
    bytes_per_refresh = max(1, int((125_000_000 / decimation) * 2 / 60))
    return min(2_097_152, max(2048, 1 << (bytes_per_refresh.bit_length() - 1)))


def acquisition_channel_count(device: Device | None) -> int:
    if device is None:
        return 2
    return max(int(device.channel_1_enabled) + int(device.channel_2_enabled), 1)


def acquisition_batch_seconds(
    block_size_bytes: int, channel_count: int, sample_rate_hz: float
) -> float:
    """The vendor block size is per channel; simultaneous channels share its duration."""
    if block_size_bytes <= 0 or channel_count <= 0 or sample_rate_hz <= 0:
        return 0.0
    samples_per_channel = block_size_bytes / 2
    return samples_per_channel / sample_rate_hz


def format_duration(seconds: float) -> str:
    if not np.isfinite(seconds) or seconds <= 0:
        return "-"
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.0f} us"
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.2f} s"


def format_voltage(value: float) -> str:
    return f"{value:.3g} V"


def format_frequency(value: float) -> str:
    if not np.isfinite(value):
        return "-"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f} MHz"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.2f} kHz"
    return f"{value:.1f} Hz"


def compute_spectrum(
    samples: Any, sample_rate_hz: float, requested_size: int
) -> dict[str, Any] | None:
    if samples is None or len(samples) < 16:
        return None

    limit = min(max(requested_size, 16), len(samples))
    fft_size = 1 << (limit.bit_length() - 1)
    if fft_size < 16:
        return None

    data = samples_to_numpy(samples)[-fft_size:].astype(np.float64, copy=False)
    data = data - float(np.mean(data))
    window = np.hanning(fft_size)
    window_sum = max(float(np.sum(window)), 1e-12)
    bins = np.fft.rfft(data * window)
    magnitudes = np.abs(bins) * 2.0 / (window_sum * ADC_COUNTS_PER_VOLT)
    if len(magnitudes) > 0:
        magnitudes[0] *= 0.5

    if len(magnitudes) > 1:
        peak_index = int(np.argmax(magnitudes[1:]) + 1)
    else:
        peak_index = 0
    peak_magnitude = max(float(magnitudes[peak_index]), 1e-12)
    return {
        "magnitudes": magnitudes,
        "peak_hz": peak_index * sample_rate_hz / fft_size,
        "peak_db": 20.0 * np.log10(peak_magnitude),
    }


def draw_channel(
    canvas: tk.Canvas,
    samples: Any,
    color: str,
    y_offset: float,
    y_scale: float,
    width: int,
    x_offset: int = 0,
    point_budget: int = MAX_POINTS,
    *,
    tag: str = "",
    sample_positions: Any = None,
    sample_span: int | None = None,
) -> int | None:
    if samples is None or len(samples) < 2:
        if tag:
            canvas.itemconfigure(tag, state="hidden")
        return None
    indices = np.linspace(0, len(samples) - 1, min(len(samples), point_budget), dtype=np.intp)
    data = samples_to_numpy(samples)[indices]
    points = np.empty((len(data), 2), dtype=np.float64)
    if sample_positions is None:
        points[:, 0] = x_offset + indices * (width / (len(samples) - 1))
    else:
        positions = np.asarray(sample_positions)
        span = sample_span - 1 if sample_span is not None else positions[-1]
        points[:, 0] = x_offset + positions[indices] * (width / max(span, 1))
    points[:, 1] = y_offset - data * y_scale
    return update_trace_line(canvas, points.ravel().tolist(), color, tag, "signal_trace")


def update_trace_line(
    canvas: tk.Canvas, points: list[float], color: str, tag: str, group: str
) -> int:
    items = canvas.find_withtag(tag) if tag else ()
    if items:
        item = int(items[0])
        canvas.coords(item, *points)
        canvas.itemconfigure(item, fill=color, state="normal")
        canvas.tag_raise(item)
        return item
    # Wide polylines are expensive to rasterize with Windows GDI, particularly
    # for two dense/noisy channels. Keep native one-pixel strokes and reuse them.
    return int(canvas.create_line(*points, fill=color, width=1, tags=(tag, group)))


def draw_spectrum_line(
    canvas: tk.Canvas,
    values: np.ndarray[Any, Any],
    color: str,
    width: int,
    height: int,
    x_offset: int,
) -> int | None:
    if len(values) < 2:
        return None
    indices = np.linspace(0, len(values) - 1, min(len(values), MAX_POINTS), dtype=np.intp)
    points = np.empty((len(indices), 2), dtype=np.float64)
    points[:, 0] = x_offset + indices * (width / (len(values) - 1))
    points[:, 1] = (1.0 - values[indices]) * height
    return update_trace_line(
        canvas, points.ravel().tolist(), color, "spectrum_trace", "spectrum_trace"
    )
