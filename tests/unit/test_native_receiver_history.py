from __future__ import annotations

import threading
from dataclasses import replace

import numpy as np
import pytest

from apps.native_receiver import native_receiver as nr
from quantum_light_engine.post_processing import (
    ClusterProcessingConfig,
    RealtimeClusterProcessor,
)
from tests.unit.test_native_receiver_connect_flow import Value


def frame(sequence: int, start: int, count: int = 4) -> nr.SampleFrame:
    data = np.arange(start, start + count, dtype=np.int16)
    return nr.SampleFrame(3, sequence, 1000.0, count, 0, 0, 0, memoryview(data), memoryview(-data))


def test_signal_script_feature_flag_accepts_enabled_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(nr.SIGNAL_SCRIPT_FEATURE_ENV, raising=False)
    assert not nr.signal_script_feature_enabled()
    for value in ("1", "true", "yes", "on", "enabled"):
        monkeypatch.setenv(nr.SIGNAL_SCRIPT_FEATURE_ENV, value)
        assert nr.signal_script_feature_enabled()


def test_history_wraps_and_owns_snapshots() -> None:
    history = nr.SampleHistory(capacity=10)
    for sequence in range(1, 4):
        history.append(frame(sequence, (sequence - 1) * 4))
    snapshot = history.snapshot(10)
    assert snapshot is not None
    assert snapshot.samples_per_channel == 10
    assert snapshot.sequence == 3
    np.testing.assert_array_equal(snapshot.channel_1, np.arange(2, 12))
    np.testing.assert_array_equal(snapshot.channel_2, -np.arange(2, 12))
    frozen = history.frozen_copy()
    history.append(frame(4, 12))
    np.testing.assert_array_equal(snapshot.channel_1, np.arange(2, 12))
    np.testing.assert_array_equal(frozen.snapshot(10).channel_1, np.arange(2, 12))
    assert history.size == 10
    assert sum(array.nbytes for array in history.channels.values()) == 40


def test_history_includes_packets_that_the_display_skips() -> None:
    history = nr.SampleHistory(capacity=32)
    for sequence in range(1, 6):
        history.append(frame(sequence, (sequence - 1) * 4))
    snapshot = history.snapshot(16)
    assert snapshot is not None
    np.testing.assert_array_equal(snapshot.channel_1, np.arange(4, 20))


@pytest.mark.parametrize("change", ["sequence", "channels", "rate", "size"])
def test_history_resets_at_stream_discontinuities(change: str) -> None:
    history = nr.SampleHistory(capacity=32)
    history.append(frame(1, 0))
    newest = frame(2, 4)
    if change == "sequence":
        newest = replace(newest, sequence=4)
    elif change == "channels":
        newest = replace(newest, channel_mask=2, channel_1=None)
    elif change == "rate":
        newest = replace(newest, sample_rate_hz=2000)
    else:
        newest = frame(2, 4, count=8)
    history.append(newest)
    assert history.snapshot(32) is newest
    assert history.size == newest.samples_per_channel
    history.clear()
    assert history.snapshot(32) is None
    assert history.channels == {}


def test_zoom_out_uses_history_and_fit_returns_to_latest_batch() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.frame_lock = threading.Lock()
    app.sample_history = nr.SampleHistory(capacity=32)
    app.time_zoom = 1.0
    app.time_pan = 0.0
    for sequence in range(1, 5):
        app.latest_frame = frame(sequence, (sequence - 1) * 4)
        app.sample_history.append(app.latest_frame)
    app._update_time_controls = lambda: None  # type: ignore[method-assign]
    app._redraw_latest_signal = lambda: None  # type: ignore[method-assign]
    app.zoom_time(0.25)
    assert app.time_zoom == 0.25
    display = app._frame_for_signal(app.latest_frame)
    assert display.samples_per_channel == 16
    assert app._window_bounds(16) == (0, 16)
    np.testing.assert_array_equal(display.channel_1, np.arange(16))
    app.fit_time()
    assert app.time_zoom == 1.0
    assert app._frame_for_signal(app.latest_frame) is app.latest_frame


def test_trigger_crossing_supports_rising_falling_and_nearest_match() -> None:
    samples = np.array([-1.0, 1.0, 1.0, -1.0, -1.0, 1.0])

    assert nr.find_trigger_crossing(samples, 0.0, "Rising", target_index=1) == 1
    assert nr.find_trigger_crossing(samples, 0.0, "Rising", target_index=5) == 5
    assert nr.find_trigger_crossing(samples, 0.0, "Falling") == 3
    assert nr.find_trigger_crossing(samples, 2.0, "Rising") is None


def test_raw_trigger_hysteresis_ignores_near_level_recrossings() -> None:
    samples = np.array([-10, -6, -2, 1, -1, 1, -1, 2, 6], dtype=np.int16)
    history = nr.SampleHistory(capacity=len(samples))
    history.append(
        nr.SampleFrame(1, 1, 1000.0, len(samples), 0, 0, 0, memoryview(samples), None)
    )

    assert history.find_channel_crossing_before(
        1,
        len(samples),
        0,
        "Rising",
        target_index=len(samples) - 1,
    ) == 7
    assert history.find_channel_crossing_before(
        1,
        len(samples),
        0,
        "Rising",
        target_index=len(samples) - 1,
        hysteresis=5,
    ) == 3


def test_adc_counts_use_stemlab_125_14_voltage_scale() -> None:
    samples = np.array([-8192, 0, 8191], dtype=np.int16)

    volts = nr.adc_counts_to_volts(samples)

    np.testing.assert_allclose(volts, [-1.0, 0.0, 8191 / 8192])


def test_trigger_uses_raw_history_when_display_decimation_misses_crossing() -> None:
    count = 200_000
    display_indices = set(np.linspace(0, count - 1, nr.MAX_POINTS, dtype=np.intp))
    pulse_index = next(
        index
        for index in range(100_000, 101_000)
        if index not in display_indices and index + 1 not in display_indices
    )
    samples = np.full(count, -1000, dtype=np.int16)
    samples[pulse_index : pulse_index + 2] = 1000
    source = nr.SampleFrame(
        1,
        1,
        1_000_000.0,
        count,
        0,
        0,
        0,
        memoryview(samples),
        None,
    )
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.frame_lock = threading.Lock()
    app.sample_history = nr.SampleHistory(capacity=count)
    app.sample_history.append(source)
    app.time_zoom = 2.0
    app.time_pan = 0.0
    app.signal_scripts_enabled = False
    app.signal_resolution = Value("Medium")
    app.trigger_enabled = Value(True)
    app.trigger_channel = Value("CH1")
    app.trigger_edge = Value("Rising")
    app.trigger_level = Value("0")
    app.trigger_status = Value("")

    channel_1, channel_2, _rate, search_count = app._channels_for_signal(source)
    plotted_volts = nr.adc_counts_to_volts(channel_1)

    assert channel_2 is None
    assert search_count == count // 2
    assert app.trigger_window_precomputed
    assert app.precomputed_trigger_index == round(
        (search_count - 1) * nr.TRIGGER_PRE_SAMPLES_FRACTION
    )
    assert nr.find_trigger_crossing(plotted_volts, 0.0, "Rising") is not None


def test_voltage_trigger_aligns_crossing_at_one_quarter_of_visible_period() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.time_zoom = 4.0
    app.time_pan = 0.5
    app.signal_padding_samples = 0
    app.trigger_enabled = Value(True)
    app.trigger_channel = Value("CH1")
    app.trigger_edge = Value("Rising")
    app.trigger_level = Value("0.25")
    app.trigger_status = Value("")
    channel_1 = np.full(100, -0.5)
    channel_1[60:] = 0.5

    start, end, crossing = app._trigger_window_bounds(channel_1, None, 100)

    assert (start, end, crossing) == (54, 79, 60)
    assert crossing - start == pytest.approx(
        (end - start - 1) * nr.TRIGGER_PRE_SAMPLES_FRACTION,
        abs=1,
    )


def test_voltage_trigger_uses_history_to_align_a_full_batch() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.time_zoom = 1.0
    app.time_pan = 0.0
    app.signal_padding_samples = 0
    app.signal_window_samples = 100
    app.trigger_enabled = Value(True)
    app.trigger_channel = Value("CH1")
    app.trigger_edge = Value("Rising")
    app.trigger_level = Value("0")
    app.trigger_status = Value("")
    channel_1 = np.full(200, -0.5)
    channel_1[120:] = 0.5

    start, end, crossing = app._trigger_window_bounds(channel_1, None, 200)

    assert (start, end, crossing) == (95, 195, 120)
    assert crossing - start == 25


def test_trigger_searches_extra_history_without_expanding_the_display_window() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.frame_lock = threading.Lock()
    app.sample_history = nr.SampleHistory(capacity=400)
    app.time_zoom = 1.0
    app.time_pan = 0.0
    app.signal_scripts_enabled = False
    app.signal_resolution = Value("Medium")
    app.trigger_enabled = Value(True)
    first = frame(1, 0, count=100)
    second = frame(2, 100, count=100)
    app.sample_history.append(first)
    app.sample_history.append(second)
    app.latest_frame = second

    channel_1, _channel_2, _rate, search_count = app._channels_for_signal(second)

    assert search_count == 200
    assert app.signal_window_samples == 100
    np.testing.assert_array_equal(channel_1, np.arange(200))


def test_triggered_plot_stays_phase_aligned_across_packet_boundaries() -> None:
    count = 4096
    period = 997
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.frame_lock = threading.Lock()
    app.sample_history = nr.SampleHistory(capacity=count * 3)
    app.time_zoom = 1.0
    app.time_pan = 0.0
    app.signal_scripts_enabled = False
    app.signal_resolution = Value("Medium")
    app.trigger_enabled = Value(True)
    app.trigger_channel = Value("CH1")
    app.trigger_edge = Value("Rising")
    app.trigger_level = Value("0")
    app.trigger_status = Value("")
    captures: list[np.ndarray] = []

    for sequence in range(1, 6):
        indices = np.arange((sequence - 1) * count, sequence * count)
        samples = ((indices % period) - period // 2).astype(np.int16)
        source = nr.SampleFrame(
            1,
            sequence,
            1_000_000.0,
            count,
            0,
            0,
            0,
            memoryview(samples),
            None,
        )
        app.sample_history.append(source)
        app.latest_frame = source
        channel_1, _channel_2, _rate, visible = app._channels_for_signal(source)
        if app.precomputed_trigger_index is not None:
            assert visible == count
            captures.append(np.asarray(channel_1).copy())

    assert len(captures) == 5
    # The first batch now displays its available portion with missing history blank.
    np.testing.assert_allclose(captures[0], captures[1][-len(captures[0]):], atol=1e-9)
    for capture in captures[2:]:
        np.testing.assert_allclose(capture, captures[1], atol=1e-9)


def test_voltage_trigger_stays_armed_when_threshold_is_not_crossed() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.time_zoom = 2.0
    app.time_pan = 0.0
    app.signal_padding_samples = 0
    app.trigger_enabled = Value(True)
    app.trigger_channel = Value("CH2")
    app.trigger_edge = Value("Falling")
    app.trigger_level = Value("0")
    app.trigger_status = Value("")

    start, end, crossing = app._trigger_window_bounds(None, np.ones(20), 20)

    assert (start, end, crossing) == (0, 10, None)
    assert "waiting" in app.trigger_status.get().lower()


@pytest.mark.parametrize("edge,sign", [("Rising", 1), ("Falling", -1)])
def test_display_trigger_noise_guard_rejects_recrossings_and_accepts_real_edges(edge, sign):
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.frame_lock = threading.Lock()
    app.sample_history = nr.SampleHistory(capacity=1024)
    app.time_zoom = 2.0
    app.time_pan = 0.0
    app.signal_scripts_enabled = False
    app.signal_resolution = Value("Medium")
    app.trigger_enabled = Value(True)
    app.trigger_channel = Value("CH1")
    app.trigger_edge = Value(edge)
    app.trigger_level = Value("0")
    app.trigger_status = Value("")
    noise = np.tile(np.array([-20, 20], dtype=np.int16), 256) * sign
    source = nr.SampleFrame(1, 1, 1000, 512, 0, 0, 0, memoryview(noise), None)
    app.sample_history.append(source)
    app._channels_for_signal(source)
    assert app.precomputed_trigger_index is None
    clean = np.full(512, -1000 * sign, dtype=np.int16)
    clean[300:] *= -1
    source = replace(source, sequence=2, channel_1=memoryview(clean))
    app.sample_history.append(source)
    app._channels_for_signal(source)
    assert app.precomputed_trigger_index is not None


@pytest.mark.parametrize("edge,sign", [("Rising", 1), ("Falling", -1)])
def test_downsampled_trigger_preserves_selected_edge_at_its_real_position(edge, sign):
    samples = (2000 * np.sin(np.arange(8192) * 2 * np.pi / 17.3)).astype(np.int16)
    history = nr.SampleHistory(capacity=8192)
    history.append(nr.SampleFrame(1, 1, 1000, len(samples), 0, 0, 0,
                                 memoryview(samples), None))
    capture = history.triggered_plot_channels(8192, 4096, 1, 0, edge, .25, 32, 80)
    assert capture.trigger_index is not None
    positions = np.asarray(capture.sample_positions)
    values = np.asarray(capture.channel_1)
    left = np.flatnonzero(positions < capture.trigger_index)[-1]
    right = left + 1
    assert values[left] * sign < 0 <= values[right] * sign
    assert positions[right] - positions[left] == pytest.approx(1)
    assert np.all(np.diff(positions) >= 0)
    assert len(positions) == len(values) == 32


@pytest.mark.parametrize("edge,sign", [("Rising", 1), ("Falling", -1)])
def test_processed_signal_trigger_uses_the_same_noise_guard(edge, sign):
    assert nr.find_trigger_crossing(np.array([-.002, .002, -.002, .002]) * sign,
                                    0, edge, hysteresis=.01) is None
    assert nr.find_trigger_crossing(np.array([-.05, -.005, .005, .05]) * sign,
                                    0, edge, hysteresis=.01) == 2


@pytest.mark.parametrize("edge,sign", [("Rising", 1), ("Falling", -1)])
def test_noise_guard_can_arm_and_confirm_outside_the_allowed_edge_positions(edge, sign):
    samples = (np.arange(24) - 10).astype(np.int16) * sign
    history = nr.SampleHistory(capacity=24)
    history.append(nr.SampleFrame(1, 1, 1000, 24, 0, 0, 0, memoryview(samples), None))
    assert history.find_channel_crossing_before(
        1, 24, 0, edge, target_index=12, minimum_index=8, hysteresis=5,
    ) == 10
    assert nr.find_trigger_crossing(samples, 0, edge, minimum_index=8,
                                    maximum_index=12, hysteresis=5) == 10


@pytest.mark.parametrize("edge", ["Rising", "Falling"])
def test_one_batch_trigger_acquires_without_old_history_after_loss(edge):
    samples = (2000 * np.sin(np.arange(4096) * 2 * np.pi / 500 + .37)).astype(np.int16)
    history = nr.SampleHistory(capacity=8192)
    for sequence in range(1, 4):
        history.append(nr.SampleFrame(1, sequence, 1000, 4096, 0, sequence, 0,
                                      memoryview(samples), None))
        assert history.size == 4096
        capture = history.triggered_plot_channels(8192, 4096, 1, 0, edge, .25, 800, 82)
        assert capture.trigger_index == round(4095 * .25)
        assert capture.samples_per_channel == 4096
        assert capture.channel_1 is not None
        positions = np.asarray(capture.sample_positions)
        assert positions[0] > 0  # unavailable prehistory remains blank
        assert positions[-1] <= 4095


def test_large_packet_keeps_the_history_within_its_memory_limit() -> None:
    history = nr.SampleHistory(capacity=10)
    latest = frame(1, 0, count=20)
    history.append(latest)
    assert history.size == 10
    assert all(len(array) == 10 for array in history.channels.values())


def test_plot_samples_are_bounded_and_preserve_history_endpoints() -> None:
    history = nr.SampleHistory(capacity=8192)
    history.append(frame(1, 0, count=4096))
    history.append(frame(2, 4096, count=4096))
    first, second, rate, count = history.plot_channels(8192)
    assert count == 8192
    assert rate == 1000.0
    assert len(first) == len(second) == nr.MAX_POINTS
    assert first[0] == 0 and first[-1] == 8191
    assert second[0] == 0 and second[-1] == -8191
    history.append(frame(3, 8192, count=4096))
    assert first[0] == 0 and first[-1] == 8191


def test_plot_samples_follow_selected_resolution() -> None:
    history = nr.SampleHistory(capacity=8192)
    history.append(frame(1, 0, count=4096))
    first, second, _rate, _count = history.plot_channels(4096, 1600)
    assert len(first) == len(second) == 1600


def test_default_time_span_starts_with_one_complete_batch() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.time_span_choice = Value(nr.DEFAULT_TIME_SPAN)
    app.time_zoom = 1.0
    app.time_pan = 0.0
    app.frame_lock = threading.Lock()
    app.latest_frame = frame(1, 0, count=1000)
    app._update_time_controls = lambda: None  # type: ignore[method-assign]
    app._redraw_latest_signal = lambda: None  # type: ignore[method-assign]
    assert not app._apply_time_span_to_frame(nr.DEFAULT_TIME_SPAN, app.latest_frame)
    assert app.time_zoom == 1.0
    assert app.time_span_choice.get() == nr.DEFAULT_TIME_SPAN


def test_signal_resolution_controls_plot_points() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.signal_resolution = Value("High")
    assert app._signal_plot_points() == 1600
    app.signal_resolution = Value("unknown")
    assert app._signal_plot_points() == nr.MAX_POINTS


def test_signal_script_receives_full_history_samples() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.frame_lock = threading.Lock()
    app.sample_history = nr.SampleHistory(capacity=8192)
    app.sample_history.append(frame(1, 0, count=4096))
    app.latest_frame = frame(2, 4096, count=4096)
    app.sample_history.append(app.latest_frame)
    app.time_zoom = 0.5
    app.selected_device = None
    app.signal_scripts_enabled = True
    app.signal_processor = lambda signals, _np: signals["selected.ch1"] * 2
    first, second, rate, count = app._channels_for_signal(app.latest_frame)
    assert count == 8192
    assert rate == 1000.0
    assert second is None
    np.testing.assert_array_equal(first, np.arange(8192) * 2)


def test_disabled_signal_script_returns_raw_channels() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.signal_scripts_enabled = False
    app.signal_processor = lambda _signals, _np: pytest.fail("script should not run")
    source = frame(1, 0)

    first, second = app._processed_channels(source)

    assert first is source.channel_1
    assert second is source.channel_2
    assert app.signal_processor is None


def test_set_signal_script_requires_feature_flag() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.signal_scripts_enabled = False

    with pytest.raises(ValueError, match=nr.SIGNAL_SCRIPT_FEATURE_ENV):
        app.set_signal_script("def process(signals, np):\n    return signals.get('selected.ch1')\n")


def test_live_frames_feed_the_stateful_cluster_processor() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.cluster_processor = RealtimeClusterProcessor(
        ClusterProcessingConfig(
            explicit_mode_template=(1.0, -1.0, 1.0, -1.0),
            min_samples_per_mode=4,
        )
    )
    app.cluster_lock = threading.Lock()
    app.latest_cluster_result = None
    app.cluster_error = None
    app.cluster_last_sequence = None

    app._process_cluster_frame(frame(1, 0, count=16))

    result, error = app.cluster_snapshot()
    assert error is None
    assert result is not None
    assert len(result.quadratures["a"]) == 4
    assert set(result.nullifiers) == {"cluster_x"}


def test_cluster_processor_reports_an_unsafe_sample_rate_without_stopping_stream() -> None:
    app = nr.ReceiverApp.__new__(nr.ReceiverApp)
    app.cluster_processor = RealtimeClusterProcessor()
    app.cluster_lock = threading.Lock()
    app.latest_cluster_result = None
    app.cluster_error = None
    app.cluster_last_sequence = None

    app._process_cluster_frame(frame(1, 0, count=16))

    result, error = app.cluster_snapshot()
    assert result is None
    assert error is not None and "at least 16" in error
