from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from quantum_light_engine.devices.session import DeviceSession

router = APIRouter()


async def stream_session(websocket: WebSocket, session: DeviceSession) -> None:
    await websocket.accept()
    client_id, client = session.broadcaster.subscribe()
    disconnect_task = asyncio.create_task(_watch_for_disconnect(websocket))
    queue_task: asyncio.Task[bytes] | None = None
    try:
        while True:
            queue_task = asyncio.create_task(client.queue.get())
            done, _ = await asyncio.wait(
                (queue_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED
            )
            if disconnect_task in done:
                disconnect_task.result()
                if queue_task.done():
                    queue_task.result()
                    client.queue.task_done()
                else:
                    queue_task.cancel()
                    await asyncio.gather(queue_task, return_exceptions=True)
                queue_task = None
                return
            message = queue_task.result()
            queue_task = None
            try:
                if not message:
                    await websocket.close(code=1001, reason="engine shutdown")
                    return
                await websocket.send_bytes(message)
            finally:
                client.queue.task_done()
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        if queue_task is not None and not queue_task.done():
            queue_task.cancel()
            await asyncio.gather(queue_task, return_exceptions=True)
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)
        session.broadcaster.unsubscribe(client_id)


async def _watch_for_disconnect(websocket: WebSocket) -> None:
    """Return as soon as the client or server closes the WebSocket."""

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
    except (WebSocketDisconnect, RuntimeError):
        return


@router.websocket("/ws/v1/devices/{device_id}/samples")
async def device_samples(websocket: WebSocket, device_id: str) -> None:
    try:
        parsed = UUID(device_id)
    except ValueError:
        await websocket.close(code=4404, reason="unknown device")
        return
    session = websocket.app.state.session_manager.get(parsed)
    if session is None:
        await websocket.close(code=4404, reason="unknown device")
        return
    await stream_session(websocket, session)
