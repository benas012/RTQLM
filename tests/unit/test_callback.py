import asyncio

import numpy as np
import pytest

from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.redpitaya.callback import create_callback
from tests.fakes.fake_rp_stream import ADCCallback, ADCPack


@pytest.mark.asyncio
async def test_callback_copies_and_validates_two_channels() -> None:
    frames = []
    events = []
    callback = create_callback(
        ADCCallback,
        asyncio.get_running_loop(),
        Settings(_env_file=None),
        frames.append,
        lambda event, detail: events.append((event, detail)),
    )
    pack = ADCPack(5)
    callback.recievePack(object(), pack)
    await asyncio.sleep(0)
    assert not events
    assert len(frames) == 1
    frame = frames[0]
    assert frame.channel_1 is not None and frame.channel_2 is not None
    assert frame.channel_1.dtype == np.dtype("<i2")
    assert frame.channel_1.flags.owndata and not frame.channel_1.flags.writeable
    pack.channel1.raw[0] = 999
    assert frame.channel_1[0] == 0


@pytest.mark.asyncio
async def test_callback_rejects_mismatch_and_detects_packet_gap() -> None:
    frames = []
    events = []
    callback = create_callback(
        ADCCallback,
        asyncio.get_running_loop(),
        Settings(_env_file=None),
        frames.append,
        lambda event, detail: events.append((event, detail)),
    )
    callback.receivePack(ADCPack(1, mismatch=True))
    await asyncio.sleep(0)
    assert events[0][0] == "malformed"
    callback.receivePack(ADCPack(2))
    callback.receivePack(ADCPack(4))
    await asyncio.sleep(0)
    assert frames[-1].packet_gap_detected


@pytest.mark.asyncio
async def test_callback_accepts_channel_pack_id_mismatch_as_gap() -> None:
    frames = []
    events = []
    callback = create_callback(
        ADCCallback,
        asyncio.get_running_loop(),
        Settings(_env_file=None),
        frames.append,
        lambda event, detail: events.append((event, detail)),
    )
    callback.receivePack(ADCPack(5, channel_pack_mismatch=True))
    await asyncio.sleep(0)
    assert not events
    assert len(frames) == 1
    assert frames[0].packet_gap_detected


@pytest.mark.parametrize(
    "decimation,reported_rate", [(1, 125_000_000), (64, 1_953_125), (256, 488_281)]
)
async def test_vendor_rate_is_not_decimated_twice(decimation: int, reported_rate: int) -> None:
    frames = []
    callback = create_callback(
        ADCCallback,
        asyncio.get_running_loop(),
        Settings(_env_file=None, decimation=decimation),
        frames.append,
        lambda *_args: None,
    )
    pack = ADCPack(1)
    pack.channel1.baseRate = pack.channel2.baseRate = reported_rate
    callback.receivePack(pack)
    await asyncio.sleep(0)
    assert frames[0].sample_rate_hz == reported_rate
    assert frames[0].base_rate_hz == reported_rate * decimation


async def test_missing_vendor_rate_uses_configured_sample_rate() -> None:
    frames = []
    callback = create_callback(
        ADCCallback, asyncio.get_running_loop(), Settings(_env_file=None, decimation=256),
        frames.append, lambda *_args: None,
    )
    pack = ADCPack(1)
    del pack.channel1.baseRate
    del pack.channel2.baseRate
    callback.receivePack(pack)
    await asyncio.sleep(0)
    assert frames[0].sample_rate_hz == 125_000_000 / 256
    assert frames[0].base_rate_hz == 125_000_000


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf")])
async def test_invalid_vendor_rate_is_rejected(rate: float) -> None:
    frames = []
    events = []
    callback = create_callback(
        ADCCallback, asyncio.get_running_loop(), Settings(_env_file=None),
        frames.append, lambda *args: events.append(args),
    )
    pack = ADCPack(1)
    pack.channel1.baseRate = pack.channel2.baseRate = rate
    callback.receivePack(pack)
    await asyncio.sleep(0)
    assert not frames
    assert events[0][0] == "malformed"
