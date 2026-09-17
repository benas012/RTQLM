import asyncio

import numpy as np
import pytest

from redpitaya_acquisition.acquisition.manager import AcquisitionManager
from redpitaya_acquisition.acquisition.simulator import SimulatedAcquisitionSource
from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.domain.frame import RawSampleFrame
from redpitaya_acquisition.domain.state import AcquisitionState
from redpitaya_acquisition.streaming.broadcaster import Broadcaster


@pytest.mark.asyncio
async def test_start_stop_are_idempotent_and_sequences_advance() -> None:
    settings = Settings(_env_file=None, simulator_samples_per_packet=64)
    manager = AcquisitionManager(settings, SimulatedAcquisitionSource, Broadcaster(2))
    await manager.start()
    await manager.start()
    await asyncio.sleep(0.02)
    assert manager.state is AcquisitionState.STREAMING
    assert manager.metrics.sequence > 0
    sequence = manager.metrics.sequence
    await manager.stop()
    await manager.stop()
    assert manager.state is AcquisitionState.STOPPED
    assert manager.metrics.sequence == sequence


def test_internal_queue_drops_oldest_and_marks_newest() -> None:
    settings = Settings(_env_file=None, internal_queue_capacity=1)
    manager = AcquisitionManager(settings, SimulatedAcquisitionSource, Broadcaster(2))
    samples = np.arange(2, dtype="<i2")
    first = RawSampleFrame(1, 0, 1, 1, 125_000_000, 1, 125_000_000, 2, 1, samples, None)
    second = RawSampleFrame(1, 0, 2, 2, 125_000_000, 1, 125_000_000, 2, 1, samples, None)
    manager._accept_frame(first)
    manager._accept_frame(second)
    queued = manager._queue.get_nowait()
    assert queued.source_pack_id == 2
    assert queued.service_drop_detected
    assert manager.metrics.service_dropped_frames == 1
