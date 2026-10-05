"""Qualify threshold edges independently of the desired display-window bounds."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np


def qualified_crossing(
    window: Callable[[int, int], Any], count: int, level: float, edge: str,
    lower: int, upper: int, hysteresis: float, chunk_size: int = 65_536,
) -> int | None:
    """Bound the edge position; arming/confirmation can lie outside those bounds."""
    if lower > upper or count < 2:
        return None
    rising = edge.lower() != "falling"
    step = max(int(chunk_size), 2)
    armed_level = level - hysteresis if rising else level + hysteresis
    confirmed_level = level + hysteresis if rising else level - hysteresis

    def matches(values: Any, threshold: float, above: bool) -> Any:
        return (values >= threshold) if above else (values <= threshold)

    def last(end: int, threshold: float, above: bool) -> int | None:
        while end >= 0:
            start = max(0, end - step + 1)
            indices = np.flatnonzero(matches(window(start, end + 1), threshold, above))
            if len(indices):
                return start + int(indices[-1])
            end = start - 1
        return None

    # Only look ahead until the next rearming excursion, rather than scanning
    # every future cycle when a wide capture puts the trigger early in history.
    confirmation_end = count - 1
    for start in range(upper + 1, count, step):
        indices = np.flatnonzero(matches(window(start, min(start + step, count)),
                                       armed_level, not rising))
        if len(indices):
            confirmation_end = start + int(indices[0]) - 1
            break
    while confirmation_end >= 1:
        reached = last(confirmation_end, confirmed_level, rising)
        if reached is None:
            return None
        armed = last(reached - 1, armed_level, not rising)
        if armed is None:
            return None
        values = window(armed, reached + 1)
        before, after = values[:-1], values[1:]
        crossings = np.flatnonzero(
            (before < level) & (after >= level) if rising else
            (before > level) & (after <= level)
        )
        if len(crossings):
            candidate = armed + int(crossings[0]) + 1
            if candidate < lower:
                return None
            if candidate <= upper:
                return candidate
        confirmation_end = armed - 1
    return None
