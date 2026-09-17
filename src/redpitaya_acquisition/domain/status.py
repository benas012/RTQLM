from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from .state import AcquisitionState


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.title() for word in rest)


class StatusResponse(BaseModel):
    model_config = ConfigDict(alias_generator=lambda s: _camel(s), populate_by_name=True)

    state: AcquisitionState
    source: str
    host: str
    connected: bool
    stream_id: int | None
    sequence: int
    packets_received: int
    frames_published: int
    malformed_packets: int
    service_dropped_frames: int
    web_socket_clients: int
    fpga_lost_ch1: int
    fpga_lost_ch2: int
    fpga_loss_deltas_ch1: int
    fpga_loss_deltas_ch2: int
    packet_id_gaps: int
    decimation: int
    base_rate_hz: int
    sample_rate_hz: float
    last_frame_utc: datetime | None
    last_error: str | None
    streaming_module: str | None
    streaming_module_path: str | None


def jsonable_status(status: StatusResponse) -> dict[str, Any]:
    return status.model_dump(mode="json", by_alias=True)
