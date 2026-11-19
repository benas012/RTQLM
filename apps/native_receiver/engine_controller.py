from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from apps.native_receiver.api_client import EngineClient
from apps.native_receiver.constants import (
    ENGINE_PING_INTERVAL_MS,
    ENGINE_PROCESS_TITLE,
    ENGINE_READY_TIMEOUT_SECONDS,
    WORKSPACE_ROOT,
)
from apps.native_receiver.helpers import (
    short_error,
    terminate_process_tree,
)


class EngineControllerMixin:
    def ensure_engine_running(self) -> None:
        self._ui_log("start engine requested")
        self.save_settings()
        self._set_engine_status("checking")
        if self.engine_process and self.engine_process.poll() is None:
            self.status.set("engine starting")
            self._wait_for_engine_ready_async()
            return

        api_base = self._api_base()
        self._run_busy(
            "Checking engine",
            lambda: EngineClient(api_base).is_running(),
            self._handle_engine_check,
        )

    def _handle_engine_check(self, running: bool) -> None:
        if running:
            self._set_engine_status("ok")
            self.status.set("engine already running")
            self.activity.set("")
            self._ui_log("engine already running")
            return
        self._set_engine_status("starting")
        self._launch_engine_process()

    def _launch_engine_process(self) -> None:
        try:
            env = os.environ.copy()
            default_module_path = (
                Path(env.get("LOCALAPPDATA", ""))
                / "RedPitaya"
                / "rpsa-client-2.07-48"
                / "python_lib"
            )
            if "QL_STREAMING_MODULE_PATH" not in env and default_module_path.exists():
                env["QL_STREAMING_MODULE_PATH"] = str(default_module_path)

            env["RTQML_ENGINE_PROCESS_NAME"] = ENGINE_PROCESS_TITLE
            command = self._engine_command()
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.engine_process = subprocess.Popen(
                command,
                cwd=WORKSPACE_ROOT,
                env=env,
                creationflags=flags,
                text=True,
            )
            self.status.set("engine starting")
            self._set_engine_status("starting")
            self._wait_for_engine_ready_async()
        except Exception as exc:
            self._set_engine_status("error")
            self._stop_spinner()
            self._show_error(exc)

    def _engine_command(self) -> list[str]:
        launcher = WORKSPACE_ROOT / ".venv" / "Scripts" / "quantum-light-engine.exe"
        if launcher.exists():
            return [str(launcher), "run", "--host", "127.0.0.1", "--port", "8080"]
        return [
            sys.executable,
            "-c",
            (
                f"RTQML_ENGINE_PROCESS_NAME={ENGINE_PROCESS_TITLE!r}; "
                "from quantum_light_engine.cli import main; "
                "raise SystemExit(main(['run','--host','127.0.0.1','--port','8080']))"
            ),
        ]

    def stop_engine(self) -> None:
        self._ui_log("stop engine requested")
        if self.engine_process and self.engine_process.poll() is None:
            process = self.engine_process
            self.status.set("stopping acquisition engine")
            self._set_engine_status("stopping")

            def work() -> subprocess.Popen[Any]:
                self._prepare_engine_shutdown()
                terminate_process_tree(process)
                return process

            def apply(stopped_process: subprocess.Popen[Any]) -> None:
                if self.engine_process is stopped_process:
                    self.engine_process = None
                self.status.set("engine stopped")
                self._set_engine_status("stopped")

            self._run_busy("Stopping acquisition engine", work, apply)
            return
        self.status.set("engine not launched by this app")
        self._set_engine_status("unknown")

    def _prepare_engine_shutdown(self, *, timeout_seconds: float = 10) -> None:
        """Let the engine stop remote devices before its process is terminated."""

        try:
            self._api_for_base(
                self._api_base(),
                "POST",
                "/api/v1/engine/prepare-shutdown",
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            # Compatibility fallback for an older or already-unreachable engine.
            self._ui_log(
                "engine prepare-shutdown request failed",
                error=short_error(exc, 180),
            )

    def _wait_for_engine_ready_async(self) -> None:
        api_base = self._api_base()
        delay_seconds = 0.5
        attempts = max(1, int(ENGINE_READY_TIMEOUT_SECONDS / delay_seconds))
        self._run_busy(
            "Launching engine",
            lambda: EngineClient(api_base).wait_until_running(
                attempts=attempts, delay_seconds=delay_seconds
            ),
            self._handle_engine_ready,
        )

    def _handle_engine_ready(self, running: bool) -> None:
        if running:
            self._set_engine_status("ok")
            self.status.set("engine running")
            self.refresh_devices()
            return
        self._set_engine_status("timeout")
        self.status.set("engine launch timeout")

    def _ping_engine_status_async(self) -> None:
        if getattr(self, "closing", False):
            return
        if self.engine_ping_in_flight:
            self.after(ENGINE_PING_INTERVAL_MS, self._ping_engine_status_async)
            return

        api_base = self._api_base()
        self.engine_ping_in_flight = True

        def runner() -> None:
            started = time.monotonic()
            running = EngineClient(api_base).is_running()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self.events.put(("engine_ping", (running, elapsed_ms)))

        threading.Thread(target=runner, daemon=True).start()
        self.after(ENGINE_PING_INTERVAL_MS, self._ping_engine_status_async)

    def _handle_engine_ping(self, running: bool, elapsed_ms: int) -> None:
        self.engine_ping_in_flight = False
        if running:
            self._set_engine_status("ok")
            return

        if self.engine_process is not None and self.engine_process.poll() is not None:
            self.engine_process = None
            self._set_engine_status("exited")
            self.status.set("engine exited")
        else:
            self._set_engine_status("offline")
        self._ui_log("engine ping failed", elapsed_ms=elapsed_ms)
