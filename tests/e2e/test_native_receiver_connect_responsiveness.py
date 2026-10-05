from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from apps.native_receiver import native_receiver as nr


def check_slow_redpitaya_connect_keeps_tk_ui_responsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nr,
        "load_settings",
        lambda: nr.AppSettings(launch_engine_on_start=False),
    )
    monkeypatch.setattr(nr.ReceiverApp, "refresh_devices", lambda self: None)
    monkeypatch.setattr(
        nr.ReceiverApp, "_engine_is_running_for_base", lambda self, _base: False,
    )
    app = nr.ReceiverApp()
    try:
        app.attributes("-alpha", 0)
        # Complete the initial window mapping before measuring a user's Connect action.
        app.update()
        assert "disabled" in app.connect_button.state()
        device = nr.Device(
            id="11111111-1111-1111-1111-111111111111",
            name="RP slow",
            host="rp-slow.local",
            state="disconnected",
            channel_1_enabled=True,
            channel_2_enabled=True,
            decimation=64,
            block_size_bytes=16_384,
        )
        app.devices = [device]
        app.selected_device = device
        app._update_connect_controls()
        assert "disabled" not in app.connect_button.state()
        app._start_socket = lambda _device_id: None  # type: ignore[method-assign]
        app._open_device_tab = lambda _device_id: None  # type: ignore[method-assign]
        app._apply_loaded_devices = lambda _devices: None  # type: ignore[method-assign]
        ui_thread = threading.get_ident()

        def api(
            _api_base: str,
            method: str,
            path: str,
            _body: dict[str, Any] | None = None,
            *,
            timeout_seconds: float = 10,
        ) -> dict[str, str] | None:
            del timeout_seconds
            assert threading.get_ident() != ui_thread
            if path.endswith("/status"):
                return {"state": "disconnected"}
            if method == "POST" and path.endswith("/connect"):
                time.sleep(1.1)
                return {"state": "ready"}
            if method == "POST" and path.endswith("/start"):
                return {"state": "streaming"}
            return None

        app._api_for_base = api  # type: ignore[method-assign]
        app._load_devices = lambda _api_base: [device]  # type: ignore[method-assign]

        app.connect_selected()
        heartbeats: list[float] = []

        def heartbeat() -> None:
            heartbeats.append(time.monotonic())
            app.after(25, heartbeat)

        app.after(25, heartbeat)
        seen: list[str] = []
        end = time.monotonic() + 0.7
        while time.monotonic() < end:
            app.update()
            seen.append(app.loading_message.get())
            time.sleep(0.02)

        assert len(set(seen)) > 2
        assert len(heartbeats) >= 8
        assert app.loading_active
        assert app.cget("cursor") == ""
        assert app.connection_overlay is not None
        assert app.connection_overlay.winfo_ismapped()
        assert app.connection_overlay.cget("bg") == nr.CONNECTION_OVERLAY_BG
        assert nr.CONNECTION_OVERLAY_BG != "#000000"
        assert app.connection_overlay_bg_rect is not None
        assert app.connection_overlay_canvas is not None
        assert (
            app.connection_overlay_canvas.itemcget(app.connection_overlay_bg_rect, "fill")
            == nr.CONNECTION_OVERLAY_BG
        )
        assert app.connection_overlay_title.get() == "Connecting to Red Pitaya"
        app._show_toast(
            "Connection failed",
            "Could not connect to Red Pitaya. Timed out after 8s",
            level="error",
        )
        app.update()

        assert len(app.toast_frames) == 1
        assert app.toast_frames[0].winfo_exists()
        app._dismiss_toast(app.toast_frames[0])
        app._show_connection_failure(
            "Connection failed",
            "Could not connect to Red Pitaya. Timed out after 8s",
        )
        app.update()

        assert app.status.get() == "RP not connected to engine"
        assert app.connection_failure_active
        assert app.signal_notice_items
        assert app.canvas.itemcget(app.signal_notice_items[-1], "text") == (
            "Red Pitaya is not connected to the RTQLM acquisition engine"
        )
        for toast in [*app.toast_frames]:
            app._dismiss_toast(toast)
        app._show_toast(
            "Connection failed",
            "Could not connect to Red Pitaya. Timed out after 8s",
            level="error",
            duration_ms=10,
        )
        deadline = time.monotonic() + 1.0
        while app.toast_frames and time.monotonic() < deadline:
            app.update()
            time.sleep(0.02)

        assert app.toast_frames == []
    finally:
        app._on_close()


def test_slow_redpitaya_connect_keeps_tk_ui_responsive() -> None:
    # Tk interpreters and their background callbacks must not leak across pytest tests.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pytest; from tests.e2e.test_native_receiver_connect_responsiveness "
            "import check_slow_redpitaya_connect_keeps_tk_ui_responsive; "
            "patch = pytest.MonkeyPatch(); "
            "check_slow_redpitaya_connect_keeps_tk_ui_responsive(patch); patch.undo()",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
