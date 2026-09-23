from __future__ import annotations

import struct

import numpy as np
import pytest

from apps.native_receiver.protocol import HEADER_SIZE, parse_frame
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
