from __future__ import annotations

import importlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from redpitaya_acquisition.acquisition.manager import NonRecoverableAcquisitionError


@dataclass(frozen=True, slots=True)
class LoadedStreamingLibrary:
    module: ModuleType
    name: str
    path: str | None


def load_streaming_library(configured_name: str | None = None) -> LoadedStreamingLibrary:
    names: tuple[str, ...]
    if configured_name and Path(configured_name).expanduser().exists():
        _prepend_library_paths(_configured_path_candidates(Path(configured_name)))
        names = ("rp_stream", "streaming")
    else:
        if configured_name is None:
            _prepend_library_paths(_installed_client_candidates())
        names = (configured_name,) if configured_name else ("rp_stream", "streaming")
    failures: list[str] = []
    for name in names:
        if not name:
            continue
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            failures.append(f"{name}: {exc}")
            continue
        missing = [item for item in ("ADCStreamClient", "ADCCallback") if not hasattr(module, item)]
        if missing:
            raise NonRecoverableAcquisitionError(
                f"streaming module '{name}' is incompatible; missing {', '.join(missing)}"
            )
        return LoadedStreamingLibrary(module, name, getattr(module, "__file__", None))
    tried = ", ".join(name for name in names if name)
    raise NonRecoverableAcquisitionError(
        "Red Pitaya streaming library not found. Install the client library matching the board OS "
        f"and configure its module name or python_lib directory if needed (tried: {tried}). "
        + "; ".join(failures)
    )


def _configured_path_candidates(path: Path) -> list[Path]:
    root = path.expanduser().resolve()
    return [root, root / "python_lib"] if root.is_dir() else [root.parent]


def _installed_client_candidates() -> list[Path]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return []
    redpitaya_root = Path(local_app_data) / "RedPitaya"
    if not redpitaya_root.exists():
        return []
    return sorted(
        (path / "python_lib" for path in redpitaya_root.glob("rpsa-client-*")),
        key=lambda path: _client_version(path.parent.name),
        reverse=True,
    )


def _client_version(directory_name: str) -> tuple[int, ...]:
    """Return a naturally sortable version from an rpsa-client directory name."""

    return tuple(int(part) for part in re.findall(r"\d+", directory_name))


def _prepend_library_paths(candidates: list[Path]) -> None:
    for candidate in reversed(candidates):
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
