"""Slow external control-signal edge detection; no ADC time alignment implied."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from redpitaya_acquisition.redpitaya.digital_input import DigitalInputReader


class TriggerConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    enabled: bool = False
    pin: str = Field(default="DIO0_P", pattern=r"^DIO[0-7]_[PN]$")
    edge: Literal["rising", "falling", "either"] = "rising"
    poll_interval_ms: float = Field(default=10, alias="pollIntervalMs", ge=1, le=1_000)


class ExternalTrigger:
    def __init__(self) -> None:
        self.config = TriggerConfig()
        self.state = "disabled"
        self.level: int | None = None
        self.trigger_count = 0
        self.last_trigger_at: str | None = None
        self.last_read_at: str | None = None
        self.last_poll_interval_ms: float | None = None
        self.last_error: str | None = None
        self._last_read_monotonic: float | None = None
        self._reader: DigitalInputReader | None = None
        self._task: asyncio.Task[None] | None = None

    async def configure(
        self, host: str, config: TriggerConfig, password: str | None = None
    ) -> dict[str, object]:
        await self.stop()
        self.config = config
        self.trigger_count = 0
        self.last_trigger_at = self.last_read_at = None
        self._last_read_monotonic = self.last_poll_interval_ms = None
        self.last_error = None
        if not config.enabled:
            return self.status()
        reader = DigitalInputReader(host, password)
        self._reader = reader
        self.state = "connecting"
        try:
            self._observe(await asyncio.to_thread(reader.connect, config.pin))
        except BaseException as exc:
            await self.stop()
            if isinstance(exc, asyncio.CancelledError):
                raise
            self._fail(exc)
            raise RuntimeError(self.last_error) from exc
        self.state = "watching"
        self._task = asyncio.create_task(self._poll(reader), name="external-trigger-poll")
        return self.status()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        reader, self._reader = self._reader, None
        if reader is not None:
            # Closing the channel also releases a pending blocking read.
            await asyncio.to_thread(reader.close)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        self.config = self.config.model_copy(update={"enabled": False})
        self.state = "disabled"
        self.level = None

    async def _poll(self, reader: DigitalInputReader) -> None:
        period = self.config.poll_interval_ms / 1_000
        deadline = time.monotonic() + period
        try:
            while True:
                await asyncio.sleep(max(0, deadline - time.monotonic()))
                started = time.monotonic()
                self._observe(await asyncio.to_thread(reader.read))
                # Never overlap reads or burst to catch up after a delayed reply.
                deadline = max(started + period, time.monotonic())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail(exc)
            await asyncio.to_thread(reader.close)
            if self._reader is reader:
                self._reader = None

    def _observe(self, level: int) -> None:
        if type(level) is not int or level not in (0, 1):
            raise ValueError("digital input must be 0 or 1")
        now = datetime.now(UTC).isoformat()
        monotonic = time.monotonic()
        if self._last_read_monotonic is not None:
            self.last_poll_interval_ms = (monotonic - self._last_read_monotonic) * 1_000
        if self.level is not None and self.level != level:
            edge = "rising" if level else "falling"
            if self.config.edge in (edge, "either"):
                self.trigger_count += 1
                self.last_trigger_at = now
        self.level = level
        self.last_read_at = now
        self._last_read_monotonic = monotonic

    def _fail(self, exc: BaseException) -> None:
        self.state = "error"
        self.level = None
        self.last_error = (" ".join(str(exc).split()) or type(exc).__name__)[:500]

    def status(self) -> dict[str, object]:
        return {
            "config": self.config.model_dump(mode="json", by_alias=True),
            "state": self.state,
            "level": self.level,
            "triggerCount": self.trigger_count,
            "lastTriggerAtUtc": self.last_trigger_at,
            "lastReadAtUtc": self.last_read_at,
            "lastPollIntervalMs": self.last_poll_interval_ms,
            "lastError": self.last_error,
        }
