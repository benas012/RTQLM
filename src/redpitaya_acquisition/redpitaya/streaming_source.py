from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import AsyncIterator
from typing import Any

import numpy as np

from redpitaya_acquisition.acquisition.manager import NonRecoverableAcquisitionError
from redpitaya_acquisition.acquisition.source import AcquisitionSource
from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.domain.frame import RawSampleFrame

from .app_manager import (
    request_legacy_streaming_app_start,
    request_streaming_app_start,
    request_streaming_app_stop,
)
from .callback import create_callback
from .compatibility import StreamingCompatibility
from .host import streaming_host
from .library_loader import LoadedStreamingLibrary, load_streaming_library
from .native_worker import (
    NativeStreamingWorker,
    isolated_client_is_version_3,
    should_isolate_native_client,
)

logger = logging.getLogger(__name__)

POST_RESTART_CONNECT_TIMEOUT_SECONDS = 30.0
POST_RESTART_CONNECT_INTERVAL_SECONDS = 1.0
STREAMING_APP_RESTART_ATTEMPTS = 3
VERSION_3_CONFIG_PORT = 18_901
STREAMING_PORT_PROBE_TIMEOUT_SECONDS = 0.5
STREAMING_READY_CONSECUTIVE_PROBES = 3
STREAMING_READY_PROBE_INTERVAL_SECONDS = 0.25
TEST_OUTPUT_DAC_RATE_HZ = 1_953_125
TEST_OUTPUT_DAC_BUFFER_SIZE_BYTES = 1_638_400
TEST_OUTPUT_BUFFER_PERIODS = 64
# Despite saying "64 bytes", version 3.00 of the vendor client rejects
# 16-bit buffers unless the sample count itself is a multiple of 64.
DAC_16_BIT_SAMPLE_ALIGNMENT = 64


class RedPitayaStreamingSource(AcquisitionSource):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._library: LoadedStreamingLibrary | None = None
        self._client: Any = None
        self._config_client: Any = None
        self._adapter: StreamingCompatibility | None = None
        self._callback: Any = None
        self._test_output_client: Any = None
        self._native_worker: NativeStreamingWorker | None = None
        self._isolate_native_client = should_isolate_native_client(settings.streaming_module)
        self._isolated_client_is_version_3 = (
            self._isolate_native_client
            and isolated_client_is_version_3(settings.streaming_module)
        )
        self._module_name: str | None = None
        self._module_path: str | None = None
        self._queue: asyncio.Queue[RawSampleFrame | BaseException] = asyncio.Queue(
            maxsize=settings.internal_queue_capacity
        )
        self._wait_task: asyncio.Task[None] | None = None
        self._running = False
        self._connection_lost = False
        self._dropped_frames = 0
        self._malformed_packets = 0

    @property
    def module_name(self) -> str | None:
        return self._library.name if self._library else self._module_name

    @property
    def module_path(self) -> str | None:
        return self._library.path if self._library else self._module_path

    def take_dropped_frames(self) -> int:
        value, self._dropped_frames = self._dropped_frames, 0
        return value

    def take_malformed_packets(self) -> int:
        value, self._malformed_packets = self._malformed_packets, 0
        return value

    async def connect(self) -> None:
        host = streaming_host(self._settings.host)
        if not self._isolate_native_client:
            self._library = load_streaming_library(self._settings.streaming_module)
        try:
            await self._connect_native_client(host)
            if self._native_worker is not None:
                return
            assert self._adapter is not None
            await asyncio.to_thread(self._adapter.stop_remote_stream)
            self._adapter.register_callback(self._callback)
            self._adapter.set_verbose(self._settings.verbose_library_logging)
            await asyncio.to_thread(self._adapter.configure, self._settings)
        except Exception:
            await self.disconnect()
            await self.shutdown_remote_service()
            raise

    async def _connect_native_client(self, host: str) -> None:
        """Connect without disrupting an already-running board-side stream service."""

        if self._uses_version_3_client() and not await self._streaming_service_stable(host):
            if not self._settings.auto_start_streaming_app:
                raise ConnectionError(
                    f"Red Pitaya 3.00 streaming service is not listening on '{host}'"
                )
            logger.info(
                "Red Pitaya streaming service unavailable; starting the board application",
                extra={"host": self._settings.host},
            )
            await self._restart_streaming_app_until_connected(host)
            return

        try:
            await self._connect_native_client_once(host)
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
            if self._uses_version_3_client() and not await self._streaming_service_stable(host):
                if time.monotonic() >= deadline:
                    raise ConnectionError(
                        f"Red Pitaya 3.00 streaming service did not open configuration port "
                        f"{VERSION_3_CONFIG_PORT} on '{host}'"
                    )
                await asyncio.sleep(POST_RESTART_CONNECT_INTERVAL_SECONDS)
                continue
            try:
                await self._connect_native_client_once(host)
                return
            except Exception as exc:
                last_error = exc
                await self.disconnect()
                if time.monotonic() >= deadline:
                    raise last_error from exc
                await asyncio.sleep(POST_RESTART_CONNECT_INTERVAL_SECONDS)

    async def _streaming_service_stable(self, host: str) -> bool:
        for probe in range(STREAMING_READY_CONSECUTIVE_PROBES):
            if not await asyncio.to_thread(_streaming_service_ready, host):
                return False
            if probe + 1 < STREAMING_READY_CONSECUTIVE_PROBES:
                await asyncio.sleep(STREAMING_READY_PROBE_INTERVAL_SECONDS)
        return True

    def _uses_version_3_client(self) -> bool:
        if self._isolate_native_client:
            return self._isolated_client_is_version_3
        return self._library is not None and hasattr(self._library.module, "ConfigStreamClient")

    async def _connect_native_client_once(self, host: str) -> None:
        if self._isolate_native_client:
            worker = NativeStreamingWorker(self._settings, self._enqueue, self._event)
            try:
                await worker.connect(host)
            except Exception:
                await worker.close()
                raise
            self._native_worker = worker
            self._module_name = worker.module_name
            self._module_path = worker.module_path
            return
        self._prepare_native_client()
        assert self._adapter is not None
        await asyncio.to_thread(self._adapter.connect, host)

    def _prepare_native_client(self) -> None:
        assert self._library is not None
        module = self._library.module
        config_client_type = getattr(module, "ConfigStreamClient", None)
        if config_client_type is not None:
            self._config_client = config_client_type()
            self._client = module.ADCStreamClient(self._config_client)
        else:
            self._config_client = None
            self._client = module.ADCStreamClient()
        self._adapter = StreamingCompatibility(self._client, self._config_client)
        loop = asyncio.get_running_loop()
        self._callback = create_callback(
            module.ADCCallback, loop, self._settings, self._enqueue, self._event
        )

    async def start(self) -> None:
        if self._native_worker is not None:
            await self._native_worker.start()
            self._connection_lost = False
            self._running = True
            self._wait_task = self._native_worker.wait_task
            return
        if self._adapter is None:
            raise RuntimeError("source is not connected")
        await asyncio.to_thread(self._adapter.start)
        self._connection_lost = False
        self._running = True
        self._wait_task = asyncio.create_task(
            asyncio.to_thread(self._adapter.wait), name="redpitaya-native-wait"
        )

    async def stop(self) -> None:
        self._running = False
        if self._native_worker is not None:
            if not self._connection_lost:
                await self._native_worker.stop()
            return
        if self._adapter is not None and not self._connection_lost:
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
        worker, self._native_worker = self._native_worker, None
        if worker is not None:
            await worker.close()
            self._wait_task = None
            self._connection_lost = False
            return
        if self._connection_lost:
            self._test_output_client = None
        else:
            await self.set_test_output1_sine(
                False,
                frequency_hz=1_000,
                amplitude_volts=0.2,
            )
        if self._adapter is not None and not self._connection_lost:
            await asyncio.to_thread(self._adapter.disconnect)
        self._adapter = None
        self._client = None
        self._config_client = None
        self._callback = None
        self._connection_lost = False

    async def shutdown_remote_service(self) -> None:
        if not self._settings.auto_start_streaming_app:
            return
        await asyncio.to_thread(request_streaming_app_stop, self._settings.host)

    async def set_test_output1_sine(
        self,
        enabled: bool,
        *,
        frequency_hz: float,
        amplitude_volts: float,
    ) -> None:
        if self._native_worker is not None:
            await self._native_worker.set_test_output1_sine(
                enabled,
                frequency_hz=frequency_hz,
                amplitude_volts=amplitude_volts,
            )
            return
        if enabled:
            await asyncio.to_thread(
                self._start_test_output1_sine,
                frequency_hz,
                amplitude_volts,
            )
        else:
            await asyncio.to_thread(self._stop_test_output1_sine)

    def _start_test_output1_sine(self, frequency_hz: float, amplitude_volts: float) -> None:
        if self._test_output_client is not None:
            return
        if self._library is None or self._config_client is None or self._adapter is None:
            raise RuntimeError(
                "OUT1 testing requires an active Red Pitaya 3.x streaming connection"
            )
        module = self._library.module
        if not hasattr(module, "DACStreamClient"):
            raise RuntimeError("the installed Red Pitaya streaming client has no DAC support")
        if frequency_hz <= 0 or not 0 < amplitude_volts <= 1:
            raise ValueError("test sine frequency and amplitude must be positive and safe")

        host = self._adapter.host or streaming_host(self._settings.host)
        dac_client = module.DACStreamClient(self._config_client)
        try:
            for key, value in (
                ("dac_pass_mode", "DAC_NET"),
                ("dac_rate", str(TEST_OUTPUT_DAC_RATE_HZ)),
                ("dac_size", str(TEST_OUTPUT_DAC_BUFFER_SIZE_BYTES)),
            ):
                try:
                    result = self._config_client.sendConfig(host, key, value)
                except TypeError:
                    result = self._config_client.sendConfig(key, value)
                _require_success(f"configure {key}", result)

            unaligned_samples = TEST_OUTPUT_DAC_RATE_HZ * TEST_OUTPUT_BUFFER_PERIODS / frequency_hz
            buffer_samples = max(
                DAC_16_BIT_SAMPLE_ALIGNMENT,
                round(unaligned_samples / DAC_16_BIT_SAMPLE_ALIGNMENT)
                * DAC_16_BIT_SAMPLE_ALIGNMENT,
            )
            phase = np.arange(buffer_samples, dtype=np.float64) * (
                2 * np.pi * TEST_OUTPUT_BUFFER_PERIODS / buffer_samples
            )
            peak_counts = round(np.iinfo(np.int16).max * amplitude_volts)
            samples = (np.sin(phase) * peak_counts).astype(np.int16).tolist()
            _require_success("load OUT1 sine buffer", dac_client.setMemory16Bit(1, samples))
            dac_client.setRepeatInf(True)
            _require_success(
                "start OUT1 DAC stream",
                dac_client.startStreamingFromMemory(host),
            )
        except Exception:
            try:
                dac_client.notifyStop()
            except Exception:
                pass
            raise
        self._test_output_client = dac_client

    def _stop_test_output1_sine(self) -> None:
        client, self._test_output_client = self._test_output_client, None
        if client is None:
            return
        try:
            if hasattr(client, "stopStreaming"):
                client.stopStreaming()
        finally:
            client.notifyStop()
        client.wait()

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
            self._connection_lost = True
            if self._running:
                logger.warning("Red Pitaya streaming connection lost: %s", detail or event)
                self._put_error(ConnectionError(detail or event))

    def _put_error(self, error: BaseException) -> None:
        if self._queue.full():
            self._queue.get_nowait()
            self._queue.task_done()
        self._queue.put_nowait(error)


def _require_success(operation: str, result: Any) -> None:
    integer_failure = isinstance(result, int) and not isinstance(result, bool) and result < 0
    if result is False or integer_failure:
        raise RuntimeError(f"Red Pitaya could not {operation}")


def _streaming_service_ready(host: str) -> bool:
    """Avoid entering the 3.00 native client until its configuration service is listening.

    The vendor library terminates the whole process on some refused connections,
    so this Python-level probe is a required crash guard rather than only a
    readiness optimization. The ADC data port must not be probed: its server
    treats a bare TCP connection as a real stream and stops when it closes.
    """

    try:
        with socket.create_connection(
            (host, VERSION_3_CONFIG_PORT), timeout=STREAMING_PORT_PROBE_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        return False
