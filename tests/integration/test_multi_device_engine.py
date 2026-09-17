from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from quantum_light_engine.config.settings import EngineSettings
from quantum_light_engine.devices.models import DeviceProfileCreate
from quantum_light_engine.devices.persistence import FileDeviceProfileRepository
from quantum_light_engine.devices.session import DeviceSessionState
from quantum_light_engine.devices.session_manager import DeviceSessionManager


def profile(name: str, seed: int) -> DeviceProfileCreate:
    return DeviceProfileCreate(
        name=name,
        source="simulator",
        simulator_seed=seed,
        simulator_samples_per_packet=64,
    )


@pytest.mark.asyncio
async def test_three_simulators_are_independent(tmp_path: Path) -> None:
    settings = EngineSettings(_env_file=None, data_dir=tmp_path, log_dir=tmp_path / "logs")
    repository = FileDeviceProfileRepository(tmp_path)
    await repository.initialize()
    manager = DeviceSessionManager(repository, settings)
    await manager.initialize()
    sessions = [await manager.create(profile(f"Device {i}", i)) for i in range(3)]
    await asyncio.gather(*(session.start() for session in sessions))
    await asyncio.sleep(0.03)
    assert all(session.state is DeviceSessionState.STREAMING for session in sessions)
    assert len({session.manager.latest_frame.stream_id for session in sessions}) == 3  # type: ignore[union-attr]
    sequences = [session.manager.metrics.sequence for session in sessions]
    assert all(sequence > 0 for sequence in sequences)
    await sessions[0].stop()
    assert sessions[0].state is DeviceSessionState.READY
    assert all(session.state is DeviceSessionState.STREAMING for session in sessions[1:])
    before = sessions[1].manager.metrics.sequence
    await asyncio.sleep(0.01)
    assert sessions[1].manager.metrics.sequence > before
    await manager.shutdown()
    assert all(session.state is DeviceSessionState.DISCONNECTED for session in sessions)
