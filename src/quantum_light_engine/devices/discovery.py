from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from .persistence import DeviceProfileRepository


class DiscoveredDevice(BaseModel):
    name: str
    source: str
    host: str | None
    saved: bool


class DeviceDiscoveryProvider(Protocol):
    async def discover(self, timeout_seconds: float) -> list[DiscoveredDevice]: ...


class SavedProfileDiscovery:
    def __init__(self, repository: DeviceProfileRepository) -> None:
        self.repository = repository

    async def discover(self, timeout_seconds: float) -> list[DiscoveredDevice]:
        del timeout_seconds
        seen: set[tuple[str, str | None]] = set()
        results: list[DiscoveredDevice] = []
        for profile in await self.repository.list():
            key = (profile.source.value, profile.host)
            if key not in seen:
                seen.add(key)
                results.append(
                    DiscoveredDevice(
                        name=profile.name,
                        source=profile.source.value,
                        host=profile.host,
                        saved=True,
                    )
                )
        return results


class StaticDiscovery:
    """Deterministic bounded discovery provider for integration tests and embedders."""

    def __init__(self, devices: list[DiscoveredDevice]) -> None:
        self.devices = devices

    async def discover(self, timeout_seconds: float) -> list[DiscoveredDevice]:
        del timeout_seconds
        unique: dict[tuple[str, str | None], DiscoveredDevice] = {}
        for device in self.devices:
            unique[(device.source, device.host)] = device
        return list(unique.values())
