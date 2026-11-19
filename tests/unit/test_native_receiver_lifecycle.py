from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from apps.native_receiver import native_receiver


@pytest.mark.parametrize("owns_engine", [True, False])
def test_close_cleans_up_ui_and_stops_only_the_owned_engine(
    monkeypatch: pytest.MonkeyPatch, owns_engine: bool
) -> None:
    app = native_receiver.ReceiverApp.__new__(native_receiver.ReceiverApp)
    calls = []
    process = SimpleNamespace(poll=lambda: None)
    overlay = SimpleNamespace(winfo_exists=lambda: True, destroy=lambda: calls.append("overlay"))
    app.tk = SimpleNamespace(call=lambda *_args: ("timer-1", "timer-2"))
    app.stop_event = threading.Event()
    app.toast_frames = ["toast"]
    app.connection_overlay = overlay
    app.engine_process = process if owns_engine else None
    app._ui_log = lambda *_args: None
    app._stop_spinner = lambda: calls.append("spinner")
    app.after_cancel = lambda timer: calls.append(timer)
    app._dismiss_toast = lambda toast: calls.append(toast)
    app._prepare_engine_shutdown = lambda **kwargs: calls.append(
        ("prepare", kwargs["timeout_seconds"])
    )
    app.destroy = lambda: calls.append("window")
    monkeypatch.setattr(
        native_receiver, "terminate_process_tree", lambda item: calls.append(("stop", item))
    )

    app._on_close()

    assert app.closing and app.stop_event.is_set()
    assert calls[:5] == ["spinner", "timer-1", "timer-2", "toast", "overlay"]
    assert calls[-1] == "window"
    if owns_engine:
        assert calls[5:-1] == [("prepare", 3), ("stop", process)]
    else:
        assert calls[5:-1] == []
