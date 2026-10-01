from __future__ import annotations

import asyncio
import queue
import threading
from pathlib import Path
from typing import Any

import pytest

from apps.native_receiver import native_receiver as nr


class Value:
    def __init__(self, value: Any = "") -> None:
        self.value = value

    def get(self) -> Any:
        return self.value

    def set(self, value: Any) -> None:
        self.value = value


class FakeStatusDot:
    def __init__(self) -> None:
        self.configured: list[tuple[int, dict[str, str]]] = []

    def itemconfigure(self, item: int, **kwargs: str) -> None:
        self.configured.append((item, kwargs))


def app_for_connect(state: str) -> nr.ReceiverApp:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.selected_device = nr.Device(
        id="11111111-1111-1111-1111-111111111111",
        name="RP test",
        host="rp-test.local",
        state=state,
        channel_1_enabled=True,
        channel_2_enabled=True,
        decimation=64,
        block_size_bytes=16_384,
    )
    app.socket_thread = None
    app.status = Value("idle")
    app.api_base = Value("http://127.0.0.1:8080")
    return app


def test_streaming_board_connect_attaches_socket_without_backend_lifecycle() -> None:
    app = app_for_connect("streaming")
    calls: list[str] = []

    app._open_device_tab = lambda device_id: calls.append(f"tab:{device_id}")  # type: ignore[method-assign]
    app._start_socket = lambda device_id: calls.append(f"socket:{device_id}")  # type: ignore[method-assign]
    app._run_busy = lambda *_args, **_kwargs: calls.append("busy")  # type: ignore[method-assign]

    app.connect_selected()

    assert calls == [
        "tab:11111111-1111-1111-1111-111111111111",
        "socket:11111111-1111-1111-1111-111111111111",
    ]
    assert app.status.get() == "attaching sample socket"


def test_stopped_board_connect_attaches_socket_before_starting_hardware() -> None:
    app = app_for_connect("disconnected")
    calls: list[str] = []
    api_calls: list[tuple[str, str, float]] = []

    def api(
        _api_base: str,
        method: str,
        path: str,
        _body: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 10,
    ) -> dict[str, str] | None:
        api_calls.append((method, path, timeout_seconds))
        if path.endswith("/status"):
            return {"state": "disconnected"}
        return None

    def run_busy(
        label: str,
        work: Any,
        callback: Any | None = None,
    ) -> None:
        calls.append(f"busy:{label}")
        result = work()
        if callback is not None:
            callback(result)

    app._open_device_tab = lambda device_id: calls.append(f"tab:{device_id}")  # type: ignore[method-assign]
    app._start_socket = lambda device_id: calls.append(f"socket:{device_id}")  # type: ignore[method-assign]
    app._run_busy = run_busy  # type: ignore[method-assign]
    app._api_for_base = api  # type: ignore[method-assign]
    app._load_devices = lambda _api_base: []  # type: ignore[method-assign]
    app._apply_loaded_devices = lambda _devices: calls.append("devices")  # type: ignore[method-assign]
    app.events = Value()  # type: ignore[assignment]
    app.events.put = lambda item: calls.append(f"event:{item[1]}")  # type: ignore[attr-defined]

    app.connect_selected()

    assert calls[:3] == [
        "tab:11111111-1111-1111-1111-111111111111",
        "socket:11111111-1111-1111-1111-111111111111",
        "busy:Starting Red Pitaya acquisition",
    ]
    assert api_calls == [
        (
            "GET",
            "/api/v1/devices/11111111-1111-1111-1111-111111111111/status",
            nr.ENGINE_CONNECT_TIMEOUT_SECONDS,
        ),
        (
            "POST",
            "/api/v1/devices/11111111-1111-1111-1111-111111111111/connect",
            nr.REDPITAYA_CONNECT_TIMEOUT_SECONDS,
        ),
        (
            "POST",
            "/api/v1/devices/11111111-1111-1111-1111-111111111111/start",
            nr.REDPITAYA_START_TIMEOUT_SECONDS,
        ),
    ]


def test_engine_indicator_distinguishes_ok_starting_and_failure() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.engine_status = Value()
    app.engine_status_dot = FakeStatusDot()  # type: ignore[assignment]
    app.engine_status_dot_item = 12
    app._ui_log = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    app._set_engine_status("ok")
    assert app.engine_status.get() == "OK"
    assert app.engine_status_dot.configured[-1][1]["fill"] == nr.COLORS["ch1"]

    app._set_engine_status("starting")
    assert app.engine_status.get() == "Starting"
    assert app.engine_status_dot.configured[-1][1]["fill"] == nr.COLORS["warning"]

    app._set_engine_status("error")
    assert app.engine_status.get() == "Error"
    assert app.engine_status_dot.configured[-1][1]["fill"] == nr.COLORS["danger"]


@pytest.mark.parametrize(
    ("state", "label", "tag"),
    [
        ("streaming", "● Streaming", "streaming"),
        ("ready", "● Ready", "streaming"),
        ("reconnecting", "◐ Reconnecting", "warning"),
        ("disconnected", "○ Offline", "offline"),
        ("failed", "! Failed", "warning"),
    ],
)
def test_device_state_labels_pair_text_with_semantic_state(
    state: str, label: str, tag: str
) -> None:
    assert nr.ReceiverApp._device_state_label(state) == (label, tag)


def test_channel_vertical_offsets_are_independent() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.channel_1_offset_divisions = Value(1.0)  # type: ignore[assignment]
    app.channel_2_offset_divisions = Value(-1.0)  # type: ignore[assignment]

    assert app._channel_y_offset(1, 800) == 300
    assert app._channel_y_offset(2, 800) == 500


def test_connection_failure_event_shows_toast() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.events = queue.Queue()
    app.events.put(
        (
            "connection_error",
            {
                "title": "Connection failed",
                "message": "Could not open the Red Pitaya sample stream.",
            },
        )
    )
    app.status = Value("connecting")
    app.frame_lock = threading.Lock()
    app.latest_frame = None
    app.socket_frame_count = 0
    app.rendered_frame_count = 0
    app.last_render_monotonic_ns = 0
    calls: list[tuple[Any, ...]] = []

    app._set_engine_status = lambda status: calls.append(("engine", status))  # type: ignore[method-assign]
    app._stop_spinner = lambda **kwargs: calls.append(  # type: ignore[method-assign]
        ("stop-spinner", kwargs.get("restore_notices"))
    )
    app._show_toast = lambda title, message, **kwargs: calls.append(  # type: ignore[method-assign]
        ("toast", title, message, kwargs.get("level"))
    )
    app._ui_log = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    app.after = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    app._drain_events()

    assert app.status.get() == "RP not connected to engine"
    assert ("stop-spinner", False) in calls
    assert app.connection_failure_active
    assert (
        "toast",
        "Connection failed",
        "Could not open the Red Pitaya sample stream.",
        "error",
    ) in calls


def test_connection_failure_message_keeps_error_short() -> None:
    message = nr.ReceiverApp._connection_failure_message(ConnectionRefusedError("refused"))

    assert message.startswith("Could not open the Red Pitaya sample stream.")
    assert "refused" in message


def test_drain_events_catches_ui_event_handler_error() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.events = queue.Queue()
    app.events.put(("connection_error", {}))
    app.status = Value("connecting")
    app.frame_lock = threading.Lock()
    app.latest_frame = None
    app.socket_frame_count = 0
    app.rendered_frame_count = 0
    app.last_render_monotonic_ns = 0
    app._handle_ui_event = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        TypeError("toast log broke")
    )
    app.after = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    app._drain_events()

    assert app.status.get() == "ui event error"


def test_socket_loop_catches_connect_error_without_traceback_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.events = queue.Queue()
    app.stop_event = threading.Event()

    def fail_connect(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionRefusedError("refused")

    warning_calls: list[str] = []
    monkeypatch.setattr(nr.websockets, "connect", fail_connect)
    monkeypatch.setattr(
        nr.UI_LOGGER,
        "warning",
        lambda message, *args, **_kwargs: warning_calls.append(message % args),
    )
    monkeypatch.setattr(
        nr.UI_LOGGER,
        "exception",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("traceback logging should not be used for socket connection failures")
        ),
    )

    asyncio.run(app._socket_loop("ws://127.0.0.1:9/ws"))

    events = [app.events.get_nowait() for _ in range(app.events.qsize())]
    assert any(event == "connection_error" for event, _payload in events)
    assert any(event == "socket_closed" for event, _payload in events)
    assert warning_calls == ["sample socket failed error=refused"]


def test_redpitaya_connect_background_failure_shows_toast() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.events = queue.Queue()
    app.events.put(
        (
            "busy_error",
            (
                "Starting Red Pitaya acquisition",
                nr.ApiError("Timed out after 8s"),
                8000,
            ),
        )
    )
    app.status = Value("starting")
    app.loading_label = "Starting Red Pitaya acquisition"
    app.frame_lock = threading.Lock()
    app.latest_frame = None
    app.socket_frame_count = 0
    app.rendered_frame_count = 0
    app.last_render_monotonic_ns = 0
    calls: list[tuple[Any, ...]] = []

    app._stop_spinner = lambda **kwargs: calls.append(  # type: ignore[method-assign]
        ("stop-spinner", kwargs.get("restore_notices"))
    )
    app._show_toast = lambda title, message, **kwargs: calls.append(  # type: ignore[method-assign]
        ("toast", title, message, kwargs.get("level"))
    )
    app._show_error = lambda exc: calls.append(("modal", str(exc)))  # type: ignore[method-assign]
    app._ui_log = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    app.after = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    app._drain_events()

    assert app.status.get() == "RP not connected to engine"
    assert ("stop-spinner", False) in calls
    assert app.connection_failure_active
    assert not any(call[0] == "modal" for call in calls)
    assert any(
        call[0] == "toast"
        and call[1] == "Connection failed"
        and "Timed out after 8s" in call[2]
        and call[3] == "error"
        for call in calls
    )


def test_engine_ping_event_updates_status_indicator() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.engine_ping_in_flight = True
    app.engine_process = None
    calls: list[str] = []

    app._set_engine_status = lambda status: calls.append(status)  # type: ignore[method-assign]
    app._ui_log = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    app._handle_engine_ping(True, 12)
    assert app.engine_ping_in_flight is False
    assert calls[-1] == "ok"

    app.engine_ping_in_flight = True
    app._handle_engine_ping(False, 31)
    assert app.engine_ping_in_flight is False
    assert calls[-1] == "offline"


def test_engine_ping_marks_exited_owned_process() -> None:
    class FinishedProcess:
        def poll(self) -> int:
            return 1

    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.engine_ping_in_flight = True
    app.engine_process = FinishedProcess()  # type: ignore[assignment]
    app.status = Value("engine running")
    calls: list[str] = []

    app._set_engine_status = lambda status: calls.append(status)  # type: ignore[method-assign]
    app._ui_log = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    app._handle_engine_ping(False, 7)

    assert app.engine_process is None
    assert app.status.get() == "engine exited"
    assert calls[-1] == "exited"


def test_stop_engine_terminates_owned_process_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    class RunningProcess:
        def poll(self) -> None:
            return None

    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    process = RunningProcess()
    app.engine_process = process  # type: ignore[assignment]
    app.status = Value("engine running")
    calls: list[str] = []
    terminated: list[object] = []
    prepared: list[bool] = []

    app._ui_log = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    app._set_engine_status = lambda status: calls.append(status)  # type: ignore[method-assign]
    app._prepare_engine_shutdown = lambda: prepared.append(True)  # type: ignore[method-assign]

    def run_busy(label: str, work: Any, callback: Any) -> None:
        calls.append(label)
        callback(work())

    app._run_busy = run_busy  # type: ignore[method-assign]
    monkeypatch.setattr(nr, "terminate_process_tree", lambda item: terminated.append(item))

    app.stop_engine()

    assert prepared == [True]
    assert terminated == [process]
    assert app.engine_process is None
    assert app.status.get() == "engine stopped"
    assert "Stopping acquisition engine" in calls
    assert "stopped" in calls


def test_engine_command_prefers_named_project_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / ".venv" / "Scripts" / "quantum-light-engine.exe"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"")
    monkeypatch.setattr(nr, "WORKSPACE_ROOT", tmp_path)

    app = nr.ReceiverApp.__new__(nr.ReceiverApp)

    assert app._engine_command() == [
        str(launcher),
        "run",
        "--host",
        "127.0.0.1",
        "--port",
        "8080",
    ]


def test_out1_test_button_toggles_the_selected_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeButton:
        def __init__(self) -> None:
            self.style = ""

        def configure(self, *, style: str) -> None:
            self.style = style

    app = app_for_connect("streaming")
    app.test_output_enabled = False
    app.test_output_device_id = None
    app.test_output_label = Value("OUT1 sine")
    app.test_output_button = FakeButton()  # type: ignore[assignment]
    app._ui_log = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    app.api_base = Value("http://engine.local:8080")
    calls: list[tuple[str, str, dict[str, bool]]] = []

    def api(
        _api_base: str,
        method: str,
        path: str,
        body: dict[str, bool],
    ) -> dict[str, object]:
        calls.append((method, path, body))
        return {
            "enabled": body["enabled"],
            "frequencyHz": 1_000,
            "amplitudeVolts": 0.2,
        }

    app._api_for_base = api  # type: ignore[method-assign]

    def run_busy(_label: str, work: Any, callback: Any) -> None:
        callback(work())

    app._run_busy = run_busy  # type: ignore[method-assign]

    app.toggle_test_output()
    first_device_id = "11111111-1111-1111-1111-111111111111"
    assert calls[-1] == (
        "POST",
        f"/api/v1/devices/{first_device_id}/outputs/1/test-sine",
        {"enabled": True},
    )
    assert app.test_output_enabled
    assert app.test_output_device_id == first_device_id
    assert app.test_output_label.get() == "Stop OUT1"
    assert app.test_output_button.style == "Active.TButton"

    app.selected_device = nr.Device(
        id="22222222-2222-2222-2222-222222222222",
        name="Other RP",
        host="other-rp.local",
        state="streaming",
        channel_1_enabled=True,
        channel_2_enabled=True,
        decimation=64,
        block_size_bytes=16_384,
    )
    app.toggle_test_output()
    assert calls[-1] == (
        "POST",
        f"/api/v1/devices/{first_device_id}/outputs/1/test-sine",
        {"enabled": False},
    )
    assert not app.test_output_enabled
    assert app.test_output_device_id is None
    assert app.test_output_label.get() == "OUT1 sine"
    assert app.test_output_button.style == "Compact.TButton"
