"""Experimental safety regressions for calibration and acquisition continuity."""

from dataclasses import replace

import numpy as np
import pytest

from quantum_light_engine.post_processing import (
    ClusterProcessingConfig,
    NullifierSpec,
    NullifierTerm,
    RealtimeClusterProcessor,
    lab_processing_config,
)


def _lab_config(**overrides: object) -> ClusterProcessingConfig:
    return replace(
        lab_processing_config(),
        **{
            "explicit_mode_template": (1.0, -1.0),
            "min_samples_per_mode": 2,
            "channel_b_delay_samples": 0,
            "collection_guard_seconds": 0.0,
            "collection_end_guard_samples": 0,
            "lock_seconds": 0.004,
            "collect_seconds": 0.008,
            "calibration_estimator": "segment_mean",
            **overrides,
        },
    )


def _cycle(quadratures: np.ndarray) -> np.ndarray:
    """Four lock samples followed by four two-sample modes at 1 kS/s."""
    samples = np.zeros(12)
    samples[4::2] = quadratures / np.sqrt(0.001 / 2)
    return samples


def test_discarded_calibration_fragment_cannot_satisfy_minimum_mode_count() -> None:
    processor = RealtimeClusterProcessor(_lab_config(window_mode="continuous"))
    processor.start_calibration()
    before_gap = np.tile([0.0, 1.0, 0.0, -1.0], 50)
    processor.feed(before_gap, before_gap, 1_000)
    after_gap = np.array([0.0, 100.0, 0.0, -100.0])
    processor.feed(after_gap, after_gap, 1_000, discontinuity=True)

    # The 100 discarded modes must not approve a variance estimated from only 2.
    with pytest.raises(ValueError):
        processor.finish_calibration(minimum_modes=100)
    assert not processor.calibrated


def test_lab_calibration_retains_completed_windows_and_drops_lost_fragment() -> None:
    processor = RealtimeClusterProcessor(_lab_config())
    processor.start_calibration()
    complete = _cycle(np.array([-1.0, 1.0, -1.0, 1.0]))
    for _ in range(3):
        processor.feed(complete, complete, 1_000)
    discarded = _cycle(np.array([-100.0, 100.0, -100.0, 100.0]))[:8]
    processor.feed(discarded, discarded, 1_000)
    retained = _cycle(np.array([-3.0, 3.0, -3.0, 3.0]))[:8]
    processor.feed(retained, retained, 1_000, discontinuity=True)

    # 12 complete-window modes plus 2 retained modes; the lost 2 do not count.
    with pytest.raises(ValueError):
        processor.finish_calibration(minimum_modes=15)
    coefficients = processor.finish_calibration(minimum_modes=14)
    expected = 1 / np.sqrt(2 * np.mean([1.0, 1.0, 1.0, 9.0]))
    assert coefficients["a"] == pytest.approx(expected)
    assert coefficients["b"] == pytest.approx(expected)


def _clock_config() -> ClusterProcessingConfig:
    return ClusterProcessingConfig(
        explicit_mode_template=(1.0, 1.0),
        min_samples_per_mode=2,
        window_mode="periodic",
        lock_seconds=0.005,
        collect_seconds=0.005,
        calibration_estimator="pooled",
        nullifiers=(NullifierSpec("a", (NullifierTerm("a"),)),),
    )


def test_start_calibration_preserves_existing_five_ms_acquisition_clock() -> None:
    processor = RealtimeClusterProcessor(_clock_config())
    processor.feed(np.zeros(6), np.zeros(6), 2_000)
    processor.start_calibration()
    # At 3 ms, four lock samples remain; then four complete measurement modes.
    samples = np.array([999.0] * 4 + [-1.0, -1.0, 1.0, 1.0, -1.0, -1.0, 1.0, 1.0])
    result = processor.feed(samples, samples, 2_000)
    assert result is not None
    assert processor.calibration_mode_counts == {"a": 4, "b": 4}
    np.testing.assert_allclose(
        result.quadratures["a"], np.array([-1.0, 1.0, -1.0, 1.0]) / np.sqrt(1_000)
    )


def test_finish_calibration_preserves_existing_five_ms_acquisition_clock() -> None:
    processor = RealtimeClusterProcessor(_clock_config())
    processor.start_calibration()
    shot_noise = np.arange(36, dtype=float)
    processor.feed(shot_noise, shot_noise, 2_000)
    coefficients = processor.finish_calibration(minimum_modes=8)

    # Continue at 18 ms: 2 collect modes, 5 ms locking, then 3 collect modes.
    samples = np.array([2.0, 2.0, 3.0, 3.0] + [999.0] * 10 + [4.0, 4.0, 5.0, 5.0, 6.0, 6.0])
    result = processor.feed(samples, samples, 2_000)
    assert result is not None
    expected = np.array([2.0, 3.0, 4.0, 5.0, 6.0]) / np.sqrt(1_000) * coefficients["a"]
    np.testing.assert_allclose(result.quadratures["a"], expected)


def test_loss_discards_partial_mode_and_prevents_nullifier_variance_bridging() -> None:
    config = ClusterProcessingConfig(
        explicit_mode_template=(1.0, 1.0, 1.0, 1.0),
        min_samples_per_mode=4,
        variance_window_seconds=0.1,
        nullifiers=(
            NullifierSpec("neighbor", (NullifierTerm("a"), NullifierTerm("a", 1, -1))),
        ),
    )
    processor = RealtimeClusterProcessor(config)
    before_gap = np.full(11, 10_000.0)  # Two modes and a three-sample tail.
    processor.feed(before_gap, before_gap, 1_000)
    after_gap = np.arange(12, dtype=float) ** 2
    recovered = processor.feed(after_gap, -after_gap, 1_000, discontinuity=True)
    fresh = RealtimeClusterProcessor(config).feed(after_gap, -after_gap, 1_000)
    assert recovered is not None and fresh is not None
    for source in ("a", "b"):
        np.testing.assert_allclose(recovered.quadratures[source], fresh.quadratures[source])
    np.testing.assert_allclose(recovered.nullifiers["neighbor"], fresh.nullifiers["neighbor"])
    np.testing.assert_allclose(recovered.variances["neighbor"], fresh.variances["neighbor"])


def _boundary_config() -> ClusterProcessingConfig:
    return ClusterProcessingConfig(
        explicit_mode_template=(1.0, 1.0, 1.0, 1.0),
        min_samples_per_mode=4,
        calibration_estimator="pooled",
        variance_window_seconds=0.1,
        nullifiers=(NullifierSpec("a", (NullifierTerm("a"),)),),
    )


def test_start_calibration_excludes_straddling_mode_and_preserves_later_bins() -> None:
    processor = RealtimeClusterProcessor(_boundary_config())
    old_signal = np.full(3, 1_000.0)
    assert processor.feed(old_signal, old_signal, 1_000) is None
    processor.start_calibration()
    # The first sample completes an invalid old/new mode. The next bins stay aligned.
    shot_noise = np.array([2.0] + [-1.0] * 4 + [1.0] * 4)
    result = processor.feed(shot_noise, shot_noise, 1_000)
    assert result is not None
    expected = np.array([np.nan, -1.0, 1.0]) * np.sqrt(4 / 1_000)
    np.testing.assert_allclose(result.quadratures["a"], expected)
    assert processor.calibration_mode_counts["a"] == 2
    coefficients = processor.finish_calibration(minimum_modes=2)
    assert coefficients["a"] == pytest.approx(1 / np.sqrt(2 * 4 / 1_000))


def test_finish_calibration_excludes_straddling_mode_and_preserves_later_bins() -> None:
    processor = RealtimeClusterProcessor(_boundary_config())
    processor.start_calibration()
    shot_noise = np.array([-1.0] * 4 + [1.0] * 4 + [1_000.0] * 3)
    processor.feed(shot_noise, shot_noise, 1_000)
    coefficients = processor.finish_calibration(minimum_modes=2)
    signal = np.array([2.0] + [3.0] * 4 + [5.0] * 4)
    result = processor.feed(signal, signal, 1_000)
    assert result is not None
    expected = np.array([np.nan, 3.0, 5.0]) * np.sqrt(4 / 1_000) * coefficients["a"]
    np.testing.assert_allclose(result.quadratures["a"], expected)
    np.testing.assert_allclose(result.nullifiers["a"], expected)
    # The boundary is excluded from variance rather than replacing it with zero.
    np.testing.assert_allclose(result.variances["a"], np.var(expected[1:]))
