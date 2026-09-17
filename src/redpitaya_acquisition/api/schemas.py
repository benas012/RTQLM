from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic_settings import SettingsConfigDict

from redpitaya_acquisition.config.settings import Settings, SourceKind


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.title() for word in rest)


class ConfigurationResponse(BaseModel):
    model_config = ConfigDict(alias_generator=lambda s: _camel(s), populate_by_name=True)

    source: SourceKind
    host: str
    channel_1_enabled: bool
    channel_2_enabled: bool
    decimation: int
    block_size_bytes: int
    adc_buffer_size_bytes: int
    internal_queue_capacity: int
    client_queue_capacity: int
    reconnect_initial_seconds: float
    reconnect_maximum_seconds: float
    bind_host: str
    bind_port: int
    streaming_module: str | None
    verbose_library_logging: bool
    auto_start: bool
    simulator_samples_per_packet: int
    simulator_seed: int
    simulator_packet_gap_every: int | None
    simulator_fpga_loss_every: int | None

    @classmethod
    def from_settings(cls, settings: Settings) -> ConfigurationResponse:
        return cls.model_validate(settings.model_dump())


class Problem(BaseModel):
    detail: str


class ConfigurationUpdate(Settings):
    model_config = SettingsConfigDict(
        env_prefix="RP_",
        env_file=None,
        alias_generator=_camel,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )
