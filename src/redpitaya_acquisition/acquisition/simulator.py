from __future__ import annotations

import asyncio
import math
import secrets
import time
from collections.abc import AsyncIterator

import numpy as np

from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.domain.frame import RawSampleFrame

from .source import AcquisitionSource


class SimulatedAcquisitionSource(AcquisitionSource):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._running = False
        self._stream_id = 0
        self._rng = np.random.default_rng(settings.simulator_seed)

    async def connect(self) -> None:
        await asyncio.sleep(0)

    async def start(self) -> None:
        self._stream_id = secrets.randbits(64)
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def disconnect(self) -> None:
        self._running = False

    async def frames(self) -> AsyncIterator[RawSampleFrame]:
        settings = self._settings
        count = settings.simulator_samples_per_packet
        sample_rate = settings.sample_rate_hz
        packet_duration = count / sample_rate
        sample_index = 0
        pack_id = 0
        lost_1 = 0
        lost_2 = 0
        deadline = asyncio.get_running_loop().time()
        while self._running:
            deadline += packet_duration
            pack_id += 1
            packet_gap = False
            if (
                settings.simulator_packet_gap_every
                and pack_id % settings.simulator_packet_gap_every == 0
            ):
                pack_id += 1
                packet_gap = True
            if (
                settings.simulator_fpga_loss_every
                and pack_id % settings.simulator_fpga_loss_every == 0
            ):
                lost_1 += int(settings.channel_1_enabled)
                lost_2 += int(settings.channel_2_enabled)
            indices = np.arange(sample_index, sample_index + count, dtype=np.float64)
            times = indices / sample_rate
            noise = self._rng.integers(-12, 13, size=count, dtype=np.int16)
            ch1 = ch2 = None
            if settings.channel_1_enabled:
                signal = np.sin(2.0 * math.pi * 1_000.0 * times) * 12_000.0
                ch1 = np.ascontiguousarray(signal.astype("<i2") + noise, dtype="<i2")
                ch1.flags.writeable = False
            if settings.channel_2_enabled:
                signal = np.sin(2.0 * math.pi * 1_700.0 * times + 0.7) * 9_000.0
                ch2 = np.ascontiguousarray(signal.astype("<i2") - noise, dtype="<i2")
                ch2.flags.writeable = False
            sample_index += count
            yield RawSampleFrame(
                stream_id=self._stream_id,
                sequence=0,
                source_pack_id=pack_id,
                captured_at_unix_ns=time.time_ns(),
                base_rate_hz=125_000_000,
                decimation=settings.decimation,
                sample_rate_hz=sample_rate,
                samples_per_channel=count,
                channel_mask=settings.channel_mask,
                channel_1=ch1,
                channel_2=ch2,
                fpga_lost_ch1=lost_1,
                fpga_lost_ch2=lost_2,
                packet_gap_detected=packet_gap,
            )
            await asyncio.sleep(max(0.0, deadline - asyncio.get_running_loop().time()))
