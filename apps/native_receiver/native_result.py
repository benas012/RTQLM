"""Decode board-derived results without recalculating the measurement on the PC."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import numpy as np

from quantum_light_engine.post_processing import ClusterProcessingResult


def native_result(payload: dict[str, Any]) -> ClusterProcessingResult:
    values: dict[str, Any] = {}
    for field in fields(ClusterProcessingResult):
        first, *rest = field.name.split("_")
        key = first + "".join(word.title() for word in rest)
        if key in payload:
            values[field.name] = payload[key]
    for key in ("quadratures", "nullifiers", "variances", "squeezing_db", "variance_counts"):
        values[key] = {
            name: np.asarray(array, dtype=np.int64 if key == "variance_counts" else np.float64)
            for name, array in values.get(key, {}).items()
        }
    return ClusterProcessingResult(**values)
