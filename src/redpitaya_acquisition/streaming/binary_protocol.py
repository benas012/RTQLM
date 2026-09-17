from __future__ import annotations

import struct
from typing import Any

import numpy as np

from redpitaya_acquisition.domain.frame import RawSampleFrame

MAGIC = b"RPA1"
PROTOCOL_VERSION = 1
HEADER = struct.Struct("<4sBBHQQQQdIIIIQQ")
HEADER_SIZE = 80
FLAG_PACKET_GAP = 1 << 0
FLAG_SERVICE_DROP = 1 << 1
FLAG_LOCAL_TIMESTAMP = 1 << 2


def serialize_frame(frame: RawSampleFrame) -> bytes:
    arrays = [array for array in (frame.channel_1, frame.channel_2) if array is not None]
    for array in arrays:
        if array.dtype != np.dtype("<i2") or not array.flags.c_contiguous or array.ndim != 1:
            raise ValueError("invalid channel storage for binary serialization")
    expected = frame.samples_per_channel * len(arrays) * 2
    actual = sum(array.nbytes for array in arrays)
    if expected != actual:
        raise ValueError(f"payload size mismatch: expected {expected}, got {actual}")
    flags = FLAG_LOCAL_TIMESTAMP
    flags |= FLAG_PACKET_GAP if frame.packet_gap_detected else 0
    flags |= FLAG_SERVICE_DROP if frame.service_drop_detected else 0
    header = HEADER.pack(
        MAGIC,
        PROTOCOL_VERSION,
        frame.channel_mask,
        HEADER_SIZE,
        frame.stream_id,
        frame.sequence,
        frame.source_pack_id,
        frame.captured_at_unix_ns,
        frame.sample_rate_hz,
        frame.decimation,
        frame.samples_per_channel,
        actual,
        flags,
        frame.fpga_lost_ch1,
        frame.fpga_lost_ch2,
    )
    message = bytearray(HEADER_SIZE + actual)
    message[:HEADER_SIZE] = header
    offset = HEADER_SIZE
    for array in arrays:
        view = array.data.cast("B")
        message[offset : offset + len(view)] = view
        offset += len(view)
    return bytes(message)


def decode_header(message: bytes | bytearray | memoryview) -> dict[str, Any]:
    if len(message) < HEADER_SIZE:
        raise ValueError("message is shorter than the fixed header")
    values = HEADER.unpack_from(message)
    names = (
        "magic",
        "protocol_version",
        "channel_mask",
        "header_size",
        "stream_id",
        "sequence",
        "source_pack_id",
        "captured_at_unix_ns",
        "sample_rate_hz",
        "decimation",
        "samples_per_channel",
        "payload_size_bytes",
        "flags",
        "fpga_lost_ch1",
        "fpga_lost_ch2",
    )
    result = dict(zip(names, values, strict=True))
    if result["magic"] != MAGIC or result["header_size"] != HEADER_SIZE:
        raise ValueError("unsupported Red Pitaya acquisition message")
    return result
