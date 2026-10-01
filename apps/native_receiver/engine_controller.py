from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Any

from apps.native_receiver.constants import (
    CONNECTION_FAILED_NOTICE,
    CONNECTION_FAILED_SPECTRUM_NOTICE,
    CONNECTION_OVERLAY_BG,
    CONNECTION_OVERLAY_BUSY_LABELS,
    CONNECTION_OVERLAY_OPACITY,
    ENGINE_PING_INTERVAL_MS,
    ENGINE_PROCESS_TITLE,
    ENGINE_READY_TIMEOUT_SECONDS,
    WORKSPACE_ROOT,
)
from apps.native_receiver.helpers import (
    short_error,
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
            lambda: self._engine_is_running_for_base(api_base),
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
        from apps.native_receiver import native_receiver as receiver_api

        launcher = receiver_api.WORKSPACE_ROOT / ".venv" / "Scripts" / "quantum-light-engine.exe"
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
                from apps.native_receiver import native_receiver as receiver_api

                self._prepare_engine_shutdown()
                receiver_api.terminate_process_tree(process)
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
            lambda: self._wait_engine_ready(
                api_base, attempts=attempts, delay_seconds=delay_seconds
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
            running = self._engine_is_running_for_base(api_base)
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

    def _start_spinner(self, label: str) -> None:
        self._ui_log("loading started", label=label)
        self.spinner_token += 1
        token = self.spinner_token
        self.spinner_running = True
        self.loading_active = True
        self.loading_label = label
        self.spinner_index = 0
        self._update_connect_controls()
        if label in CONNECTION_OVERLAY_BUSY_LABELS:
            self._show_connection_overlay("Connecting to Red Pitaya", label)
        else:
            self._set_signal_notice(label)
            self._set_spectrum_notice(label)
        self.update_idletasks()
        self._spin(label, token)

    def _spin(self, label: str, token: int) -> None:
        if not self.spinner_running or token != self.spinner_token:
            return
        frames = "|/-\\"
        message = f"{frames[self.spinner_index % len(frames)]} {label}..."
        self.activity.set(message)
        self.loading_message.set(message)
        if label in CONNECTION_OVERLAY_BUSY_LABELS:
            self._update_connection_overlay_spinner(message)
        else:
            self._set_signal_notice(message)
            self._set_spectrum_notice(message)
        self.spinner_index += 1
        self.after(100, lambda: self._spin(label, token))

    def _stop_spinner(self, *, restore_notices: bool = True) -> None:
        if self.loading_label:
            self._ui_log("loading stopped", label=self.loading_label)
        self.spinner_running = False
        self.spinner_token += 1
        self.loading_active = False
        self.loading_label = ""
        self.activity.set("")
        self.loading_message.set("Ready")
        self._update_connect_controls()
        self._hide_connection_overlay()
        if not restore_notices:
            return
        with self.frame_lock:
            has_frame = self.latest_frame is not None
        if not has_frame:
            self._restore_idle_notices()
        else:
            self._clear_signal_notice()
            self._clear_spectrum_notice()

    def _show_connection_overlay(self, title: str, detail: str) -> None:
        self.connection_overlay_title.set(title)
        self.connection_overlay_detail.set(detail)
        overlay = self.connection_overlay
        canvas = self.connection_overlay_canvas
        if overlay is None or canvas is None:
            return
        overlay.configure(bg=CONNECTION_OVERLAY_BG)
        canvas.configure(bg=CONNECTION_OVERLAY_BG)
        try:
            overlay.attributes("-alpha", CONNECTION_OVERLAY_OPACITY)
        except tk.TclError:
            pass
        if self.connection_overlay_bg_rect is not None:
            canvas.itemconfigure(
                self.connection_overlay_bg_rect,
                fill=CONNECTION_OVERLAY_BG,
                outline=CONNECTION_OVERLAY_BG,
            )
        if self.connection_overlay_title_item is not None:
            canvas.itemconfigure(
                self.connection_overlay_title_item,
                text=title,
            )
        if self.connection_overlay_detail_item is not None:
            canvas.itemconfigure(
                self.connection_overlay_detail_item,
                text=detail,
            )
        self._position_connection_overlay()
        overlay.deiconify()
        overlay.lift(self)

    def _update_connection_overlay_spinner(self, detail: str) -> None:
        self.connection_overlay_detail.set(detail)
        canvas = self.connection_overlay_canvas
        if canvas is None or self.connection_overlay_spinner_arc is None:
            return
        self._position_connection_overlay()
        if self.connection_overlay_detail_item is not None:
            canvas.itemconfigure(self.connection_overlay_detail_item, text=detail)
        canvas.itemconfigure(
            self.connection_overlay_spinner_arc,
            start=(90 - self.spinner_index * 24) % 360,
        )

    def _hide_connection_overlay(self) -> None:
        if self.connection_overlay is not None and self.connection_overlay.winfo_exists():
            self.connection_overlay.withdraw()

    def _restore_idle_notices(self) -> None:
        if self.connection_failure_active:
            self._set_connection_failed_notice()
            return
        self._set_signal_notice("Waiting for live samples")
        self._set_spectrum_notice("Waiting for spectrum data")

    def _set_connection_failed_notice(self) -> None:
        self.connection_failure_active = True
        self._set_signal_notice(CONNECTION_FAILED_NOTICE)
        self._set_spectrum_notice(CONNECTION_FAILED_SPECTRUM_NOTICE)
