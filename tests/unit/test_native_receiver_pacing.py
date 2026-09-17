from __future__ import annotations

import asyncio
import queue
import threading
from types import SimpleNamespace

import pytest

from examples import native_receiver as nr
from tests.unit.test_native_receiver_connect_flow import Value


@pytest.mark.parametrize(
    ("work_ms", "delay_ms"), [(0, 16), (2, 14), (10, 6), (16, 1), (50, 1)]
)
def test_refresh_budget_accounts_for_drawing_time(work_ms: int, delay_ms: int) -> None:
    assert nr.next_frame_delay_ms(1_000_000_000, 1_000_000_000 + work_ms * 1_000_000) == delay_ms


def test_idle_receiver_waits_without_redrawing_or_busy_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.events = queue.Queue()
    app.frame_lock = threading.Lock()
    app.latest_frame = None
    app.socket_frame_count = 0
    app.rendered_frame_count = 0
    app.last_render_monotonic_ns = 0
    clock = iter([1_000_000_000, 1_002_000_000, 1_016_000_000, 1_018_000_000])
    monkeypatch.setattr(nr, "time_monotonic_ns", lambda: next(clock))
    delays = []
    app.after = lambda delay, _callback: delays.append(delay)  # type: ignore[method-assign]
    app._drain_events()
    app._drain_events()
    assert delays == [14, 14]


def test_smooth_preset_preserves_sample_rate() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.acquisition_decimation = Value("256")
    app.acquisition_block_size = Value("131072")
    app.acquisition_rate = Value()
    app.acquisition_batch = Value()
    applied = []
    app._ui_log = lambda *_args: None  # type: ignore[method-assign]
    app.apply_acquisition_settings = lambda: applied.append(True)  # type: ignore[method-assign]
    app.apply_fast_acquisition()
    assert app.acquisition_decimation.get() == "256"
    assert app.acquisition_block_size.get() == "8192"
    assert applied == [True]


def test_stable_two_channel_preset_sets_balanced_speed() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.acquisition_decimation = Value("256")
    app.acquisition_block_size = Value("131072")
    app.acquisition_rate = Value()
    app.acquisition_batch = Value()
    applied = []
    app._ui_log = lambda *_args: None  # type: ignore[method-assign]
    app.apply_acquisition_settings = lambda: applied.append(True)  # type: ignore[method-assign]
    app.apply_stable_two_channel()
    assert app.acquisition_decimation.get() == "1024"
    assert app.acquisition_block_size.get() == "32768"
    assert app.acquisition_rate.get() == "Sample rate: 122.07 kHz"
    assert app.acquisition_batch.get() == "Batch: 67 ms · 2 ch"
    assert applied == [True]


def test_acquisition_batch_estimate_uses_enabled_channels() -> None:
    device = nr.Device("id", "RP", "rp.local", "ready", True, False, 1024, 32768)
    assert nr.acquisition_channel_count(device) == 1
    assert nr.acquisition_batch_seconds(32768, 2, 122_070.3125) == pytest.approx(0.067108864)
    assert nr.format_duration(0.067108864) == "67 ms"


@pytest.mark.parametrize("decimation", [1, 64, 256, 1024, 8192, 65536])
def test_smooth_preset_bounds_packet_overhead(decimation: int) -> None:
    block = nr.smooth_acquisition_block_size(decimation)
    assert 2048 <= block <= 2_097_152
    assert block & (block - 1) == 0
    assert (125_000_000 / decimation) * 2 / block < 120


@pytest.mark.parametrize("result", [0, 1])
def test_windows_display_timer_is_released_only_if_acquired(
    monkeypatch: pytest.MonkeyPatch, result: int
) -> None:
    calls = []
    winmm = SimpleNamespace(
        timeBeginPeriod=lambda period: (calls.append(("begin", period)), result)[1],
        timeEndPeriod=lambda period: calls.append(("end", period)),
    )
    monkeypatch.setattr(nr.sys, "platform", "win32")
    monkeypatch.setattr(nr.ctypes, "WinDLL", lambda _name: winmm, raising=False)
    release = nr.start_display_timer()
    assert calls == [("begin", 1)]
    release()
    assert calls == ([("begin", 1), ("end", 1)] if result == 0 else [("begin", 1)])


def test_socket_failure_releases_display_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.events = queue.Queue()
    app.stop_event = threading.Event()
    released = []
    monkeypatch.setattr(nr, "start_display_timer", lambda: lambda: released.append(True))

    def fail_connect(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("test")

    monkeypatch.setattr(nr.websockets, "connect", fail_connect)
    asyncio.run(app._socket_loop("ws://127.0.0.1:9/test"))
    assert released == [True]


def test_auto_display_slows_as_the_view_widens() -> None:
    intervals = [nr.display_interval_ms("Auto", zoom) for zoom in (1, 1/4, 1/64, 1/4096)]
    assert intervals == [16, 32, 128, 1000]
    assert nr.display_interval_ms("Auto", 1/4096, visible_seconds=7.999) == 1000
    assert nr.display_interval_ms("Auto", 1/4096, visible_seconds=8.0) == 500
    assert nr.display_interval_ms("Live", 1/4096) == 16
    assert nr.display_interval_ms("Slow", 1) == 500


def test_slow_display_keeps_latest_frame_without_a_backlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.events = queue.Queue()
    app.frame_lock = threading.Lock()
    app.latest_frame = object()
    app.rendered_frame_count = 0
    app.display_mode = Value("Slow")
    app.time_zoom = 1.0
    app.display_paused = False
    app.after = lambda *_args: None  # type: ignore[method-assign]
    received = []
    app._handle_frame = lambda _frame, count: received.append(count)  # type: ignore[method-assign]
    for count, elapsed_ms in enumerate((0, 16, 32, 499, 500, 516, 1000), start=1):
        monkeypatch.setattr(
            nr, "time_monotonic_ns", lambda elapsed=elapsed_ms: 1_000_000_000 + elapsed * 1_000_000
        )
        app.socket_frame_count = count
        app._drain_events()
    assert received == [1, 5, 7]
