import asyncio

import pytest

from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.redpitaya.streaming_source import RedPitayaStreamingSource


@pytest.mark.asyncio
async def test_hardware_adapter_with_fake_official_library() -> None:
    settings = Settings(
        _env_file=None,
        source="redpitaya",
        host="explicit-host",
        streaming_module="tests.fakes.fake_rp_stream",
    )
    source = RedPitayaStreamingSource(settings)
    await source.connect()
    await source.start()
    frame = await asyncio.wait_for(anext(source.frames()), timeout=1)
    assert frame.channel_1 is not None and frame.channel_2 is not None
    assert frame.source_pack_id == 1
    await source.stop()
    await source.disconnect()
