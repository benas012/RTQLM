from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Int16Array = npt.NDArray[np.int16]


@dataclass(frozen=True, slots=True)
class RawSampleFrame:
    stream_id: int
    sequence: int
    source_pack_id: int
    captured_at_unix_ns: int
    base_rate_hz: int
    decimation: int
    sample_rate_hz: float
    samples_per_channel: int
    channel_mask: int
    channel_1: Int16Array | None
    channel_2: Int16Array | None
    fpga_lost_ch1: int = 0
    fpga_lost_ch2: int = 0
    packet_gap_detected: bool = False
    service_drop_detected: bool = False
    adc_capture_unix_ns: int = 0
    adc_zero_ch1: float = 0.0
    adc_zero_ch2: float = 0.0
    adc_scale_ch1: float = 1 / 8192
    adc_scale_ch2: float = 1 / 8192

    def __post_init__(self) -> None:
        if self.channel_mask not in (1, 2, 3):
            raise ValueError("channel_mask must enable channel 1, channel 2, or both")
        if self.samples_per_channel <= 0:
            raise ValueError("samples_per_channel must be positive")
        expected = ((1, self.channel_1), (2, self.channel_2))
        for bit, array in expected:
            enabled = bool(self.channel_mask & bit)
            if enabled != (array is not None):
                raise ValueError(f"channel bit {bit} does not match sample presence")
            if array is not None:
                if array.ndim != 1 or len(array) != self.samples_per_channel:
                    raise ValueError("channel arrays must be 1-D with matching sample counts")
                if array.dtype != np.dtype("<i2"):
                    raise ValueError("channel arrays must use little-endian signed int16")
                if not array.flags.c_contiguous:
                    raise ValueError("channel arrays must be C-contiguous")
                array.flags.writeable = False
        if self.channel_1 is not None and self.channel_2 is not None:
            if self.channel_1.shape != self.channel_2.shape:
                raise ValueError("enabled channel sample counts must match")
