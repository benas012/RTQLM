from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from quantum_light_engine.api.websocket_routes import stream_session
from redpitaya_acquisition.streaming.broadcaster import Broadcaster
from redpitaya_acquisition.streaming.client_queue import ClientQueue


class SignalingBroadcaster(Broadcaster):
    def __init__(self) -> None:
        super().__init__(queue_capacity=1)
        self.subscribed = asyncio.Event()

    def subscribe(self) -> tuple[int, ClientQueue]:
        subscription = super().subscribe()
        self.subscribed.set()
        return subscription


class ClosingWebSocket:
    def __init__(self) -> None:
        self.disconnect = asyncio.Event()
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict[str, str]:
        await self.disconnect.wait()
        return {"type": "websocket.disconnect"}

    async def send_bytes(self, _message: bytes) -> None:
        return

    async def close(self, *, code: int, reason: str) -> None:
        return


@pytest.mark.asyncio
async def test_stream_session_exits_when_an_idle_websocket_disconnects() -> None:
    broadcaster = SignalingBroadcaster()
    session = SimpleNamespace(broadcaster=broadcaster)
    websocket = ClosingWebSocket()
    task = asyncio.create_task(stream_session(websocket, session))

    await broadcaster.subscribed.wait()
    websocket.disconnect.set()

    await asyncio.wait_for(task, timeout=0.2)

    assert websocket.accepted
    assert broadcaster.client_count == 0
