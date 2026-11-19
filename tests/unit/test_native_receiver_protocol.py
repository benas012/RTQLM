from __future__ import annotations

import struct
from dataclasses import replace

import numpy as np
import pytest

from apps.native_receiver.protocol import HEADER_SIZE, SampleFrame, SampleHistory, parse_frame
from redpitaya_acquisition.domain.frame import RawSampleFrame
from redpitaya_acquisition.streaming.binary_protocol import serialize_frame


def encoded_frame() -> bytes:
    samples = np.array([1, 2, 3, 4], dtype="<i2")
    return serialize_frame(
        RawSampleFrame(
            stream_id=1,
            sequence=2,
            source_pack_id=3,
            captured_at_unix_ns=4,
            base_rate_hz=125_000_000,
            decimation=256,
            sample_rate_hz=125_000_000 / 256,
            samples_per_channel=len(samples),
            channel_mask=1,
            channel_1=samples,
            channel_2=None,
        )
    )


def test_parse_frame_rejects_truncated_payload() -> None:
    with pytest.raises(ValueError, match="invalid message size"):
        parse_frame(encoded_frame()[:-2])


def test_parse_frame_rejects_trailing_bytes() -> None:
    with pytest.raises(ValueError, match="invalid message size"):
        parse_frame(encoded_frame() + b"\x00\x00")


def test_parse_frame_rejects_header_payload_mismatch() -> None:
    message = bytearray(encoded_frame())
    struct.pack_into("<I", message, 56, len(message) - HEADER_SIZE + 2)
    with pytest.raises(ValueError, match="invalid payload size"):
        parse_frame(bytes(message))


def test_parse_frame_preserves_stream_identity_for_nullifier_resynchronization() -> None:
    assert parse_frame(encoded_frame()).stream_id == 1


@pytest.mark.parametrize("rate", [0.0, -1.0, float("nan"), float("inf")])
def test_parse_frame_rejects_invalid_sample_rate(rate: float) -> None:
    message = bytearray(encoded_frame())
    struct.pack_into("<d", message, 40, rate)
    with pytest.raises(ValueError, match="positive and finite"):
        parse_frame(bytes(message))


def test_raw_trigger_search_handles_wrapped_history() -> None:
    history = SampleHistory(capacity=100)
    first = np.full(60, -1000, dtype=np.int16)
    second = np.full(60, -1000, dtype=np.int16)
    second[10:] = 1000
    for sequence, samples in enumerate((first, second), start=1):
        history.append(
            SampleFrame(
                channel_mask=1,
                sequence=sequence,
                sample_rate_hz=1_000.0,
                samples_per_channel=len(samples),
                flags=0,
                fpga_lost_ch1=0,
                fpga_lost_ch2=0,
                channel_1=memoryview(samples),
                channel_2=None,
            )
        )

    crossing = history.find_channel_crossing_before(
        1,
        requested_samples=100,
        level=0,
        edge="Rising",
        target_index=80,
    )

    assert crossing == 50


def test_history_starts_fresh_after_a_reported_packet_gap() -> None:
    history = SampleHistory(capacity=16)
    first = np.arange(4, dtype=np.int16)
    after_gap = np.arange(100, 104, dtype=np.int16)
    history.append(SampleFrame(1, 1, 1000.0, 4, 0, 0, 0, memoryview(first), None))
    history.append(SampleFrame(1, 2, 1000.0, 4, 1, 0, 0, memoryview(after_gap), None))

    snapshot = history.snapshot(16)

    assert snapshot is not None
    assert snapshot.samples_per_channel == 4
    np.testing.assert_array_equal(snapshot.channel_1, after_gap)


@pytest.mark.parametrize("change", [
    {"stream_id": 2}, {"fpga_lost_ch1": 10}, {"fpga_lost_ch2": 10},
])
@pytest.mark.parametrize("edge,sign", [("Rising", 1), ("Falling", -1)])
def test_trigger_does_not_join_an_edge_across_lost_samples_or_streams(change, edge, sign):
    history = SampleHistory(capacity=32)
    low = np.full(8, -1000 * sign, dtype=np.int16)
    high = -low
    first = SampleFrame(3, 1, 1000, 8, 0, 0, 0, memoryview(low), memoryview(low), 1)
    history.append(first)
    history.append(replace(first, sequence=2, channel_1=memoryview(high),
                           channel_2=memoryview(high), **change))
    assert history.size == 8
    assert history.find_channel_crossing_before(1, 32, 0, edge, target_index=31) is None
