import numpy as np

from redpitaya_acquisition.domain.frame import RawSampleFrame
from redpitaya_acquisition.streaming.binary_protocol import HEADER, decode_header, serialize_frame


def test_binary_header_and_payload_order() -> None:
    ch1 = np.array([1, -2, 3], dtype="<i2")
    ch2 = np.array([10, 20, -30], dtype="<i2")
    frame = RawSampleFrame(7, 9, 11, 13, 125_000_000, 4, 31_250_000, 3, 3, ch1, ch2)
    message = serialize_frame(frame)
    header = decode_header(message)
    assert HEADER.size == 80
    assert header["payload_size_bytes"] == 12
    decoded = np.frombuffer(message, dtype="<i2", offset=80)
    np.testing.assert_array_equal(decoded[:3], ch1)
    np.testing.assert_array_equal(decoded[3:], ch2)
