from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from quantum_light_engine.config.settings import EngineSettings
from redpitaya_acquisition.redpitaya.host import streaming_host

from .models import DeviceProfile, DeviceProfileCreate, DeviceProfileUpdate, DeviceSource
from .persistence import DeviceProfileRepository
from .session import BUSY_SESSION_STATES, DeviceSession, DeviceSessionState

logger = logging.getLogger(__name__)


class DeviceSessionManager:
    def __init__(self, repository: DeviceProfileRepository, settings: EngineSettings) -> None:
        self.repository = repository
        self.settings = settings
        self._sessions: dict[UUID, DeviceSession] = {}
        self._accepting_operations = True

    async def initialize(self) -> None:
        for profile in await self.repository.list():
            self._sessions[profile.id] = DeviceSession(profile, self.settings)

    def sessions(self) -> list[DeviceSession]:
        return list(self._sessions.values())

    def get(self, device_id: UUID) -> DeviceSession | None:
        return self._sessions.get(device_id)

    def require(self, device_id: UUID) -> DeviceSession:
        session = self.get(device_id)
        if session is None:
            raise KeyError(device_id)
        return session

    def default(self) -> DeviceSession | None:
        return next(
            (session for session in self._sessions.values() if session.profile.is_default), None
        )

    async def create(self, request: DeviceProfileCreate) -> DeviceSession:
        self._ensure_accepting()
        await self._ensure_unique_redpitaya_host(request)
        profile = await self.repository.create(request)
        session = DeviceSession(profile, self.settings)
        self._sessions[profile.id] = session
        return session

    async def update(self, device_id: UUID, request: DeviceProfileUpdate) -> DeviceSession:
        self._ensure_accepting()
        session = self.require(device_id)
        if session.state in BUSY_SESSION_STATES:
            raise RuntimeError("device configuration cannot be changed in the current state")
        await self._ensure_unique_redpitaya_host(request, current_id=device_id)
        profile = await self.repository.update(device_id, request)
        await session.update_profile(profile)
        return session

    async def delete(self, device_id: UUID) -> None:
        self._ensure_accepting()
        session = self.require(device_id)
        if session.state is not DeviceSessionState.DISCONNECTED:
            raise RuntimeError("a connected device cannot be deleted")
        await self.repository.delete(device_id)
        del self._sessions[device_id]

    async def auto_start(self) -> None:
        enabled = [session for session in self._sessions.values() if session.profile.enabled]
        operation = "start" if self.settings.auto_start else "connect"
        if not self.settings.auto_start and not self.settings.auto_connect:
            return
        results = await asyncio.gather(
            *(getattr(session, operation)() for session in enabled), return_exceptions=True
        )
        for session, result in zip(enabled, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "automatic device %s failed: %s",
                    operation,
                    result,
                    extra={"device_id": str(session.profile.id)},
                )

    async def shutdown(self) -> None:
        self._accepting_operations = False
        tasks = [session.disconnect() for session in self._sessions.values()]
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self.settings.shutdown_timeout_seconds,
            )
        except TimeoutError:
            logger.error("bounded engine shutdown timed out")
        finally:
            for session in self._sessions.values():
                session.broadcaster.close()

    def replace_profile(self, profile: DeviceProfile) -> None:
        self._sessions[profile.id] = DeviceSession(profile, self.settings)

    def _ensure_accepting(self) -> None:
        if not self._accepting_operations:
            raise RuntimeError("engine is stopping")

    async def _ensure_unique_redpitaya_host(
        self, request: DeviceProfileCreate | DeviceProfileUpdate, *, current_id: UUID | None = None
    ) -> None:
        if request.source is not DeviceSource.REDPITAYA or request.host is None:
            return
        candidate = await _canonical_streaming_host(request.host)
        for session in self._sessions.values():
            profile = session.profile
            if profile.id == current_id:
                continue
            if profile.source is not DeviceSource.REDPITAYA or profile.host is None:
                continue
            existing = await _canonical_streaming_host(profile.host)
            if existing == candidate:
                raise ValueError(
                    "a Red Pitaya profile already exists for this board "
                    f"({profile.name}: {profile.host})"
                )


async def _canonical_streaming_host(host: str) -> str:
    return (await asyncio.to_thread(streaming_host, host)).lower()
