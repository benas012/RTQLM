from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from redpitaya_acquisition.acquisition.manager import AcquisitionManager
from redpitaya_acquisition.acquisition.source import AcquisitionSource
from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.domain.frame import RawSampleFrame
from redpitaya_acquisition.domain.state import AcquisitionState
from redpitaya_acquisition.streaming.broadcaster import Broadcaster


class DisconnectingSource(AcquisitionSource):
    def __init__(self, remote_shutdown: asyncio.Event) -> None:
        self.remote_shutdown = remote_shutdown

    async def connect(self) -> None:
        return

    async def start(self) -> None:
        return

    async def stop(self) -> None:
        return

    async def disconnect(self) -> None:
        return

    async def shutdown_remote_service(self) -> None:
        self.remote_shutdown.set()

    async def frames(self) -> AsyncIterator[RawSampleFrame]:
        if False:
            yield  # type: ignore[misc]
        raise ConnectionError("device link lost")


@pytest.mark.asyncio
async def test_reconnecting_stops_the_remote_service_before_backoff() -> None:
    remote_shutdown = asyncio.Event()
    settings = Settings(
        _env_file=None,
        source="redpitaya",
        host="rp-test.local",
        reconnect_initial_seconds=10,
        reconnect_maximum_seconds=10,
    )
    manager = AcquisitionManager(
        settings,
        lambda _settings: DisconnectingSource(remote_shutdown),
        Broadcaster(queue_capacity=4),
    )

    await manager.start()
    await asyncio.wait_for(remote_shutdown.wait(), timeout=1)

    assert manager.state is AcquisitionState.RECONNECTING
    await manager.stop()
