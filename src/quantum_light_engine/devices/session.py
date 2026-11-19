from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from quantum_light_engine.config.settings import EngineSettings
from redpitaya_acquisition.acquisition.manager import AcquisitionManager
from redpitaya_acquisition.acquisition.simulator import SimulatedAcquisitionSource
from redpitaya_acquisition.acquisition.source import AcquisitionSource
from redpitaya_acquisition.config.settings import Settings as AcquisitionSettings
from redpitaya_acquisition.domain.frame import RawSampleFrame
from redpitaya_acquisition.domain.state import AcquisitionState
from redpitaya_acquisition.redpitaya.output_signals import OutputSignalsConfig
from redpitaya_acquisition.redpitaya.streaming_source import RedPitayaStreamingSource
from redpitaya_acquisition.streaming.broadcaster import Broadcaster

from .board_nullifiers import BoardNullifierProcessor
from .buffered_source import BufferedCaptureSource
from .models import DeviceProfile, DeviceSource
from .nullifiers import DeviceNullifierProcessor
from .trigger import ExternalTrigger, TriggerConfig

logger = logging.getLogger(__name__)


class DeviceSessionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    READY = "ready"
    STARTING = "starting"
    STREAMING = "streaming"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    FAULTED = "faulted"


BUSY_SESSION_STATES = frozenset(
    {
        DeviceSessionState.CONNECTING,
        DeviceSessionState.STARTING,
        DeviceSessionState.STREAMING,
        DeviceSessionState.RECONNECTING,
        DeviceSessionState.STOPPING,
    }
)


class _PreparedSource(AcquisitionSource):
    """Hands an already connected source to the reusable acquisition manager."""

    def __init__(
        self, source: AcquisitionSource, *, connected: bool = True,
        prepare: Callable[[], None] | None = None,
    ) -> None:
        self.source = source
        self._connected = connected
        self._prepare = prepare

    async def connect(self) -> None:
        if self._prepare is not None:
            await asyncio.to_thread(self._prepare)
        if not self._connected:
            await self.source.connect()
            self._connected = True

    async def start(self) -> None:
        await self.source.start()

    async def stop(self) -> None:
        await self.source.stop()

    async def disconnect(self) -> None:
        await self.source.disconnect()

    def frames(self) -> AsyncIterator[RawSampleFrame]:
        return self.source.frames()

    @property
    def module_name(self) -> str | None:
        return self.source.module_name

    @property
    def module_path(self) -> str | None:
        return self.source.module_path

    def take_dropped_frames(self) -> int:
        return self.source.take_dropped_frames()

    def take_malformed_packets(self) -> int:
        return self.source.take_malformed_packets()

    async def set_test_output1_sine(
        self,
        enabled: bool,
        *,
        frequency_hz: float,
        amplitude_volts: float,
    ) -> None:
        await self.source.set_test_output1_sine(
            enabled,
            frequency_hz=frequency_hz,
            amplitude_volts=amplitude_volts,
        )

    async def set_output_signals(self, config: OutputSignalsConfig) -> None:
        await self.source.set_output_signals(config)

    def output_status(self) -> dict[str, object]:
        return self.source.output_status()

    async def shutdown_remote_service(self) -> None:
        await self.source.shutdown_remote_service()


class DeviceSession:
    def __init__(self, profile: DeviceProfile, engine_settings: EngineSettings) -> None:
        self.profile = profile
        self.engine_settings = engine_settings
        self._settings = acquisition_settings(profile, engine_settings)
        self.broadcaster = Broadcaster(engine_settings.client_queue_capacity)
        self.trigger = ExternalTrigger()
        self.nullifiers: DeviceNullifierProcessor = self._new_nullifiers()
        self._prepared_source: AcquisitionSource | None = None
        self._buffered_source: BufferedCaptureSource | None = None
        self._lock = asyncio.Lock()
        self._state = DeviceSessionState.DISCONNECTED
        self._last_error: str | None = None
        self._disconnecting = False
        self.manager = AcquisitionManager(
            self._settings, self._source_factory, self.broadcaster,
            frame_handler=self.nullifiers.process_frame,
        )

    @property
    def state(self) -> DeviceSessionState:
        if self.manager.state is AcquisitionState.RECONNECTING:
            return DeviceSessionState.RECONNECTING
        if (
            self.manager.state is AcquisitionState.FAULTED
            and self._state is not DeviceSessionState.READY
        ):
            return DeviceSessionState.FAULTED
        return self._state

    @property
    def connected(self) -> bool:
        return self.state not in {DeviceSessionState.DISCONNECTED, DeviceSessionState.FAULTED}

    async def connect(self) -> dict[str, Any]:
        async with self._lock:
            if self.connected:
                return self.status()
            await self._connect_locked()
            return self.status()

    async def _connect_locked(self) -> None:
        await self.trigger.stop()
        self._transition(DeviceSessionState.CONNECTING)
        source: AcquisitionSource | None = None
        try:
            source = self._new_source()
            if (isinstance(self.nullifiers, BoardNullifierProcessor)
                    and self.profile.acquisition_mode != "buffered"):
                await asyncio.to_thread(self.nullifiers.prepare_stream)
            await source.connect()
            if self.profile.server_stream_processing:
                await asyncio.to_thread(self.nullifiers.prepare_stream)
            self._prepared_source = source
            self._last_error = None
            self._transition(DeviceSessionState.READY)
        except Exception as exc:
            self._last_error = _sanitize(exc)
            self._transition(DeviceSessionState.FAULTED)
            try:
                if source is not None:
                    await source.disconnect()
                if isinstance(self.nullifiers, BoardNullifierProcessor):
                    await asyncio.to_thread(self.nullifiers.close)
                elif self.profile.server_stream_processing:
                    await asyncio.to_thread(self.nullifiers.close)
            except Exception:
                logger.debug("source cleanup after failed connect", exc_info=True)
            raise

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self.state is DeviceSessionState.STREAMING:
                return self.status()
            if self.state in {DeviceSessionState.DISCONNECTED, DeviceSessionState.FAULTED}:
                await self._connect_locked()
            self._transition(DeviceSessionState.STARTING)
            if self.profile.acquisition_mode != "buffered":
                await self.trigger.stop()
            try:
                await self.manager.start()
                self._transition(DeviceSessionState.STREAMING)
                self._last_error = None
            except Exception as exc:
                self._last_error = _sanitize(exc)
                self._transition(DeviceSessionState.FAULTED)
                raise
            return self.status()

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            if self.state in {DeviceSessionState.DISCONNECTED, DeviceSessionState.READY}:
                return self.status()
            self._transition(DeviceSessionState.STOPPING)
            await self.manager.stop()
            if isinstance(self.nullifiers, BoardNullifierProcessor):
                await asyncio.to_thread(self.nullifiers.stop_stream)
            await asyncio.to_thread(self.nullifiers.reset_stream)
            if self._disconnecting:
                self._transition(DeviceSessionState.DISCONNECTED)
            elif self._buffered_source is not None and self.profile.acquisition_mode == "buffered":
                self._prepared_source = self._buffered_source
                self._transition(DeviceSessionState.READY)
            else:
                await self._connect_locked()
            return self.status()

    async def disconnect(self) -> dict[str, Any]:
        async with self._lock:
            await self.trigger.stop()
            active_source = self._prepared_source or self.manager.source
            if self.state is DeviceSessionState.DISCONNECTED:
                if isinstance(self.nullifiers, BoardNullifierProcessor):
                    await asyncio.to_thread(self.nullifiers.close)
                elif self.profile.server_stream_processing:
                    await asyncio.to_thread(self.nullifiers.close)
                if active_source is None and self.profile.source is DeviceSource.REDPITAYA:
                    active_source = self._new_source()
                if active_source is not None:
                    await active_source.shutdown_remote_service()
                return self.status()
            self._disconnecting = True
            try:
                if self.manager.state is not AcquisitionState.STOPPED:
                    self._transition(DeviceSessionState.STOPPING)
                    await self.manager.stop()
                await asyncio.to_thread(self.nullifiers.reset_stream, clear_calibration=True)
                if self._prepared_source is not None:
                    await self._prepared_source.disconnect()
                    self._prepared_source = None
                if active_source is not None:
                    await active_source.shutdown_remote_service()
                if isinstance(self.nullifiers, BoardNullifierProcessor):
                    await asyncio.to_thread(self.nullifiers.close)
                self._transition(DeviceSessionState.DISCONNECTED)
                if self.profile.server_stream_processing:
                    await asyncio.to_thread(self.nullifiers.close)
            finally:
                self._disconnecting = False
            return self.status()

    async def update_profile(self, profile: DeviceProfile) -> None:
        async with self._lock:
            if self.state not in {DeviceSessionState.DISCONNECTED, DeviceSessionState.READY}:
                raise RuntimeError("device profile cannot be updated in the current state")
            await self.trigger.stop()
            if self._prepared_source is not None:
                source = self._prepared_source
                await source.disconnect()
                await source.shutdown_remote_service()
                self._prepared_source = None
            if self.profile.server_stream_processing:
                await asyncio.to_thread(self.nullifiers.close)
            self.profile = profile
            if isinstance(self.nullifiers, BoardNullifierProcessor):
                await asyncio.to_thread(self.nullifiers.close)
            self.nullifiers = self._new_nullifiers()
            await asyncio.to_thread(self.nullifiers.reset_stream, clear_calibration=True)
            self._settings = acquisition_settings(profile, self.engine_settings)
            self.manager.settings = self._settings
            self._transition(DeviceSessionState.DISCONNECTED)

    async def set_test_output1_sine(
        self,
        enabled: bool,
        *,
        frequency_hz: float,
        amplitude_volts: float,
    ) -> dict[str, Any]:
        async with self._lock:
            if self.profile.source is not DeviceSource.REDPITAYA:
                raise RuntimeError("OUT1 test output is only available for Red Pitaya devices")
            if self.state not in {DeviceSessionState.READY, DeviceSessionState.STREAMING}:
                raise RuntimeError("connect the Red Pitaya before controlling OUT1")
            source = self._prepared_source if self.state is DeviceSessionState.READY else None
            source = source or self.manager.source
            if source is None:
                raise RuntimeError("the active Red Pitaya source is unavailable")
            await source.set_test_output1_sine(
                enabled,
                frequency_hz=frequency_hz,
                amplitude_volts=amplitude_volts,
            )
            return {
                "enabled": enabled,
                "channel": 1,
                "waveform": "sine",
                "frequencyHz": frequency_hz,
                "amplitudeVolts": amplitude_volts,
            }

    async def set_output_signals(self, config: OutputSignalsConfig) -> dict[str, object]:
        async with self._lock:
            if self.profile.source is not DeviceSource.REDPITAYA:
                raise RuntimeError("custom outputs are only available for Red Pitaya devices")
            if self.state not in {DeviceSessionState.READY, DeviceSessionState.STREAMING}:
                raise RuntimeError("connect the Red Pitaya before controlling outputs")
            source = self._prepared_source or self.manager.source
            if source is None:
                raise RuntimeError("the active Red Pitaya source is unavailable")
            await source.set_output_signals(config)
            return source.output_status()

    async def configure_trigger(self, config: TriggerConfig) -> dict[str, object]:
        async with self._lock:
            if not config.enabled:
                return await self.trigger.configure(self.profile.host or "", config)
            if self.profile.source is not DeviceSource.REDPITAYA:
                raise RuntimeError("external triggering is only available for Red Pitaya devices")
            buffered_live = (
                self.profile.acquisition_mode == "buffered"
                and isinstance(self.nullifiers, BoardNullifierProcessor)
                and self.state is DeviceSessionState.STREAMING
            )
            if (self.state not in {DeviceSessionState.DISCONNECTED, DeviceSessionState.READY}
                    and not buffered_live):
                raise RuntimeError("stop ADC acquisition before enabling external pin polling")
            password = self.engine_settings.trigger_ssh_password
            return await self.trigger.configure(
                self.profile.host or "", config,
                password.get_secret_value() if password is not None else None,
            )

    def output_status(self) -> dict[str, object]:
        source = self._prepared_source or self.manager.source
        if source is not None and self.state in {
            DeviceSessionState.READY,
            DeviceSessionState.STREAMING,
        }:
            return source.output_status()
        return {
            "state": "unavailable",
            "config": OutputSignalsConfig().model_dump(mode="json", by_alias=True),
        }

    def status(self) -> dict[str, Any]:
        base = self.manager.status().model_dump(mode="json", by_alias=True)
        base.update(
            {
                "deviceId": str(self.profile.id),
                "name": self.profile.name,
                "source": self.profile.source.value,
                "host": self.profile.host,
                "state": self.state.value,
                "connected": self.connected,
                "lastError": self._last_error or base.get("lastError"),
                "externalTrigger": self.trigger.status(),
                "nullifierBackend": getattr(self.nullifiers, "backend", "host-python"),
                "acquisitionMode": self.profile.acquisition_mode,
                "captureSamples": self.profile.capture_samples,
                "captureDurationSeconds": (
                    .005 if self.profile.server_stream_processing else
                    self.profile.capture_samples / self._settings.sample_rate_hz),
                "measurementWindowMs": self.profile.measurement_window_ms,
            }
        )
        return base

    def _source_factory(self, _settings: AcquisitionSettings) -> AcquisitionSource:
        prepare = (
            self.nullifiers.prepare_stream
            if (isinstance(self.nullifiers, BoardNullifierProcessor)
                and self.profile.acquisition_mode != "buffered") else None
        )
        if self._prepared_source is not None:
            source, self._prepared_source = self._prepared_source, None
            return _PreparedSource(source, prepare=prepare)
        if prepare is not None:
            return _PreparedSource(self._new_source(), connected=False, prepare=prepare)
        return self._new_source()

    def _new_source(self) -> AcquisitionSource:
        if self.profile.server_stream_processing:
            from .ring_source import RingStreamingSource
            return RingStreamingSource(self.profile)
        if self.profile.source is DeviceSource.SIMULATOR:
            return SimulatedAcquisitionSource(self._settings)
        if self.profile.acquisition_mode == "buffered":
            if not isinstance(self.nullifiers, BoardNullifierProcessor):
                raise RuntimeError(
                    f"ARM processing is not enabled for {self.profile.host}. "
                    "Check QL_NATIVE_NULLIFIER_ENABLED and QL_NATIVE_NULLIFIER_HOSTS, "
                    "then restart the engine. The C processor is checked after enabling ARM "
                    "processing; missing processors are installed automatically if "
                    "QL_NATIVE_NULLIFIER_AUTO_INSTALL=true."
                )
            # The session owns the C processor. Keep its calibration and SDK
            # outputs across Stop; Disconnect releases the hardware explicitly.
            if (self._buffered_source is None
                    or self._buffered_source.processor is not self.nullifiers):
                self._buffered_source = BufferedCaptureSource(
                    self.nullifiers, close_processor=False)
            return self._buffered_source
        return RedPitayaStreamingSource(self._settings)

    def _new_nullifiers(self) -> DeviceNullifierProcessor:
        if self.profile.server_stream_processing:
            from .gated_streaming import GatedStreamingProcessor
            return GatedStreamingProcessor(
                self.profile.host or "", self.profile.measurement_window_ms)
        if (self.profile.source is DeviceSource.REDPITAYA
                and self.profile.acquisition_mode == "buffered"
                and self.profile.buffered_processing == "server"):
            from .server_nullifiers import ServerNullifierProcessor
            processor = ServerNullifierProcessor(
                self.profile.host or "", self.engine_settings.buffered_board_executable,
                self.profile.decimation, 3, self.engine_settings.native_nullifier_port,
                server_command=self.engine_settings.buffered_server_command,
                transport=self.engine_settings.buffered_transport,
                binary_port=self.engine_settings.buffered_binary_port)
            processor.measurement_window_ms = self.profile.measurement_window_ms
            processor.capture_samples = self.profile.capture_samples
            processor.auto_install = self.engine_settings.native_nullifier_auto_install
            return processor
        if _native_processing(self.profile, self.engine_settings):
            board_processor: BoardNullifierProcessor
            host = self.profile.host or ""
            board_args = (
                self.profile.decimation,
                int(self.profile.channel_1_enabled) | (int(self.profile.channel_2_enabled) << 1),
                self.engine_settings.native_nullifier_port,
            )
            if (self.profile.acquisition_mode == "buffered"
                    and self.engine_settings.buffered_transport == "tcp"):
                from .tcp_board_nullifiers import TcpBoardNullifierProcessor
                password = self.engine_settings.buffered_ssh_password
                board_processor = TcpBoardNullifierProcessor(
                    host, self.engine_settings.buffered_board_executable, *board_args,
                    binary_port=self.engine_settings.buffered_binary_port,
                    known_hosts=self.engine_settings.buffered_ssh_known_hosts,
                    ssh_password=password.get_secret_value() if password is not None else None,
                )
                board_processor.raw_readout = self.engine_settings.buffered_arm_readout
                board_processor.variance_only = self.engine_settings.buffered_display == "variances"
            else:
                board_processor = BoardNullifierProcessor(
                    host, self.engine_settings.native_nullifier_executable, *board_args,
                )
            board_processor.buffered = self.profile.acquisition_mode == "buffered"
            board_processor.capture_samples = self.profile.capture_samples
            board_processor.measurement_window_ms = self.profile.measurement_window_ms
            board_processor.auto_install = self.engine_settings.native_nullifier_auto_install
            return board_processor
        return DeviceNullifierProcessor(sample_rate_hz=125_000_000 / self.profile.decimation)

    def _transition(self, state: DeviceSessionState) -> None:
        if state is not self._state:
            logger.info(
                "device session transition %s -> %s",
                self._state,
                state,
                extra={"device_id": str(self.profile.id)},
            )
            self._state = state


def _native_processing(profile: DeviceProfile, engine: EngineSettings) -> bool:
    if profile.server_stream_processing:
        return False
    value = profile.host or ""
    host = urlsplit(value if "://" in value else "http://" + value).hostname
    return (
        (engine.native_nullifier_enabled or (
            profile.acquisition_mode == "buffered" and profile.buffered_processing == "server"
        )) and profile.source is DeviceSource.REDPITAYA
        and (not engine.native_nullifier_hosts or host in engine.native_nullifier_hosts)
    )


def acquisition_settings(profile: DeviceProfile, engine: EngineSettings) -> AcquisitionSettings:
    module = profile.streaming_module_path or engine.streaming_module_path
    return AcquisitionSettings(
        _env_file=None,
        source=profile.source.value,
        host=profile.host or "simulator",
        channel_1_enabled=profile.channel_1_enabled,
        channel_2_enabled=profile.channel_2_enabled,
        decimation=profile.decimation,
        require_adc_timestamps=profile.server_stream_processing,
        block_size_bytes=profile.block_size_bytes,
        adc_buffer_size_bytes=profile.adc_buffer_size_bytes,
        internal_queue_capacity=engine.internal_queue_capacity,
        client_queue_capacity=engine.client_queue_capacity,
        reconnect_initial_seconds=engine.reconnect_initial_seconds,
        reconnect_maximum_seconds=engine.reconnect_maximum_seconds,
        streaming_module=module,
        # The user explicitly requires keeping v0.94. This candidate server
        # path must never load stream_app implicitly when its service is absent.
        auto_start_streaming_app=(False if profile.server_stream_processing
                                  else engine.auto_start_streaming_app),
        streaming_app_startup_delay_seconds=engine.streaming_app_startup_delay_seconds,
        simulator_samples_per_packet=profile.simulator_samples_per_packet,
        simulator_seed=profile.simulator_seed,
        native_nullifier_port=(
            engine.native_nullifier_port
            if _native_processing(profile, engine) else 0
        ),
    )


def _sanitize(exc: BaseException) -> str:
    return (" ".join(str(exc).split()) or type(exc).__name__)[:500]
