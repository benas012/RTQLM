import io

import numpy as np
import pytest

from redpitaya_acquisition.redpitaya.buffered_binary import (
    BufferedBurst, HEADER, SAMPLES, receive_burst,
)


class Fragmented:
    def __init__(self, data):
        self.data = io.BytesIO(data)

    def recv_into(self, view):
        data = self.data.read(min(len(view), 7919))
        view[:len(data)] = data
        return len(data)


@pytest.mark.parametrize('decimation', [1, 2])
def test_roundtrip(decimation):
    a = np.arange(SAMPLES[decimation], dtype=np.int16)
    b = np.bitwise_not(a)
    burst = BufferedBurst(7, decimation, 123456, (0.25, -1.5), (a, b))
    packet = burst.encode()
    assert len(packet) == HEADER.size + 4 * SAMPLES[decimation]
    result = receive_burst(Fragmented(packet), expected_sequence=7)
    assert result.zero == burst.zero
    for actual, expected in zip(result.channels, burst.channels):
        np.testing.assert_array_equal(actual, expected)
    with pytest.raises(ValueError, match='reordered'):
        receive_burst(Fragmented(packet), expected_sequence=8)
    with pytest.raises(ConnectionError):
        receive_burst(Fragmented(packet[:-1]))
    damaged = bytearray(packet)
    damaged[-1] ^= 1
    with pytest.raises(ValueError, match='Corrupted'):
        receive_burst(Fragmented(damaged))
