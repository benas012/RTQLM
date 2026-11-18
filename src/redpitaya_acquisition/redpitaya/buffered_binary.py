"""Experimental stopped-burst wire format (not the continuous ADC protocol).

Planar little-endian int16 DMA words retain the original 14-bit ADC encoding.
Offsets are ADC-count zero corrections, not optical calibration coefficients.
Sequence numbers detect transport gaps; they do not prove lossless acquisition.
"""
from dataclasses import dataclass
import math
import struct
import zlib

import numpy as np

from .binary_adc import read_exact

MAGIC = b"RTQB0001"
HEADER = struct.Struct("<8sQIIQddI")
SAMPLES = {1: 625000, 2: 312500}


@dataclass(frozen=True)
class BufferedBurst:
    sequence: int
    decimation: int
    captured_monotonic_ns: int
    zero: tuple[float, float]
    channels: tuple[np.ndarray, np.ndarray]

    def encode(self) -> bytes:
        count = SAMPLES.get(self.decimation)
        if count is None or any(x.shape != (count,) or x.dtype != np.dtype('<i2')
                                for x in self.channels):
            raise ValueError("Expected two int16 channels of exactly 5 ms")
        if len(self.zero) != 2 or not all(math.isfinite(x) for x in self.zero):
            raise ValueError("Invalid ADC offsets")
        payload = b''.join(x.tobytes() for x in self.channels)
        return HEADER.pack(MAGIC, self.sequence, self.decimation, count,
                           self.captured_monotonic_ns, *self.zero,
                           zlib.crc32(payload)) + payload


def receive_burst(connection, *, expected_sequence=None) -> BufferedBurst:
    """Read one bounded frame; caller must set a socket timeout and close on error."""
    magic, sequence, decimation, count, timestamp, za, zb, checksum = HEADER.unpack(
        read_exact(connection, HEADER.size))
    if magic != MAGIC or decimation not in SAMPLES or count != SAMPLES[decimation]:
        raise ValueError("Invalid burst header")
    if not all(math.isfinite(x) for x in (za, zb)):
        raise ValueError("Invalid ADC offsets")
    if expected_sequence is not None and sequence != expected_sequence:
        raise ValueError("Missing, duplicate or reordered burst")
    payload = read_exact(connection, count * 4)
    if zlib.crc32(payload) != checksum:
        raise ValueError("Corrupted burst payload")
    samples = np.frombuffer(bytes(payload), dtype='<i2').reshape(2, count)
    return BufferedBurst(sequence, decimation, timestamp, (za, zb),
                         (samples[0], samples[1]))
