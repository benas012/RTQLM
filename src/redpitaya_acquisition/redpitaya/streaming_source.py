from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from redpitaya_acquisition.acquisition.manager import NonRecoverableAcquisitionError
from redpitaya_acquisition.acquisition.source import AcquisitionSource
from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.domain.frame import RawSampleFrame

from .app_manager import request_legacy_streaming_app_start, request_streaming_app_start
from .callback import create_callback
from .compatibility import StreamingCompatibility
from .host import streaming_host
from .library_loader import LoadedStreamingLibrary, load_streaming_library

logger = logging.getLogger(__name__)

POST_RESTART_CONNECT_TIMEOUT_SECONDS = 30.0
POST_RESTART_CONNECT_INTERVAL_SECONDS = 1.0
STREAMING_APP_RESTART_ATTEMPTS = 3


class RedPitayaStreamingSource(AcquisitionSource):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._library: LoadedStreamingLibrary | None = None
        self._client: Any = None
        self._adapter: StreamingCompatibility | None = None
        self._callback: Any = None
        self._queue: asyncio.Queue[RawSampleFrame | BaseException] = asyncio.Queue(
            maxsize=settings.internal_queue_capacity
        )
        self._wait_task: asyncio.Task[None] | None = None
        self._running = False
        self._dropped_frames = 0
        self._malformed_packets = 0

    @property
    def module_name(self) -> str | None:
        return self._library.name if self._library else None

    @property
    def module_path(self) -> str | None:
        return self._library.path if self._library else None

    def take_dropped_frames(self) -> int:
        value, self._dropped_frames = self._dropped_frames, 0
        return value

    def take_malformed_packets(self) -> int:
        value, self._malformed_packets = self._malformed_packets, 0
        return value

    async def connect(self) -> None:
        host = streaming_host(self._settings.host)
        self._library = load_streaming_library(self._settings.streaming_module)
        try:
            await self._connect_native_client(host)
            assert self._adapter is not None
            self._adapter.register_callback(self._callback)
            self._adapter.set_verbose(self._settings.verbose_library_logging)
            await asyncio.to_thread(self._adapter.configure, self._settings)
        except Exception:
            await self.disconnect()
            raise

    async def _connect_native_client(self, host: str) -> None:
        """Connect without disrupting an already-running board-side stream service."""

        self._prepare_native_client()
        assert self._adapter is not None
        try:
            await asyncio.to_thread(self._adapter.connect, host)
            return
        except Exception:
            if not self._settings.auto_start_streaming_app:
                raise
            logger.info(
                "Red Pitaya streaming service unavailable; restarting the board application",
                extra={"host": self._settings.host},
            )
            await self._restart_streaming_app_until_connected(host)

    async def _restart_streaming_app_until_connected(self, host: str) -> None:
        last_error: BaseException | None = None

        # Existing/newer Red Pitaya path
        for _attempt in range(1, STREAMING_APP_RESTART_ATTEMPTS + 1):
            await self.disconnect()

            await asyncio.to_thread(
                request_streaming_app_start,
                self._settings.host,
                self._settings.streaming_app_startup_delay_seconds,
            )

            try:
                await self._connect_native_client_after_restart(host)
                return
            except Exception as exc:
                last_error = exc

        # Legacy Red Pitaya fallback
        logger.info(
            "Bazaar streaming startup failed; trying legacy Red Pitaya startup",
            extra={"host": self._settings.host},
        )

        await self.disconnect()

        await asyncio.to_thread(
            request_legacy_streaming_app_start,
            self._settings.host,
        )

        try:
            await self._connect_native_client_after_restart(host)
            return
        except Exception as exc:
            last_error = exc

        assert last_error is not None
        raise last_error

    async def _connect_native_client_after_restart(self, host: str) -> None:
        deadline = time.monotonic() + POST_RESTART_CONNECT_TIMEOUT_SECONDS
        last_error: BaseException | None = None
        while True:
            self._prepare_native_client()
            assert self._adapter is not None
            try:
                await asyncio.to_thread(self._adapter.connect, host)
                return
            except Exception as exc:
                last_error = exc
                await self.disconnect()
                if time.monotonic() >= deadline:
                    raise last_error from exc
                await asyncio.sleep(POST_RESTART_CONNECT_INTERVAL_SECONDS)

    def _prepare_native_client(self) -> None:
        assert self._library is not None
        self._client = self._library.module.ADCStreamClient()
        self._adapter = StreamingCompatibility(self._client)
        loop = asyncio.get_running_loop()
        self._callback = create_callback(
            self._library.module.ADCCallback, loop, self._settings, self._enqueue, self._event
        )

    async def start(self) -> None:
        if self._adapter is None:
            raise RuntimeError("source is not connected")
        await asyncio.to_thread(self._adapter.start)
        self._running = True
        self._wait_task = asyncio.create_task(
            asyncio.to_thread(self._adapter.wait), name="redpitaya-native-wait"
        )

    async def stop(self) -> None:
        self._running = False
        if self._adapter is not None:
            await asyncio.to_thread(self._adapter.stop)
        if self._wait_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._wait_task), timeout=5.0)
            except TimeoutError:
                self._wait_task.cancel()
            except Exception:
                logger.debug("native wait ended with an error during stop", exc_info=True)
            self._wait_task = None

    async def disconnect(self) -> None:
        self._running = False
        if self._adapter is not None:
            await asyncio.to_thread(self._adapter.disconnect)
        self._adapter = None
        self._client = None
        self._callback = None

    async def frames(self) -> AsyncIterator[RawSampleFrame]:
        while self._running:
            get_task = asyncio.create_task(self._queue.get())
            watched: set[asyncio.Task[Any]] = {get_task}
            if self._wait_task is not None:
                watched.add(self._wait_task)
            done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
            if self._wait_task in done and not get_task.done():
                get_task.cancel()
                await asyncio.gather(get_task, return_exceptions=True)
                error = self._wait_task.exception() if self._wait_task else None
                raise ConnectionError("Red Pitaya streaming wait ended") from error
            item = get_task.result()
            self._queue.task_done()
            if isinstance(item, BaseException):
                raise item
            yield item

    def _enqueue(self, frame: RawSampleFrame) -> None:
        if self._queue.full():
            self._queue.get_nowait()
            self._queue.task_done()
            from dataclasses import replace

            frame = replace(frame, service_drop_detected=True)
            self._dropped_frames += 1
        self._queue.put_nowait(frame)

    def _event(self, event: str, detail: str | None) -> None:
        if event == "malformed":
            self._malformed_packets += 1
            logger.warning("malformed Red Pitaya packet rejected: %s", detail)
            return
        if event == "config_error":
            self._put_error(NonRecoverableAcquisitionError(detail or event))
        elif event in {"disconnected", "error"}:
            self._put_error(ConnectionError(detail or event))

    def _put_error(self, error: BaseException) -> None:
        if self._queue.full():
            self._queue.get_nowait()
            self._queue.task_done()
        self._queue.put_nowait(error)
