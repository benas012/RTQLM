from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from redpitaya_acquisition.acquisition.manager import AcquisitionManager
from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.domain.state import AcquisitionState
from redpitaya_acquisition.domain.status import StatusResponse

from .schemas import ConfigurationResponse, ConfigurationUpdate

router = APIRouter()


def _manager(request: Request) -> AcquisitionManager:
    return request.app.state.manager  # type: ignore[no-any-return]


@router.get("/api/status", response_model=StatusResponse, response_model_by_alias=True)
async def get_status(request: Request) -> StatusResponse:
    return _manager(request).status()


@router.get("/api/config", response_model=ConfigurationResponse, response_model_by_alias=True)
async def get_config(request: Request) -> ConfigurationResponse:
    return ConfigurationResponse.from_settings(_manager(request).settings)


@router.put("/api/config", response_model=ConfigurationResponse, response_model_by_alias=True)
async def put_config(request: Request, configuration: ConfigurationUpdate) -> ConfigurationResponse:
    manager = _manager(request)
    if manager.state is not AcquisitionState.STOPPED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="configuration can only be changed while acquisition is stopped",
        )
    await manager.replace_settings(Settings.model_validate(configuration.model_dump()))
    return ConfigurationResponse.from_settings(manager.settings)


@router.post("/api/start", response_model=StatusResponse, response_model_by_alias=True)
async def start(request: Request) -> StatusResponse:
    try:
        return await _manager(request).start()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/api/stop", response_model=StatusResponse, response_model_by_alias=True)
async def stop(request: Request) -> StatusResponse:
    return await _manager(request).stop()


@router.get("/api/latest")
async def latest(request: Request) -> dict[str, object]:
    metadata = _manager(request).latest_metadata()
    if metadata is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no frame received yet")
    return metadata


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    manager = _manager(request)
    if manager.state is AcquisitionState.FAULTED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="service faulted"
        )
    return {"status": "ready"}
