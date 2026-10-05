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
) -> tuple[Any, Any, float, Any]:
    """Return a fractionally aligned display window from a raw sample ring."""
    trigger_buffer = history.channels[trigger_channel]
    crossing_pair = history._channel_window(trigger_buffer, count, crossing - 1, crossing + 1)
    before, after = (float(crossing_pair[0]), float(crossing_pair[1]))
    fraction = 1.0 if after == before else (level - before) / (after - before)
    trigger_position = crossing - 1 + min(max(fraction, 0.0), 1.0)

    start = trigger_position - pre_trigger_samples
    point_count = min(visible, max(int(point_budget), 2))
    positions = start + np.linspace(0.0, visible - 1, point_count)
    # Keep the actual edge pair at its actual horizontal positions. Uniform
    # display sampling can land on the opposite edge of a fast waveform, even
    # when the raw trigger was correct. Other crossings don't validate this one.
    right = int(np.searchsorted(positions, trigger_position, side="right"))
    if (0 < right < point_count and crossing - 1 >= start
            and crossing <= start + visible - 1):
        positions[right - 1] = crossing - 1
        positions[right] = crossing
    positions = positions[(positions >= 0) & (positions <= count - 1)]
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
    trigger_offset = float(pre_trigger_samples)
    return first, second, trigger_offset, positions - start
