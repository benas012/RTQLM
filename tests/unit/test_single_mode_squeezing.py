from dataclasses import replace
from itertools import pairwise

import numpy as np
import pytest

from quantum_light_engine.post_processing import (
    RealtimeClusterProcessor,
    config_from_dict,
    config_to_dict,
    processing_nullifiers,
    single_mode_processing_config,
)


def _flat_config(**changes):
    return replace(single_mode_processing_config(8), window_mode="continuous",
                   collection_guard_seconds=0, collection_end_guard_samples=0, **changes)


def test_variance_and_amplitude_scaling_have_correct_db_and_full_windows():
    cfg = _flat_config()
    p = RealtimeClusterProcessor(cfg)
    q = np.tile([-1., 1.], 1000)
    a, b = np.repeat(q, 8), np.repeat(2*q, 8)
    p.start_calibration()
    p.feed(a, b, 1000)
    p.finish_calibration()
    result = p.feed(a * .5, b / np.sqrt(2), 1000)
    assert result is not None and result.calibrated
    assert set(result.squeezing_db) == {"CH1", "CH2"}
    for name, ratio in [("CH1", .25), ("CH2", .5)]:
        db = result.squeezing_db[name]
        assert np.isnan(db[:50]).all()
        assert np.isnan(db[-49:]).all()
        np.testing.assert_allclose(db[50:-49], 10*np.log10(ratio), atol=1e-11)
    assert result.variance_window_modes == 100


def test_single_selected_channel_calibrates_without_noise_on_unused_channel():
    cfg = _flat_config(single_channel="a")
    p = RealtimeClusterProcessor(cfg)
    a = np.repeat(np.tile([-1., 1.], 100), 8)
    p.start_calibration()
    p.feed(a, np.zeros_like(a), 1000)
    assert set(p.finish_calibration()) == {"a"}
    result = p.feed(a, np.zeros_like(a), 1000)
    assert result is not None and result.calibrated
    np.testing.assert_allclose(result.squeezing_db["CH1"][50:-49], 0, atol=1e-12)


def test_pooled_calibration_uses_full_accepted_trace_with_segment_offsets():
    cfg = replace(_flat_config(), window_mode="periodic", lock_seconds=.004,
                  collect_seconds=.004, mode_samples=2)
    p = RealtimeClusterProcessor(cfg)
    p.start_calibration()
    reference = np.array([0., 0., 0., 0., -1., -1., 1., 1.,
                          0., 0., 0., 0., 9., 9., 11., 11.])
    result = p.feed(reference, reference, 1000)
    assert result is not None
    expected = np.var(result.quadratures["a"])
    coeff = p.finish_calibration(minimum_modes=4)["a"]
    assert coeff == pytest.approx(1 / np.sqrt(2 * expected))
    assert coeff != pytest.approx(1 / np.sqrt(2 * .002))


def test_single_mode_packet_partition_does_not_change_reference_or_rolling_result():
    cfg = _flat_config()
    rng = np.random.default_rng(123)
    reference = rng.normal(size=(2, 10003))
    signal = rng.normal(size=(2, 17009))
    outputs = []
    for boundaries in [[0, 10003], [0, 1, 37, 1024, 8900, 10003]]:
        p = RealtimeClusterProcessor(cfg)
        p.start_calibration()
        for start, stop in pairwise(boundaries):
            p.feed(reference[0, start:stop], reference[1, start:stop], 1000)
        coeff = p.finish_calibration()
        stops = [0, 17009] if len(boundaries) == 2 else [0, 11, 122, 14001, 17009]
        for start, stop in pairwise(stops):
            result = p.feed(signal[0, start:stop], signal[1, start:stop], 1000)
        outputs.append((coeff, result))
    assert outputs[0][0] == pytest.approx(outputs[1][0])
    for channel in ("CH1", "CH2"):
        np.testing.assert_allclose(outputs[0][1].squeezing_db[channel],
                                   outputs[1][1].squeezing_db[channel], equal_nan=True)


def test_lab_mode_single_preset_has_pooled_calibration_and_exact_count_window():
    cfg = single_mode_processing_config()
    assert cfg.mode_samples is None
    assert cfg.mode_width_seconds == 308e-9
    assert cfg.mode_shape == "lab_gaussian_linear_cosine"
    assert cfg.calibration_estimator == "pooled"
    assert cfg.variance_window_modes == 100
    assert {spec.name for spec in processing_nullifiers(cfg)} == {"CH1", "CH2"}
    assert config_from_dict(config_to_dict(cfg)) == cfg


@pytest.mark.parametrize("data", [{"modeSamples": 2.5}, {"varianceWindowModes": True},
                                  {"varianceWindowModes": 6000, "historyModes": 5000},
                                  {"modeShape": "identity", "modeWidthSeconds": 0},
                                  {"analysisMode": "single", "varianceEstimator": "ensemble"},
                                  {"analysisMode": "single", "varianceEstimator": "lab_rolling"},
                                  {"modeSamples": 4, "explicitModeTemplate": [1., 2.]}])
def test_invalid_single_mode_settings_are_rejected(data):
    with pytest.raises(ValueError):
        config_from_dict(data)
