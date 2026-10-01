"""Bounded per-temporal-index variance over repeated, aligned acquisition windows."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
CountArray = npt.NDArray[np.int64]


class _IndexMoments:
    def __init__(self) -> None:
        self.count = np.empty(0, dtype=np.int64)
        self.mean = np.empty(0, dtype=np.float64)
        self.m2 = np.empty(0, dtype=np.float64)

    def update(self, start: int, values: FloatArray) -> None:
        stop = start + values.size
        extra = stop - self.count.size
        if extra > 0:
            self.count = np.concatenate((self.count, np.zeros(extra, dtype=np.int64)))
            self.mean = np.concatenate((self.mean, np.zeros(extra, dtype=np.float64)))
            self.m2 = np.concatenate((self.m2, np.zeros(extra, dtype=np.float64)))
        indices = np.flatnonzero(np.isfinite(values)) + start
        if not indices.size:
            return
        observed = values[indices - start]
        self.count[indices] += 1
        delta = observed - self.mean[indices]
        self.mean[indices] += delta / self.count[indices]
        self.m2[indices] += delta * (observed - self.mean[indices])

    def variance(self) -> FloatArray:
        output = np.full(self.count.size, np.nan, dtype=np.float64)
        valid = self.count >= 2
        output[valid] = np.maximum(self.m2[valid] / self.count[valid], 0.0)
        return output


class EnsembleNullifierVariance:
    """Observe each nullifier anchor once per collection, even with split packets.

    ``max_modes`` bounds both the first modes retained in the current collection
    and the per-index moments. Quadrature history beyond that prefix is still
    available to the receiver's ordinary time-history diagnostic.
    """

    def __init__(
        self,
        formulas: Mapping[str, Callable[[Mapping[str, FloatArray]], FloatArray]],
        *,
        max_modes: int,
    ) -> None:
        self._formulas = dict(formulas)
        self._max_modes = max_modes
        self._moments = {name: _IndexMoments() for name in formulas}
        self._window_id: int | None = None
        self._current = {name: np.empty(0, dtype=np.float64) for name in ("a", "b")}
        self._processed = dict.fromkeys(formulas, 0)
        self.prefix_limited = False

    def append(self, quadratures: Mapping[str, FloatArray], window_id: int) -> None:
        if window_id != self._window_id:
            self._window_id = window_id
            self._current = {name: np.empty(0, dtype=np.float64) for name in ("a", "b")}
            self._processed = dict.fromkeys(self._formulas, 0)
        remaining = self._max_modes - self._current["a"].size
        if quadratures["a"].size > remaining:
            self.prefix_limited = True
        if remaining <= 0:
            return
        for name in self._current:
            self._current[name] = np.concatenate((self._current[name],
                                                   quadratures[name][:remaining]))
        for name, formula in self._formulas.items():
            values = formula(self._current)
            start = self._processed[name]
            if values.size > start:
                self._moments[name].update(start, values[start:])
                self._processed[name] = values.size

    def snapshot(self) -> tuple[dict[str, FloatArray], dict[str, CountArray]]:
        return (
            {name: moments.variance() for name, moments in self._moments.items()},
            {name: moments.count.copy() for name, moments in self._moments.items()},
        )
