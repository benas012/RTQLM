from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from quantum_light_engine.config.settings import EngineSettings
from quantum_light_engine.devices.models import DeviceProfile, DeviceProfileCreate
from quantum_light_engine.devices.session import DeviceSession, DeviceSessionState
from redpitaya_acquisition.acquisition.source import AcquisitionSource
from redpitaya_acquisition.domain.frame import RawSampleFrame


class RemoteSource(AcquisitionSource):
    def __init__(self) -> None:
        self.disconnects = 0
        self.remote_shutdowns = 0

    async def connect(self) -> None:
        return

    async def start(self) -> None:
        return

    async def stop(self) -> None:
        return

    async def disconnect(self) -> None:
        self.disconnects += 1

    async def shutdown_remote_service(self) -> None:
        self.remote_shutdowns += 1

    async def frames(self) -> AsyncIterator[RawSampleFrame]:
        if False:
            yield  # type: ignore[misc]


def profile() -> DeviceProfile:
    return DeviceProfile.create(
        DeviceProfileCreate(
            name="RP",
            source="redpitaya",
            host="rp-test.local",
        )
    )


@pytest.mark.asyncio
async def test_ready_device_disconnect_stops_the_remote_streaming_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = RemoteSource()
    session = DeviceSession(profile(), EngineSettings(_env_file=None))
    session._prepared_source = source
    session._state = DeviceSessionState.READY

    await session.disconnect()

    assert source.disconnects == 1
    assert source.remote_shutdowns == 1
    assert session.state is DeviceSessionState.DISCONNECTED


@pytest.mark.asyncio
async def test_already_disconnected_device_still_requests_remote_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = RemoteSource()
    session = DeviceSession(profile(), EngineSettings(_env_file=None))
    monkeypatch.setattr(session, "_new_source", lambda: source)

    await session.disconnect()

    assert source.disconnects == 0
    assert source.remote_shutdowns == 1
