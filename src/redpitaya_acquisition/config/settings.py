from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourceKind(StrEnum):
    SIMULATOR = "simulator"
    REDPITAYA = "redpitaya"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RP_", env_file=".env", extra="ignore", validate_default=True
    )

    source: SourceKind = SourceKind.SIMULATOR
    host: str = "rp-xxxxxx.local"
    channel_1_enabled: bool = True
    channel_2_enabled: bool = True
    decimation: int = Field(default=256, ge=1, le=65_536)
    block_size_bytes: int = Field(default=131_072, ge=2_048, le=2_097_152)
    adc_buffer_size_bytes: int = Field(default=4_194_304, ge=1_048_576, le=104_857_600)
    internal_queue_capacity: int = Field(default=16, gt=0)
    client_queue_capacity: int = Field(default=4, gt=0)
    reconnect_initial_seconds: float = Field(default=1.0, gt=0)
    reconnect_maximum_seconds: float = Field(default=10.0, gt=0)
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8080, ge=1, le=65_535)
    streaming_module: str | None = None
    verbose_library_logging: bool = False
    auto_start_streaming_app: bool = True
    streaming_app_startup_delay_seconds: float = Field(default=6.0, ge=0, le=30)
    auto_start: bool = False
    simulator_samples_per_packet: int = Field(default=4096, gt=0, le=1_048_576)
    simulator_seed: int = 1
    simulator_packet_gap_every: int | None = Field(default=None, gt=1)
    simulator_fpga_loss_every: int | None = Field(default=None, gt=1)

    @model_validator(mode="after")
    def validate_relationships(self) -> Settings:
        if not self.channel_1_enabled and not self.channel_2_enabled:
            raise ValueError("at least one acquisition channel must be enabled")
        if self.source is SourceKind.REDPITAYA and not self.host.strip():
            raise ValueError("host must be non-empty in Red Pitaya mode")
        if self.reconnect_maximum_seconds < self.reconnect_initial_seconds:
            raise ValueError("reconnect maximum must be greater than or equal to initial")
        if self.streaming_module is not None and not self.streaming_module.strip():
            self.streaming_module = None
        return self

    @property
    def channel_mask(self) -> int:
        return int(self.channel_1_enabled) | (int(self.channel_2_enabled) << 1)

    @property
    def sample_rate_hz(self) -> float:
        return 125_000_000 / self.decimation
