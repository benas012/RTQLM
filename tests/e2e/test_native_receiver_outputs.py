from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.native_receiver.native_receiver import ReceiverApp
from apps.native_receiver.output_signals import OutputSignalDialog
from redpitaya_acquisition.redpitaya.output_signals import OutputSignalsConfig, build_output_buffers


def check_output_editor() -> None:
    calls = []
    config = OutputSignalsConfig()

    def start_ui(app):  # type: ignore[no-untyped-def]
        app.attributes("-alpha", 0)
        app._build_menu()
        app._build()

    def request(self, base, method, path, body=None, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal config
        calls.append((base, method, path, body))
        if method == "PUT":
            config = OutputSignalsConfig.model_validate(body)
        elif method == "POST":
            config = OutputSignalsConfig()
        _, metadata = build_output_buffers(config, include_buffers=False)
        return {
            "state": "running" if config.output1.enabled else "inactive",
            "config": config.model_dump(mode="json", by_alias=True),
            **metadata,
        }

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(ReceiverApp, "_start_receiver", start_ui)
        monkeypatch.setattr(ReceiverApp, "_api_for_base", request)
        monkeypatch.setattr(
            ReceiverApp, "_run_busy", lambda self, label, work, callback: callback(work())
        )
        app = ReceiverApp()
        try:
            first = OutputSignalDialog(app, SimpleNamespace(id="first", name="First board"))
            first.attributes("-alpha", 0)
            app.update()
            first._preset(-90)
            first._apply()
            assert calls[-1][2] == "/api/v1/devices/first/outputs"
            assert config.output2.phase_degrees == -90
            assert "976562" in first.status.get()
            first.variables["1.frequencyHz"].set("100")
            first.variables["2.enabled"].set(False)
            first._apply()
            assert config.sample_rate_hz == 6_553_650
            assert first.dac_rate.get() == "6553650"
            assert "100.001" in first.status.get()
            first._preset(-90)
            first._apply()
            second = OutputSignalDialog(app, SimpleNamespace(id="second", name="Second board"))
            second.attributes("-alpha", 0)
            app.update()
            first._stop()
            assert calls[-1][2] == "/api/v1/devices/first/outputs/stop"
            assert not config.output1.enabled and not config.output2.enabled
            first.variables["1.waveform"].set("arbitrary")
            first.custom_samples[1] = [-1, 0, 1, 0]
            assert first._config().output1.samples == [-1, 0, 1, 0]
            assert first.winfo_reqwidth() < 800
            first.destroy()
            second.destroy()
        finally:
            for callback in app.tk.call("after", "info"):
                app.after_cancel(callback)
            app.destroy()


def test_output_editor_presets_apply_stop_and_board_binding() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests.e2e.test_native_receiver_outputs import check_output_editor; "
            "check_output_editor()",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
