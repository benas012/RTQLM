from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QL_", env_file=".env", extra="ignore", validate_default=True
    )

    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8080, ge=1, le=65_535)
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")
    internal_queue_capacity: int = Field(default=16, gt=0)
    client_queue_capacity: int = Field(default=4, gt=0)
    reconnect_initial_seconds: float = Field(default=1.0, gt=0)
    reconnect_maximum_seconds: float = Field(default=10.0, gt=0)
    streaming_module_path: str | None = None
    auto_start_streaming_app: bool = True
    streaming_app_startup_delay_seconds: float = Field(default=6.0, ge=0, le=30)
    log_level: str = "INFO"
    auto_connect: bool = False
    auto_start: bool = False
    shutdown_timeout_seconds: float = Field(default=10.0, gt=0)
    trigger_ssh_password: SecretStr | None = None
    native_nullifier_enabled: bool = False
    native_nullifier_executable: str = "/root/rtqlm-nullifiers/current/build/rp-nullifierd"
    native_nullifier_port: int = Field(default=18902, ge=1024, le=65535)
    native_nullifier_hosts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_relationships(self) -> EngineSettings:
        if self.reconnect_maximum_seconds < self.reconnect_initial_seconds:
            raise ValueError("reconnect maximum must be greater than or equal to initial")
        level = self.log_level.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log level is invalid")
        self.log_level = level
        return self
