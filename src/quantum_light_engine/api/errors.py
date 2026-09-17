from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class EngineApiError(RuntimeError):
    def __init__(
        self,
        error_type: str,
        title: str,
        status: int,
        detail: str,
        device_id: UUID | None = None,
    ) -> None:
        super().__init__(detail)
        self.error_type = error_type
        self.title = title
        self.status = status
        self.detail = detail
        self.device_id = device_id


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(EngineApiError)
    async def engine_error(_request: Request, exc: EngineApiError) -> JSONResponse:
        body: dict[str, Any] = {
            "type": exc.error_type,
            "title": exc.title,
            "status": exc.status,
            "detail": exc.detail,
        }
        if exc.device_id is not None:
            body["deviceId"] = str(exc.device_id)
        return JSONResponse(status_code=exc.status, content=body)


def not_found(device_id: UUID) -> EngineApiError:
    return EngineApiError(
        "device-not-found", "Device not found", 404, "No registered device has this ID.", device_id
    )


def state_conflict(device_id: UUID, detail: str) -> EngineApiError:
    return EngineApiError(
        "device-state-conflict", "Device lifecycle operation is not allowed", 409, detail, device_id
    )
