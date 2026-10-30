"""Binary frame decoding and bounded sample history for the native receiver."""

from __future__ import annotations

import math
import struct
import sys
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from apps.native_receiver.trigger_capture import interpolate_trigger_window
from apps.native_receiver.trigger_search import qualified_crossing

HEADER = struct.Struct("<4sBBHQQQQdIIIIQQ")
HEADER_SIZE = 80
MAGIC = b"RPA1"
PROTOCOL_VERSION = 1
MAX_POINTS = 800
HISTORY_MAX_SAMPLES = 16_777_216


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    host: str
    state: str
    channel_1_enabled: bool
    channel_2_enabled: bool
    decimation: int
    block_size_bytes: int
    acquisition_mode: str = "streaming"
    capture_samples: int = 625_024


@dataclass(frozen=True)
class SampleFrame:
    channel_mask: int
    sequence: int
    sample_rate_hz: float
    samples_per_channel: int
    flags: int
    fpga_lost_ch1: int
    fpga_lost_ch2: int
    channel_1: memoryview | None
    channel_2: memoryview | None
    stream_id: int = 0


@dataclass(frozen=True)
class TriggeredPlot:
    channel_1: Any
    channel_2: Any
    sample_rate_hz: float
    samples_per_channel: int
    trigger_index: float | None
    sample_positions: Any = None


class SampleHistory:
    """Bounded raw-sample ring; callers synchronize writes and snapshots."""

    def __init__(self, capacity: int = HISTORY_MAX_SAMPLES) -> None:
        self.capacity = capacity
        self.clear()

    def clear(self) -> None:
        self.latest: SampleFrame | None = None
        self.size = 0
        self.position = 0
        self.channels: dict[int, np.ndarray[Any, Any]] = {}

    def frozen_copy(self) -> SampleHistory:
        frozen = SampleHistory(max(self.size, 1))
        frozen.latest = self.latest
        frozen.size = self.size
        start = (self.position - self.size) % self.capacity
        first = min(self.size, self.capacity - start)
        for channel, buffer in self.channels.items():
            data = np.empty(self.size, dtype=np.int16)
            data[:first] = buffer[start : start + first]
            data[first:] = buffer[: self.size - first]
            frozen.channels[channel] = data
        return frozen

    def append(self, frame: SampleFrame) -> None:
        previous = self.latest
        if frame.flags & 0b11 or (
            previous is not None
            and (
                frame.sequence != previous.sequence + 1
                or frame.stream_id != previous.stream_id
                or frame.fpga_lost_ch1 != previous.fpga_lost_ch1
                or frame.fpga_lost_ch2 != previous.fpga_lost_ch2
                or frame.channel_mask != previous.channel_mask
                or frame.sample_rate_hz != previous.sample_rate_hz
                or frame.samples_per_channel != previous.samples_per_channel
            )
        ):
            self.clear()
        count = min(frame.samples_per_channel, self.capacity)
        first = min(count, self.capacity - self.position)
        for channel, samples in ((1, frame.channel_1), (2, frame.channel_2)):
            if samples is None:
                continue
            if channel not in self.channels:
                self.channels[channel] = np.empty(self.capacity, dtype=np.int16)
            buffer = self.channels[channel]
            data = np.asarray(samples)[-count:]
            buffer[self.position : self.position + first] = data[:first]
            buffer[: count - first] = data[first:]
        self.position = (self.position + count) % self.capacity
        self.size = min(self.size + count, self.capacity)
        self.latest = frame

    def snapshot(self, requested_samples: int) -> SampleFrame | None:
        frame = self.latest
        if frame is None:
            return None
        count = min(requested_samples, self.size)
        if count <= frame.samples_per_channel:
            return frame
        start = (self.position - count) % self.capacity
        first = min(count, self.capacity - start)
        result: dict[int, memoryview] = {}
        for channel, buffer in self.channels.items():
            # Snapshot ownership prevents the socket overwriting samples during drawing.
            data = np.empty(count, dtype=np.int16)
            data[:first] = buffer[start : start + first]
            data[first:] = buffer[: count - first]
            data.flags.writeable = False
            result[channel] = memoryview(data)
        return replace(
            frame,
            samples_per_channel=count,
            channel_1=result.get(1),
            channel_2=result.get(2),
        )

    def plot_channels(
        self, requested_samples: int, point_budget: int = MAX_POINTS
    ) -> tuple[Any, Any, float, int] | None:
        if self.latest is None:
            return None
        count = min(requested_samples, self.size)
        limit = max(2, point_budget)
        indices = np.linspace(0, count - 1, min(count, limit), dtype=np.intp)
        indices = (self.position - count + indices) % self.capacity
        first = self.channels[1][indices] if 1 in self.channels else None
        second = self.channels[2][indices] if 2 in self.channels else None
        return first, second, self.latest.sample_rate_hz, count

    def triggered_plot_channels(
        self,
        requested_samples: int,
        visible_samples: int,
        trigger_channel: int,
        level: float,
        edge: str,
        pre_trigger_fraction: float,
        point_budget: int = MAX_POINTS,
        hysteresis: float = 0.0,
    ) -> TriggeredPlot | None:
        """Select and downsample one trigger-aligned window atomically.

        Callers hold their history lock while invoking this method. Finding the
        crossing and copying the selected display points in one operation keeps
        a fast producer from advancing the ring between those two steps.
        """
        if self.latest is None:
            return None
        count = min(max(int(requested_samples), 0), self.size)
        visible = max(int(visible_samples), 2)
        if count < 2:
            return None

        pre_trigger_samples = round((visible - 1) * pre_trigger_fraction)
        post_trigger_samples = visible - 1 - pre_trigger_samples
        target_index = count - 1 - post_trigger_samples
        crossing = self.find_channel_crossing_before(
            trigger_channel,
            count,
            level,
            edge,
            target_index=target_index,
            minimum_index=max(pre_trigger_samples, 1),
            hysteresis=hysteresis,
        )
        if crossing is None:
            # A loss boundary can leave too little history for a full window.
            # Trigger on real samples anyway; extraction leaves missing data blank.
            crossing = self.find_channel_crossing_before(
                trigger_channel, count, level, edge,
                target_index=target_index if target_index >= 1 else count - 1,
                hysteresis=hysteresis,
            )
        if crossing is None:
            return TriggeredPlot(
                None,
                None,
                self.latest.sample_rate_hz,
                visible,
                None,
            )

        first, second, trigger_offset, positions = interpolate_trigger_window(
            self,
            count,
            visible,
            crossing,
            trigger_channel,
            level,
            pre_trigger_samples,
            point_budget,
        )
        return TriggeredPlot(
            first,
            second,
            self.latest.sample_rate_hz,
            visible,
            trigger_offset,
            positions,
        )

    def find_channel_crossing_before(
        self,
        channel: int,
        requested_samples: int,
        level: float,
        edge: str,
        *,
        target_index: int,
        minimum_index: int = 1,
        chunk_size: int = 65_536,
        hysteresis: float = 0.0,
    ) -> int | None:
        """Find the latest raw-sample threshold crossing at or before a target.

        Plot data is intentionally decimated. Trigger detection must instead use
        the raw ring so narrow or high-frequency crossings are not skipped.
        Searching backwards in chunks normally inspects only the most recent
        fraction of a period while keeping very wide history views inexpensive.
        """

        buffer = self.channels.get(channel)
        count = min(max(int(requested_samples), 0), self.size)
        if buffer is None or count < 2 or not np.isfinite(level):
            return None
        lower = min(max(int(minimum_index), 1), count - 1)
        upper = min(max(int(target_index), 0), count - 1)
        if upper < lower:
            return None

        hysteresis = max(float(hysteresis), 0.0)
        if hysteresis:
            return qualified_crossing(
                lambda start, end: self._channel_window(buffer, count, start, end),
                count, level, edge, lower, upper, hysteresis, chunk_size,
            )

        chunk_size = max(int(chunk_size), 2)
        while upper >= lower:
            chunk_lower = max(lower, upper - chunk_size + 1)
            values = self._channel_window(buffer, count, chunk_lower - 1, upper + 1)
            before = values[:-1]
            after = values[1:]
            if edge.lower() == "falling":
                matches = (before > level) & (after <= level)
            else:
                matches = (before < level) & (after >= level)
            crossings = np.flatnonzero(matches)
            if len(crossings):
                return chunk_lower + int(crossings[-1])
            upper = chunk_lower - 1
        return None

    def _find_last_level_index(
        self,
        buffer: np.ndarray[Any, Any],
        count: int,
        lower: int,
        upper: int,
        threshold: float,
        *,
        above: bool,
        chunk_size: int,
    ) -> int | None:
        chunk_size = max(int(chunk_size), 1)
        while upper >= lower:
            chunk_lower = max(lower, upper - chunk_size + 1)
            values = self._channel_window(buffer, count, chunk_lower, upper + 1)
            matches = values >= threshold if above else values <= threshold
            indices = np.flatnonzero(matches)
            if len(indices):
                return chunk_lower + int(indices[-1])
            upper = chunk_lower - 1
        return None

    def _channel_window(
        self,
        buffer: np.ndarray[Any, Any],
        count: int,
        start: int,
        end: int,
    ) -> np.ndarray[Any, Any]:
        """Return a logical history slice, joining only when the ring wraps."""

        length = max(end - start, 0)
        physical_start = (self.position - count + start) % self.capacity
        first = min(length, self.capacity - physical_start)
        if first == length:
            return buffer[physical_start : physical_start + length]
        return np.concatenate((buffer[physical_start:], buffer[: length - first]))


def parse_frame(message: bytes) -> SampleFrame:
    """Decode and validate one protocol-v1 binary WebSocket message."""
    if len(message) < HEADER_SIZE:
        raise ValueError(f"message too short: {len(message)} bytes")
    values = HEADER.unpack_from(message)
    magic, protocol_version, channel_mask, header_size = values[:4]
    if magic != MAGIC or protocol_version != PROTOCOL_VERSION or header_size != HEADER_SIZE:
        raise ValueError(
            f"unsupported frame: magic={magic!r}, version={protocol_version}, header={header_size}"
        )
    if channel_mask not in (1, 2, 3):
        raise ValueError(f"invalid channel mask: {channel_mask}")

    sequence = int(values[5])
    sample_rate_hz = float(values[8])
    if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("sample rate must be positive and finite")
    samples_per_channel = int(values[10])
    payload_size = int(values[11])
    flags = int(values[12])
    fpga_lost_ch1 = int(values[13])
    fpga_lost_ch2 = int(values[14])

    channel_count = int(bool(channel_mask & 1)) + int(bool(channel_mask & 2))
    expected_payload_size = samples_per_channel * channel_count * 2
    if samples_per_channel <= 0 or payload_size != expected_payload_size:
        raise ValueError(
            "invalid payload size: "
            f"expected {expected_payload_size}, header declares {payload_size}"
        )
    expected_message_size = HEADER_SIZE + payload_size
    if len(message) != expected_message_size:
        raise ValueError(
            f"invalid message size: expected {expected_message_size}, got {len(message)}"
        )
    if sys.byteorder != "little":
        raise RuntimeError("native receiver currently expects a little-endian host")

    samples = memoryview(message)[HEADER_SIZE:].cast("h")
    offset = 0
    channel_1 = None
    channel_2 = None
    if channel_mask & 1:
        channel_1 = samples[offset : offset + samples_per_channel]
        offset += samples_per_channel
    if channel_mask & 2:
        channel_2 = samples[offset : offset + samples_per_channel]

    return SampleFrame(
        channel_mask=channel_mask,
        sequence=sequence,
        sample_rate_hz=sample_rate_hz,
        samples_per_channel=samples_per_channel,
        flags=flags,
        fpga_lost_ch1=fpga_lost_ch1,
        fpga_lost_ch2=fpga_lost_ch2,
        channel_1=channel_1,
        channel_2=channel_2,
        stream_id=int(values[4]),
    )
