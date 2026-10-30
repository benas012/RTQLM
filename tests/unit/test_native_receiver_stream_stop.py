import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.unit.test_native_receiver_connect_flow import Value, app_for_connect


def stopped_app():
    app = app_for_connect('streaming')
    app._api_for_base = Mock()
    app._load_devices = Mock(return_value=[replace(app.selected_device, state='ready')])
    app._apply_loaded_devices = Mock()
    app._select_device_by_id = Mock()
    app._run_busy = lambda _label, work, apply: apply(work())
    return app


def test_stop_halts_the_device_and_keeps_the_last_frame():
    app = stopped_app()
    frame, result = object(), object()
    app.latest_frame, app.latest_cluster_result = frame, result
    app.stop_streaming()
    app._api_for_base.assert_called_once_with(
        app._api_base(), 'POST', f'/api/v1/devices/{app.selected_device.id}/stop')
    assert app.stream_stopped and app.status.get() == 'Streaming stopped'
    assert app.latest_frame is frame and app.latest_cluster_result is result


def test_stop_cancels_an_active_reference_before_halting_acquisition():
    app = stopped_app()
    app._calibration_started = 123
    app._calibration_target = (app._api_base(), app.selected_device.id)
    app._cancel_calibration_timer = Mock()
    app.calibration_progress, app.calibration_text = Value(3), Value('Recording')
    app.cluster_lock = threading.RLock()
    app.cluster_processor = Mock()
    app.stop_streaming()
    app._cancel_calibration_timer.assert_called_once()
    app.cluster_processor.clear_calibration.assert_called_once()
    assert app.calibration_progress.get() == 0
    assert app._api_for_base.call_args_list[0].args[-1] == {'command': 'clear_calibration'}
    assert app._api_for_base.call_args_list[1].args[2].endswith('/stop')


def test_stop_failure_resumes_result_updates():
    app = stopped_app()
    app._api_for_base.side_effect = RuntimeError('board unavailable')
    with pytest.raises(RuntimeError, match='board unavailable'):
        app.stop_streaming()
    assert not app.stream_stopped


def test_start_from_stopped_reuses_the_display_connection_and_restarts_acquisition():
    app = app_for_connect('ready')
    app.stream_stopped = True
    app.socket_thread = SimpleNamespace(is_alive=lambda: True)
    app._open_device_tab = Mock()
    app._start_socket = Mock()
    app._load_devices = Mock(return_value=[])
    app._apply_loaded_devices = Mock()
    app._api_for_base = Mock(return_value={'state': 'ready'})
    app._run_busy = lambda _label, work, apply: apply(work())
    app.events = Mock()
    app.connect_selected()
    assert not app.stream_stopped
    app._start_socket.assert_not_called()
    assert app._api_for_base.call_args_list[-1].args[2].endswith('/start')
    assert not any(call.args[2].endswith('/connect')
                   for call in app._api_for_base.call_args_list)


@pytest.mark.parametrize('state,stop_state,label', [
    ('streaming', 'normal', 'Connect'),
    ('ready', 'disabled', 'Start streaming'),
    ('disconnected', 'disabled', 'Reconnect'),
])
def test_stream_buttons_follow_the_device_state(state, stop_state, label):
    app = app_for_connect(state)
    app.socket_thread = SimpleNamespace(is_alive=lambda: True)
    app.loading_active = False
    connect, stop = {}, {}
    app.connect_button = SimpleNamespace(configure=lambda **kw: connect.update(kw))
    app.stop_stream_button = SimpleNamespace(configure=lambda **kw: stop.update(kw))
    app._update_connect_controls()
    assert stop['state'] == stop_state
    assert connect['text'] == label
