from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.title() for word in rest)


class EngineStatus(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)

    engine_version: str = "0.2.0"
    api_version: str = "1"
    state: str
    started_at_utc: datetime
    uptime_seconds: float
    device_count: int
    connected_device_count: int
    streaming_device_count: int
    total_web_socket_clients: int


class TestOutputRequest(BaseModel):
    enabled: bool
