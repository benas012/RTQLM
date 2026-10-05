"""Validated two-channel waveforms for the stock 3.x DAC streaming backend."""

from __future__ import annotations

import math
import threading
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DAC_UPLOAD_TIMEOUT_SECONDS = 5.0


def dac_memory_size(block_size_bytes: int) -> int:
    """Stock 3.x requires twelve memory blocks, including each wire header."""
    return max(1_638_400, 12 * (block_size_bytes + 128))


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.title() for word in rest)


class OutputChannelConfig(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel, populate_by_name=True, extra="forbid", allow_inf_nan=False
    )

    enabled: bool = False
    waveform: Literal["sine", "square", "triangle", "sawtooth", "dc", "noise", "arbitrary"] = "sine"
    frequency_hz: float = Field(default=1_000, gt=0, le=50_000_000)
    amplitude_volts: float = Field(default=0.1, ge=0, le=1)
    offset_volts: float = Field(default=0, ge=-1, le=1)
    phase_degrees: float = Field(default=0, ge=-360, le=360)
    duty_cycle: float = Field(default=0.5, gt=0, lt=1)
    noise_seed: int = Field(default=1, ge=0, le=2**32 - 1)
    samples: list[float] = Field(default_factory=list, max_length=16_384)

    @model_validator(mode="after")
    def validate_signal(self) -> OutputChannelConfig:
        if self.amplitude_volts + abs(self.offset_volts) > 1:
            raise ValueError("peak amplitude + absolute offset must be at most 1 V")
        if any(not np.isfinite(value) or abs(value) > 1 for value in self.samples):
            raise ValueError("custom waveform samples must be finite values between -1 and 1")
        if self.waveform == "arbitrary" and len(self.samples) < 2:
            raise ValueError("an arbitrary waveform needs at least two normalized samples")
        return self


class OutputSignalsConfig(BaseModel):
    model_config = OutputChannelConfig.model_config

    output1: OutputChannelConfig = Field(default_factory=OutputChannelConfig)
    output2: OutputChannelConfig = Field(default_factory=OutputChannelConfig)
    sample_rate_hz: int = Field(default=15_625_000, ge=1_908, le=125_000_000)
    buffer_samples: int = Field(default=65_536, ge=64, le=262_144)

    @model_validator(mode="after")
    def validate_buffer(self) -> OutputSignalsConfig:
        if self.buffer_samples % 64:
            raise ValueError("DAC buffer size must be a multiple of 64 samples")
        # The pinned stream_app generator uses a 16-bit fractional rate step.
        actual_rate = dac_sample_rate(self.sample_rate_hz)
        for number, channel in enumerate((self.output1, self.output2), start=1):
            if channel.enabled and channel.waveform not in {"dc", "noise"}:
                cycles = round(channel.frequency_hz * self.buffer_samples / actual_rate)
                if cycles < 1:
                    raise ValueError(
                        f"OUT{number}: {channel.frequency_hz:g} Hz is too slow for this output "
                        f"buffer (one cycle is {actual_rate / self.buffer_samples:g} Hz). "
                        "Enable 'Fit rate for slow signals', lower the DAC rate, or increase "
                        "Buffer samples. The receiver sample rate is independent."
                    )
                if 2 * cycles >= self.buffer_samples:
                    raise ValueError(f"OUT{number}: frequency must remain below DAC Nyquist")
        return self


def dac_sample_rate(requested_rate: int) -> float:
    return int(requested_rate * 65_536 / 125_000_000) * 125_000_000 / 65_536


def fit_low_frequency_output(payload: dict[str, Any]) -> OutputSignalsConfig:
    """Lower the shared DAC rate only if an enabled periodic signal rounds to zero cycles.

    All other limits still apply, including both channels' Nyquist limits. The
    adjusted rate is returned explicitly for the editor to show before sending.
    """
    try:
        return OutputSignalsConfig.model_validate(payload)
    except ValidationError as initial_error:
        channels = tuple(OutputChannelConfig.model_validate(payload.get(f"output{i}", {}))
                         for i in (1, 2))
        disabled = {f"output{i}": channel.model_dump(by_alias=True) | {"enabled": False}
                    for i, channel in enumerate(channels, start=1)}
        base = OutputSignalsConfig.model_validate(payload | disabled)
        periodic = [channel.frequency_hz for channel in channels
                    if channel.enabled and channel.waveform not in {"dc", "noise"}]
        actual_rate = dac_sample_rate(base.sample_rate_hz)
        if not periodic or all(round(f * base.buffer_samples / actual_rate) >= 1
                               for f in periodic):
            raise initial_error
        # One period of the slowest waveform, using the closest hardware rate step.
        step = max(1, round(min(periodic) * base.buffer_samples * 65_536 / 125_000_000))
        requested_rate = max(1_908, math.ceil(step * 125_000_000 / 65_536))
        return OutputSignalsConfig.model_validate(payload | {"sampleRateHz": requested_rate})


def build_output_buffers(
    config: OutputSignalsConfig,
    *,
    include_buffers: bool = True,
) -> tuple[dict[int, list[int]], dict[str, Any]]:
    """Use a shared periodic buffer so both DAC channels start at the same index."""
    actual_rate = dac_sample_rate(config.sample_rate_hz)
    indices = np.arange(config.buffer_samples, dtype=np.float64)
    buffers: dict[int, list[int]] = {}
    channels: list[dict[str, Any]] = []
    for number, channel in enumerate((config.output1, config.output2), start=1):
        cycles = round(channel.frequency_hz * config.buffer_samples / actual_rate)
        phase = np.remainder(
            indices * cycles / config.buffer_samples + channel.phase_degrees / 360, 1
        )
        channels.append(
            {
                "channel": number,
                **channel.model_dump(mode="json", by_alias=True, exclude={"samples"}),
                "actualFrequencyHz": (
                    cycles * actual_rate / config.buffer_samples
                    if channel.waveform not in {"dc", "noise"}
                    else None
                ),
            }
        )
        if not include_buffers:
            continue
        if channel.waveform == "sine":
            shape = np.sin(2 * np.pi * phase)
        elif channel.waveform == "square":
            shape = np.where(phase < channel.duty_cycle, 1.0, -1.0)
        elif channel.waveform == "triangle":
            shape = 1 - 4 * np.abs(phase - 0.5)
        elif channel.waveform == "sawtooth":
            shape = 2 * phase - 1
        elif channel.waveform == "dc":
            shape = np.zeros(config.buffer_samples)
        elif channel.waveform == "noise":
            shape = np.random.default_rng(channel.noise_seed).uniform(-1, 1, config.buffer_samples)
        else:
            table = np.asarray(channel.samples)
            shape = np.interp(phase * len(table), np.arange(len(table) + 1), np.r_[table, table[0]])
        volts = channel.amplitude_volts * shape + channel.offset_volts
        # Always send both channels; an unchecked output is driven to zero,
        # preventing a previously enabled rail from continuing unnoticed.
        buffers[number] = (
            np.rint((volts if channel.enabled else np.zeros_like(volts)) * 32_767)
            .astype(np.int16)
            .tolist()
        )
    return buffers, {
        "channels": channels,
        "actualSampleRateHz": actual_rate,
        "bufferSamples": config.buffer_samples,
        "payloadBytesPerSecond": actual_rate * 4
        if any(channel.enabled for channel in (config.output1, config.output2))
        else 0,
        "payloadRateMeaning": (
            "equivalent continuous sample payload; small buffers may replay on the board"
        ),
        "synchronization": "shared two-channel buffer on one board",
        "repeats": True,
    }


def start_output_signals(
    module: Any, adapter: Any, config_client: Any, config: OutputSignalsConfig,
    *, block_size_bytes: int = 65_536,
) -> Any:
    if config_client is None or not hasattr(module, "DACStreamClient"):
        raise RuntimeError("custom outputs require the Red Pitaya 3.x DAC streaming client")
    if adapter.host is None:
        raise RuntimeError("Red Pitaya streaming host is unavailable")
    buffers, _ = build_output_buffers(config)
    client = module.DACStreamClient(config_client)
    try:
        for key, value in (
            ("dac_pass_mode", "DAC_NET"),
            ("dac_rate", str(config.sample_rate_hz)),
            ("dac_size", str(dac_memory_size(block_size_bytes))),
        ):
            try:
                result = config_client.sendConfig(adapter.host, key, value)
            except TypeError:
                result = config_client.sendConfig(key, value)
            require_success(f"configure {key}", result)
        for channel, samples in buffers.items():
            require_success(f"load OUT{channel}", client.setMemory16Bit(channel, samples))
        client.setRepeatInf(True)
        start_dac_memory(module, client, adapter.host, tuple(buffers), config_client)
    except BaseException:
        try:
            stop_output_signals(client)
        except BaseException:
            pass
        raise
    return client


def start_dac_memory(
    module: Any, client: Any, host: str, channels: tuple[int, ...], config_client: Any = None
) -> None:
    """The 3.x start return value precedes the asynchronous connection/upload."""
    callback_type = getattr(module, "DACCallback", None)
    if callback_type is None or not hasattr(client, "setCallback"):
        require_success("start DAC outputs", client.startStreamingFromMemory(host))
        return
    ready = threading.Event()
    failure: list[str] = []

    class PlaybackCallback(callback_type):  # type: ignore[valid-type, misc]
        def sentPack(self, _client: Any, ch1_size: int, ch2_size: int) -> None:
            sizes = {1: ch1_size, 2: ch2_size}
            if all(sizes[channel] > 0 for channel in channels):
                ready.set()

        def error(self, _client: Any, _host: str, code: int) -> None:
            failure.append(f"DAC connection failed (code {code})")
            ready.set()

        def stopped(self, *_args: Any) -> None:
            if not ready.is_set():
                failure.append("DAC stopped before waveform upload")
                ready.set()

        stoppedEmpty = stopped
        stoppedFileBroken = stopped
        stoppedMemError = stopped
        stoppedMemModify = stopped
        stoppedMissingFile = stopped

    callback = PlaybackCallback()
    # Retain the director for the full client lifetime; never disown it.
    client._playback_callback = callback
    client.setCallback(callback)
    # The vendor setCallback removes its internal configuration observer.
    # Supply a retained observer so board-side startup errors still reach us.
    if config_client is not None and hasattr(module, "ConfigCallback"):
        class BoardCallback(module.ConfigCallback):  # type: ignore[misc]
            def configError(self, _client: Any, _host: str, code: int) -> None:
                failure.append(f"DAC board configuration failed (code {code})")
                ready.set()

            def failed(self, reason: str) -> None:
                failure.append(f"DAC board stopped before waveform playback: {reason}")
                ready.set()

            def dacServerStoppedMemError(self, *_args: Any) -> None:
                self.failed(
                    "insufficient board memory shared by capture and outputs; reduce the "
                    "ADC buffer reservation or acquisition block size, then disconnect "
                    "and reconnect"
                )

            def dacServerStoppedMemModify(self, *_args: Any) -> None:
                self.failed("board memory configuration changed")

            def dacServerStoppedConfigError(self, *_args: Any) -> None:
                self.failed("invalid board DAC configuration")

            def dacServerStoppedFileMissed(self, *_args: Any) -> None:
                self.failed("waveform file missing")

            def configErrorTimeout(self, *_args: Any) -> None:
                self.failed("board configuration timed out")

        client._playback_config_client = config_client
        client._playback_config_callback = BoardCallback()
        config_client.addCallback(client._playback_config_callback)
    require_success("start DAC outputs", client.startStreamingFromMemory(host))
    if not ready.wait(DAC_UPLOAD_TIMEOUT_SECONDS):
        raise RuntimeError("Red Pitaya DAC did not upload the waveform within 5 seconds")
    if failure:
        raise RuntimeError(failure[0])


def stop_output_signals(client: Any) -> None:
    if client is None:
        return
    try:
        client.stopStreaming()
    finally:
        client.notifyStop()
    client.wait()
    config_client = getattr(client, "_playback_config_client", None)
    if config_client is not None:
        config_client.removeCallback(client._playback_config_callback)
        client._playback_config_client = None


def require_success(operation: str, result: Any) -> None:
    if result is False or (isinstance(result, int) and not isinstance(result, bool) and result < 0):
        raise RuntimeError(f"Red Pitaya could not {operation}")
