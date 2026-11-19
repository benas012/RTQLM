"""Protocol integrity tests independent of the connected Red Pitaya."""
import unittest

import numpy as np

from capture import decode_payload


class DecodeTests(unittest.TestCase):
    def test_signed_adc_limits_and_e1(self):
        a = np.array([-8192, -1, 0, 8191], dtype=np.int16)
        b = a[::-1]
        e = np.array([0, 1, 1, 0], dtype=np.uint16)
        words = (np.stack([a, b]).astype(np.uint16) & 0x3fff) | 0x8000 | (e << 14)
        actual = decode_payload(words.astype("<u2").tobytes(), 4)
        for result, expected in zip(actual, (a, b, e)):
            np.testing.assert_array_equal(result, expected)

    def test_diagnostic_wraps_and_corruption(self):
        a = np.arange(16000, 33000, dtype=np.uint16) & 0x3fff
        e = ((a >> 5) & 1) << 14
        words = np.stack([a, a ^ 0x3fff]) | 0x8000 | e
        decode_payload(words.astype("<u2").tobytes(), len(a), True)
        for rail, index, mask in ((0, 3, 0x8000), (1, 50, 0x4000),
                                  (1, 70, 1), (0, 90, 1)):
            damaged = words.copy()
            damaged[rail, index] ^= mask
            with self.assertRaises(ValueError):
                decode_payload(damaged.astype("<u2").tobytes(), len(a), True)

    def test_truncation(self):
        with self.assertRaises(ValueError):
            decode_payload(b"\x00" * 15, 4)


if __name__ == "__main__":
    unittest.main()
