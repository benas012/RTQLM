from __future__ import annotations

import logging

from .client_queue import ClientQueue

logger = logging.getLogger(__name__)


class Broadcaster:
    def __init__(self, queue_capacity: int) -> None:
        self._capacity = queue_capacity
        self._clients: dict[int, ClientQueue] = {}
        self._next_id = 0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def subscribe(self) -> tuple[int, ClientQueue]:
        self._next_id += 1
        client = ClientQueue(self._capacity)
        self._clients[self._next_id] = client
        logger.info("WebSocket client connected", extra={"client_id": self._next_id})
        return self._next_id, client

    def unsubscribe(self, client_id: int) -> None:
        client = self._clients.pop(client_id, None)
        if client is not None:
            logger.info(
                "WebSocket client disconnected",
                extra={"client_id": client_id, "dropped_messages": client.dropped_messages},
            )

    def publish(self, message: bytes) -> None:
        for client in tuple(self._clients.values()):
            client.put_latest(message)

    def close(self) -> None:
        for client in tuple(self._clients.values()):
            client.put_latest(b"")
        self._clients.clear()
