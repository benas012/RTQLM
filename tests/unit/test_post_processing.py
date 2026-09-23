import numpy as np
import pytest

from quantum_light_engine.post_processing import (
    DEFAULT_CLUSTER_P,
    DEFAULT_CLUSTER_X,
    ClusterProcessingConfig,
    NullifierSpec,
    NullifierTerm,
    RealtimeClusterProcessor,
    apply_long_mode_to_trace,
    build_gauss_lin_cos_mode,
    build_mode_template,
    cluster_squeezing_db,
    compute_nullifier,
    config_from_dict,
    config_to_dict,
    normalize_mode,
    nullifier_shot_noise_variance,
    rolling_variance,
    shot_noise_cluster_coefficient,
    tile_mode,
    validate_mode_sampling,
)


def test_apply_long_mode_extracts_one_quadrature_per_mode() -> None:
    signal = np.array([1.0, 2.0, 3.0, 4.0])
    short_mode = np.array([0.5, 1.0])
    long_mode = tile_mode(short_mode, signal.size)

    modulated, quadratures, points_per_mode = apply_long_mode_to_trace(
        signal, long_mode, short_mode, dt_seconds=0.25
    )

    assert points_per_mode == 2
    np.testing.assert_allclose(modulated, [0.5, 2.0, 1.5, 4.0])
    np.testing.assert_allclose(quadratures, [0.625, 1.375])


def test_default_cluster_nullifiers_match_lab_formula() -> None:
    a = np.array([1.0, 2.0, 4.0, 8.0])
    b = np.array([10.0, 20.0, 40.0, 80.0])

    x_nullifier = compute_nullifier({"a": a, "b": b}, DEFAULT_CLUSTER_X)
    p_nullifier = compute_nullifier({"a": a, "b": b}, DEFAULT_CLUSTER_P)

    np.testing.assert_allclose(x_nullifier, [-7.0, -14.0, -28.0])
    np.testing.assert_allclose(p_nullifier, [58.0, 116.0])


def test_custom_nullifier_spec_supports_named_sources_offsets_and_weights() -> None:
    spec = NullifierSpec(
        name="difference",
        terms=(NullifierTerm("left", 0, 1.0), NullifierTerm("right", 1, -2.0)),
    )

    result = compute_nullifier(
        {
            "left": np.array([1.0, 2.0, 3.0]),
            "right": np.array([10.0, 20.0, 30.0]),
        },
        spec,
    )

    np.testing.assert_allclose(result, [-39.0, -58.0])


def test_rolling_variance_uses_centered_windows() -> None:
    result = rolling_variance(np.array([1.0, 2.0, 4.0, 8.0]), 4)

    np.testing.assert_allclose(result, [0.25, 14 / 9, 7.1875, 56 / 9])


def test_mode_template_is_normalized_to_continuous_time() -> None:
    dt = 1e-9
    mode = build_mode_template(40e-9, 6e6, dt, oversample=10)
    normalized = normalize_mode(mode, dt)

    assert mode.ndim == 1
    assert len(mode) == 40
    assert np.sum(mode**2) * dt == pytest.approx(1.0)
    assert np.sum(normalized**2) * dt == pytest.approx(1.0)


def test_shot_noise_coefficient_and_cluster_squeezing() -> None:
    coefficient = shot_noise_cluster_coefficient([np.array([-1.0, 1.0]), np.array([-2.0, 2.0])])

    assert coefficient == pytest.approx(1 / np.sqrt(5))
    np.testing.assert_allclose(cluster_squeezing_db(np.array([2.0])), [0.0])


def test_mode_carrier_uses_frequency_in_hertz() -> None:
    times = np.array([-0.1e-6, 0.0, 0.1e-6])
    mode = build_gauss_lin_cos_mode(1e-6, 1e6, times)
    angular_frequency = 2 * np.pi * 1e6
    expected = (
        times * np.cos(angular_frequency * times) * np.exp(-0.5 * angular_frequency**2 * times**2)
    )
    np.testing.assert_allclose(mode, expected)


def test_sampling_validation_rejects_aliasing_and_coarse_modes() -> None:
    with pytest.raises(ValueError, match="Nyquist"):
        validate_mode_sampling(0.308e-6, 6e6, 1.953125e6)
    with pytest.raises(ValueError, match="only 5 samples"):
        validate_mode_sampling(0.308e-6, 6e6, 15.625e6, min_samples_per_mode=16)
    assert validate_mode_sampling(0.308e-6, 6e6, 125e6) == 38


def test_odd_rolling_window_uses_the_requested_number_of_points() -> None:
    result = rolling_variance(np.array([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
    assert result[2] == pytest.approx(2 / 3)


def test_rolling_variance_ignores_rejected_modes() -> None:
    result = rolling_variance(np.array([1.0, np.nan, 3.0, 5.0]), 3)
    assert result[1] == pytest.approx(1.0)
    assert result[2] == pytest.approx(1.0)


def test_custom_nullifier_has_matching_shot_noise_reference() -> None:
    spec = NullifierSpec(
        "weighted",
        (NullifierTerm("a", weight=1.0), NullifierTerm("b", weight=-2.0)),
    )
    assert nullifier_shot_noise_variance(spec) == pytest.approx(2.5)


def test_streaming_mode_extraction_is_independent_of_packet_boundaries() -> None:
    config = ClusterProcessingConfig(
        explicit_mode_template=(1.0, -1.0, 1.0, -1.0),
        min_samples_per_mode=4,
        variance_window_seconds=0.02,
    )
    a = np.arange(100, dtype=np.float64)
    b = -a
    whole = RealtimeClusterProcessor(config).feed(a, b, 1_000.0)
    assert whole is not None

    chunked_processor = RealtimeClusterProcessor(config)
    chunks: list[np.ndarray] = []
    start = 0
    for size in (7, 13, 2, 29, 11, 38):
        result = chunked_processor.feed(a[start : start + size], b[start : start + size], 1_000.0)
        start += size
        if result is not None:
            chunks.append(result.quadratures["a"])
    np.testing.assert_allclose(np.concatenate(chunks), whole.quadratures["a"])


def test_discontinuity_does_not_join_modes_across_a_gap() -> None:
    config = ClusterProcessingConfig(
        explicit_mode_template=(1.0, 1.0, -1.0, -1.0),
        min_samples_per_mode=4,
    )
    processor = RealtimeClusterProcessor(config)
    assert processor.feed(np.ones(3), np.ones(3), 1_000.0) is None
    after_gap = processor.feed(np.arange(5.0), np.arange(5.0), 1_000.0, discontinuity=True)
    expected = RealtimeClusterProcessor(config).feed(np.arange(5.0), np.arange(5.0), 1_000.0)
    assert after_gap is not None and expected is not None
    np.testing.assert_allclose(after_gap.quadratures["a"], expected.quadratures["a"])


def test_channel_delay_aligns_samples_before_mode_extraction() -> None:
    delayed = ClusterProcessingConfig(
        explicit_mode_template=(1.0, -1.0),
        min_samples_per_mode=2,
        channel_b_delay_samples=2,
    )
    aligned = ClusterProcessingConfig(
        explicit_mode_template=(1.0, -1.0),
        min_samples_per_mode=2,
    )
    a = np.arange(10.0)
    b = 100 + np.arange(10.0)
    result = RealtimeClusterProcessor(delayed).feed(a, b, 1_000.0)
    expected = RealtimeClusterProcessor(aligned).feed(a[:8], b[2:], 1_000.0)
    assert result is not None and expected is not None
    np.testing.assert_allclose(result.quadratures["a"], expected.quadratures["a"])
    np.testing.assert_allclose(result.quadratures["b"], expected.quadratures["b"])


def test_absolute_gate_rejects_lock_or_noise_modes() -> None:
    config = ClusterProcessingConfig(
        explicit_mode_template=(1.0, -1.0),
        min_samples_per_mode=2,
        gate_absolute_threshold=1.0,
    )
    result = RealtimeClusterProcessor(config).feed(
        np.array([0.1, 0.2, 2.0, 0.1]),
        np.array([0.2, 0.1, 0.2, 0.1]),
        1_000.0,
    )
    assert result is not None
    assert result.rejected_modes == 1
    assert np.isfinite(result.quadratures["a"][0])
    assert np.isnan(result.quadratures["a"][1])


def test_shot_noise_calibration_normalizes_each_channel_to_half_variance() -> None:
    rng = np.random.default_rng(1234)
    config = ClusterProcessingConfig(
        explicit_mode_template=(1.0, -1.0, -1.0, 1.0),
        min_samples_per_mode=4,
        variance_window_seconds=0.2,
    )
    processor = RealtimeClusterProcessor(config)
    processor.start_calibration()
    calibration = processor.feed(rng.standard_normal(40_000), rng.standard_normal(40_000), 1_000.0)
    assert calibration is not None and calibration.calibrating
    coefficients = processor.finish_calibration()
    assert set(coefficients) == {"a", "b"}

    result = processor.feed(rng.standard_normal(40_000), rng.standard_normal(40_000), 1_000.0)
    assert result is not None and result.calibrated
    assert np.var(result.quadratures["a"]) == pytest.approx(0.5, rel=0.06)
    assert np.var(result.quadratures["b"]) == pytest.approx(0.5, rel=0.06)
    assert set(result.squeezing_db) == {"cluster_x", "cluster_p"}


def test_processing_configuration_round_trips_json_shape() -> None:
    config = ClusterProcessingConfig(
        channel_b_delay_samples=3,
        gate_absolute_threshold=0.7,
        explicit_mode_template=(1.0, -1.0),
        min_samples_per_mode=2,
    )
    assert config_from_dict(config_to_dict(config)) == config
