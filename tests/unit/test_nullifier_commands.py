from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest
from pydantic import ValidationError

from quantum_light_engine.devices.nullifiers import DeviceNullifierProcessor, NullifierCommand
from quantum_light_engine.post_processing import LAB_CLUSTER_P, LAB_CLUSTER_X, config_to_dict
from redpitaya_acquisition.domain.frame import RawSampleFrame


def configure(processor: DeviceNullifierProcessor, **changes: object) -> dict[str, object]:
    return processor.execute(NullifierCommand(command="configure", config={
        "modeShape": "identity", "modeSamples": 4, "minSamplesPerMode": 2,
        "windowMode": "continuous", "varianceEstimator": "rolling",
        "varianceWindowModes": 2, "channelBDelaySamples": 0,
        "collectionGuardSeconds": 0, "collectionEndGuardSamples": 0,
        **changes,
    }))


def frame(
    a: list[int], b: list[int] | None = None, *, sequence: int = 1,
) -> RawSampleFrame:
    return RawSampleFrame(
        stream_id=1, sequence=sequence, source_pack_id=sequence, captured_at_unix_ns=1,
        base_rate_hz=125_000_000, decimation=1, sample_rate_hz=1000,
        samples_per_channel=len(a), channel_mask=1 if b is None else 3,
        channel_1=np.array(a, dtype="<i2"),
        channel_2=np.array(b, dtype="<i2") if b is not None else None,
    )


def formula(name: str = "custom", weight: float = 1) -> list[dict[str, object]]:
    return [{"name": name, "terms": [{"source": "a", "weight": weight}]}]


def test_commanded_weights_offsets_and_start_drive_the_live_calculation() -> None:
    processor = DeviceNullifierProcessor()
    assert not processor.status()["enabled"]
    configure(processor, nullifiers=[{
        "name": "difference", "start": 1,
        "terms": [{"source": "a", "weight": 2},
                  {"source": "b", "offset": 1, "weight": -3}],
    }])
    a = [1, 2, 4, 8]
    b = [10, 20, 40, 80]
    processor.feed(frame(np.repeat(a, 4).tolist(), np.repeat(b, 4).tolist()))
    result = processor.latest()
    assert result is not None and result["revision"] == 1
    # The normalized flat four-sample mode projects constant ADC samples to
    # 2*sample/sqrt(rate), after converting counts to volts.
    expected = (2 * np.array(a[1:-1]) - 3 * np.array(b[2:])) * 2 / np.sqrt(1000) / 8192
    np.testing.assert_allclose(result["nullifiers"]["difference"], expected)  # type: ignore[index]


def test_configure_discards_partial_modes_and_previous_formula_results() -> None:
    processor = DeviceNullifierProcessor()
    configure(processor, nullifiers=formula())
    processor.feed(frame([300, 300]))
    configure(processor, nullifiers=formula("changed", 2))
    assert processor.latest() is None
    processor.feed(frame([10, 10], sequence=2))
    assert processor.latest() is None
    processor.feed(frame([20, 20], sequence=3))
    result = processor.latest()
    assert result is not None and result["revision"] == 2
    np.testing.assert_allclose(
        result["nullifiers"]["changed"], [60 / np.sqrt(1000) / 8192],  # type: ignore[index]
    )
    assert "custom" not in result["nullifiers"]  # type: ignore[operator]


@pytest.mark.parametrize("changes", [
    {"modeShpae": "identity"}, {"nullifiers": None}, {"nullifiers": []},
    {"nullifiers": [{"name": "bad", "terms": [{"source": "ch3"}]}]},
    {"nullifiers": [{"name": "bad", "terms": [{"source": "a", "offset": -1}]}]},
    {"nullifiers": [{"name": "bad", "terms": [{"source": "a", "weight": "nan"}]}]},
    {"nullifiers": [{"name": "bad", "terms": [{"source": "a", "wieght": 2}]}]},
    {"nullifiers": [{"name": "bad", "terms": [{"source": "a"}], "strat": 1}]},
])
def test_invalid_commands_leave_the_active_configuration_and_result_intact(
    changes: dict[str, object],
) -> None:
    processor = DeviceNullifierProcessor()
    configure(processor, nullifiers=formula())
    processor.feed(frame([1, 2, 3, 4]))
    before = processor.status()
    result = processor.latest()
    with pytest.raises(ValueError):
        processor.execute(NullifierCommand(command="configure", config=changes))
    assert processor.status() == before
    assert processor.latest() == result


@pytest.mark.parametrize("body", [
    {"command": "eval", "code": "print(1)"}, {"command": "configure"},
    {"command": "reset", "config": {}}, {"command": "reset", "minimumModes": 2},
    {"command": "finish_calibration", "minimumModes": True},
])
def test_command_schema_rejects_unknown_commands_and_inapplicable_arguments(
    body: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        NullifierCommand.model_validate(body)


def test_calibration_commands_support_a_formula_using_only_one_physical_channel() -> None:
    processor = DeviceNullifierProcessor()
    configure(processor, nullifiers=formula())
    processor.execute(NullifierCommand(command="start_calibration"))
    processor.feed(frame(np.repeat([-100, 100, -200, 200], 4).tolist()))
    status = processor.execute(NullifierCommand(command="finish_calibration", minimum_modes=4))
    assert status["calibrated"] and not status["calibrating"]
    assert set(status["shotNoiseCoefficients"]) == {"a"}  # type: ignore[arg-type]
    processor.feed(frame(np.repeat([-100, 100, -200, 200], 4).tolist(), sequence=2))
    result = processor.latest()
    assert result is not None and result["calibrated"]
    assert result["squeezingDb"]
    processor.execute(NullifierCommand(
        command="configure", config={"nullifiers": formula(weight=2)},
    ))
    assert not processor.status()["calibrated"]
    assert processor.latest() is None


def test_reset_preserves_calibration_and_disable_clears_it() -> None:
    processor = DeviceNullifierProcessor()
    configure(processor, nullifiers=formula())
    processor.execute(NullifierCommand(command="start_calibration"))
    processor.feed(frame([1, 2, 3, 4] * 4))
    # A constant projected reference must not finish calibration.
    with pytest.raises(ValueError, match="invalid"):
        processor.execute(NullifierCommand(command="finish_calibration", minimum_modes=2))
    assert processor.status()["calibrating"]
    processor.feed(frame(np.repeat([1, 2, 3, 4], 4).tolist(), sequence=2))
    processor.execute(NullifierCommand(command="finish_calibration", minimum_modes=2))
    processor.execute(NullifierCommand(command="reset"))
    assert processor.status()["calibrated"]
    processor.execute(NullifierCommand(command="disable"))
    processor.feed(frame([1, 2, 3, 4]))
    assert processor.latest() is None
    assert not processor.status()["enabled"] and not processor.status()["calibrated"]


@pytest.mark.parametrize("kind", ["sequence", "stream", "rate", "mask", "loss", "flag"])
def test_commanded_processing_never_joins_partial_modes_across_discontinuities(kind: str) -> None:
    processor = DeviceNullifierProcessor()
    configure(processor, nullifiers=formula())
    processor.feed(frame([300, 300]))
    next_frame = frame([10, 10], sequence=2)
    if kind == "sequence":
        next_frame = replace(next_frame, sequence=3)
    elif kind == "stream":
        next_frame = replace(next_frame, stream_id=2)
    elif kind == "rate":
        next_frame = replace(next_frame, sample_rate_hz=2000)
    elif kind == "mask":
        next_frame = replace(next_frame, channel_mask=3, channel_2=np.zeros(2, dtype="<i2"))
    elif kind == "loss":
        next_frame = replace(next_frame, fpga_lost_ch1=1)
    else:
        next_frame = replace(next_frame, packet_gap_detected=True)
    processor.feed(next_frame)
    assert processor.latest() is None


def test_missing_channels_report_analysis_error_and_recover_on_valid_data() -> None:
    processor = DeviceNullifierProcessor()
    configure(processor)
    processor.feed(frame([1, 2, 3, 4]))
    assert processor.status()["error"] == "nullifier processing requires CH2"
    processor.feed(frame([1, 2, 3, 4] * 3, [4, 3, 2, 1] * 3, sequence=2))
    assert processor.latest() is not None
    assert processor.status()["error"] is None


def test_calibration_commands_preserve_loss_baseline_and_detect_a_new_stream() -> None:
    processor = DeviceNullifierProcessor()
    configure(processor, nullifiers=formula())
    lost = replace(frame([1, 2, 3, 4]), fpga_lost_ch1=1)
    processor.feed(lost)
    assert processor.status()["error"]
    processor.feed(replace(lost, sequence=2))
    assert processor.status()["error"] is None
    processor.execute(NullifierCommand(command="start_calibration"))
    # An unchanged cumulative loss count must not exclude a valid packet just
    # because a calibration command was issued between these two packets.
    processor.feed(replace(lost, sequence=3))
    assert processor.status()["calibrationModeCounts"]["a"] == 1  # type: ignore[index]
    # A new stream with an existing loss count must still discard its first
    # affected packet, even when it starts just after a calibration command.
    processor.execute(NullifierCommand(command="start_calibration"))
    processor.feed(replace(lost, sequence=4, stream_id=2))
    assert processor.status()["error"]
    assert processor.status()["calibrationModeCounts"]["a"] == 0  # type: ignore[index]


def test_basis_only_command_switches_lab_preset_and_preserves_custom_formulas() -> None:
    processor = DeviceNullifierProcessor()
    configure(processor, measurementBasis="p")
    expected = config_to_dict(replace(processor._processor.config, nullifiers=(LAB_CLUSTER_P,)))
    assert processor.status()["config"] == expected
    processor.execute(NullifierCommand(command="configure", config={"measurementBasis": "x"}))
    assert processor._processor.config.nullifiers == (LAB_CLUSTER_X,)
    custom = formula("mine")
    configure(processor, nullifiers=custom)
    processor.execute(NullifierCommand(command="configure", config={"measurementBasis": "p"}))
    assert processor.status()["config"]["nullifiers"][0]["name"] == "mine"  # type: ignore[index]


def test_nonfinite_result_values_are_json_null_without_losing_positions() -> None:
    processor = DeviceNullifierProcessor()
    configure(processor, nullifiers=formula(), gateAbsoluteThreshold=0.01)
    processor.feed(frame(np.repeat([1, 1000, 2], 4).tolist()))
    result = processor.latest()
    assert result is not None
    values = result["nullifiers"]["custom"]  # type: ignore[index]
    assert len(values) == 3 and values[1] is None
    json.dumps(result, allow_nan=False)
