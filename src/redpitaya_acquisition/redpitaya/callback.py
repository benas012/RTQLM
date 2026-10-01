from __future__ import annotations

import asyncio
import logging
import math
import secrets
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.domain.frame import RawSampleFrame

logger = logging.getLogger(__name__)

FrameHandler = Callable[[RawSampleFrame], None]
EventHandler = Callable[[str, str | None], None]


def create_callback(
    base_class: type[Any],
    loop: asyncio.AbstractEventLoop,
    settings: Settings,
    on_frame: FrameHandler,
    on_event: EventHandler,
) -> Any:
    def dispatch_frame(frame: RawSampleFrame) -> None:
        loop.call_soon_threadsafe(on_frame, frame)

    def dispatch_event(event: str, detail: str | None) -> None:
        loop.call_soon_threadsafe(on_event, event, detail)

    return create_synchronous_callback(
        base_class,
        settings,
        dispatch_frame,
        dispatch_event,
    )


def create_synchronous_callback(
    base_class: type[Any],
    settings: Settings,
    on_frame: FrameHandler,
    on_event: EventHandler,
) -> Any:
    """Build a callback whose handlers are safe to supply from a worker process.

    ``create_callback`` marshals vendor callback threads onto an asyncio loop.
    The isolated native worker has no asyncio loop, so it uses this lower-level
    form and serializes the converted frame over its IPC connection instead.
    """

    stream_id = secrets.randbits(64)

    class NativeCallback(base_class):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self._previous_pack_id: int | None = None

        def _receive(self, pack: Any) -> None:
            try:
                frame = self._convert(pack)
            except Exception as exc:
                on_event("malformed", _safe_error(exc))
                return
            on_frame(frame)

        def receivePack(self, *args: Any) -> None:
            # 2.07 wrappers call receivePack(pack); 3.00 calls
            # receivePack(client, pack).
            if not args:
                on_event("malformed", "missing ADC packet")
                return
            self._receive(args[-1])

        def recievePack(self, _client: Any, pack: Any) -> None:
            self._receive(pack)

        def _convert(self, pack: Any) -> RawSampleFrame:
            channel_1 = _copy_channel(pack, "channel1") if settings.channel_1_enabled else None
            channel_2 = _copy_channel(pack, "channel2") if settings.channel_2_enabled else None
            meta_1 = getattr(pack, "channel1", None)
            meta_2 = getattr(pack, "channel2", None)
            channel_pack_mismatch = False
            if channel_1 is not None and channel_2 is not None:
                if channel_1.size != channel_2.size:
                    raise ValueError("channel sample-count mismatch")
                channel_pack_mismatch = _field(meta_1, "packId", 0) != _field(meta_2, "packId", 0)
                if _sample_rate(meta_1, settings) != _sample_rate(meta_2, settings):
                    raise ValueError("channel sample rates do not match")
            meta = meta_1 if channel_1 is not None else meta_2
            pack_id = int(_field(meta, "packId", _field(pack, "packId", 0)))
            packet_gap = (
                self._previous_pack_id is not None and pack_id != self._previous_pack_id + 1
            )
            self._previous_pack_id = pack_id
            sample_rate = _sample_rate(meta, settings)
            sample_count = int(channel_1.size if channel_1 is not None else channel_2.size)  # type: ignore[union-attr]
            return RawSampleFrame(
                stream_id=stream_id,
                sequence=0,
                source_pack_id=pack_id,
                captured_at_unix_ns=time.time_ns(),
                base_rate_hz=round(sample_rate * settings.decimation),
                decimation=settings.decimation,
                sample_rate_hz=sample_rate,
                samples_per_channel=sample_count,
                channel_mask=settings.channel_mask,
                channel_1=channel_1,
                channel_2=channel_2,
                fpga_lost_ch1=int(_field(meta_1, "fpgaLost", 0)),
                fpga_lost_ch2=int(_field(meta_2, "fpgaLost", 0)),
                packet_gap_detected=packet_gap or channel_pack_mismatch,
            )

        def connected(self, *_args: Any) -> None:
            on_event("connected", None)

        def disconnected(self, *_args: Any) -> None:
            on_event("disconnected", None)

        def error(self, *_args: Any) -> None:
            detail = _safe_error(_args[-1] if _args else None)
            on_event("error", detail)

        def stopped(self, *_args: Any) -> None:
            on_event("stopped", None)

        def stoppedNoActiveChannels(self, *_args: Any) -> None:
            on_event("error", "no active channels")

        def stoppedMemError(self, *_args: Any) -> None:
            on_event("error", "streaming memory error")

        def stoppedMemModify(self, *_args: Any) -> None:
            on_event("error", "streaming memory changed")

        def configError(self, *_args: Any) -> None:
            detail = _safe_error(_args[-1]) if _args else "configuration rejected"
            on_event("config_error", detail)

        def configErrorTimeout(self, *_args: Any) -> None:
            on_event("config_error", "configuration timeout")

    return NativeCallback()


def _copy_channel(pack: Any, name: str) -> np.ndarray[Any, np.dtype[np.int16]]:
    channel = getattr(pack, name, None)
    if channel is None or not hasattr(channel, "raw"):
        raise ValueError(f"enabled {name} is missing raw samples")
    array = np.array(channel.raw, dtype="<i2", copy=True, order="C")
    if array.ndim != 1 or array.size == 0:
        raise ValueError(f"enabled {name} must contain a non-empty 1-D array")
    array.flags.writeable = False
    return array


def _field(value: Any, name: str, default: Any) -> Any:
    return getattr(value, name, default) if value is not None else default


def _sample_rate(meta: Any, settings: Settings) -> float:
    # Despite its name, ADCChannel.baseRate is already decimated. In vendor
    # 2.07-48 the server sets it via setOSCRate(getADCRate() / rate), and the
    # client forwards it unchanged. Dividing again corrupts time and FFT axes.
    rate = float(_field(meta, "baseRate", settings.sample_rate_hz))
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("channel sample rate must be finite and positive")
    return rate


def _safe_error(value: Any) -> str:
    return " ".join(str(value).split())[:500]
