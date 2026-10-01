from __future__ import annotations

import json
import time
import tkinter as tk
from tkinter import messagebox
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from apps.native_receiver.constants import (
    UI_LOGGER,
)
from apps.native_receiver.dialogs import ApiError
from apps.native_receiver.helpers import (
    short_error,
    terminate_process_tree,
)


class ApiClientMixin:
    def _api(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 10,
    ) -> Any:
        return self._api_for_base(
            self._api_base(), method, path, body, timeout_seconds=timeout_seconds
        )

    def _api_for_base(
        self,
        api_base: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 10,
    ) -> Any:
        started = time.monotonic()
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            f"{api_base.rstrip('/')}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                UI_LOGGER.info(
                    "api %s %s -> %s elapsed_ms=%s",
                    method,
                    path,
                    response.status,
                    elapsed_ms,
                )
                if response.status == 204:
                    return None
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            detail = exc.reason
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail = payload.get("detail", detail)
            except Exception:
                pass
            UI_LOGGER.info(
                "api %s %s -> %s elapsed_ms=%s error=%s",
                method,
                path,
                exc.code,
                elapsed_ms,
                detail,
            )
            raise ApiError(str(detail)) from exc
        except TimeoutError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            detail = f"Timed out after {timeout_seconds:g}s"
            UI_LOGGER.info(
                "api %s %s timeout elapsed_ms=%s error=%s",
                method,
                path,
                elapsed_ms,
                detail,
            )
            raise ApiError(detail) from exc
        except URLError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            UI_LOGGER.info(
                "api %s %s failed elapsed_ms=%s error=%s",
                method,
                path,
                elapsed_ms,
                exc.reason,
            )
            raise ApiError(str(exc.reason)) from exc

    def _api_base(self) -> str:
        return self.api_base.get().strip().rstrip("/")

    def _websocket_url(self, device_id: str) -> str:
        parsed = urlparse(self._api_base())
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}/ws/v1/devices/{device_id}/samples"

    def _show_error(self, exc: BaseException) -> None:
        self.status.set("error")
        if hasattr(self, "engine_status"):
            self._set_engine_status("error")
        self._ui_log("ui error", error=short_error(exc, 180))
        messagebox.showerror("Error", str(exc))

    def _on_close(self) -> None:
        self._ui_log("app closing")
        self.closing = True
        self.stop_event.set()
        self._stop_spinner()
        try:
            for callback_id in self.tk.call("after", "info"):
                try:
                    self.after_cancel(callback_id)
                except tk.TclError:
                    pass
        except tk.TclError:
            pass
        for toast in [*self.toast_frames]:
            self._dismiss_toast(toast)
        if self.connection_overlay is not None and self.connection_overlay.winfo_exists():
            self.connection_overlay.destroy()
        if self.engine_process and self.engine_process.poll() is None:
            self._prepare_engine_shutdown(timeout_seconds=3)
            terminate_process_tree(self.engine_process)
        self.destroy()
