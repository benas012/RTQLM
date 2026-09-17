from __future__ import annotations

import asyncio


class ClientQueue:
    def __init__(self, capacity: int) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=capacity)
        self.dropped_messages = 0

    def put_latest(self, message: bytes) -> None:
        if self.queue.full():
            self.queue.get_nowait()
            self.queue.task_done()
            self.dropped_messages += 1
        self.queue.put_nowait(message)
