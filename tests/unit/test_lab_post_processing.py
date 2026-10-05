from dataclasses import replace

import numpy as np
import pytest

from apps.native_receiver.cluster_config import receiver_cluster_config
from apps.native_receiver.nullifier_acquisition import match_builtin_basis
from quantum_light_engine.post_processing import (
    LAB_CLUSTER_P,
    LAB_CLUSTER_X,
    ClusterProcessingConfig,
    NullifierSpec,
    NullifierTerm,
    RealtimeClusterProcessor,
    build_lab_mode_template,
    compute_nullifier,
    config_from_dict,
    config_to_dict,
    lab_processing_config,
    rolling_variance,
)


@pytest.mark.parametrize("rate", [125e6, 100e6, 31.25e6, 15.625e6])
def test_lab_weights_match_supplied_script_at_each_rate(rate: float) -> None:
    dt = 1 / rate
    # Independent transcription of the lab's fine-grid construction/interpolation.
    time = np.arange(0, 308e-9, dt / 1000)
    t = time - time[int(len(time) / 2)]
    raw = t * np.cos(t * 6e6) * np.exp(-0.5 * (2 * np.pi * 6e6) ** 2 * t**2)
    raw *= np.heaviside(t + 308e-9, 1) * np.heaviside(-(t - 308e-9), 1)
    raw /= np.sqrt(np.sum(raw**2) * dt / 1000)
    expected = np.interp(np.linspace(0, 308e-9, int(308e-9 / dt), endpoint=False), time, raw)
    np.testing.assert_allclose(build_lab_mode_template(308e-9, 6e6, dt), expected, rtol=1e-13)


def test_lab_templates_report_script_truncation_and_resolution_policy() -> None:
    assert len(build_lab_mode_template(308e-9, 6e6, 64e-9)) == 4
    with pytest.raises(ValueError, match="only 4 samples"):
        build_lab_mode_template(308e-9, 6e6, 64e-9, min_samples_per_mode=16)


def test_lab_signs_basis_and_p_start_match_supplied_script() -> None:
    a = np.array([1., 2., 4., 8.])
    b = np.array([10., 20., 40., 80.])
    np.testing.assert_allclose(compute_nullifier({"a": a, "b": b}, LAB_CLUSTER_X),
                               (a[:-1] + a[1:]) + (b[:-1] - b[1:]))
    np.testing.assert_allclose(compute_nullifier({"a": a, "b": b}, LAB_CLUSTER_P),
                               (a[1:-1] - a[2:]) + (b[1:-1] + b[2:]))
    x = lab_processing_config()
    assert match_builtin_basis(replace(x, measurement_basis="p")).nullifiers == (LAB_CLUSTER_P,)
    assert config_from_dict(config_to_dict(x)) == x
    assert receiver_cluster_config({}).mode_shape == "lab_gaussian_linear_cosine"


@pytest.mark.parametrize("width", [10, 11, 17])
def test_lab_rolling_boundaries_match_numpy_population_variance(width: int) -> None:
    signal = np.array([1., 2., -3., 8., -5., 3., 12., 2., -2., 0., 4., 9.])
    half = width // 2
    expected = [np.var(signal[max(0, i-half):min(len(signal), i+half)])
                for i in range(len(signal))]
    np.testing.assert_allclose(rolling_variance(signal, width, lab_window=True), expected)


def test_lab_stream_matches_offline_projection_across_packets() -> None:
    rng = np.random.default_rng(34)
    a, b = rng.normal(size=(2, 4020))
    cfg = replace(lab_processing_config(), window_mode="continuous",
                  channel_b_delay_samples=2, collection_guard_seconds=0,
                  collection_end_guard_samples=0)
    mode = build_lab_mode_template(308e-9, 6e6, 1 / 15.625e6)
    usable = (len(a) - 2) // len(mode) * len(mode)
    expected_a = a[:usable].reshape(-1, len(mode)) @ mode / 15.625e6
    expected_b = b[2:usable+2].reshape(-1, len(mode)) @ mode / 15.625e6
    processor = RealtimeClusterProcessor(cfg)
    qa, qb = [], []
    for start, stop in [(0, 3), (3, 251), (251, 1013), (1013, 4020)]:
        result = processor.feed(a[start:stop], b[start:stop], 15.625e6)
        if result is not None:
            qa.extend(result.quadratures["a"])
            qb.extend(result.quadratures["b"])
    np.testing.assert_allclose(qa, expected_a)
    np.testing.assert_allclose(qb, expected_b)


def _small_config() -> ClusterProcessingConfig:
    return replace(
        lab_processing_config(), explicit_mode_template=(1., -1.),
        min_samples_per_mode=2, channel_b_delay_samples=0,
        collection_guard_seconds=0, collection_end_guard_samples=0,
        lock_seconds=.004, collect_seconds=.008, variance_window_seconds=.01,
        nullifiers=(NullifierSpec("single", (NullifierTerm("a"),)),),
    )


def _shot(q: np.ndarray) -> np.ndarray:
    samples = np.zeros(12)
    samples[4::2] = q / np.sqrt(.001 / 2)
    return samples


def test_lab_calibration_averages_segment_variances_not_pooled_offsets() -> None:
    cfg = _small_config()
    processor = RealtimeClusterProcessor(cfg)
    processor.start_calibration()
    first = _shot(np.array([-1., 1., -1., 1.]))
    second = _shot(np.array([8., 12., 8., 12.]))
    processor.feed(first, first, 1000)
    processor.feed(second, second, 1000)
    coefficients = processor.finish_calibration(minimum_modes=8)
    assert coefficients["a"] == pytest.approx(1 / np.sqrt(2 * np.mean([1., 4.])))


def test_lab_mean_db_is_average_of_segment_db_not_db_of_mean_variance() -> None:
    cfg = _small_config()
    processor = RealtimeClusterProcessor(cfg)
    processor.start_calibration()
    vacuum = _shot(np.array([-1., 1., -1., 1.]))
    processor.feed(vacuum, vacuum, 1000)
    processor.finish_calibration(minimum_modes=4)
    q = np.array([0., 1., 3., 7.])
    shot1, shot2 = _shot(q), _shot(2*q)
    processor.feed(shot1, shot1, 1000)
    result = processor.feed(shot2, shot2, 1000)
    assert result is not None
    variance1 = np.var(q / np.sqrt(2))
    variance2 = np.var(2*q / np.sqrt(2))
    expected_db = np.mean(10*np.log10(np.array([variance1, variance2]) / .5))
    np.testing.assert_allclose(result.squeezing_db["single"], expected_db)
    np.testing.assert_array_equal(result.variance_counts["single"], 2)
    assert expected_db != pytest.approx(10*np.log10(np.mean([variance1, variance2]) / .5))


def test_collection_guards_preserve_cycle_and_exclude_boundary_samples() -> None:
    cfg = replace(_small_config(), collection_guard_seconds=.001,
                  collection_end_guard_samples=1)
    signal = np.arange(24, dtype=float)
    processor = RealtimeClusterProcessor(cfg)
    result = processor.feed(signal, signal, 1000)
    assert result is not None
    expected = np.concatenate([signal[5:11].reshape(-1, 2) @
                               np.array([1., -1.]) / np.sqrt(2000),
                               signal[17:23].reshape(-1, 2) @
                               np.array([1., -1.]) / np.sqrt(2000)])
    np.testing.assert_allclose(result.quadratures["a"], expected)
    assert processor.accepted_samples == 12


def test_lab_configuration_change_clears_segment_statistics_and_calibration() -> None:
    processor = RealtimeClusterProcessor(_small_config())
    q = _shot(np.array([0., 1., 3., 7.]))
    processor.feed(q, q, 1000)
    processor.configure(replace(_small_config(), measurement_frequency_hz=7e6))
    result = processor.feed(q, q, 1000)
    assert result is not None
    np.testing.assert_array_equal(result.variance_counts["single"], 1)
    assert not result.calibrated
