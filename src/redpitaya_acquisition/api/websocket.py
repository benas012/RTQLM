from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from redpitaya_acquisition.acquisition.manager import AcquisitionManager

router = APIRouter()


@router.websocket("/ws/samples")
async def samples(websocket: WebSocket) -> None:
    await websocket.accept()
    manager: AcquisitionManager = websocket.app.state.manager
    client_id, client = manager.broadcaster.subscribe()
    try:
        while True:
            message = await client.queue.get()
            try:
                await websocket.send_bytes(message)
            finally:
                client.queue.task_done()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        manager.broadcaster.unsubscribe(client_id)
