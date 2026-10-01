from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from apps.native_receiver import settings
from apps.native_receiver.nullifier_acquisition import (
    acquisition_description,
    match_builtin_basis,
)
from apps.native_receiver.nullifier_renderer import finite_runs, select_nullifier_traces
from quantum_light_engine.post_processing import (
    DEFAULT_CLUSTER_P,
    DEFAULT_CLUSTER_X,
    ClusterProcessingConfig,
    ClusterProcessingResult,
    NullifierSpec,
    NullifierTerm,
    config_to_dict,
)


def test_chart_keeps_collection_gaps_and_rejected_modes_disconnected() -> None:
    runs = finite_runs([1.0, 2.0, np.nan, np.nan, 3.0, np.inf, 4.0, 5.0])
    assert [positions.tolist() for positions, _values in runs] == [[0, 1], [4], [6, 7]]
    assert [values.tolist() for _positions, values in runs] == [[1.0, 2.0], [3.0], [4.0, 5.0]]


def test_ensemble_chart_downsampling_covers_full_window_and_preserves_gaps() -> None:
    values = np.arange(25_000, dtype=np.float64)
    values[12_345] = np.nan
    runs = finite_runs(values, max_points=2000)
    assert len(runs) == 2
    assert runs[0][0][0] == 0
    assert runs[0][0][-1] == 12_344
    assert runs[1][0][0] == 12_346
    assert runs[1][0][-1] == 24_999
    assert sum(len(indices) for indices, _values in runs) < 2100


def test_uncalibrated_chart_displays_variance_and_calibrated_chart_displays_db() -> None:
    result = ClusterProcessingResult(
        mode_period_seconds=1e-6,
        mode_index_start=0,
        quadratures={},
        nullifiers={"cluster_x": np.array([-2.0, 2.0])},
        variances={"cluster_x": np.array([4.0, 4.0])},
        squeezing_db={"cluster_x": np.array([-3.0, -3.0])},
        shot_noise_coefficients={},
        calibrated=False,
        calibrating=False,
        rejected_modes=0,
        status="testing",
    )
    traces, unit, logarithmic = select_nullifier_traces(result)
    assert traces is result.variances
    assert unit == "rolling variance across modes"
    assert not logarithmic
    calibrated = replace(result, calibrated=True)
    traces, unit, logarithmic = select_nullifier_traces(calibrated)
    assert traces is calibrated.squeezing_db
    assert unit == "dB relative to shot noise"
    assert logarithmic
    assert select_nullifier_traces(calibrated, "Amplitude")[0] is calibrated.nullifiers


def test_basis_change_switches_builtin_formula_and_preserves_custom_formulas() -> None:
    config = ClusterProcessingConfig(nullifiers=(DEFAULT_CLUSTER_X,))
    assert match_builtin_basis(replace(config, measurement_basis="p")).nullifiers == (
        DEFAULT_CLUSTER_P,
    )
    assert match_builtin_basis(replace(config, measurement_basis="x")).nullifiers == (
        DEFAULT_CLUSTER_X,
    )
    custom = replace(
        config,
        measurement_basis="p",
        nullifiers=(NullifierSpec("custom", (NullifierTerm("a", 2, -3.0),)),),
    )
    assert match_builtin_basis(custom) is custom


def test_receiver_defaults_to_requested_lock_collect_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CLUSTER_CONFIG_PATH", tmp_path / "missing.json")
    config = settings.load_cluster_config()
    assert config.window_mode == "periodic"
    assert config.lock_seconds == pytest.approx(0.005)
    assert config.collect_seconds == pytest.approx(0.005)
    assert config.nullifiers == (DEFAULT_CLUSTER_X,)
    assert config.variance_estimator == "ensemble"
    assert config.history_modes == 25_000
    assert "5 ms lock" in acquisition_description(config)
    assert "5 ms collect" in acquisition_description(config)


def test_saved_custom_timing_and_formulas_survive_receiver_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CLUSTER_CONFIG_PATH", tmp_path / "cluster.json")
    config = ClusterProcessingConfig(
        window_mode="triggered",
        collect_seconds=0.002,
        trigger_channel="b",
        trigger_edge="falling",
        trigger_level_volts=0.12,
        trigger_delay_seconds=0.001,
        measurement_basis="p",
        nullifiers=(NullifierSpec("custom", (NullifierTerm("b", 3, 2.0),)),),
    )
    settings.save_cluster_config(config)
    assert settings.load_cluster_config() == config
    description = acquisition_description(config)
    assert "CH2 falling at 0.12 V" in description
    assert "delay 1 ms, collect 2 ms" in description


def _legacy_data() -> dict[str, object]:
    config = ClusterProcessingConfig(
        mode_width_seconds=0.308e-6,
        measurement_frequency_hz=6e6,
        nullifiers=(
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
        ),
    )
    data = config_to_dict(config)
    for key in list(data):
        if key.startswith("trigger") or key in {
            "modeShape",
            "measurementBasis",
            "windowMode",
            "lockSeconds",
            "collectSeconds",
        }:
            data.pop(key)
    data["varianceWindowSeconds"] = 0.000333333
    return data


def test_known_legacy_preset_migrates_without_changing_saved_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cluster.json"
    source = json.dumps(_legacy_data())
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(settings, "CLUSTER_CONFIG_PATH", path)
    assert settings.load_cluster_config() == ClusterProcessingConfig(
        window_mode="periodic", variance_estimator="ensemble", history_modes=25_000
    )
    assert path.read_text(encoding="utf-8") == source


def test_empty_receiver_config_uses_full_paper_collection_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cluster.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "CLUSTER_CONFIG_PATH", path)
    config = settings.load_cluster_config()
    assert config.window_mode == "periodic"
    assert config.variance_estimator == "ensemble"
    assert config.history_modes == 25_000


def test_legacy_custom_settings_keep_their_mode_and_formulas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cluster.json"
    data = _legacy_data()
    data["channelBDelaySamples"] = 7
    data["historyModes"] = 1234
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(settings, "CLUSTER_CONFIG_PATH", path)
    config = settings.load_cluster_config()
    assert config.channel_b_delay_samples == 7
    assert config.history_modes == 1234
    assert config.mode_width_seconds == pytest.approx(0.308e-6)
    assert config.mode_shape == "gaussian_linear_cosine"
    assert len(config.nullifiers) == 2
    assert config.nullifiers[1].start == 1
    assert config.window_mode == "periodic"
