from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from apps.native_receiver import receiver_tools
from apps.native_receiver.receiver_tools import ReceiverToolsMixin
from quantum_light_engine.post_processing import (
    RealtimeClusterProcessor,
    single_mode_processing_config,
)


class Value:
    def __init__(self, value: object = None) -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class CalibrationReceiver(ReceiverToolsMixin):
    def __init__(self) -> None:
        config = replace(single_mode_processing_config(4, 2), window_mode="continuous",
                         collection_guard_seconds=0, collection_end_guard_samples=0,
                         channel_b_delay_samples=0)
        self.cluster_processor = RealtimeClusterProcessor(config)
        self.cluster_lock = threading.Lock()
        self.cluster_error = None
        self.calibration_progress = Value(0.0)
        self.calibration_text = Value()
        self._calibration_started = None
        self._calibration_after_id = None
        self._calibration_target = None
        self.jobs: dict[str, object] = {}
        self.job_count = 0
        self._clear_cluster_chart_views = Mock()
        self._show_toast = Mock()

    def after(self, _milliseconds: int, callback: object) -> str:
        self.job_count += 1
        token = str(self.job_count)
        self.jobs[token] = callback
        return token

    def after_cancel(self, token: str) -> None:
        self.jobs.pop(token)

    def tick(self) -> None:
        token = next(iter(self.jobs))
        callback = self.jobs.pop(token)
        callback()

    def collect_modes(self) -> None:
        values = np.arange(800, dtype=float)
        self.cluster_processor.feed(values, values, 1000)


@pytest.fixture
def calibration(monkeypatch: pytest.MonkeyPatch) -> tuple[CalibrationReceiver, list[float]]:
    clock = [100.0]
    monkeypatch.setattr(receiver_tools.time, "monotonic", lambda: clock[0])
    return CalibrationReceiver(), clock


def test_reference_finishes_once_at_ten_seconds_with_real_collected_modes(calibration) -> None:
    app, clock = calibration
    app.start_cluster_calibration()
    app.collect_modes()
    assert app.calibration_text.get() == "Recording · 10.0 s left"
    clock[0] = 109.9
    app.tick()
    assert app.cluster_processor.calibrating
    assert app.calibration_progress.get() == pytest.approx(9.9)
    assert app.calibration_text.get() == "Recording · 0.1 s left"
    clock[0] = 110
    app.tick()
    assert app.cluster_processor.calibrated
    assert not app.cluster_processor.calibrating
    assert app.calibration_progress.get() == 10
    assert app.calibration_text.get() == "Reference ready"
    assert not app.jobs
    assert app._calibration_started is None


@pytest.mark.parametrize("action", ["finish", "clear"])
def test_manual_finish_or_clear_cancels_the_pending_automatic_finish(calibration, action) -> None:
    app, clock = calibration
    app.start_cluster_calibration()
    app.collect_modes()
    clock[0] = 103
    if action == "finish":
        app.finish_cluster_calibration()
        assert app.cluster_processor.calibrated
    else:
        app.clear_cluster_calibration()
        assert not app.cluster_processor.calibrated
    assert not app.cluster_processor.calibrating
    assert not app.jobs
    clock[0] = 120
    app._update_calibration_progress()
    assert not app.jobs


def test_restarting_reference_resets_the_full_ten_second_countdown(calibration) -> None:
    app, clock = calibration
    app.start_cluster_calibration()
    clock[0] = 104
    app.start_cluster_calibration()
    app.collect_modes()
    assert len(app.jobs) == 1
    clock[0] = 110
    app.tick()
    assert app.cluster_processor.calibrating
    assert app.calibration_progress.get() == 6
    clock[0] = 114
    app.tick()
    assert app.cluster_processor.calibrated
    assert not app.jobs


def test_no_data_at_deadline_stops_recording_and_reports_failure(calibration) -> None:
    app, clock = calibration
    app.start_cluster_calibration()
    clock[0] = 110
    app.tick()
    assert not app.cluster_processor.calibrating
    assert not app.cluster_processor.calibrated
    assert not app.jobs
    assert app.calibration_text.get() == "Calibration failed"
    assert "Calibration failed" in app.cluster_error
    app._show_toast.assert_called_once()


def test_board_calibration_finishes_on_its_original_device_after_selection_changes(
    calibration,
) -> None:
    app, clock = calibration
    app.cluster_board_backend = True
    app.selected_device = SimpleNamespace(id="original")
    app._api_base = lambda: "http://engine"
    app._board_nullifier_command = Mock(return_value={"shotNoiseCoefficients": {"a": 1.5}})
    app.start_cluster_calibration()
    app.selected_device = SimpleNamespace(id="another")
    clock[0] = 110
    app.tick()
    assert app._board_nullifier_command.call_args.args == ("finish_calibration",)
    assert app._board_nullifier_command.call_args.kwargs == {
        "target": ("http://engine", "original")
    }
    assert app.calibration_text.get() == "Reference ready"
    assert not app.jobs
