from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.title() for word in rest)


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True, extra="forbid")


class DeviceSource(StrEnum):
    REDPITAYA = "redpitaya"
    SIMULATOR = "simulator"


class ProfileFields(CamelModel):
    name: str = Field(min_length=1, max_length=200)
    source: DeviceSource
    host: str | None = None
    enabled: bool = True
    is_default: bool = False
    channel_1_enabled: bool = True
    channel_2_enabled: bool = True
    decimation: int = Field(default=256, ge=1, le=65_536)
    block_size_bytes: int = Field(default=131_072, ge=2_048, le=2_097_152)
    adc_buffer_size_bytes: int = Field(default=4_194_304, ge=1_048_576, le=104_857_600)
    streaming_module_path: str | None = None
    simulator_samples_per_packet: int = Field(default=4096, gt=0, le=1_048_576)
    simulator_seed: int = 1

    @model_validator(mode="after")
    def validate_profile(self) -> ProfileFields:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("device name must not be empty")
        if self.source is DeviceSource.REDPITAYA and not (self.host and self.host.strip()):
            raise ValueError("a Red Pitaya profile requires a non-empty host")
        if not self.channel_1_enabled and not self.channel_2_enabled:
            raise ValueError("at least one acquisition channel must be enabled")
        if self.host is not None:
            self.host = self.host.strip() or None
        return self


class DeviceProfileCreate(ProfileFields):
    """Validated fields accepted when registering a device."""


class DeviceProfileUpdate(ProfileFields):
    """Complete replacement payload for an existing device profile."""


class DeviceProfile(ProfileFields):
    id: UUID
    created_at_utc: datetime
    updated_at_utc: datetime

    @model_validator(mode="after")
    def timestamps_are_utc(self) -> DeviceProfile:
        if self.created_at_utc.tzinfo is None or self.updated_at_utc.tzinfo is None:
            raise ValueError("profile timestamps must include UTC timezone information")
        self.created_at_utc = self.created_at_utc.astimezone(UTC)
        self.updated_at_utc = self.updated_at_utc.astimezone(UTC)
        return self

    @classmethod
    def create(cls, request: DeviceProfileCreate) -> DeviceProfile:
        now = datetime.now(UTC)
        return cls(id=uuid4(), created_at_utc=now, updated_at_utc=now, **request.model_dump())
