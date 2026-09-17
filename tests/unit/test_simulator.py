import numpy as np
import pytest

from redpitaya_acquisition.acquisition.simulator import SimulatedAcquisitionSource
from redpitaya_acquisition.config.settings import Settings


@pytest.mark.asyncio
async def test_simulator_output_and_timing() -> None:
    settings = Settings(
        _env_file=None, decimation=65_536, simulator_samples_per_packet=32, channel_2_enabled=False
    )
    source = SimulatedAcquisitionSource(settings)
    await source.connect()
    await source.start()
    iterator = source.frames()
    first = await anext(iterator)
    second = await anext(iterator)
    await source.stop()
    assert first.channel_1 is not None and first.channel_2 is None
    assert first.channel_1.dtype == np.dtype("<i2")
    assert second.source_pack_id == first.source_pack_id + 1
    assert first.stream_id == second.stream_id
