from dataclasses import replace

import numpy as np
import pytest

from quantum_light_engine.post_processing import (
    RealtimeClusterProcessor,
    mode_sample_bounds,
    resolve_mode_schedule,
    single_mode_processing_config,
)

RATE = 125_000_000


def config(**changes):
    defaults = dict(mode_shape="identity", channel_b_delay_samples=0, history_modes=25000,
                    window_mode="continuous", collection_guard_seconds=0,
                    collection_end_guard_samples=0)
    return replace(single_mode_processing_config(), **(defaults | changes))


def test_308ns_modes_follow_38_39_boundaries_across_arbitrary_packets():
    count = 20_000
    boundaries = (np.arange(count + 1, dtype=np.int64) * 77) // 2
    signal = np.arange(boundaries[-1], dtype=np.float64) / boundaries[-1]
    lengths = np.diff(boundaries)
    expected = (boundaries[:-1] + (lengths - 1) / 2) * np.sqrt(lengths / RATE) / boundaries[-1]
    processor = RealtimeClusterProcessor(config())
    actual = []
    for chunk in np.split(signal, [1, 38, 77, 512, 16383, 50001]):
        result = processor.feed(chunk, chunk, RATE)
        if result is not None:
            actual.extend(result.quadratures["a"])
            assert (result.mode_samples, result.mode_samples_max) == (38, 39)
            assert result.mode_period_seconds == pytest.approx(308e-9)
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-16)
    assert boundaries[-1] / RATE == pytest.approx(count * 308e-9)


@pytest.mark.parametrize("shape", ["identity", "gaussian_linear", "lab_gaussian_linear_cosine"])
def test_both_lengths_have_equal_discrete_white_noise_gain(shape):
    processor = RealtimeClusterProcessor(config(mode_shape=shape))
    processor.feed(np.zeros(77), np.zeros(77), RATE)
    assert set(processor._mode_templates) == {38, 39}
    for weights in processor._mode_templates.values():
        assert np.sum(weights**2) / RATE == pytest.approx(1, abs=1e-13)


def test_fractional_bins_keep_reference_normalization_and_half_amplitude_db():
    rng = np.random.default_rng(807)
    signal = rng.normal(size=77 * 1000)
    processor = RealtimeClusterProcessor(config())
    processor.start_calibration()
    processor.feed(signal, signal, RATE)
    processor.finish_calibration()
    result = processor.feed(signal / 2, signal / 2, RATE)
    assert result is not None
    reference = RealtimeClusterProcessor(config()).feed(signal, signal, RATE)
    assert reference is not None
    # Compare the same complete rolling windows before and after normalization.
    coefficient = processor.shot_noise_coefficients["a"]
    expected = 10 * np.log10(reference.variances["CH1"] * coefficient**2 * .25 / .5)
    np.testing.assert_allclose(result.squeezing_db["CH1"], expected, equal_nan=True, atol=1e-9)


def test_gap_resets_the_pattern_and_fixed_sample_configuration_remains_fixed():
    processor = RealtimeClusterProcessor(config())
    first = processor.feed(np.ones(38), np.ones(38), RATE)
    assert first is not None
    assert processor.feed(np.ones(38), np.ones(38), RATE) is None
    restarted = processor.feed(np.ones(38), np.ones(38), RATE, discontinuity=True)
    assert restarted is not None
    fixed = RealtimeClusterProcessor(config(mode_samples=38))
    result = fixed.feed(np.ones(76), np.ones(76), RATE)
    assert result is not None and len(result.quadratures["a"]) == 2
    assert result.mode_samples == result.mode_samples_max == 38
    assert result.mode_period_seconds == pytest.approx(304e-9)


def test_integer_roundoff_does_not_invent_alternating_bins():
    assert mode_sample_bounds(320e-9, RATE) == (40, 40)
    assert mode_sample_bounds(308e-9, RATE) == (38, 39)


def test_fractional_effective_rate_keeps_the_integer_phase_at_large_indices():
    schedule = resolve_mode_schedule(7.36e-6, RATE / 24)
    np.testing.assert_array_equal(np.diff(schedule.boundaries(10**15, 115)), [38, 39, 38])


@pytest.mark.parametrize(("width_us", "pattern"), [
    (0.304, [38]), (0.308, [38, 39]), (0.310, [38, 39, 39, 39]),
])
def test_schedule_resolves_receiver_width_and_reuses_it_until_rate_changes(width_us, pattern):
    settings = config(mode_width_seconds=width_us * 1e-6)
    processor = RealtimeClusterProcessor()
    processor.configure(settings, sample_rate_hz=RATE)
    schedule = resolve_mode_schedule(settings.mode_width_seconds, RATE)
    assert processor._mode_schedule is schedule
    np.testing.assert_array_equal(np.diff(schedule.boundaries(0, sum(pattern) * 3)), pattern * 3)
    data = np.zeros(100)
    processor.feed(data, data, RATE)
    assert processor._mode_schedule is schedule
    processor.feed(data, data, RATE // 2)
    assert processor._mode_schedule is resolve_mode_schedule(settings.mode_width_seconds, RATE // 2)
    assert processor._mode_schedule.step == schedule.step / 2


@pytest.mark.parametrize("with_high", [False, True])
def test_e1_low_bins_keep_phase_across_packets_and_restart_after_high(with_high):
    processor = RealtimeClusterProcessor(config())
    data = np.arange(77, dtype=np.float64)
    low = np.zeros(77, dtype=bool)
    assert processor.feed(data[:20], data[:20], RATE, e1_high=low[:20]) is None
    if with_high:
        high = np.ones(1, dtype=bool)
        assert processor.feed(data[:1], data[:1], RATE, e1_high=high) is None
        expected_bins = [data[20:58]]
    else:
        expected_bins = [data[:38], data[38:]]
    result = processor.feed(data[20:], data[20:], RATE, e1_high=low[20:])
    assert result is not None
    expected = [bin_.sum() / np.sqrt(RATE * bin_.size) for bin_ in expected_bins]
    for actual in result.quadratures.values():
        np.testing.assert_allclose(actual, expected, rtol=2e-11, atol=1e-16)
    assert processor.skipped_samples == int(with_high)
