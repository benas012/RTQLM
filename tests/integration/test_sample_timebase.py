from __future__ import annotations

import asyncio

import numpy as np
import pytest

from apps.native_receiver.helpers import compute_spectrum
from apps.native_receiver.protocol import parse_frame
from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.redpitaya.callback import create_callback
from redpitaya_acquisition.streaming.binary_protocol import serialize_frame
from tests.fakes.fake_rp_stream import ADCCallback, ADCPack


async def test_vendor_rate_reaches_receiver_time_and_frequency_axes() -> None:
    frames = []
    callback = create_callback(
        ADCCallback,
        asyncio.get_running_loop(),
        Settings(_env_file=None, decimation=256),
        frames.append,
        lambda *_args: None,
    )
    rate = 488_281
    count = 4096
    tone_hz = 8 * rate / count
    signal = (12000 * np.sin(2 * np.pi * tone_hz * np.arange(count) / rate)).astype(np.int16)
    pack = ADCPack(1, count=count)
    pack.channel1.raw = signal
    pack.channel2.raw = signal.copy()
    callback.receivePack(pack)
    await asyncio.sleep(0)
    received = parse_frame(serialize_frame(frames[0]))
    assert received.sample_rate_hz == rate
    assert received.samples_per_channel / received.sample_rate_hz * 1000 == pytest.approx(8.3886123)
    # A 64-batch history is about half a second, not 137 seconds.
    assert 64 * count / received.sample_rate_hz * 1000 == pytest.approx(536.8711869)
    spectrum = compute_spectrum(received.channel_1, received.sample_rate_hz, count)
    assert spectrum is not None
    assert spectrum["peak_hz"] == pytest.approx(tone_hz)
