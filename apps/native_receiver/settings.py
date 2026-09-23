"""Persisted settings owned by the native receiver."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from quantum_light_engine.post_processing import (
    ClusterProcessingConfig,
    config_from_dict,
    config_to_dict,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = WORKSPACE_ROOT / "data" / "native_receiver_settings.json"
CLUSTER_CONFIG_PATH = WORKSPACE_ROOT / "data" / "cluster_processing.json"
SIGNAL_SCRIPT_FEATURE_ENV = "QL_NATIVE_RECEIVER_SIGNAL_SCRIPT"
LOGGER = logging.getLogger("quantum_light_engine.native_ui")


@dataclass(frozen=True)
class AppSettings:
    api_base: str = "http://127.0.0.1:8080"
    launch_engine_on_start: bool = True


def load_settings() -> AppSettings:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("receiver settings must be an object")
        return AppSettings(
            api_base=str(data.get("api_base") or AppSettings.api_base),
            launch_engine_on_start=bool(
                data.get("launch_engine_on_start", AppSettings.launch_engine_on_start)
            ),
        )
    except (FileNotFoundError, TypeError, ValueError, OSError):
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    _write_json_atomic(
        SETTINGS_PATH,
        {
            "api_base": settings.api_base,
            "launch_engine_on_start": settings.launch_engine_on_start,
        },
    )


def load_cluster_config() -> ClusterProcessingConfig:
    try:
        data = json.loads(CLUSTER_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("cluster processing configuration must be an object")
        return config_from_dict(data)
    except FileNotFoundError:
        return ClusterProcessingConfig()
    except (TypeError, ValueError, OSError) as exc:
        LOGGER.warning("invalid cluster processing configuration: %s", exc)
        return ClusterProcessingConfig()


def save_cluster_config(config: ClusterProcessingConfig) -> None:
    _write_json_atomic(CLUSTER_CONFIG_PATH, config_to_dict(config))


def signal_script_feature_enabled() -> bool:
    return os.environ.get(SIGNAL_SCRIPT_FEATURE_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def _write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)
