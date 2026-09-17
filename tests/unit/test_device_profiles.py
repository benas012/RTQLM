from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from quantum_light_engine.devices.models import (
    DeviceProfileCreate,
    DeviceProfileUpdate,
    DeviceSource,
)
from quantum_light_engine.devices.persistence import FileDeviceProfileRepository


def simulator(name: str = "Simulator", *, default: bool = False) -> DeviceProfileCreate:
    return DeviceProfileCreate(
        name=name,
        source=DeviceSource.SIMULATOR,
        is_default=default,
        channel_1_enabled=True,
        channel_2_enabled=True,
    )


def test_profile_requires_host_for_hardware_but_not_simulator() -> None:
    with pytest.raises(ValidationError, match="non-empty host"):
        DeviceProfileCreate(name="RP", source="redpitaya")
    assert simulator().host is None


@pytest.mark.asyncio
async def test_atomic_create_update_delete_and_reload(tmp_path: Path) -> None:
    repository = FileDeviceProfileRepository(tmp_path)
    await repository.initialize()
    created = await repository.create(simulator(default=True))
    path = tmp_path / "devices" / f"{created.id}.json"
    assert path.exists()
    assert not list(path.parent.glob("*.tmp"))
    updated = await repository.update(
        created.id,
        DeviceProfileUpdate(**{**simulator(default=True).model_dump(), "name": "Updated"}),
    )
    assert updated.name == "Updated"
    assert updated.updated_at_utc > created.updated_at_utc
    reloaded = FileDeviceProfileRepository(tmp_path)
    await reloaded.initialize()
    assert (await reloaded.get(created.id)).name == "Updated"  # type: ignore[union-attr]
    await reloaded.delete(created.id)
    assert not path.exists()


@pytest.mark.asyncio
async def test_only_one_default_is_allowed(tmp_path: Path) -> None:
    repository = FileDeviceProfileRepository(tmp_path)
    await repository.initialize()
    await repository.create(simulator("First", default=True))
    with pytest.raises(ValueError, match="only one"):
        await repository.create(simulator("Second", default=True))


@pytest.mark.asyncio
async def test_corrupt_profile_is_quarantined(tmp_path: Path) -> None:
    devices = tmp_path / "devices"
    devices.mkdir(parents=True)
    bad = devices / "broken.json"
    bad.write_text(json.dumps({"not": "a profile"}), encoding="utf-8")
    repository = FileDeviceProfileRepository(tmp_path)
    await repository.initialize()
    assert await repository.list() == []
    assert not bad.exists()
    assert len(list((tmp_path / "quarantine").glob("broken.json.*.corrupt"))) == 1
