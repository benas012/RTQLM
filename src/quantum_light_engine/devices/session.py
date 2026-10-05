from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any

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

from .models import DeviceProfile, DeviceSource

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

    def __init__(self, source: AcquisitionSource) -> None:
        self.source = source

    async def connect(self) -> None:
        return

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
        self._prepared_source: AcquisitionSource | None = None
        self._lock = asyncio.Lock()
        self._state = DeviceSessionState.DISCONNECTED
        self._last_error: str | None = None
        self._disconnecting = False
        self.manager = AcquisitionManager(self._settings, self._source_factory, self.broadcaster)

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
        self._transition(DeviceSessionState.CONNECTING)
        source = self._new_source()
        try:
            await source.connect()
            self._prepared_source = source
            self._last_error = None
            self._transition(DeviceSessionState.READY)
        except Exception as exc:
            self._last_error = _sanitize(exc)
            self._transition(DeviceSessionState.FAULTED)
            try:
                await source.disconnect()
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
            if self._disconnecting:
                self._transition(DeviceSessionState.DISCONNECTED)
            else:
                await self._connect_locked()
            return self.status()

    async def disconnect(self) -> dict[str, Any]:
        async with self._lock:
            active_source = self._prepared_source or self.manager.source
            if self.state is DeviceSessionState.DISCONNECTED:
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
                if self._prepared_source is not None:
                    await self._prepared_source.disconnect()
                    self._prepared_source = None
                if active_source is not None:
                    await active_source.shutdown_remote_service()
                self._transition(DeviceSessionState.DISCONNECTED)
            finally:
                self._disconnecting = False
            return self.status()

    async def update_profile(self, profile: DeviceProfile) -> None:
        async with self._lock:
            if self.state not in {DeviceSessionState.DISCONNECTED, DeviceSessionState.READY}:
                raise RuntimeError("device profile cannot be updated in the current state")
            if self._prepared_source is not None:
                source = self._prepared_source
                await source.disconnect()
                await source.shutdown_remote_service()
                self._prepared_source = None
            self.profile = profile
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
            }
        )
        return base

    def _source_factory(self, _settings: AcquisitionSettings) -> AcquisitionSource:
        if self._prepared_source is not None:
            source, self._prepared_source = self._prepared_source, None
            return _PreparedSource(source)
        return self._new_source()

    def _new_source(self) -> AcquisitionSource:
        if self.profile.source is DeviceSource.SIMULATOR:
            return SimulatedAcquisitionSource(self._settings)
        return RedPitayaStreamingSource(self._settings)

    def _transition(self, state: DeviceSessionState) -> None:
        if state is not self._state:
            logger.info(
                "device session transition %s -> %s",
                self._state,
                state,
                extra={"device_id": str(self.profile.id)},
            )
            self._state = state


def acquisition_settings(profile: DeviceProfile, engine: EngineSettings) -> AcquisitionSettings:
    module = profile.streaming_module_path or engine.streaming_module_path
    return AcquisitionSettings(
        _env_file=None,
        source=profile.source.value,
        host=profile.host or "simulator",
        channel_1_enabled=profile.channel_1_enabled,
        channel_2_enabled=profile.channel_2_enabled,
        decimation=profile.decimation,
        block_size_bytes=profile.block_size_bytes,
        adc_buffer_size_bytes=profile.adc_buffer_size_bytes,
        internal_queue_capacity=engine.internal_queue_capacity,
        client_queue_capacity=engine.client_queue_capacity,
        reconnect_initial_seconds=engine.reconnect_initial_seconds,
        reconnect_maximum_seconds=engine.reconnect_maximum_seconds,
        streaming_module=module,
        auto_start_streaming_app=engine.auto_start_streaming_app,
        streaming_app_startup_delay_seconds=engine.streaming_app_startup_delay_seconds,
        simulator_samples_per_packet=profile.simulator_samples_per_packet,
        simulator_seed=profile.simulator_seed,
    )


def _sanitize(exc: BaseException) -> str:
    return (" ".join(str(exc).split()) or type(exc).__name__)[:500]
