from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response, status

from quantum_light_engine.api.errors import EngineApiError, not_found, state_conflict
from quantum_light_engine.api.schemas import TestOutputRequest
from quantum_light_engine.devices.discovery import SavedProfileDiscovery
from quantum_light_engine.devices.models import DeviceProfileCreate, DeviceProfileUpdate
from quantum_light_engine.devices.nullifiers import NullifierCommand
from quantum_light_engine.devices.session import DeviceSession
from quantum_light_engine.devices.session_manager import DeviceSessionManager
from quantum_light_engine.devices.trigger import TriggerConfig
from redpitaya_acquisition.redpitaya.output_signals import OutputSignalsConfig

router = APIRouter(prefix="/api/v1/devices")


def _manager(request: Request) -> DeviceSessionManager:
    return request.app.state.session_manager  # type: ignore[no-any-return]


def _session(request: Request, device_id: UUID) -> DeviceSession:
    session = _manager(request).get(device_id)
    if session is None:
        raise not_found(device_id)
    return session


def _device_response(session: DeviceSession) -> dict[str, Any]:
    return {
        **session.profile.model_dump(mode="json", by_alias=True),
        "runtime": session.status(),
    }


@router.get("")
async def list_devices(request: Request) -> list[dict[str, Any]]:
    return [_device_response(session) for session in _manager(request).sessions()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_device(request: Request, profile: DeviceProfileCreate) -> dict[str, Any]:
    try:
        return _device_response(await _manager(request).create(profile))
    except ValueError as exc:
        raise state_conflict(UUID(int=0), str(exc)) from exc


@router.post("/discover")
async def discover_devices(request: Request, timeout_seconds: float = 1.0) -> list[dict[str, Any]]:
    bounded = min(max(timeout_seconds, 0.05), 10.0)
    provider = SavedProfileDiscovery(_manager(request).repository)
    devices = await asyncio.wait_for(provider.discover(bounded), timeout=bounded)
    return [device.model_dump() for device in devices]


@router.get("/{device_id}")
async def get_device(request: Request, device_id: UUID) -> dict[str, Any]:
    return _device_response(_session(request, device_id))


@router.put("/{device_id}")
async def update_device(
    request: Request, device_id: UUID, profile: DeviceProfileUpdate
) -> dict[str, Any]:
    try:
        return _device_response(await _manager(request).update(device_id, profile))
    except KeyError as exc:
        raise not_found(device_id) from exc
    except (RuntimeError, ValueError) as exc:
        raise state_conflict(device_id, str(exc)) from exc


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(request: Request, device_id: UUID) -> Response:
    try:
        await _manager(request).delete(device_id)
    except KeyError as exc:
        raise not_found(device_id) from exc
    except RuntimeError as exc:
        raise state_conflict(device_id, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _lifecycle(request: Request, device_id: UUID, operation: str) -> dict[str, Any]:
    session = _session(request, device_id)
    try:
        result: dict[str, Any] = await getattr(session, operation)()
        return result
    except Exception as exc:
        lifecycle_conflict = isinstance(exc, RuntimeError) and "current state" in str(exc).lower()
        code = 409 if lifecycle_conflict else 503
        if code == 503:
            raise EngineApiError(
                "streaming-component-unavailable",
                "Streaming component unavailable",
                503,
                str(exc),
                device_id,
            ) from exc
        raise state_conflict(device_id, str(exc)) from exc


@router.post("/{device_id}/connect")
async def connect(request: Request, device_id: UUID) -> dict[str, Any]:
    return await _lifecycle(request, device_id, "connect")


@router.post("/{device_id}/disconnect")
async def disconnect(request: Request, device_id: UUID) -> dict[str, Any]:
    return await _lifecycle(request, device_id, "disconnect")


@router.post("/{device_id}/start")
async def start(request: Request, device_id: UUID) -> dict[str, Any]:
    return await _lifecycle(request, device_id, "start")


@router.post("/{device_id}/stop")
async def stop(request: Request, device_id: UUID) -> dict[str, Any]:
    return await _lifecycle(request, device_id, "stop")


@router.post("/{device_id}/outputs/1/test-sine")
async def set_test_output_sine(
    request: Request,
    device_id: UUID,
    output: TestOutputRequest,
) -> dict[str, Any]:
    session = _session(request, device_id)
    try:
        return await session.set_test_output1_sine(
            output.enabled,
            frequency_hz=1_000,
            amplitude_volts=0.2,
        )
    except RuntimeError as exc:
        detail = str(exc)
        if "connect the Red Pitaya" in detail or "only available" in detail:
            raise state_conflict(device_id, detail) from exc
        raise EngineApiError(
            "test-output-unavailable",
            "Red Pitaya test output unavailable",
            503,
            detail,
            device_id,
        ) from exc
    except Exception as exc:
        raise EngineApiError(
            "test-output-unavailable",
            "Red Pitaya test output unavailable",
            503,
            str(exc),
            device_id,
        ) from exc


@router.get("/{device_id}/outputs")
async def get_outputs(request: Request, device_id: UUID) -> dict[str, object]:
    return _session(request, device_id).output_status()


@router.put("/{device_id}/outputs")
async def configure_outputs(
    request: Request, device_id: UUID, config: OutputSignalsConfig
) -> dict[str, object]:
    try:
        return await _session(request, device_id).set_output_signals(config)
    except RuntimeError as exc:
        detail = str(exc)
        if "connect the Red Pitaya" in detail or "only available" in detail:
            raise state_conflict(device_id, detail) from exc
        raise EngineApiError(
            "outputs-unavailable", "Outputs unavailable", 503, detail, device_id
        ) from exc
    except Exception as exc:
        raise EngineApiError(
            "outputs-unavailable", "Outputs unavailable", 503, str(exc), device_id
        ) from exc


@router.post("/{device_id}/outputs/stop")
async def stop_outputs(request: Request, device_id: UUID) -> dict[str, object]:
    config = OutputSignalsConfig.model_validate(
        _session(request, device_id).output_status()["config"]
    )
    config.output1.enabled = False
    config.output2.enabled = False
    return await configure_outputs(request, device_id, config)


@router.get("/{device_id}/status")
async def device_status(request: Request, device_id: UUID) -> dict[str, Any]:
    return _session(request, device_id).status()


@router.get("/{device_id}/trigger")
async def get_trigger(request: Request, device_id: UUID) -> dict[str, object]:
    return _session(request, device_id).trigger.status()


@router.put("/{device_id}/trigger")
async def configure_trigger(
    request: Request, device_id: UUID, config: TriggerConfig
) -> dict[str, object]:
    session = _session(request, device_id)
    try:
        return await session.configure_trigger(config)
    except RuntimeError as exc:
        detail = str(exc)
        if "only available" in detail or "stop ADC acquisition" in detail:
            raise state_conflict(device_id, detail) from exc
        raise EngineApiError(
            "external-trigger-unavailable", "External trigger unavailable", 503, detail, device_id
        ) from exc


@router.get("/{device_id}/latest")
async def latest(request: Request, device_id: UUID) -> dict[str, Any]:
    metadata = _session(request, device_id).manager.latest_metadata()
    if metadata is None:
        raise EngineApiError(
            "frame-not-available",
            "No frame available",
            404,
            "The device has not produced a valid frame.",
            device_id,
        )
    return metadata


@router.get("/{device_id}/nullifiers")
async def nullifier_status(request: Request, device_id: UUID) -> dict[str, object]:
    return await asyncio.to_thread(_session(request, device_id).nullifiers.status)


@router.post("/{device_id}/nullifiers/commands")
async def nullifier_command(
    request: Request, device_id: UUID, command: NullifierCommand,
) -> dict[str, object]:
    processor = _session(request, device_id).nullifiers
    try:
        return await asyncio.to_thread(processor.execute, command)
    except ValueError as exc:
        code = 422 if command.command == "configure" else 409
        raise EngineApiError(
            "invalid-nullifier-command", "Nullifier command rejected", code, str(exc), device_id,
        ) from exc
    except RuntimeError as exc:
        raise EngineApiError(
            "nullifier-state-conflict", "Nullifier command rejected", 409, str(exc), device_id,
        ) from exc


@router.get("/{device_id}/nullifiers/latest")
async def latest_nullifiers(request: Request, device_id: UUID) -> dict[str, object]:
    result = await asyncio.to_thread(_session(request, device_id).nullifiers.latest)
    if result is None:
        raise EngineApiError(
            "nullifiers-not-available", "No nullifier result available", 404,
            "Configure nullifier processing and acquire complete temporal modes.", device_id,
        )
    return result
