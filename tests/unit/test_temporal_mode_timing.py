from dataclasses import replace

import numpy as np
import pytest

from quantum_light_engine.post_processing import (
    RealtimeClusterProcessor,
    mode_sample_bounds,
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
