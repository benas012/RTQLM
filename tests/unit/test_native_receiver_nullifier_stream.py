from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from apps.native_receiver.protocol import SampleFrame
from apps.native_receiver.stream_controller import StreamControllerMixin
from quantum_light_engine.post_processing import (
    ClusterProcessingConfig,
    RealtimeClusterProcessor,
    single_mode_processing_config,
)


def receiver(config: ClusterProcessingConfig | None = None) -> SimpleNamespace:
    calls: list[bool] = []
    resets: list[bool] = []
    inputs: list[tuple[np.ndarray, np.ndarray, float]] = []

    def feed(_a: np.ndarray, _b: np.ndarray, _rate: float, *, discontinuity: bool) -> None:
        calls.append(discontinuity)
        inputs.append((_a.copy(), _b.copy(), _rate))

    return SimpleNamespace(
        cluster_lock=threading.Lock(),
        cluster_processor=SimpleNamespace(
            config=config or ClusterProcessingConfig(),
            feed=feed, reset_stream=lambda: resets.append(True),
        ),
        cluster_last_sequence=None,
        latest_cluster_result=None,
        cluster_error=None,
        calls=calls,
        resets=resets,
        inputs=inputs,
    )


def frame(sequence: int = 1) -> SampleFrame:
    values = memoryview(np.arange(16, dtype=np.int16))
    return SampleFrame(3, sequence, 125_000_000.0, 16, 0, 0, 0, values, values, 8)


@pytest.mark.parametrize(
    "change",
    [
        {"sequence": 3},
        {"stream_id": 9},
        {"sample_rate_hz": 62_500_000.0},
        {"fpga_lost_ch1": 1},
        {"fpga_lost_ch2": 2},
        {"flags": 1},
        {"flags": 2},
    ],
)
def test_nullifier_stream_discards_partial_modes_after_loss_or_restart(change: dict) -> None:
    app = receiver()
    StreamControllerMixin._process_cluster_frame(app, frame())
    app.latest_cluster_result = object()
    StreamControllerMixin._process_cluster_frame(app, replace(frame(2), **change))
    if "fpga_lost_ch1" in change or "fpga_lost_ch2" in change:
        assert app.calls == [False]
        assert app.resets == [True]
        assert "FPGA samples lost" in app.cluster_error
    else:
        assert app.calls == [False, True]
    assert app.latest_cluster_result is None


def test_fpga_loss_packet_is_isolated_before_next_clean_packet() -> None:
    app = receiver()
    StreamControllerMixin._process_cluster_frame(app, frame())
    StreamControllerMixin._process_cluster_frame(app, replace(frame(2), fpga_lost_ch1=5))
    StreamControllerMixin._process_cluster_frame(app, replace(frame(3), fpga_lost_ch1=10))
    StreamControllerMixin._process_cluster_frame(app, replace(frame(4), fpga_lost_ch1=10))
    assert app.calls == [False, False]
    assert app.resets == [True, True]
    assert app.cluster_error is None


def test_nullifier_stream_processes_every_packet_without_display_pacing() -> None:
    app = receiver()
    for sequence in range(1, 11):
        StreamControllerMixin._process_cluster_frame(app, frame(sequence))
    assert app.calls == [False] * 10


def test_missing_channel_resets_incomplete_collection_and_recovers() -> None:
    app = receiver()
    StreamControllerMixin._process_cluster_frame(app, frame())
    StreamControllerMixin._process_cluster_frame(
        app, replace(frame(2), channel_mask=1, channel_2=None)
    )
    assert app.resets == [True]
    assert app.cluster_last_sequence is None
    assert app.cluster_error == "Cluster processing requires both CH1 and CH2"
    StreamControllerMixin._process_cluster_frame(app, frame(3))
    assert app.cluster_error is None
    assert app.calls == [False, False]


@pytest.mark.parametrize("selected", ["a", "b"])
def test_single_mode_accepts_only_the_selected_channel(selected: str) -> None:
    app = receiver(replace(single_mode_processing_config(), single_channel=selected))
    single_frame = replace(
        frame(), channel_mask=1 if selected == "a" else 2,
        channel_1=frame().channel_1 if selected == "a" else None,
        channel_2=frame().channel_2 if selected == "b" else None,
    )
    StreamControllerMixin._process_cluster_frame(app, single_frame)
    StreamControllerMixin._process_cluster_frame(app, replace(single_frame, sequence=2))
    assert app.calls == [False, False]
    assert app.resets == []
    assert app.cluster_error is None
    a, b, rate = app.inputs[0]
    actual, unused = (a, b) if selected == "a" else (b, a)
    assert actual.shape == unused.shape == (single_frame.samples_per_channel,)
    assert rate == single_frame.sample_rate_hz
    assert np.any(actual != 0)
    np.testing.assert_array_equal(unused, np.zeros(single_frame.samples_per_channel))


@pytest.mark.parametrize("selected", ["a", "b", "both"])
def test_single_mode_rejects_a_missing_selected_channel(selected: str) -> None:
    app = receiver(replace(single_mode_processing_config(), single_channel=selected))
    missing = "a" if selected in {"a", "both"} else "b"
    sample_frame = replace(
        frame(), channel_mask=2 if missing == "a" else 1,
        channel_1=None if missing == "a" else frame().channel_1,
        channel_2=None if missing == "b" else frame().channel_2,
    )
    StreamControllerMixin._process_cluster_frame(app, sample_frame)
    assert app.calls == []
    assert app.resets == [True]
    assert app.cluster_last_sequence is None
    assert app.cluster_last_frame is None
    assert "requires" in app.cluster_error
    required = "both CH1 and CH2" if selected == "both" else "CH1" if selected == "a" else "CH2"
    assert required in app.cluster_error


@pytest.mark.parametrize("loss_channel", ["fpga_lost_ch1", "fpga_lost_ch2"])
def test_single_mode_still_excludes_fpga_loss_packets(loss_channel: str) -> None:
    app = receiver(replace(single_mode_processing_config(), single_channel="a"))
    clean = replace(frame(), channel_mask=1, channel_2=None)
    StreamControllerMixin._process_cluster_frame(app, clean)
    loss = replace(clean, sequence=2, **{loss_channel: 1})
    StreamControllerMixin._process_cluster_frame(app, loss)
    assert app.calls == [False]
    assert app.resets == [True]
    assert "FPGA samples lost" in app.cluster_error
    StreamControllerMixin._process_cluster_frame(app, replace(loss, sequence=3))
    assert app.calls == [False, False]
    assert app.cluster_error is None


def test_single_mode_channel_mask_change_still_resets_continuity() -> None:
    app = receiver(replace(single_mode_processing_config(), single_channel="a"))
    StreamControllerMixin._process_cluster_frame(app, frame())
    StreamControllerMixin._process_cluster_frame(
        app, replace(frame(2), channel_mask=1, channel_2=None),
    )
    assert app.calls == [False, True]


@pytest.mark.parametrize("selected", ["a", "b"])
def test_single_mode_reports_a_missing_sampled_trigger_input(selected: str) -> None:
    trigger = "b" if selected == "a" else "a"
    app = receiver(replace(
        single_mode_processing_config(), single_channel=selected,
        window_mode="triggered", trigger_channel=trigger,
    ))
    single_frame = replace(
        frame(), channel_mask=1 if selected == "a" else 2,
        channel_1=frame().channel_1 if selected == "a" else None,
        channel_2=frame().channel_2 if selected == "b" else None,
    )
    StreamControllerMixin._process_cluster_frame(app, single_frame)
    channel = "CH1" if trigger == "a" else "CH2"
    assert app.cluster_error == f"Triggered acquisition requires sampled trigger input {channel}"
    assert app.resets == [True]
    assert app.calls == []
    assert app.cluster_last_sequence is None
    assert app.cluster_last_frame is None
    StreamControllerMixin._process_cluster_frame(app, frame(2))
    assert app.cluster_error is None
    assert app.calls == [False]


@pytest.mark.parametrize("selected", ["a", "b"])
def test_single_mode_can_trigger_from_its_only_present_input(selected: str) -> None:
    app = receiver(replace(
        single_mode_processing_config(), single_channel=selected,
        window_mode="triggered", trigger_channel=selected,
    ))
    single_frame = replace(
        frame(), channel_mask=1 if selected == "a" else 2,
        channel_1=frame().channel_1 if selected == "a" else None,
        channel_2=frame().channel_2 if selected == "b" else None,
    )
    StreamControllerMixin._process_cluster_frame(app, single_frame)
    assert app.cluster_error is None
    assert app.resets == []
    assert app.calls == [False]


@pytest.mark.parametrize("selected", ["a", "b"])
def test_selected_channel_can_calibrate_and_calculate_without_the_other_input(
    selected: str,
) -> None:
    config = replace(
        single_mode_processing_config(mode_samples=4, variance_window_modes=2),
        single_channel=selected, window_mode="continuous",
        collection_guard_seconds=0, collection_end_guard_samples=0,
    )
    app = receiver(config)
    app.cluster_processor = RealtimeClusterProcessor(config)
    app.cluster_processor.start_calibration()
    sample_frame = replace(
        frame(), channel_mask=1 if selected == "a" else 2,
        channel_1=frame().channel_1 if selected == "a" else None,
        channel_2=frame().channel_2 if selected == "b" else None,
    )
    for sequence in range(1, 26):
        StreamControllerMixin._process_cluster_frame(app, replace(sample_frame, sequence=sequence))
    assert app.cluster_error is None
    assert set(app.cluster_processor.finish_calibration()) == {selected}
    StreamControllerMixin._process_cluster_frame(app, replace(sample_frame, sequence=26))
    assert app.cluster_error is None
    assert app.latest_cluster_result.calibrated
    expected = "CH1" if selected == "a" else "CH2"
    assert set(app.latest_cluster_result.squeezing_db) == {expected}
    assert np.isfinite(app.latest_cluster_result.squeezing_db[expected]).any()
