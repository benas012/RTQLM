"""Per-device, command-configurable nullifier processing on the engine host."""

from __future__ import annotations

import asyncio
from dataclasses import fields
from threading import RLock
from typing import Literal, Self

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from quantum_light_engine.post_processing import (
    ClusterProcessingResult,
    RealtimeClusterProcessor,
    config_from_dict,
    config_to_dict,
    lab_processing_config,
    processing_nullifiers,
)
from redpitaya_acquisition.domain.frame import RawSampleFrame


class NullifierCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    command: Literal[
        "configure", "disable", "reset", "start_calibration",
        "finish_calibration", "clear_calibration",
    ]
    config: dict[str, object] | None = None
    minimum_modes: int = Field(default=100, alias="minimumModes", strict=True, ge=2)

    @model_validator(mode="after")
    def validate_arguments(self) -> Self:
        if self.command == "configure":
            if not self.config:
                raise ValueError("configure requires a nonempty config object")
        elif "config" in self.model_fields_set:
            raise ValueError("config is only valid for configure")
        if self.command != "finish_calibration" and "minimum_modes" in self.model_fields_set:
            raise ValueError("minimumModes is only valid for finish_calibration")
        return self


class DeviceNullifierProcessor:
    """Serialize commands and complete raw packets at the same processing boundary.

    Processing is opt-in and independent of any connected desktop receiver.
    The lock also protects an in-flight worker when acquisition is cancelled.
    """

    def __init__(self, *, sample_rate_hz: float | None = None) -> None:
        self._processor = RealtimeClusterProcessor(lab_processing_config())
        self._sample_rate_hz = sample_rate_hz
        self._lock = RLock()
        self._enabled = False
        self._revision = 0
        self._epoch = 0
        self._previous: RawSampleFrame | None = None
        self._latest: ClusterProcessingResult | None = None
        self._result_frame: RawSampleFrame | None = None
        self._error: str | None = None

    def execute(self, command: NullifierCommand) -> dict[str, object]:
        with self._lock:
            processor = self._processor
            if command.command == "configure":
                update = command.config or {}
                current = config_to_dict(processor.config)
                _validate_config_keys(update, set(current))
                # Parse and validate everything before changing live state.
                config = config_from_dict({**current, **update})
                # A basis-only command must switch a built-in formula as well.
                if "measurementBasis" in update and "nullifiers" not in update:
                    old_basis = processor.config.measurement_basis
                    if processor.config.nullifiers == lab_processing_config(old_basis).nullifiers:
                        current["nullifiers"] = config_to_dict(
                            lab_processing_config(config.measurement_basis)
                        )["nullifiers"]
                        config = config_from_dict({**current, **update})
                rate = self._previous.sample_rate_hz if self._previous else self._sample_rate_hz
                processor.configure(config, sample_rate_hz=rate)
                self._revision += 1
                self._enabled = True
            elif command.command == "disable":
                self._enabled = False
                processor.clear_calibration()
                processor.reset_stream()
            elif command.command == "reset":
                processor.reset_stream()
            elif command.command == "start_calibration":
                self._require_enabled()
                processor.start_calibration()
            elif command.command == "finish_calibration":
                self._require_enabled()
                if not processor.calibrating:
                    raise RuntimeError("start shot-noise calibration before finishing it")
                required = {
                    term.source for spec in processing_nullifiers(processor.config)
                    for term in spec.terms
                }
                processor.finish_calibration(
                    minimum_modes=command.minimum_modes, required_sources=required,
                )
            elif command.command == "clear_calibration":
                processor.clear_calibration()
            self._clear_result(
                reset_continuity=command.command in {"configure", "disable", "reset"},
            )
            return self.status()

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise RuntimeError("configure nullifier processing before starting calibration")

    def reset_stream(self, *, clear_calibration: bool = False) -> None:
        with self._lock:
            if clear_calibration:
                self._processor.clear_calibration()
            self._processor.reset_stream()
            self._clear_result()

    def _clear_result(self, *, reset_continuity: bool = True) -> None:
        if reset_continuity:
            self._epoch += 1
            self._previous = None
        self._latest = None
        self._result_frame = None
        self._error = None

    async def process_frame(self, frame: RawSampleFrame) -> None:
        if self._enabled:
            await asyncio.to_thread(self.feed, frame, epoch=self._epoch)

    def feed(self, frame: RawSampleFrame, *, epoch: int | None = None) -> None:
        with self._lock:
            if not self._enabled or (epoch is not None and epoch != self._epoch):
                return
            try:
                self._feed(frame)
            except Exception as exc:
                # Analysis failures must leave the raw sample stream available.
                self._processor.reset_stream()
                self._clear_result()
                self._error = str(exc)

    def _feed(self, frame: RawSampleFrame) -> None:
        config = self._processor.config
        required = {term.source for spec in processing_nullifiers(config) for term in spec.terms}
        if config.window_mode == "triggered":
            required.add(config.trigger_channel)
        channels = {"a": frame.channel_1, "b": frame.channel_2}
        for name in required:
            if channels[name] is None:
                raise ValueError(f"nullifier processing requires {'CH1' if name == 'a' else 'CH2'}")

        previous = self._previous
        lost = (
            bool(frame.fpga_lost_ch1 or frame.fpga_lost_ch2)
            if previous is None or frame.stream_id != previous.stream_id else
            (frame.fpga_lost_ch1 != previous.fpga_lost_ch1
             or frame.fpga_lost_ch2 != previous.fpga_lost_ch2)
        )
        discontinuity = frame.packet_gap_detected or frame.service_drop_detected or (
            previous is not None and (
                frame.sequence != previous.sequence + 1
                or frame.stream_id != previous.stream_id
                or frame.sample_rate_hz != previous.sample_rate_hz
                or frame.channel_mask != previous.channel_mask
            )
        )
        self._previous = frame
        if lost:
            self._processor.reset_stream()
            self._latest = None
            self._result_frame = None
            self._error = "FPGA samples lost: packet excluded; awaiting continuous data"
            return
        if discontinuity:
            self._latest = None
            self._result_frame = None
        # Same standard +/-1 V STEMlab scale used by the receiver. Calibration
        # is applied to projected modes, before the commanded weighted sums.
        volts = {
            name: ((samples.astype(np.float64) - (frame.adc_zero_ch1 if name == 'a' else frame.adc_zero_ch2))
                   * (frame.adc_scale_ch1 if name == 'a' else frame.adc_scale_ch2) if samples is not None else
                   np.zeros(frame.samples_per_channel, dtype=np.float64))
            for name, samples in channels.items()
        }
        result = self._processor.feed(
            volts["a"], volts["b"], frame.sample_rate_hz, discontinuity=discontinuity,
        )
        if result is not None:
            self._latest = result
            self._result_frame = frame
        self._error = None

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "revision": self._revision,
                "config": config_to_dict(self._processor.config),
                "calibrated": self._processor.calibrated,
                "calibrating": self._processor.calibrating,
                "calibrationModeCounts": dict(self._processor.calibration_mode_counts),
                "shotNoiseCoefficients": dict(self._processor.shot_noise_coefficients),
                "hasResult": self._latest is not None,
                "error": self._error,
            }

    def latest(self) -> dict[str, object] | None:
        with self._lock:
            if self._latest is None or self._result_frame is None:
                return None
            result = {
                _camel(field.name): _json_value(getattr(self._latest, field.name))
                for field in fields(self._latest)
            }
            return {
                "revision": self._revision,
                "streamId": self._result_frame.stream_id,
                "sequence": self._result_frame.sequence,
                **result,
            }


def _validate_config_keys(data: dict[str, object], known: set[str]) -> None:
    unknown = data.keys() - known
    if unknown:
        raise ValueError(f"unknown processing settings: {', '.join(sorted(unknown))}")
    rows = data.get("nullifiers")
    if "nullifiers" in data and not isinstance(rows, list):
        raise ValueError("nullifiers must be a nonempty list")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                _validate_config_keys(row, {"name", "start", "terms"})
                terms = row.get("terms")
                if isinstance(terms, list):
                    for term in terms:
                        if isinstance(term, dict):
                            _validate_config_keys(term, {"source", "offset", "weight"})


def _camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(word.title() for word in rest)


def _json_value(value: object) -> object:
    if isinstance(value, np.ndarray):
        # JSON has no NaN or infinities. Keep array positions as explicit gaps.
        array = value.astype(object)
        array[~np.isfinite(value)] = None
        output: object = array.tolist()
        return output
    if isinstance(value, dict):
        return {name: _json_value(item) for name, item in value.items()}
    return value
