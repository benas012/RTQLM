import numpy as np
import pytest

from redpitaya_acquisition.domain.frame import RawSampleFrame


def frame(ch1: np.ndarray | None, ch2: np.ndarray | None, mask: int) -> RawSampleFrame:
    count = len(ch1) if ch1 is not None else len(ch2)  # type: ignore[arg-type]
    return RawSampleFrame(1, 1, 1, 1, 125_000_000, 1, 125_000_000, count, mask, ch1, ch2)


def test_arrays_are_int16_contiguous_and_read_only() -> None:
    samples = np.arange(8, dtype="<i2")
    result = frame(samples, None, 1)
    assert result.channel_1 is not None
    assert result.channel_1.dtype == np.dtype("<i2")
    assert result.channel_1.flags.c_contiguous
    assert not result.channel_1.flags.writeable


def test_mismatched_channels_are_rejected() -> None:
    with pytest.raises(ValueError, match="sample counts"):
        frame(np.arange(4, dtype="<i2"), np.arange(3, dtype="<i2"), 3)


def test_empty_channel_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        frame(np.array([], dtype="<i2"), None, 1)
