from pathlib import Path

import pytest

from quantum_light_engine.devices.migration import migrate_legacy_environment
from quantum_light_engine.devices.models import DeviceSource
from quantum_light_engine.devices.persistence import FileDeviceProfileRepository


@pytest.mark.asyncio
async def test_legacy_environment_migrates_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RP_SOURCE", "simulator")
    monkeypatch.setenv("RP_DECIMATION", "512")
    repository = FileDeviceProfileRepository(tmp_path)
    await repository.initialize()
    assert await migrate_legacy_environment(repository)
    assert not await migrate_legacy_environment(repository)
    profiles = await repository.list()
    assert len(profiles) == 1
    assert profiles[0].source is DeviceSource.SIMULATOR
    assert profiles[0].decimation == 512
    assert profiles[0].is_default
