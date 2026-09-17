from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Request, Response

from quantum_light_engine.api.errors import EngineApiError
from quantum_light_engine.devices.models import DeviceProfileUpdate
from quantum_light_engine.devices.session import DeviceSession

router = APIRouter()
DEPRECATED_HEADER = {"X-QuantumLight-Deprecated": "true"}


def _default(request: Request) -> DeviceSession:
    session = cast(DeviceSession | None, request.app.state.session_manager.default())
    if session is None:
        raise EngineApiError(
            "default-device-missing",
            "No default device",
            409,
            "Mark one registered profile as the default device.",
        )
    return session


def _deprecated(response: Response) -> None:
    response.headers.update(DEPRECATED_HEADER)


@router.get("/api/status")
async def status(request: Request, response: Response) -> dict[str, Any]:
    _deprecated(response)
    return _default(request).status()


@router.get("/api/config")
async def config(request: Request, response: Response) -> dict[str, Any]:
    _deprecated(response)
    return _default(request).profile.model_dump(mode="json", by_alias=True)


@router.put("/api/config")
async def update_config(
    request: Request, response: Response, profile: DeviceProfileUpdate
) -> dict[str, Any]:
    _deprecated(response)
    session = _default(request)
    updated = await request.app.state.session_manager.update(session.profile.id, profile)
    return cast(dict[str, Any], updated.profile.model_dump(mode="json", by_alias=True))


@router.post("/api/start")
async def start(request: Request, response: Response) -> dict[str, Any]:
    _deprecated(response)
    return await _default(request).start()


@router.post("/api/stop")
async def stop(request: Request, response: Response) -> dict[str, Any]:
    _deprecated(response)
    return await _default(request).stop()


@router.get("/api/latest")
async def latest(request: Request, response: Response) -> dict[str, Any]:
    _deprecated(response)
    session = _default(request)
    metadata = session.manager.latest_metadata()
    if metadata is None:
        raise EngineApiError(
            "frame-not-available", "No frame available", 404, "No valid frame is available."
        )
    return metadata
