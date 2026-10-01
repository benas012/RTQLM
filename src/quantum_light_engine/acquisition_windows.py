"""Sample-clock acquisition windows, independent of transport packet boundaries."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True, slots=True)
class AcquisitionSpan:
    start: int
    stop: int
    window_id: int
    closes_window: bool


class AcquisitionWindowGate:
    """Select contiguous collect spans before temporal-mode integration.

    Periodic timing starts with locking. Trigger holdoff is measured from the
    accepted edge; edges during a pending or active collection are ignored.
    A trigger is a measured ADC threshold crossing, not an FPGA digital input.
    """

    def __init__(
        self,
        *,
        mode: str,
        sample_rate_hz: float,
        lock_seconds: float,
        collect_seconds: float,
        trigger_channel: str,
        trigger_edge: str,
        trigger_level_volts: float,
        trigger_delay_seconds: float,
        trigger_holdoff_seconds: float,
        trigger_hysteresis_volts: float,
    ) -> None:
        self.mode = mode
        self.lock_samples = round(lock_seconds * sample_rate_hz)
        self.collect_samples = round(collect_seconds * sample_rate_hz)
        self.delay_samples = round(trigger_delay_seconds * sample_rate_hz)
        self.holdoff_samples = round(trigger_holdoff_seconds * sample_rate_hz)
        if mode != "continuous" and self.collect_samples < 1:
            raise ValueError("collection duration is shorter than one ADC sample")
        if mode == "periodic" and lock_seconds > 0 and self.lock_samples < 1:
            raise ValueError("locking duration is shorter than one ADC sample")
        self.trigger_channel = trigger_channel
        self.trigger_edge = trigger_edge
        self.trigger_level = trigger_level_volts
        self.trigger_hysteresis = trigger_hysteresis_volts
        self.position = 0
        self.accepted_samples = 0
        self.skipped_samples = 0
        self.collection_windows = 0
        self.state = "continuous" if mode == "continuous" else (
            "locking" if mode == "periodic" else "waiting for trigger"
        )
        self._previous: float | None = None
        self._armed = False
        self._next_allowed_edge = 0
        self._windows: list[tuple[int, int, int]] = []
        self._last_periodic_window = -1

    def select(
        self, channel_a: npt.NDArray[np.float64], channel_b: npt.NDArray[np.float64]
    ) -> list[AcquisitionSpan]:
        if channel_a.ndim != 1 or channel_b.ndim != 1:
            raise ValueError("ADC channels must be one-dimensional")
        if channel_a.size != channel_b.size:
            raise ValueError("ADC channels must have equal sample counts")
        count = channel_a.size
        if count == 0:
            return []
        if self.mode == "continuous":
            spans = [AcquisitionSpan(0, count, 0, False)]
            self.collection_windows = 1
        elif self.mode == "periodic":
            spans = self._periodic(count)
        else:
            source = channel_a if self.trigger_channel == "a" else channel_b
            spans = self._triggered(source)
        accepted = sum(span.stop - span.start for span in spans)
        self.accepted_samples += accepted
        self.skipped_samples += count - accepted
        self.position += count
        return spans

    def _periodic(self, count: int) -> list[AcquisitionSpan]:
        period = self.lock_samples + self.collect_samples
        stop = self.position + count
        cursor = self.position
        spans: list[AcquisitionSpan] = []
        while cursor < stop:
            cycle = cursor // period
            collect_start = cycle * period + self.lock_samples
            collect_stop = (cycle + 1) * period
            cursor = max(cursor, collect_start)
            if cursor >= stop:
                break
            end = min(stop, collect_stop)
            spans.append(AcquisitionSpan(cursor - self.position, end - self.position,
                                         cycle, end == collect_stop))
            if cycle != self._last_periodic_window:
                self.collection_windows += 1
                self._last_periodic_window = cycle
            cursor = end
        self.state = "locking" if stop % period < self.lock_samples else "collecting"
        return spans

    def _crossings(self, source: npt.NDArray[np.float64]) -> npt.NDArray[np.int64]:
        previous = np.empty_like(source)
        previous[0] = np.nan if self._previous is None else self._previous
        previous[1:] = source[:-1]
        finite_pair = np.isfinite(previous) & np.isfinite(source)
        invalid = np.flatnonzero(~np.isfinite(source))
        if self.trigger_edge == "rising":
            crossings = np.flatnonzero(finite_pair & (previous <= self.trigger_level)
                                      & (source > self.trigger_level))
            arm = np.flatnonzero(np.isfinite(source)
                                 & (source <= self.trigger_level - self.trigger_hysteresis))
        else:
            crossings = np.flatnonzero(finite_pair & (previous >= self.trigger_level)
                                      & (source < self.trigger_level))
            arm = np.flatnonzero(np.isfinite(source)
                                 & (source >= self.trigger_level + self.trigger_hysteresis))
        if crossings.size:
            arm_indices = np.searchsorted(arm, crossings, side="left") - 1
            last_arm = np.full(crossings.size, -1, dtype=np.int64)
            has_arm = arm_indices >= 0
            last_arm[has_arm] = arm[arm_indices[has_arm]]
            last_crossing = np.concatenate(([-1], crossings[:-1]))
            invalid_indices = np.searchsorted(invalid, crossings, side="left") - 1
            last_invalid = np.full(crossings.size, -1, dtype=np.int64)
            has_invalid = invalid_indices >= 0
            last_invalid[has_invalid] = invalid[invalid_indices[has_invalid]]
            eligible = last_arm > np.maximum(last_crossing, last_invalid)
            eligible[0] |= self._armed and last_invalid[0] < 0
            accepted = crossings[eligible]
            barrier = max(int(crossings[-1]), int(invalid[-1]) if invalid.size else -1)
            self._armed = bool(arm.size and arm[-1] > barrier)
        else:
            accepted = crossings
            if invalid.size:
                self._armed = bool(arm.size and arm[-1] > invalid[-1])
            else:
                self._armed |= bool(arm.size)
        # An invalid tail cannot arm an edge across a missing ADC value.
        self._previous = float(source[-1])
        if not np.isfinite(source[-1]):
            self._armed = False
        return np.asarray(accepted, dtype=np.int64)

    def _triggered(self, source: npt.NDArray[np.float64]) -> list[AcquisitionSpan]:
        edges = self._crossings(source) + self.position
        edge_index = int(np.searchsorted(edges, self._next_allowed_edge))
        while edge_index < edges.size:
            edge = int(edges[edge_index])
            start = edge + self.delay_samples
            stop = start + self.collect_samples
            window_id = self.collection_windows
            self.collection_windows += 1
            self._windows.append((start, stop, window_id))
            self._next_allowed_edge = max(edge + self.holdoff_samples, stop)
            edge_index = int(np.searchsorted(edges, self._next_allowed_edge))
        packet_stop = self.position + source.size
        spans: list[AcquisitionSpan] = []
        pending: list[tuple[int, int, int]] = []
        for start, stop, window_id in self._windows:
            first, last = max(start, self.position), min(stop, packet_stop)
            if last > first:
                spans.append(AcquisitionSpan(first - self.position, last - self.position,
                                             window_id, stop <= packet_stop))
            if stop > packet_stop:
                pending.append((start, stop, window_id))
        self._windows = pending
        if any(start <= packet_stop < stop for start, stop, _ in pending):
            self.state = "collecting"
        elif pending:
            self.state = "trigger delay"
        else:
            self.state = "waiting for trigger"
        return spans
