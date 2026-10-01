"""Trigger-aligned sample extraction for the native waveform view."""

from __future__ import annotations

from typing import Any

import numpy as np


def interpolate_trigger_window(
    history: Any,
    count: int,
    visible: int,
    crossing: int,
    trigger_channel: int,
    level: float,
    pre_trigger_samples: int,
    point_budget: int,
) -> tuple[Any, Any, float]:
    """Return a fractionally aligned display window from a raw sample ring."""
    trigger_buffer = history.channels[trigger_channel]
    crossing_pair = history._channel_window(trigger_buffer, count, crossing - 1, crossing + 1)
    before, after = (float(crossing_pair[0]), float(crossing_pair[1]))
    fraction = 1.0 if after == before else (level - before) / (after - before)
    trigger_position = crossing - 1 + min(max(fraction, 0.0), 1.0)

    max_start = max(count - visible, 0)
    start = min(max(trigger_position - pre_trigger_samples, 0.0), float(max_start))
    point_count = min(visible, max(int(point_budget), 2))
    positions = start + np.linspace(0.0, visible - 1, point_count)
    lower_indices = np.floor(positions).astype(np.intp)
    upper_indices = np.minimum(lower_indices + 1, count - 1)
    fractions = positions - lower_indices
    ring_start = history.position - count
    lower_physical = (ring_start + lower_indices) % history.capacity
    upper_physical = (ring_start + upper_indices) % history.capacity

    def interpolate(channel: int) -> Any:
        buffer = history.channels.get(channel)
        if buffer is None:
            return None
        lower = buffer[lower_physical].astype(np.float64)
        upper = buffer[upper_physical].astype(np.float64)
        return lower + fractions * (upper - lower)

    first = interpolate(1)
    second = interpolate(2)
    trigger_offset = trigger_position - start
    # A pulse can be narrower than one display interval. Preserve the raw pair
    # that established the edge while the rest of the trace stays interpolated.
    plot_index = round(trigger_offset * (point_count - 1) / (visible - 1))
    trigger_values = first if trigger_channel == 1 else second
    if trigger_values is not None and plot_index > 0:
        if after >= before:
            crossing_visible = np.any(
                (trigger_values[:-1] < level) & (trigger_values[1:] >= level)
            )
        else:
            crossing_visible = np.any(
                (trigger_values[:-1] > level) & (trigger_values[1:] <= level)
            )
        if not crossing_visible:
            trigger_values[plot_index - 1] = before
            trigger_values[plot_index] = after
    return first, second, trigger_offset
