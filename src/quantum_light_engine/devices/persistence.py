from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .models import DeviceProfile, DeviceProfileCreate, DeviceProfileUpdate

logger = logging.getLogger(__name__)


class DeviceProfileRepository(Protocol):
    async def list(self) -> list[DeviceProfile]: ...
    async def get(self, device_id: UUID) -> DeviceProfile | None: ...
    async def create(self, request: DeviceProfileCreate) -> DeviceProfile: ...
    async def update(self, device_id: UUID, request: DeviceProfileUpdate) -> DeviceProfile: ...
    async def delete(self, device_id: UUID) -> None: ...


class FileDeviceProfileRepository:
    def __init__(self, data_dir: Path) -> None:
        self.devices_dir = data_dir / "devices"
        self.quarantine_dir = data_dir / "quarantine"
        self._profiles: dict[UUID, DeviceProfile] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self.devices_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        loaded: dict[UUID, DeviceProfile] = {}
        for path in self.devices_dir.glob("*.json"):
            try:
                profile = DeviceProfile.model_validate_json(path.read_bytes())
                if path.stem != str(profile.id):
                    raise ValueError("profile filename does not match its UUID")
                if profile.is_default and any(item.is_default for item in loaded.values()):
                    raise ValueError("only one device profile may be marked as default")
                loaded[profile.id] = profile
            except Exception as exc:
                target = self._unique_quarantine_path(path.name)
                os.replace(path, target)
                logger.error("quarantined corrupt profile %s: %s", path.name, exc)
        self._profiles = loaded

    async def list(self) -> list[DeviceProfile]:
        async with self._lock:
            return sorted(self._profiles.values(), key=lambda item: item.created_at_utc)

    async def get(self, device_id: UUID) -> DeviceProfile | None:
        async with self._lock:
            return self._profiles.get(device_id)

    async def create(self, request: DeviceProfileCreate) -> DeviceProfile:
        async with self._lock:
            profile = DeviceProfile.create(request)
            self._check_default(profile)
            await asyncio.to_thread(self._atomic_write, profile)
            self._profiles[profile.id] = profile
            logger.info("created device profile", extra={"device_id": str(profile.id)})
            return profile

    async def update(self, device_id: UUID, request: DeviceProfileUpdate) -> DeviceProfile:
        from datetime import UTC, datetime

        async with self._lock:
            current = self._profiles.get(device_id)
            if current is None:
                raise KeyError(device_id)
            updated = DeviceProfile(
                id=device_id,
                created_at_utc=current.created_at_utc,
                updated_at_utc=datetime.now(UTC),
                **request.model_dump(),
            )
            self._check_default(updated)
            await asyncio.to_thread(self._atomic_write, updated)
            self._profiles[device_id] = updated
            logger.info("updated device profile", extra={"device_id": str(device_id)})
            return updated

    async def delete(self, device_id: UUID) -> None:
        async with self._lock:
            if device_id not in self._profiles:
                raise KeyError(device_id)
            await asyncio.to_thread((self.devices_dir / f"{device_id}.json").unlink, True)
            del self._profiles[device_id]
            logger.info("deleted device profile", extra={"device_id": str(device_id)})

    def _check_default(self, candidate: DeviceProfile) -> None:
        if candidate.is_default and any(
            item.is_default and item.id != candidate.id for item in self._profiles.values()
        ):
            raise ValueError("only one device profile may be marked as default")

    def _atomic_write(self, profile: DeviceProfile) -> None:
        data = profile.model_dump_json(by_alias=True, indent=2).encode("utf-8") + b"\n"
        fd, temporary = tempfile.mkstemp(
            prefix=f".{profile.id}.", suffix=".tmp", dir=self.devices_dir
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.devices_dir / f"{profile.id}.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _unique_quarantine_path(self, filename: str) -> Path:
        from datetime import UTC, datetime

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return self.quarantine_dir / f"{filename}.{stamp}.corrupt"
