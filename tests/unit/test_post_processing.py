import numpy as np
import pytest

from quantum_light_engine.post_processing import (
    DEFAULT_CLUSTER_P,
    DEFAULT_CLUSTER_X,
    ClusterProcessingConfig,
    NullifierSpec,
    NullifierTerm,
    RealtimeClusterProcessor,
    build_gauss_lin_cos_mode,
    build_gauss_lin_mode_template,
    build_mode_template,
    cluster_squeezing_db,
    compute_nullifier,
    config_from_dict,
    config_to_dict,
    mode_sample_count,
    normalize_mode,
    nullifier_shot_noise_variance,
    rolling_variance,
    validate_mode_sampling,
)


@pytest.mark.parametrize(('rate', 'expected'), [(125_000_000, 40), (15_625_000, 5)])
def test_time_width_sets_flat_mode_duration_and_samples(rate: float, expected: int) -> None:
    config = ClusterProcessingConfig(mode_shape='identity', mode_samples=None,
                                    mode_width_seconds=320e-9, min_samples_per_mode=2,
                                    channel_b_delay_samples=0, history_modes=1000)
    values = np.ones(400)
    result = RealtimeClusterProcessor(config).feed(values, values, rate)
    assert result is not None
    assert result.mode_samples == expected
    assert result.mode_period_seconds == pytest.approx(expected / rate)
    np.testing.assert_allclose(result.quadratures['a'], np.sqrt(expected / rate))


def test_time_width_cannot_produce_a_mode_below_the_sampling_limit() -> None:
    config = ClusterProcessingConfig(mode_shape='identity', mode_width_seconds=308e-9,
                                    min_samples_per_mode=2)
    with pytest.raises(ValueError, match='only 0 samples'):
        RealtimeClusterProcessor(config).feed(np.ones(100), np.ones(100), 125_000_000 / 1024)


def test_lab_sample_count_is_stable_at_an_integer_grid_boundary() -> None:
    rate = 125_000_000 / 1024
    assert mode_sample_count(458.752 * 1e-6, rate, 'lab_gaussian_linear_cosine') == 56
    assert mode_sample_count(308e-9, 125_000_000, 'lab_gaussian_linear_cosine') == 38


def test_processor_extracts_one_normalized_quadrature_per_mode() -> None:
    signal = np.array([1.0, 2.0, 3.0, 4.0])
    processor = RealtimeClusterProcessor(ClusterProcessingConfig(
        explicit_mode_template=(0.5, 1.0), min_samples_per_mode=2,
    ))
    result = processor.feed(signal, signal, 4)
    assert result is not None and result.mode_samples == 2
    expected = np.array([0.625, 1.375]) / np.sqrt(0.25 * (0.5**2 + 1))
    np.testing.assert_allclose(result.quadratures["a"], expected)
    np.testing.assert_allclose(result.quadratures["b"], expected)


def test_default_cluster_nullifiers_match_lab_formula() -> None:
    a = np.array([1.0, 2.0, 4.0, 8.0])
    b = np.array([10.0, 20.0, 40.0, 80.0])

    x_nullifier = compute_nullifier({"a": a, "b": b}, DEFAULT_CLUSTER_X)
    p_nullifier = compute_nullifier({"a": a, "b": b}, DEFAULT_CLUSTER_P)

    # Larsen chapter 3.4.2.2: a0+b0-a1+b1 and a0+b0+a1-b1.
    np.testing.assert_allclose(x_nullifier, [29.0, 58.0, 116.0])
    np.testing.assert_allclose(p_nullifier, [-7.0, -14.0, -28.0])


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
    processor = RealtimeClusterProcessor(ClusterProcessingConfig(
        explicit_mode_template=(1., 0.), min_samples_per_mode=2,
        calibration_estimator="segment_mean", window_mode="periodic",
        lock_seconds=0, collect_seconds=1,
    ))
    processor.start_calibration()
    signal = np.array([-2., 0., 2., 0., -4., 0., 4., 0.])
    processor.feed(signal, signal, 4)
    coefficients = processor.finish_calibration(minimum_modes=4)
    assert coefficients["a"] == pytest.approx(1 / np.sqrt(5))
    assert coefficients["b"] == pytest.approx(1 / np.sqrt(5))
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
    assert set(result.squeezing_db) == {"cluster_x"}


def test_processing_configuration_round_trips_json_shape() -> None:
    config = ClusterProcessingConfig(
        channel_b_delay_samples=3,
        gate_absolute_threshold=0.7,
        explicit_mode_template=(1.0, -1.0),
        min_samples_per_mode=2,
    )
    assert config_from_dict(config_to_dict(config)) == config


def test_paper_mode_is_gaussian_linear_without_cosine_and_is_normalized() -> None:
    dt = 1e-9
    mode = build_gauss_lin_mode_template(247e-9, 2.7e6, dt, oversample=1)
    time = (np.arange(247) + 0.5) * dt - 247 * dt / 2
    expected = normalize_mode(time * np.exp(-0.5 * (2 * np.pi * 2.7e6 * time) ** 2), dt)
    np.testing.assert_allclose(mode, expected)
    assert np.sum(mode**2) * dt == pytest.approx(1.0)
    np.testing.assert_allclose(mode, -mode[::-1], atol=1e-9)


def test_paper_default_uses_only_the_measured_basis_preset() -> None:
    assert ClusterProcessingConfig().nullifiers == (DEFAULT_CLUSTER_X,)
    assert ClusterProcessingConfig(measurement_basis="p").nullifiers == (DEFAULT_CLUSTER_P,)
    p_config = config_from_dict({"measurementBasis": "p"})
    assert p_config.nullifiers == (DEFAULT_CLUSTER_P,)
    assert p_config.mode_width_seconds == 247e-9
    assert p_config.measurement_frequency_hz == 2.7e6


def test_duplicate_terms_are_combined_in_vacuum_reference() -> None:
    spec = NullifierSpec("combined", (NullifierTerm("a", weight=2),
                                      NullifierTerm("a", weight=-1),
                                      NullifierTerm("b", weight=3)))
    assert nullifier_shot_noise_variance(spec) == pytest.approx(5.0)


def _window_config(**overrides: object) -> ClusterProcessingConfig:
    data = config_to_dict(ClusterProcessingConfig(
        explicit_mode_template=(1.0, 1.0), min_samples_per_mode=2,
        nullifiers=(NullifierSpec("a", (NullifierTerm("a"),)),),
        variance_window_seconds=0.1,
    ))
    data.update(overrides)
    return config_from_dict(data)


def test_periodic_gate_discards_lock_samples_and_incomplete_boundary_modes() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.005, collectSeconds=0.005)
    a = np.arange(30.0)
    processor = RealtimeClusterProcessor(config)
    result = processor.feed(a, np.zeros_like(a), 1_000)
    assert result is not None
    scale = 1 / np.sqrt(2_000)
    np.testing.assert_allclose(result.quadratures["a"],
                               np.array([11, 15, 31, 35, 51, 55]) * scale)
    assert result.accepted_samples == 15
    assert result.skipped_samples == 15
    assert result.collection_windows == 3
    assert result.acquisition_state == "locking"
    assert np.count_nonzero(np.isnan(result.nullifiers["a"])) == 2


def test_periodic_modes_nullifiers_and_variances_never_join_separate_windows() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.004, collectSeconds=0.008)
    a = np.zeros(24)
    a[4:12] = 1
    a[16:24] = 100
    result = RealtimeClusterProcessor(config).feed(a, np.zeros_like(a), 1_000)
    assert result is not None
    np.testing.assert_allclose(result.variances["a"], [0, 0, 0, 0, np.nan, 0, 0, 0, 0])
    neighbor_config = _window_config(windowMode="periodic", lockSeconds=0.004,
                                     collectSeconds=0.008)
    neighbor_config = config_from_dict({**config_to_dict(neighbor_config), "nullifiers": [
        {"name": "neighbor", "terms": [{"source": "a", "offset": 0, "weight": 1},
                                        {"source": "a", "offset": 1, "weight": -1}]}]})
    neighbor = RealtimeClusterProcessor(neighbor_config).feed(a, np.zeros_like(a), 1_000)
    assert neighbor is not None
    np.testing.assert_allclose(neighbor.nullifiers["neighbor"], [0, 0, 0, np.nan, 0, 0, 0])


@pytest.mark.parametrize("window_mode", ["periodic", "triggered"])
def test_windowed_processing_is_packet_boundary_independent(window_mode: str) -> None:
    config = _window_config(windowMode=window_mode, lockSeconds=0.005, collectSeconds=0.005,
                            triggerLevelVolts=0.5, triggerHoldoffSeconds=0.01)
    a = np.tile(np.r_[np.zeros(5), np.ones(5)], 8)
    b = np.arange(a.size, dtype=float)
    whole = RealtimeClusterProcessor(config).feed(a, b, 1_000)
    assert whole is not None
    processor = RealtimeClusterProcessor(config)
    chunks = []
    last = None
    for start in range(0, a.size, 3):
        result = processor.feed(a[start:start + 3], b[start:start + 3], 1_000)
        if result is not None:
            chunks.append(result.quadratures["b"])
            last = result
    assert last is not None
    np.testing.assert_allclose(np.concatenate(chunks), whole.quadratures["b"])
    np.testing.assert_allclose(last.nullifiers["a"], whole.nullifiers["a"])
    np.testing.assert_allclose(last.variances["a"], whole.variances["a"])
    assert processor.accepted_samples == whole.accepted_samples


def test_trigger_crossing_at_packet_boundary_opens_delayed_collection_with_holdoff() -> None:
    config = _window_config(windowMode="triggered", collectSeconds=0.004,
                            triggerLevelVolts=0.5, triggerDelaySeconds=0.002,
                            triggerHoldoffSeconds=0.020)
    a = np.zeros(35)
    a[[5, 12, 25]] = 1
    b = np.arange(a.size, dtype=float)
    processor = RealtimeClusterProcessor(config)
    assert processor.feed(a[:5], b[:5], 1_000) is None
    result = processor.feed(a[5:], b[5:], 1_000)
    assert result is not None
    np.testing.assert_allclose(result.quadratures["b"],
                               np.array([15, 19, 55, 59]) / np.sqrt(2_000))
    assert result.collection_windows == 2
    assert result.accepted_samples == 8
    assert result.skipped_samples == 27
    assert result.acquisition_state == "waiting for trigger"


def test_falling_trigger_uses_selected_channel_and_hysteresis_arming() -> None:
    config = _window_config(windowMode="triggered", triggerChannel="b", triggerEdge="falling",
                            triggerLevelVolts=0, triggerHysteresisVolts=1,
                            triggerHoldoffSeconds=0, collectSeconds=0.002)
    b = np.array([2, -0.1, 0.2, -0.2, 2, -0.3, -0.3, -0.3])
    result = RealtimeClusterProcessor(config).feed(np.arange(8.0), b, 1_000)
    assert result is not None
    assert result.collection_windows == 2  # noise crossing at index 3 is unarmed
    np.testing.assert_allclose(result.quadratures["a"], np.array([3, 11]) / np.sqrt(2_000))


def test_trigger_requires_observed_crossing_and_does_not_start_on_high_initial_value() -> None:
    processor = RealtimeClusterProcessor(_window_config(windowMode="triggered"))
    assert processor.feed(np.ones(20), np.ones(20), 1_000) is None
    assert processor.acquisition_state == "waiting for trigger"
    assert processor.skipped_samples == 20


def test_invalid_trigger_sample_disarms_hysteresis_independent_of_packet_boundaries() -> None:
    config = _window_config(windowMode="triggered", triggerLevelVolts=0,
                            triggerHysteresisVolts=1, collectSeconds=0.003,
                            triggerHoldoffSeconds=0)
    a = np.array([-2, np.nan, -0.5, 0.5, 2, 2])
    whole = RealtimeClusterProcessor(config)
    assert whole.feed(a, np.zeros_like(a), 1_000) is None
    split = RealtimeClusterProcessor(config)
    assert split.feed(a[:2], np.zeros(2), 1_000) is None
    assert split.feed(a[2:], np.zeros(4), 1_000) is None
    assert split.accepted_samples == whole.accepted_samples == 0


def test_nullifier_index_start_matches_first_source_mode_index() -> None:
    processor = RealtimeClusterProcessor(_window_config(nullifiers=[
        {"name": "pair", "terms": [{"source": "a"}, {"source": "a", "offset": 1}]}]))
    result = processor.feed(np.arange(8.0), np.arange(8.0), 1_000)
    assert result is not None
    assert result.mode_index_start == 0
    assert result.nullifiers["pair"].size == 3


@pytest.mark.parametrize("delay", [5, -5])
def test_channel_delay_longer_than_each_packet_is_retained_until_fully_skipped(delay: int) -> None:
    config = _window_config(channelBDelaySamples=delay)
    a, b = np.arange(20.0), 100 + np.arange(20.0)
    expected = RealtimeClusterProcessor(config).feed(a, b, 1_000)
    assert expected is not None
    processor = RealtimeClusterProcessor(config)
    chunks_a, chunks_b = [], []
    for start in range(0, 20, 2):
        result = processor.feed(a[start:start + 2], b[start:start + 2], 1_000)
        if result is not None:
            chunks_a.append(result.quadratures["a"])
            chunks_b.append(result.quadratures["b"])
    np.testing.assert_allclose(np.concatenate(chunks_a), expected.quadratures["a"])
    np.testing.assert_allclose(np.concatenate(chunks_b), expected.quadratures["b"])


def test_delay_alignment_never_imports_lock_samples_into_collecting_modes() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.004, collectSeconds=0.008,
                            channelBDelaySamples=3)
    a = np.ones(24)
    b = np.full(24, 1_000_000.0)
    b[4:12], b[16:24] = 1, 1
    processor = RealtimeClusterProcessor(config)
    values = []
    for start in range(0, 24, 2):
        result = processor.feed(a[start:start + 2], b[start:start + 2], 1_000)
        if result is not None:
            values.append(result.quadratures["b"])
    np.testing.assert_allclose(np.concatenate(values), np.full(4, 2 / np.sqrt(2_000)))


def test_shot_noise_calibration_only_counts_complete_collecting_modes() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.005, collectSeconds=0.005)
    processor = RealtimeClusterProcessor(config)
    processor.start_calibration()
    rng = np.random.default_rng(6)
    a = np.full(1_000, 1e9)
    b = a.copy()
    mask = np.arange(1_000) % 10 >= 5
    a[mask], b[mask] = rng.standard_normal(500), rng.standard_normal(500)
    result = processor.feed(a, b, 1_000)
    assert result is not None and result.calibrating
    assert processor.calibration_mode_counts == {"a": 200, "b": 200}
    assert np.max(np.abs(result.quadratures["a"])) < 1
    assert set(processor.finish_calibration()) == {"a", "b"}


@pytest.mark.parametrize("data", [
    {"collectSeconds": float("nan")}, {"triggerLevelVolts": float("inf")},
    {"modeWidthSeconds": float("inf")}, {"minSamplesPerMode": 2.5},
    {"channelBDelaySamples": True}, {"windowMode": "unknown"}, {"triggerChannel": "external"},
    {"triggerEdge": "both"}, {"measurementBasis": "xp"}, {"explicitModeTemplate": [1, "nan"]},
    {"nullifiers": []}, {"nullifiers": [{"name": "empty", "terms": []}]},
    {"nullifiers": [{"name": "bad", "terms": [{"source": "typo"}]}]},
    {"nullifiers": [{"name": "bad", "terms": [{"source": "a", "offset": 0.3}]}]},
    {"nullifiers": [{"name": "bad", "terms": [{"source": "a", "weight": "nan"}]}]},
    {"nullifiers": [{"name": "bad", "terms": ["wrong"]}]},
    {"nullifiers": [{"terms": [{"source": "a"}]}]},
    {"nullifiers": [{"name": None, "terms": [{"source": "a"}]}]},
    {"nullifiers": [{"name": 12, "terms": [{"source": "a"}]}]},
    {"nullifiers": [{"name": "bad", "terms": [{"weight": 2}]}]},
    {"nullifiers": [{"name": "bad", "terms": [{"source": None}]}]},
])
def test_invalid_configuration_is_rejected_before_streaming(data: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        config_from_dict(data)


def test_window_duration_shorter_than_one_adc_sample_is_rejected() -> None:
    processor = RealtimeClusterProcessor(_window_config(windowMode="periodic", collectSeconds=1e-8))
    with pytest.raises(ValueError, match="one ADC sample"):
        processor.feed(np.ones(10), np.ones(10), 1_000)


def test_ensemble_variance_uses_repeated_aligned_mode_indices_not_temporal_neighbors() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.002, collectSeconds=0.006,
                            varianceEstimator="ensemble")
    first, second = np.array([0, 0, 1, 1, 2, 2]), np.array([2, 2, 5, 5, 8, 8])
    a = np.r_[1e9, 1e9, first, 1e9, 1e9, second]
    processor = RealtimeClusterProcessor(config)
    one_shot = processor.feed(a[:8], np.zeros(8), 1_000)
    assert one_shot is not None
    np.testing.assert_array_equal(one_shot.variance_counts["a"], [1, 1, 1])
    assert np.all(np.isnan(one_shot.variances["a"]))
    result = processor.feed(a[8:], np.zeros(8), 1_000)
    assert result is not None
    np.testing.assert_array_equal(result.variance_counts["a"], [2, 2, 2])
    samples = np.stack([first.reshape(3, 2).sum(axis=1),
                        second.reshape(3, 2).sum(axis=1)]) / np.sqrt(2_000)
    np.testing.assert_allclose(result.variances["a"], np.var(samples, axis=0))
    assert result.variance_estimator == "ensemble"
    assert "ensemble across aligned collection windows" in result.status


def test_ensemble_does_not_duplicate_observations_when_windows_span_packets() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.003, collectSeconds=0.009,
                            varianceEstimator="ensemble")
    a = np.arange(120.0)
    whole = RealtimeClusterProcessor(config).feed(a, -a, 1_000)
    assert whole is not None
    split = RealtimeClusterProcessor(config)
    last = None
    for start in range(0, a.size, 3):
        result = split.feed(a[start:start + 3], -a[start:start + 3], 1_000)
        if result is not None:
            last = result
    assert last is not None
    np.testing.assert_array_equal(last.variance_counts["a"], [10, 10, 10, 10])
    np.testing.assert_allclose(last.variances["a"], whole.variances["a"])


def test_ensemble_rejected_mode_keeps_index_alignment_and_excludes_only_that_observation() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.002, collectSeconds=0.006,
                            varianceEstimator="ensemble")
    a = np.array([0, 0, 1, 1, np.nan, 2, 3, 3, 0, 0, 2, 2, 4, 4, 6, 6])
    result = RealtimeClusterProcessor(config).feed(a, np.zeros_like(a), 1_000)
    assert result is not None
    np.testing.assert_array_equal(result.variance_counts["a"], [2, 1, 2])
    assert np.isfinite(result.variances["a"][[0, 2]]).all()
    assert np.isnan(result.variances["a"][1])


def test_ensemble_accumulator_retains_a_stable_bounded_prefix_across_longer_windows() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.002, collectSeconds=0.300,
                            varianceEstimator="ensemble", historyModes=100)
    a = np.arange(604.0)
    result = RealtimeClusterProcessor(config).feed(a, -a, 1_000)
    assert result is not None
    assert result.variances["a"].size == 100
    np.testing.assert_array_equal(result.variance_counts["a"], np.full(100, 2))
    assert "first 100 temporal modes per collection retained" in result.status


def test_ensemble_future_terms_never_join_neighboring_collection_windows() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.002, collectSeconds=0.006,
                            varianceEstimator="ensemble", nullifiers=[{
                                "name": "pair", "terms": [
                                    {"source": "a"}, {"source": "a", "offset": 1}],
                            }])
    a = np.r_[0, 0, np.ones(6), 0, 0, np.ones(6) * 10]
    result = RealtimeClusterProcessor(config).feed(a, np.zeros_like(a), 1_000)
    assert result is not None
    assert result.variances["pair"].size == 2
    np.testing.assert_array_equal(result.variance_counts["pair"], [2, 2])


def test_discontinuity_invalidates_ensemble_repetitions_and_restarts_gate() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.002, collectSeconds=0.006,
                            varianceEstimator="ensemble")
    processor = RealtimeClusterProcessor(config)
    before = processor.feed(np.arange(16.0), np.arange(16.0), 1_000)
    assert before is not None
    np.testing.assert_array_equal(before.variance_counts["a"], [2, 2, 2])
    after = processor.feed(np.arange(8.0), np.arange(8.0), 1_000, discontinuity=True)
    assert after is not None
    np.testing.assert_array_equal(after.variance_counts["a"], [1, 1, 1])
    assert np.isnan(after.variances["a"]).all()


def test_continuous_ensemble_is_one_observation_and_waits_for_repeated_windows() -> None:
    processor = RealtimeClusterProcessor(_window_config(varianceEstimator="ensemble"))
    result = processor.feed(np.arange(100.0), np.arange(100.0), 1_000)
    assert result is not None
    assert np.isnan(result.variances["a"]).all()
    assert "waiting for at least 2 observations" in result.status


def test_ensemble_configuration_round_trips_and_rejects_unknown_estimator() -> None:
    config = _window_config(varianceEstimator="ensemble")
    assert config_from_dict(config_to_dict(config)) == config
    with pytest.raises(ValueError, match="variance_estimator"):
        _window_config(varianceEstimator="median")


def test_ensemble_uses_calibrated_quadratures_and_matching_shot_reference() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.002, collectSeconds=0.006,
                            varianceEstimator="ensemble")
    processor = RealtimeClusterProcessor(config)
    processor.start_calibration()
    rng = np.random.default_rng(382)
    processor.feed(rng.standard_normal(800), rng.standard_normal(800), 1_000)
    coefficient = processor.finish_calibration()["a"]
    a = np.r_[0, 0, np.ones(6), 0, 0, np.ones(6) * 2]
    result = processor.feed(a, np.zeros_like(a), 1_000)
    assert result is not None and result.calibrated
    expected = np.full(3, (2 / np.sqrt(2_000) * coefficient) ** 2 / 4)
    np.testing.assert_allclose(result.variances["a"], expected)
    np.testing.assert_allclose(result.squeezing_db["a"], 10 * np.log10(expected / 0.5))
    assert result.variance_index_starts == {"a": 0}


def test_custom_ensemble_start_reports_local_variance_index_separately_from_history() -> None:
    config = _window_config(windowMode="periodic", lockSeconds=0.002, collectSeconds=0.008,
                            varianceEstimator="ensemble", nullifiers=[{
                                "name": "late", "start": 2,
                                "terms": [{"source": "a"}],
                            }])
    result = RealtimeClusterProcessor(config).feed(np.arange(30.0), np.arange(30.0), 1_000)
    assert result is not None
    assert result.variance_index_starts == {"late": 2}
    assert result.variances["late"].size == 2
    np.testing.assert_array_equal(result.variance_counts["late"], [3, 3])
