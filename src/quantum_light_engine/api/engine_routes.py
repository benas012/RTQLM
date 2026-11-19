from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request

from quantum_light_engine.api.schemas import EngineStatus
from quantum_light_engine.devices.session import DeviceSessionState
from quantum_light_engine.devices.session_manager import DeviceSessionManager

router = APIRouter(prefix="/api/v1/engine")


@router.get("/status", response_model=EngineStatus, response_model_by_alias=True)
async def status(request: Request) -> EngineStatus:
    manager: DeviceSessionManager = request.app.state.session_manager
    sessions = manager.sessions()
    started: datetime = request.app.state.started_at_utc
    return EngineStatus(
        state=request.app.state.engine_state,
        started_at_utc=started,
        uptime_seconds=max(0.0, (datetime.now(UTC) - started).total_seconds()),
        device_count=len(sessions),
        connected_device_count=sum(session.connected for session in sessions),
        streaming_device_count=sum(
            session.state is DeviceSessionState.STREAMING for session in sessions
        ),
        total_web_socket_clients=sum(session.broadcaster.client_count for session in sessions),
    )


@router.post("/prepare-shutdown")
async def prepare_shutdown(request: Request) -> dict[str, str]:
    """Stop device sessions before the desktop launcher terminates the engine."""

    request.app.state.engine_state = "stopping"
    manager: DeviceSessionManager = request.app.state.session_manager
    await manager.shutdown()
    return {"state": "stopping"}
