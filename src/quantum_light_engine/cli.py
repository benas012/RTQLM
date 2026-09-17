from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from quantum_light_engine.application import create_app
from quantum_light_engine.config.settings import EngineSettings
from quantum_light_engine.devices.persistence import FileDeviceProfileRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantum-light-engine")
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="run the acquisition engine")
    run.add_argument("--host", default=None)
    run.add_argument("--port", type=int, default=None)
    run.add_argument("--data-dir", type=Path, default=None)
    run.add_argument("--log-dir", type=Path, default=None)
    validate = subcommands.add_parser("validate-config", help="validate settings and profiles")
    validate.add_argument("--data-dir", type=Path, default=None)
    listing = subcommands.add_parser("list-devices", help="list stored profile metadata")
    listing.add_argument("--data-dir", type=Path, default=None)
    return parser


def _settings(args: argparse.Namespace) -> EngineSettings:
    overrides = {
        "bind_host": getattr(args, "host", None),
        "bind_port": getattr(args, "port", None),
        "data_dir": getattr(args, "data_dir", None),
        "log_dir": getattr(args, "log_dir", None),
    }
    return EngineSettings(**{key: value for key, value in overrides.items() if value is not None})


async def _load_profiles(settings: EngineSettings) -> FileDeviceProfileRepository:
    repository = FileDeviceProfileRepository(settings.data_dir)
    await repository.initialize()
    return repository


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = _settings(args)
        if args.command == "run":
            uvicorn.run(create_app(settings), host=settings.bind_host, port=settings.bind_port)
            return 0
        repository = asyncio.run(_load_profiles(settings))
        profiles = asyncio.run(repository.list())
        if args.command == "validate-config":
            print(f"configuration valid; {len(profiles)} device profile(s)")
            return 0
        for profile in profiles:
            print(
                f"{profile.id}\t{profile.name}\t{profile.source.value}\t"
                f"{profile.host or '-'}\tdefault={str(profile.is_default).lower()}"
            )
        return 0
    except (ValidationError, ValueError, OSError) as exc:
        print(f"configuration invalid: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
