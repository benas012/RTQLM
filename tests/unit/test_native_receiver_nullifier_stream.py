from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from apps.native_receiver.protocol import SampleFrame
from apps.native_receiver.stream_controller import StreamControllerMixin


def receiver() -> SimpleNamespace:
    calls: list[bool] = []
    resets: list[bool] = []

    def feed(_a: object, _b: object, _rate: float, *, discontinuity: bool) -> None:
        calls.append(discontinuity)

    return SimpleNamespace(
        cluster_lock=threading.Lock(),
        cluster_processor=SimpleNamespace(feed=feed, reset_stream=lambda: resets.append(True)),
        cluster_last_sequence=None,
        latest_cluster_result=None,
        cluster_error=None,
        calls=calls,
        resets=resets,
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
    assert app.calls == [False, True]
    assert app.latest_cluster_result is None


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
