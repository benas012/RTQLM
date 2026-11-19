"""Acquire independent DMA windows; transfer only a short raw preview and C results."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import AsyncIterator

import numpy as np

from redpitaya_acquisition.acquisition.source import AcquisitionSource
from redpitaya_acquisition.domain.frame import RawSampleFrame
from redpitaya_acquisition.redpitaya.output_signals import OutputSignalsConfig

from .board_nullifiers import BoardNullifierProcessor


class BufferedCaptureSource(AcquisitionSource):
    def __init__(self, processor: BoardNullifierProcessor, *, close_processor: bool = True):
        self.processor = processor
        self.close_processor = close_processor
        self.running = False
        self.outputs = OutputSignalsConfig()

    async def connect(self) -> None:
        await asyncio.to_thread(self.processor.prepare_capture)

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False

    async def disconnect(self) -> None:
        self.running = False
        if self.close_processor:
            await asyncio.to_thread(self.processor.close)

    @property
    def module_name(self) -> str:
        return "redpitaya-buffered-captures"

    async def set_output_signals(self, config: OutputSignalsConfig) -> None:
        waves = {"sine": 0, "square": 1, "triangle": 2, "sawtooth": 3, "dc": 5, "noise": 10}
        if any(ch.enabled and ch.waveform not in waves for ch in (config.output1, config.output2)):
            raise ValueError("Buffered acquisition supports the standard SDK waveforms; "
                             "arbitrary output buffers require streaming acquisition")
        for number, ch in enumerate((config.output1, config.output2), start=1):
            await asyncio.to_thread(self.processor.set_output, {
                "command": "output", "channel": number, "enabled": ch.enabled,
                "waveform": waves.get(ch.waveform, 0), "frequency": ch.frequency_hz,
                "amplitude": ch.amplitude_volts, "offset": ch.offset_volts,
                "phase": ch.phase_degrees,
            })
        self.outputs = config

    def output_status(self) -> dict[str, object]:
        return {"state": "active" if self.outputs.output1.enabled or self.outputs.output2.enabled
                else "inactive", "config": self.outputs.model_dump(mode="json", by_alias=True),
                "generator": "SDK", "noiseSeedSupported": False}

    async def frames(self) -> AsyncIterator[RawSampleFrame]:
        while self.running:
            reply = await asyncio.to_thread(self.processor.capture_preview)
            if not self.running:
                break
            preview = reply.get("preview")
            if preview is None:
                # Variances are exposed by the existing results endpoint. There
                # are no raw waveform samples to publish in this mode.
                await asyncio.sleep(0)
                continue
            channels = [np.ascontiguousarray(np.clip(np.rint(np.asarray(preview[key]) * 8192),
                                                     -32768, 32767), dtype="<i2")
                        for key in ("a", "b")]
            display_decimation = self.processor.decimation * int(preview.get("sampleStride", 1))
            # Each window has dead time. A fresh stream ID prevents the viewer from
            # joining its preview to the preceding capture or inventing continuity.
            yield RawSampleFrame(
                stream_id=secrets.randbits(63), sequence=0, source_pack_id=0,
                captured_at_unix_ns=time.time_ns(), base_rate_hz=125_000_000,
                decimation=display_decimation,
                sample_rate_hz=125_000_000 / display_decimation,
                samples_per_channel=len(channels[0]), channel_mask=self.processor.channel_mask,
                channel_1=channels[0] if self.processor.channel_mask & 1 else None,
                channel_2=channels[1] if self.processor.channel_mask & 2 else None,
            )
            await asyncio.sleep(0)
