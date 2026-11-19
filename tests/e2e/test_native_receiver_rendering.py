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
from apps.native_receiver.constants import COLORS, HISTORY_MAX_SAMPLES, MAX_POINTS, TIME_ZOOM_MIN
from apps.native_receiver.helpers import sample_range, start_display_timer
from apps.native_receiver.protocol import SampleFrame
from apps.native_receiver.settings import AppSettings


@contextmanager
def receiver(*, show_live_signal: bool = True) -> Iterator[nr.ReceiverApp]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            nr, "load_settings", lambda: AppSettings(launch_engine_on_start=False)
        )
        monkeypatch.setattr(nr.ReceiverApp, "refresh_devices", lambda self: None)
        monkeypatch.setattr(nr.ReceiverApp, "_ping_engine_status_async", lambda self: None)
        app = nr.ReceiverApp()
        if show_live_signal:
            app.show_live_signal.set(True)
            app._update_live_signal_visibility()
        app.update()
        try:
            yield app
        finally:
            for callback in app.tk.call("after", "info"):
                app.after_cancel(callback)
            app.destroy()


def noisy_frame(count: int = 65536) -> SampleFrame:
    rng = np.random.default_rng(42)
    ch1 = rng.integers(-30000, 30000, count, dtype=np.int16)
    ch2 = rng.integers(-30000, 30000, count, dtype=np.int16)
    return SampleFrame(3, 1, 488281.25, len(ch1), 0, 0, 0, memoryview(ch1), memoryview(ch2))


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
    assert len(app.canvas.coords(ch1)) <= MAX_POINTS * 2
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
    assert app.spectrum_canvas.itemcget(spectrum, "fill") == COLORS["ch2"]

    before = app.canvas.coords(ch1)
    app.geometry("1400x900")
    app.update()
    assert app.canvas.find_withtag("signal_ch1") == ch1
    assert app.canvas.coords(ch1)[-2] > before[-2]
    app._clear_live_plot()
    assert not app.canvas.find_withtag("signal_trace")
    assert not app.spectrum_canvas.find_withtag("spectrum_trace")


def check_trigger_overlay_reuse(app: nr.ReceiverApp) -> None:
    count = 4096
    phase = np.linspace(0, 16 * np.pi, count, endpoint=False)
    ch1 = (np.sin(phase) * 2000).astype(np.int16)
    ch2 = np.zeros(count, dtype=np.int16)
    original = SampleFrame(
        3,
        1,
        488281.25,
        count,
        0,
        0,
        0,
        memoryview(ch1),
        memoryview(ch2),
    )
    app.trigger_enabled.set(True)
    app.trigger_channel.set("CH1")
    app.trigger_edge.set("Rising")
    app.trigger_level.set("0")
    app.apply_trigger_settings()

    original_vertical: tuple[int, ...] | None = None
    original_level: tuple[int, ...] | None = None
    for sequence in range(1, 10):
        frame = replace(original, sequence=sequence)
        app.latest_frame = frame
        app.sample_history.append(frame)
        app._handle_frame(frame, sequence)
        vertical = app.canvas.find_withtag("trigger_vertical")
        level = app.canvas.find_withtag("trigger_level")
        if not vertical:
            assert not level
            continue
        assert len(vertical) == len(level) == 1
        if original_vertical is None:
            original_vertical, original_level = vertical, level
        else:
            assert vertical == original_vertical
            assert level == original_level
        assert app.canvas.itemcget(vertical, "state") == "normal"
        assert app.canvas.itemcget(level, "state") == "normal"

    assert original_vertical is not None and original_level is not None
    app.trigger_enabled.set(False)
    app.apply_trigger_settings()
    assert app.canvas.itemcget(original_vertical, "state") == "hidden"
    assert app.canvas.itemcget(original_level, "state") == "hidden"
    app._clear_live_plot()
    assert not app.canvas.find_withtag("signal_overlay")


def check_trigger_directions_and_noise_hold(app: nr.ReceiverApp) -> None:
    count = 4096
    app.trigger_enabled.set(True)
    app.trigger_level.set("0")
    app.time_zoom = 4
    for sequence in range(1, 5):
        t = np.arange((sequence - 1) * count, sequence * count)
        ch1 = (2000 * np.sin(t * 2 * np.pi / 97.3)).astype(np.int16)
        ch2 = (2000 * np.cos(t * 2 * np.pi / 97.3)).astype(np.int16)
        frame = SampleFrame(3, sequence, 1000, count, 0, 0, 0,
                               memoryview(ch1), memoryview(ch2))
        app.latest_frame = frame
        app.sample_history.append(frame)
    for channel in (1, 2):
        app.trigger_channel.set(f"CH{channel}")
        for edge, sign in (("Rising", 1), ("Falling", -1)):
            app.trigger_edge.set(edge)
            app.apply_trigger_settings()
            points = np.asarray(app.canvas.coords(f"signal_ch{channel}")).reshape(-1, 2)
            trigger_x = app.canvas.coords("trigger_vertical")[0]
            left = np.flatnonzero(points[:, 0] < trigger_x - 1e-9)[-1]
            right = left + 1
            assert (points[left, 1] - points[right, 1]) * sign > 0
    before = app.canvas.coords("signal_ch2")
    trace_id = app.canvas.find_withtag("signal_ch2")
    noise = np.tile(np.array([-20, 20], dtype=np.int16), count // 2)
    frame = replace(frame, sequence=5, flags=1,
                    channel_1=memoryview(noise), channel_2=memoryview(noise))
    app.latest_frame = frame
    app.sample_history.append(frame)
    app._handle_frame(frame, 5)
    assert app.canvas.coords("signal_ch2") == before
    assert app.canvas.find_withtag("signal_ch2") == trace_id
    assert app.canvas.itemcget(trace_id, "state") == "normal"
    app.time_zoom = 1
    for sequence, edge in enumerate(("Rising", "Falling"), start=6):
        app.trigger_channel.set("CH1")
        app.trigger_edge.set(edge)
        frame = replace(frame, sequence=sequence, fpga_lost_ch1=sequence,
                        channel_1=memoryview(ch1), channel_2=memoryview(ch2))
        app.latest_frame = frame
        app.sample_history.append(frame)
        app._handle_frame(frame, sequence)
        assert app.trigger_capture_valid
        assert app.axis_samples_per_channel == count
        assert app.sample_history.size == count
        assert np.isfinite(app.canvas.coords("signal_ch1")).all()
        assert app.signal_sample_positions[0] > 0


def check_ui_callbacks(receiver: nr.ReceiverApp) -> None:
    app = receiver
    frame = noisy_frame(4096 if app.time_zoom < 1 else 65536)
    ticks: list[float] = []
    renders: list[float] = []
    stats_updates: list[str] = []
    app.frames.trace_add("write", lambda *_: stats_updates.append(app.frames.get()))
    handle_frame = app._handle_frame

    def render(frame: SampleFrame, count: int) -> None:
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
    assert app.sample_history.size <= HISTORY_MAX_SAMPLES


def check_history_zoom(app: nr.ReceiverApp) -> None:
    original = noisy_frame(4096)
    batches = HISTORY_MAX_SAMPLES // original.samples_per_channel
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
    assert app.time_zoom == TIME_ZOOM_MIN
    assert app.axis_samples_per_channel == HISTORY_MAX_SAMPLES
    assert app.axis_samples_per_channel / original.sample_rate_hz * 1000 == pytest.approx(
        34359.738368
    )
    assert sum(buffer.nbytes for buffer in app.sample_history.channels.values()) == 64 * 1024**2
    assert app.time_zoom_label.get() != "0.0x"
    assert len(app.canvas.coords("signal_ch1")) <= MAX_POINTS * 2
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
    frozen = app.paused_history.plot_channels(HISTORY_MAX_SAMPLES)[0].copy()
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
        app.paused_history.plot_channels(HISTORY_MAX_SAMPLES)[0], frozen
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
        elif scenario == "trigger":
            check_trigger_overlay_reuse(app)
        elif scenario == "trigger_edges":
            check_trigger_directions_and_noise_hold(app)
        elif scenario == "history":
            check_history_zoom(app)
        elif scenario == "pause":
            check_partial_history_and_pause(app)
        else:
            if scenario == "history_callbacks":
                app.time_zoom = TIME_ZOOM_MIN
                app.display_mode.set("Live")
            release_timer = start_display_timer()
            try:
                check_ui_callbacks(app)
            finally:
                release_timer()


@pytest.mark.parametrize(
    "scenario", ["traces", "trigger", "trigger_edges", "callbacks", "history",
                 "history_callbacks", "pause"]
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
    assert sample_range(values) == (-0.8, 0.9)
