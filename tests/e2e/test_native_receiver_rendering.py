from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from apps.native_receiver import native_receiver as nr


@contextmanager
def receiver() -> Iterator[nr.ReceiverApp]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            nr, "load_settings", lambda: nr.AppSettings(launch_engine_on_start=False)
        )
        monkeypatch.setattr(nr.ReceiverApp, "refresh_devices", lambda self: None)
        monkeypatch.setattr(nr.ReceiverApp, "_ping_engine_status_async", lambda self: None)
        app = nr.ReceiverApp()
        app.update()
        try:
            yield app
        finally:
            for callback in app.tk.call("after", "info"):
                app.after_cancel(callback)
            app.destroy()


def noisy_frame(count: int = 65536) -> nr.SampleFrame:
    rng = np.random.default_rng(42)
    ch1 = rng.integers(-30000, 30000, count, dtype=np.int16)
    ch2 = rng.integers(-30000, 30000, count, dtype=np.int16)
    return nr.SampleFrame(3, 1, 488281.25, len(ch1), 0, 0, 0, memoryview(ch1), memoryview(ch2))


def check_live_traces(receiver: nr.ReceiverApp) -> None:
    app = receiver
    frame = noisy_frame()
    app.latest_frame = frame
    app._handle_frame(frame, 1)
    app.update_idletasks()
    ch1 = app.canvas.find_withtag("signal_ch1")
    ch2 = app.canvas.find_withtag("signal_ch2")
    spectrum = app.spectrum_canvas.find_withtag("spectrum_trace")
    assert len(ch1) == len(ch2) == len(spectrum) == 1
    for count in range(2, 30):
        app._handle_frame(frame, count)
        app._draw_spectrum(frame.channel_1, frame.channel_2, frame.sample_rate_hz, force=True)
    assert app.canvas.find_withtag("signal_ch1") == ch1
    assert app.canvas.find_withtag("signal_ch2") == ch2
    assert app.spectrum_canvas.find_withtag("spectrum_trace") == spectrum
    assert len(app.canvas.coords(ch1)) <= nr.MAX_POINTS * 2
    assert not app.signal_notice_items

    app.toggle_display_channel(2)
    assert app.canvas.itemcget(ch2, "state") == "hidden"
    assert app.canvas.itemcget(ch1, "state") == "normal"
    app.toggle_display_channel(2)
    assert app.canvas.find_withtag("signal_ch2") == ch2
    assert app.canvas.itemcget(ch2, "state") == "normal"

    app.spectrum_channel.set("CH2")
    app._draw_spectrum(frame.channel_1, None, frame.sample_rate_hz, force=True)
    assert app.spectrum_canvas.itemcget(spectrum, "state") == "hidden"
    app._redraw_latest_spectrum()
    assert app.spectrum_canvas.find_withtag("spectrum_trace") == spectrum
    assert app.spectrum_canvas.itemcget(spectrum, "state") == "normal"
    assert app.spectrum_canvas.itemcget(spectrum, "fill") == nr.COLORS["ch2"]

    before = app.canvas.coords(ch1)
    app.geometry("1400x900")
    app.update()
    assert app.canvas.find_withtag("signal_ch1") == ch1
    assert app.canvas.coords(ch1)[-2] > before[-2]
    app._clear_live_plot()
    assert not app.canvas.find_withtag("signal_trace")
    assert not app.spectrum_canvas.find_withtag("spectrum_trace")


def check_ui_callbacks(receiver: nr.ReceiverApp) -> None:
    app = receiver
    frame = noisy_frame(4096 if app.time_zoom < 1 else 65536)
    ticks: list[float] = []
    renders: list[float] = []
    stats_updates: list[str] = []
    app.frames.trace_add("write", lambda *_: stats_updates.append(app.frames.get()))
    handle_frame = app._handle_frame

    def render(frame: nr.SampleFrame, count: int) -> None:
        renders.append(time.perf_counter())
        handle_frame(frame, count)

    app._handle_frame = render  # type: ignore[method-assign]

    def publish() -> None:
        with app.frame_lock:
            app.socket_frame_count += 1
            app.latest_frame = replace(frame, sequence=app.socket_frame_count)
            app.sample_history.append(app.latest_frame)
        app.after(2, publish)

    def heartbeat() -> None:
        ticks.append(time.perf_counter())
        app.after(10, heartbeat)

    publish()
    heartbeat()
    deadline = time.perf_counter() + 1.0
    while time.perf_counter() < deadline:
        app.update()
        time.sleep(0.001)
    assert len(ticks) >= 30
    assert np.percentile(np.diff(ticks), 95) < 0.08
    assert app.frame_count > 20
    assert len(renders) >= 45
    assert np.percentile(np.diff(renders), 95) < 0.04
    assert 2 <= len(stats_updates) <= 7
    assert len(app.canvas.find_withtag("signal_trace")) == 2
    assert app.script_status.get() == "signal script disabled"
    assert app.sample_history.size <= nr.HISTORY_MAX_SAMPLES


def check_history_zoom(app: nr.ReceiverApp) -> None:
    original = noisy_frame(4096)
    batches = nr.HISTORY_MAX_SAMPLES // original.samples_per_channel
    for sequence in range(1, batches + 1):
        app.latest_frame = replace(original, sequence=sequence)
        app.sample_history.append(app.latest_frame)
    app._handle_frame(app.latest_frame, batches)
    assert app.axis_samples_per_channel == 4096
    for _ in range(4):
        app.zoom_time(0.5)
    assert app.axis_samples_per_channel == 65536
    assert float(app.timebase.get().split()[0]) == pytest.approx(
        65536 / original.sample_rate_hz * 100, rel=0.01
    )
    for _ in range(8):
        app.zoom_time(0.5)
    assert app.time_zoom == nr.TIME_ZOOM_MIN
    assert app.axis_samples_per_channel == nr.HISTORY_MAX_SAMPLES
    assert app.axis_samples_per_channel / original.sample_rate_hz * 1000 == pytest.approx(
        34359.738368
    )
    assert sum(buffer.nbytes for buffer in app.sample_history.channels.values()) == 64 * 1024**2
    assert app.time_zoom_label.get() != "0.0x"
    assert len(app.canvas.coords("signal_ch1")) <= nr.MAX_POINTS * 2
    app.fit_time()
    assert app.axis_samples_per_channel == 4096
    app._clear_live_plot()
    assert app.sample_history.size == 0


def check_partial_history_and_pause(app: nr.ReceiverApp) -> None:
    original = noisy_frame(4096)
    app.latest_frame = original
    app.socket_frame_count = 1
    app.sample_history.append(original)
    app._handle_frame(original, 1)
    app.time_span_choice.set("30 s")
    app.select_time_span()
    assert app.axis_samples_per_channel / original.sample_rate_hz == pytest.approx(30, abs=1e-5)
    assert app.signal_padding_samples > 14_000_000
    assert app.canvas.coords("signal_ch1")[0] > app.canvas.winfo_width() * 0.9
    assert "collecting" in app.display_detail.get()
    assert app.time_span_choice.get() == "30 s"
    assert app._display_interval_ms() >= 500

    app.toggle_display_pause()
    assert app.display_paused
    assert app.pause_label.get() == "Resume live"
    assert "acquisition continues" in app.display_detail.get()
    frozen = app.paused_history.plot_channels(nr.HISTORY_MAX_SAMPLES)[0].copy()
    frozen_coords = app.canvas.coords("signal_ch1")
    newer = replace(original, sequence=2, channel_1=memoryview(-np.asarray(original.channel_1)))
    app.latest_frame = newer
    app.socket_frame_count = 2
    app.sample_history.append(newer)
    app._drain_events()
    assert app.canvas.coords("signal_ch1") == frozen_coords
    assert app._view_frame() is original
    assert app.sample_history.size == original.samples_per_channel * 2
    np.testing.assert_array_equal(
        app.paused_history.plot_channels(nr.HISTORY_MAX_SAMPLES)[0], frozen
    )
    app.zoom_time(2)
    assert app._view_frame() is original
    assert app.axis_samples_per_channel / original.sample_rate_hz == pytest.approx(15, abs=1e-5)
    app.toggle_display_pause()
    assert not app.display_paused
    assert app.paused_history is None
    assert app._view_frame() is newer
    assert app.pause_label.get() == "Pause"
    app.toggle_display_pause()
    app._clear_live_plot()
    assert not app.display_paused
    assert app.paused_frame is None and app.paused_history is None


def run_scenario(scenario: str) -> None:
    with receiver() as app:
        if scenario == "traces":
            check_live_traces(app)
        elif scenario == "history":
            check_history_zoom(app)
        elif scenario == "pause":
            check_partial_history_and_pause(app)
        else:
            if scenario == "history_callbacks":
                app.time_zoom = nr.TIME_ZOOM_MIN
                app.display_mode.set("Live")
            release_timer = nr.start_display_timer()
            try:
                check_ui_callbacks(app)
            finally:
                release_timer()


@pytest.mark.parametrize(
    "scenario", ["traces", "callbacks", "history", "history_callbacks", "pause"]
)
def test_native_rendering(scenario: str) -> None:
    # Give each GUI scenario its own Tcl interpreter/process, like the real app.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests.e2e.test_native_receiver_rendering import run_scenario; "
            f"run_scenario({scenario!r})",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_range_includes_peaks_between_display_points() -> None:
    values = np.zeros(65536)
    values[1], values[-2] = -0.8, 0.9
    assert nr.sample_range(values) == (-0.8, 0.9)
