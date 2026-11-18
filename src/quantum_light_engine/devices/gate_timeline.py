"""Conservative analysis windows from timestamped board-side digital polling.

ADC times and GPIO bounds must use the same *verified* board clock mapping.
This is a sampled-level gate, not an edge latch: transitions entirely between
polls cannot be detected. An unverified mapping never enables analysis.
"""
from dataclasses import dataclass
from math import ceil, floor, isfinite


@dataclass(frozen=True)
class GateObservation:
    earliest_ns: int
    latest_ns: int
    high: bool
    valid: bool = True


def high_sample_ranges(
    observations: list[GateObservation], *, first_sample_ns: int,
    sample_count: int, sample_rate_hz: float, clock_uncertainty_ns: int | None,
    max_poll_interval_ns: int = 2_000_000,
) -> list[tuple[int, int]]:
    """Return half-open sample ranges bounded by consecutive valid HIGH polls.

    Delay processing until a later observation brackets the samples. LOW,
    failed/stale reads and ambiguous transitions yield no accepted samples.
    A caller must reset incomplete modes/rolling windows between returned ranges
    and also on packet loss, channel mismatch or any acquisition restart.
    """
    if sample_count < 0 or not isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError('Invalid ADC sample range')
    if clock_uncertainty_ns is None:
        return []
    if clock_uncertainty_ns < 0 or max_poll_interval_ns <= 0:
        raise ValueError('Invalid timing uncertainty or polling interval')
    if any(o.earliest_ns > o.latest_ns for o in observations):
        raise ValueError('Invalid GPIO timestamp bounds')
    if any(b.earliest_ns < a.latest_ns for a,b in zip(observations, observations[1:])):
        raise ValueError('GPIO observations overlap or are out of order')
    # First combine uninterrupted HIGH observations in board time; eroding each
    # adjacent poll separately would invent small gaps during a sustained HIGH.
    intervals: list[tuple[int,int]] = []
    start = end = None
    for left, right in zip(observations, observations[1:]):
        accepted = (left.valid and right.valid and left.high and right.high
                    and right.latest_ns-left.earliest_ns <= max_poll_interval_ns)
        if accepted:
            if start is None: start = left.latest_ns
            end = right.earliest_ns
        elif start is not None:
            intervals.append((start,end))
            start = end = None
    if start is not None: intervals.append((start,end))
    result = []
    for low,high in intervals:
        low += clock_uncertainty_ns
        high -= clock_uncertainty_ns
        # Subtract integer epochs before floating arithmetic to retain ns precision.
        begin = max(0, ceil((low-first_sample_ns)*sample_rate_hz/1e9))
        stop = min(sample_count, floor((high-first_sample_ns)*sample_rate_hz/1e9))
        if stop > begin: result.append((begin,stop))
    return result
