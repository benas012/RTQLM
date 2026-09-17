from __future__ import annotations

from pathlib import Path

import pytest

import quantum_light_engine.devices.session_manager as session_manager_module
from quantum_light_engine.config.settings import EngineSettings
from quantum_light_engine.devices.models import DeviceProfileCreate, DeviceProfileUpdate
from quantum_light_engine.devices.persistence import FileDeviceProfileRepository
from quantum_light_engine.devices.session_manager import DeviceSessionManager


def redpitaya(name: str, host: str) -> DeviceProfileCreate:
    return DeviceProfileCreate(
        name=name,
        source="redpitaya",
        host=host,
        channel_1_enabled=True,
        channel_2_enabled=False,
    )


def simulator(name: str) -> DeviceProfileCreate:
    return DeviceProfileCreate(
        name=name,
        source="simulator",
        channel_1_enabled=True,
        channel_2_enabled=True,
    )


async def manager_for(tmp_path: Path) -> DeviceSessionManager:
    settings = EngineSettings(_env_file=None, data_dir=tmp_path, log_dir=tmp_path / "logs")
    repository = FileDeviceProfileRepository(tmp_path)
    await repository.initialize()
    manager = DeviceSessionManager(repository, settings)
    await manager.initialize()
    return manager


@pytest.mark.asyncio
async def test_create_rejects_duplicate_redpitaya_resolved_host(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_streaming_host(host: str) -> str:
        if "rp-f0f9eb.local" in host or host == "169.254.162.246":
            return "169.254.162.246"
        return host

    monkeypatch.setattr(session_manager_module, "streaming_host", fake_streaming_host)
    manager = await manager_for(tmp_path)
    await manager.create(redpitaya("URL", "http://rp-f0f9eb.local/"))

    with pytest.raises(ValueError, match="already exists"):
        await manager.create(redpitaya("IP", "169.254.162.246"))

    assert len(manager.sessions()) == 1


@pytest.mark.asyncio
async def test_update_rejects_duplicate_redpitaya_resolved_host(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_streaming_host(host: str) -> str:
        if "rp-f0f9eb.local" in host or host == "169.254.162.246":
            return "169.254.162.246"
        return host

    monkeypatch.setattr(session_manager_module, "streaming_host", fake_streaming_host)
    manager = await manager_for(tmp_path)
    first = await manager.create(redpitaya("URL", "http://rp-f0f9eb.local/"))
    second = await manager.create(redpitaya("Other", "192.168.50.20"))

    request = DeviceProfileUpdate(**redpitaya("Duplicate", "169.254.162.246").model_dump())

    with pytest.raises(ValueError, match="already exists"):
        await manager.update(second.profile.id, request)

    assert manager.require(first.profile.id).profile.host == "http://rp-f0f9eb.local/"
    assert manager.require(second.profile.id).profile.host == "192.168.50.20"


@pytest.mark.asyncio
async def test_duplicate_check_ignores_simulators(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(session_manager_module, "streaming_host", lambda host: host)
    manager = await manager_for(tmp_path)

    await manager.create(simulator("First"))
    await manager.create(simulator("Second"))

    assert len(manager.sessions()) == 2
