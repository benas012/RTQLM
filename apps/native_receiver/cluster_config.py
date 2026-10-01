"""Receiver defaults and conservative migration of the original built-in preset."""

from __future__ import annotations

from dataclasses import replace
from math import isclose

from quantum_light_engine.post_processing import (
    ClusterProcessingConfig,
    NullifierSpec,
    NullifierTerm,
    config_from_dict,
)

_LEGACY_NULLIFIERS = (
    NullifierSpec(
        "cluster_x",
        (
            NullifierTerm("a", 0, 1.0),
            NullifierTerm("a", 1, 1.0),
            NullifierTerm("b", 0, 1.0),
            NullifierTerm("b", 1, -1.0),
        ),
    ),
    NullifierSpec(
        "cluster_p",
        (
            NullifierTerm("a", 0, 1.0),
            NullifierTerm("a", 1, -1.0),
            NullifierTerm("b", 0, 1.0),
            NullifierTerm("b", 1, 1.0),
        ),
        start=1,
    ),
)


def default_receiver_cluster_config() -> ClusterProcessingConfig:
    return ClusterProcessingConfig(
        window_mode="periodic", variance_estimator="ensemble", history_modes=25_000
    )


def receiver_cluster_config(data: dict[str, object]) -> ClusterProcessingConfig:
    """Read legacy data without rewriting it or replacing custom lab formulas."""
    if not data:
        return default_receiver_cluster_config()
    normalized = dict(data)
    legacy = not any(key in data for key in ("windowMode", "modeShape", "measurementBasis"))
    normalized.setdefault("windowMode", "periodic")
    if legacy and data:
        normalized.setdefault("modeShape", "gaussian_linear_cosine")
    config = config_from_dict(normalized)
    expected_legacy = ClusterProcessingConfig(
        mode_width_seconds=0.308e-6,
        measurement_frequency_hz=6e6,
        mode_shape="gaussian_linear_cosine",
        nullifiers=_LEGACY_NULLIFIERS,
        window_mode="periodic",
    )
    if (
        legacy
        and isclose(
            config.variance_window_seconds,
            expected_legacy.variance_window_seconds,
            rel_tol=0,
            abs_tol=5e-10,
        )
        and replace(config, variance_window_seconds=expected_legacy.variance_window_seconds)
        == expected_legacy
    ):
        return default_receiver_cluster_config()
    return config
