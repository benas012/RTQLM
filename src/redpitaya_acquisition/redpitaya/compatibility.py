from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from redpitaya_acquisition.acquisition.manager import NonRecoverableAcquisitionError
from redpitaya_acquisition.config.settings import Settings


class StreamingCompatibility:
    """The only place where vendor wrapper API variations are handled."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.host: str | None = None
        self._host_specific_config = False

    def connect(self, host: str) -> None:
        self.host = host
        try:
            result = self.client.connect([host])
        except TypeError:
            result = self.client.connect()
            self._host_specific_config = False
        else:
            self._host_specific_config = True
        self._require_success(
            "connect",
            result,
            detail=(
                f"Red Pitaya streaming client could not connect to '{host}'. "
                "Make sure the Data stream control app/streaming-server is running on the board."
            ),
        )

    def register_callback(self, callback: Any) -> None:
        owned = callback.__disown__() if hasattr(callback, "__disown__") else callback
        if hasattr(self.client, "setReceiveDataCallback"):
            result = self.client.setReceiveDataCallback(owned)
        elif hasattr(self.client, "setReceiveDataFunction"):
            result = self.client.setReceiveDataFunction(owned)
        elif hasattr(self.client, "setReciveDataFunction"):
            result = self.client.setReciveDataFunction(owned)
        elif hasattr(self.client, "setCallback"):
            result = self.client.setCallback(owned)
        else:
            raise NonRecoverableAcquisitionError(
                "streaming client has no supported callback registration method"
            )
        self._require_success("callback registration", result)

    def configure(self, settings: Settings) -> None:
        values: tuple[tuple[str, Any, tuple[str, ...]], ...] = (
            ("adc_pass_mode", "NET", ("setADCPassMode", "setADCMode")),
            ("adc_decimation", settings.decimation, ("setADCDecimation", "setDecimation")),
            ("channel_state_1", "ON" if settings.channel_1_enabled else "OFF", ("setChannel1",)),
            ("channel_state_2", "ON" if settings.channel_2_enabled else "OFF", ("setChannel2",)),
            ("block_size", settings.block_size_bytes, ("setBlockSize",)),
            ("adc_size", settings.adc_buffer_size_bytes, ("setADCBufferSize", "setADCSize")),
        )
        if hasattr(self.client, "sendConfig"):
            for key, value, _ in values:
                serialized = str(value)
                if self._host_specific_config and self.host is not None:
                    result = self.client.sendConfig(self.host, key, serialized)
                else:
                    result = self.client.sendConfig(key, serialized)
                self._require_success(key, result)
            return
        if hasattr(self.client, "setConfig"):
            for key, value, _ in values:
                self._require_success(key, self.client.setConfig(key, value))
            return
        for key, value, method_names in values:
            method = self._first_method(method_names)
            self._require_success(key, method(value))

    def set_verbose(self, enabled: bool) -> None:
        if hasattr(self.client, "setVerbose"):
            self.client.setVerbose(enabled)

    def start(self) -> None:
        self._require_success("startStreaming", self.client.startStreaming())

    def stop(self) -> None:
        if hasattr(self.client, "notifyStop"):
            self._require_success("notifyStop", self.client.notifyStop())
        elif hasattr(self.client, "stopStreaming"):
            self._require_success("stopStreaming", self.client.stopStreaming())

    def disconnect(self) -> None:
        if hasattr(self.client, "disconnect"):
            self.client.disconnect()

    def wait(self) -> None:
        self.client.wait()

    def _first_method(self, names: tuple[str, ...]) -> Callable[..., Any]:
        for name in names:
            method = getattr(self.client, name, None)
            if callable(method):
                return cast(Callable[..., Any], method)
        raise NonRecoverableAcquisitionError(
            f"streaming client is incompatible; expected one of: {', '.join(names)}"
        )

    @staticmethod
    def _require_success(operation: str, result: Any, *, detail: str | None = None) -> None:
        integer_failure = isinstance(result, int) and not isinstance(result, bool) and result < 0
        if result is False or integer_failure:
            raise NonRecoverableAcquisitionError(
                detail
                or f"Red Pitaya streaming client rejected configuration operation '{operation}'"
            )
