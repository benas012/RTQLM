from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.domain.frame import RawSampleFrame
from redpitaya_acquisition.domain.state import AcquisitionState
from redpitaya_acquisition.domain.status import StatusResponse
from redpitaya_acquisition.observability.metrics import AcquisitionMetrics
from redpitaya_acquisition.streaming.binary_protocol import serialize_frame
from redpitaya_acquisition.streaming.broadcaster import Broadcaster

from .source import AcquisitionSource

logger = logging.getLogger(__name__)


class NonRecoverableAcquisitionError(RuntimeError):
    """A configuration or vendor-library error that reconnecting cannot repair."""


SourceFactory = Callable[[Settings], AcquisitionSource]


class AcquisitionManager:
    def __init__(
        self, settings: Settings, source_factory: SourceFactory, broadcaster: Broadcaster
    ) -> None:
        self.settings = settings
        self._source_factory = source_factory
        self.broadcaster = broadcaster
        self.metrics = AcquisitionMetrics()
        self.state = AcquisitionState.STOPPED
        self.connected = False
        self.last_error: str | None = None
        self.latest_frame: RawSampleFrame | None = None
        self._source: AcquisitionSource | None = None
        self._streaming_module: str | None = None
        self._streaming_module_path: str | None = None
        self._queue: asyncio.Queue[RawSampleFrame] = asyncio.Queue(
            maxsize=settings.internal_queue_capacity
        )
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._supervisor_task: asyncio.Task[None] | None = None
        self._publisher_task: asyncio.Task[None] | None = None
        self._summary_task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[None] | None = None

    @property
    def source(self) -> AcquisitionSource | None:
        return self._source

    async def start(self) -> StatusResponse:
        async with self._lock:
            if self.state in {
                AcquisitionState.STARTING,
                AcquisitionState.CONNECTING,
                AcquisitionState.STREAMING,
                AcquisitionState.RECONNECTING,
            }:
                return self.status()
            self._set_state(AcquisitionState.STARTING)
            self.last_error = None
            self._stop_event.clear()
            loop = asyncio.get_running_loop()
            self._ready = loop.create_future()
            self._publisher_task = asyncio.create_task(self._publisher(), name="frame-publisher")
            self._summary_task = asyncio.create_task(
                self._periodic_summary(), name="metrics-summary"
            )
            self._supervisor_task = asyncio.create_task(
                self._supervise_source(), name="acquisition-supervisor"
            )
            ready = self._ready
        try:
            await ready
        except Exception:
            await self._cancel_owned_tasks()
            raise
        return self.status()

    async def stop(self) -> StatusResponse:
        async with self._lock:
            if self.state is AcquisitionState.STOPPED:
                return self.status()
            self._set_state(AcquisitionState.STOPPING)
            self._stop_event.set()
        await self._cancel_owned_tasks()
        self.connected = False
        self._source = None
        self._set_state(AcquisitionState.STOPPED)
        return self.status()

    async def replace_settings(self, settings: Settings) -> None:
        async with self._lock:
            if self.state is not AcquisitionState.STOPPED:
                raise RuntimeError("configuration can only be changed while acquisition is stopped")
            self.settings = settings
            self._queue = asyncio.Queue(maxsize=settings.internal_queue_capacity)

    async def _supervise_source(self) -> None:
        initial = True
        delay = self.settings.reconnect_initial_seconds
        try:
            while not self._stop_event.is_set():
                source: AcquisitionSource | None = None
                reconnect_wait_seconds: float | None = None
                try:
                    self._set_state(
                        AcquisitionState.CONNECTING if initial else AcquisitionState.RECONNECTING
                    )
                    source = self._source_factory(self.settings)
                    self._source = source
                    await source.connect()
                    await source.start()
                    self._streaming_module = source.module_name
                    self._streaming_module_path = source.module_path
                    self.connected = True
                    self._set_state(AcquisitionState.STREAMING)
                    if initial and self._ready is not None and not self._ready.done():
                        self._ready.set_result(None)
                    initial = False
                    delay = self.settings.reconnect_initial_seconds
                    async for frame in source.frames():
                        if self._stop_event.is_set():
                            break
                        self._accept_frame(frame)
                    if not self._stop_event.is_set():
                        raise ConnectionError("acquisition source stopped unexpectedly")
                except asyncio.CancelledError:
                    raise
                except NonRecoverableAcquisitionError as exc:
                    self._fault(exc)
                    if self._ready is not None and not self._ready.done():
                        self._ready.set_exception(exc)
                    return
                except Exception as exc:
                    self.connected = False
                    self.last_error = _sanitize_error(exc)
                    if initial:
                        self._fault(exc)
                        if self._ready is not None and not self._ready.done():
                            self._ready.set_exception(RuntimeError(self.last_error))
                        return
                    self._set_state(AcquisitionState.RECONNECTING)
                    jitter = random.uniform(0.0, min(0.25, delay * 0.1))
                    logger.warning("acquisition disconnected; reconnecting", extra={"delay": delay})
                    reconnect_wait_seconds = delay + jitter
                    delay = min(delay * 2.0, self.settings.reconnect_maximum_seconds)
                finally:
                    if source is not None:
                        try:
                            await source.stop()
                        except Exception:
                            logger.debug("source stop failed during cleanup", exc_info=True)
                        try:
                            await source.disconnect()
                        except Exception:
                            logger.debug("source disconnect failed during cleanup", exc_info=True)
                        if reconnect_wait_seconds is not None:
                            try:
                                await source.shutdown_remote_service()
                            except Exception:
                                logger.debug(
                                    "remote source shutdown failed during reconnect cleanup",
                                    exc_info=True,
                                )
                    self.connected = False
                if reconnect_wait_seconds is not None:
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(), timeout=reconnect_wait_seconds
                        )
                    except TimeoutError:
                        pass
        except asyncio.CancelledError:
            return

    def _accept_frame(self, frame: RawSampleFrame) -> None:
        self._refresh_source_counters()
        self.metrics.packets_received += 1
        self.metrics.sequence += 1
        self.metrics.observe_loss(1, frame.fpga_lost_ch1)
        self.metrics.observe_loss(2, frame.fpga_lost_ch2)
        if frame.packet_gap_detected:
            self.metrics.packet_id_gaps += 1
        accepted = replace(frame, sequence=self.metrics.sequence)
        if self._queue.full():
            self._queue.get_nowait()
            self._queue.task_done()
            self.metrics.service_dropped_frames += 1
            accepted = replace(accepted, service_drop_detected=True)
            logger.warning("internal frame queue dropped oldest frame")
        self.latest_frame = accepted
        self._queue.put_nowait(accepted)

    async def _publisher(self) -> None:
        try:
            while True:
                frame = await self._queue.get()
                try:
                    message = serialize_frame(frame)
                    self.broadcaster.publish(message)
                    self.metrics.frames_published += 1
                except ValueError as exc:
                    self.metrics.malformed_packets += 1
                    logger.warning("invalid frame rejected: %s", exc)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            return

    async def _periodic_summary(self) -> None:
        try:
            while True:
                await asyncio.sleep(10)
                logger.info(
                    "acquisition summary",
                    extra={
                        "state": self.state,
                        "packets_received": self.metrics.packets_received,
                        "frames_published": self.metrics.frames_published,
                        "service_dropped_frames": self.metrics.service_dropped_frames,
                        "websocket_clients": self.broadcaster.client_count,
                    },
                )
        except asyncio.CancelledError:
            return

    async def _cancel_owned_tasks(self) -> None:
        tasks = [
            task
            for task in (self._supervisor_task, self._publisher_task, self._summary_task)
            if task
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._supervisor_task = None
        self._publisher_task = None
        self._summary_task = None

    def _fault(self, exc: BaseException) -> None:
        self.connected = False
        self.last_error = _sanitize_error(exc)
        self._set_state(AcquisitionState.FAULTED)

    def _set_state(self, state: AcquisitionState) -> None:
        if state is not self.state:
            logger.info("acquisition state transition: %s -> %s", self.state, state)
            self.state = state

    def status(self) -> StatusResponse:
        self._refresh_source_counters()
        frame = self.latest_frame
        last_frame = (
            datetime.fromtimestamp(frame.captured_at_unix_ns / 1_000_000_000, tz=UTC)
            if frame
            else None
        )
        return StatusResponse(
            state=self.state,
            source=self.settings.source.value,
            host=self.settings.host,
            connected=self.connected,
            stream_id=frame.stream_id if frame else None,
            sequence=self.metrics.sequence,
            packets_received=self.metrics.packets_received,
            frames_published=self.metrics.frames_published,
            malformed_packets=self.metrics.malformed_packets,
            service_dropped_frames=self.metrics.service_dropped_frames,
            web_socket_clients=self.broadcaster.client_count,
            fpga_lost_ch1=self.metrics.fpga_lost_ch1,
            fpga_lost_ch2=self.metrics.fpga_lost_ch2,
            fpga_loss_deltas_ch1=self.metrics.fpga_loss_deltas_ch1,
            fpga_loss_deltas_ch2=self.metrics.fpga_loss_deltas_ch2,
            packet_id_gaps=self.metrics.packet_id_gaps,
            decimation=self.settings.decimation,
            base_rate_hz=frame.base_rate_hz if frame else 125_000_000,
            sample_rate_hz=frame.sample_rate_hz if frame else self.settings.sample_rate_hz,
            last_frame_utc=last_frame,
            last_error=self.last_error,
            streaming_module=self._streaming_module,
            streaming_module_path=self._streaming_module_path,
        )

    def latest_metadata(self) -> dict[str, Any] | None:
        frame = self.latest_frame
        if frame is None:
            return None
        return {
            "streamId": frame.stream_id,
            "sequence": frame.sequence,
            "sourcePackId": frame.source_pack_id,
            "capturedAtUnixNs": frame.captured_at_unix_ns,
            "baseRateHz": frame.base_rate_hz,
            "sampleRateHz": frame.sample_rate_hz,
            "decimation": frame.decimation,
            "samplesPerChannel": frame.samples_per_channel,
            "channelMask": frame.channel_mask,
            "fpgaLostCh1": frame.fpga_lost_ch1,
            "fpgaLostCh2": frame.fpga_lost_ch2,
            "packetGapDetected": frame.packet_gap_detected,
            "serviceDropDetected": frame.service_drop_detected,
        }

    def _refresh_source_counters(self) -> None:
        if self._source is not None:
            self.metrics.service_dropped_frames += self._source.take_dropped_frames()
            self.metrics.malformed_packets += self._source.take_malformed_packets()


def _sanitize_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return (text or type(exc).__name__)[:500]
