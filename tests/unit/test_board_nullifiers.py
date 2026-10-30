import asyncio
import queue
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import numpy as np
import pytest

from apps.native_receiver.native_result import native_result
from quantum_light_engine.devices.board_nullifiers import BoardNullifierProcessor
from quantum_light_engine.post_processing import config_to_dict, single_mode_processing_config


def processor():
    return BoardNullifierProcessor('http://board.local/', '/root/processor', 8, 1, 18902)


def test_single_channel_commands_preserve_profile_rate_and_complete_variance_windows():
    board = processor()
    settings = board._settings(replace(single_mode_processing_config(), single_channel='b'))
    assert settings['decimation'] == 8
    assert settings['requireFullVarianceWindow'] is True
    assert settings['nullifiers'] == [
        {'name': 'CH2', 'start': 0, 'terms': [{'source': 'b', 'offset': 0, 'weight': 1.0}]}
    ]
    assert settings['calibrationEstimator'] == 'pooled'


def test_triggered_windows_are_rejected_before_sending_to_board():
    with pytest.raises(ValueError, match='periodic or continuous'):
        processor()._settings(replace(single_mode_processing_config(), window_mode='triggered'))


def test_transport_failure_closes_command_session_and_invalidates_cached_results():
    board = processor()
    client = board._client = Mock()
    board._stdin = Mock()
    board._stdout = Mock()
    board._stdout.readline.side_effect = TimeoutError
    board._cached = {'calibrated': True}
    board._remote_process = (1234, 5678)
    with pytest.raises(TimeoutError):
        board._call({'command': 'status'})
    client.close.assert_called_once()
    assert board._client is None
    assert board._cached == {}
    assert board._stale_process == (1234, 5678)
    assert 'needs calibration' in board.status()['error']


def test_new_command_session_records_identity_and_releases_only_its_previous_process(monkeypatch):
    board = processor()
    board._stale_process = (1234, 5678)
    client = Mock()
    cleanup_out, cleanup_err = Mock(), Mock()
    cleanup_out.channel.recv_exit_status.return_value = 0
    stdin, stdout, stderr = Mock(), Mock(), Mock()
    stdout.readline.side_effect = ['RTQLM_SESSION 2345 6789\n', '{"ok":true}\n',
                                  '{"ok":true}\n']
    client.exec_command.side_effect = [(Mock(), cleanup_out, cleanup_err),
                                       (stdin, stdout, stderr)]
    monkeypatch.setattr('quantum_light_engine.devices.board_nullifiers.paramiko.SSHClient',
                        lambda: client)
    board._connect()
    cleanup = client.exec_command.call_args_list[0].args[0]
    assert '/proc/1234/stat' in cleanup and '"5678"' in cleanup
    assert '/proc/1234/exe' in cleanup and '/root/processor' in cleanup
    assert 'kill -TERM 1234' in cleanup and 'kill -KILL 1234' in cleanup
    launch = client.exec_command.call_args_list[1].args[0]
    assert 'RTQLM_SESSION' in launch and 'exec /root/processor' in launch
    assert board._stale_process is None
    assert board._remote_process == (2345, 6789)
    client.get_transport.return_value.set_keepalive.assert_called_once_with(5)


def test_prepare_retries_once_after_transport_loss_but_never_retries_a_rejected_command():
    board = processor()
    board._connect = Mock()
    board._call = Mock(side_effect=[RuntimeError('connection closed'), {'ok': True}])
    board.prepare_stream()
    assert board._connect.call_count == board._call.call_count == 2
    board._client = Mock()
    board._call = Mock(side_effect=ValueError('unsupported configuration'))
    with pytest.raises(ValueError, match='unsupported configuration'):
        board.prepare_stream()
    board._call.assert_called_once()


def test_close_preserves_transport_failure_without_dereferencing_the_cleared_client():
    board = processor()
    board._client = Mock()
    board._stdin = Mock()
    board._stdout = Mock()
    board._stdout.readline.side_effect = TimeoutError('session lost')
    board._remote_process = (1234, 5678)
    with pytest.raises(TimeoutError, match='session lost'):
        board.close()
    assert board._client is None
    assert board._stale_process == (1234, 5678)


def test_latest_requests_compact_display_and_preserves_real_mode_period():
    board = processor()
    board._client = Mock()
    board._call = Mock(return_value={
        'modePeriodSeconds': 256e-9, 'displayStride': 20, 'captures': 100,
        'stream': {'droppedPackets': 0, 'lossPackets': 0},
    })
    result = board.latest()
    board._call.assert_called_once_with({'command': 'latest', 'maxPoints': 500})
    assert result['modePeriodSeconds'] == 256e-9
    assert result['displayStride'] == 20


def test_receiver_decodes_board_arrays_without_changing_the_estimates():
    result = native_result({
        'modePeriodSeconds': 256e-9, 'modeIndexStart': 0, 'displayStride': 20,
        'quadratures': {'a': [1, None, 2]}, 'nullifiers': {'CH1': [2, 3]},
        'variances': {'CH1': [None, 0.1]}, 'squeezingDb': {},
        'varianceCounts': {'CH1': [0, 1]}, 'shotNoiseCoefficients': {},
        'calibrated': False, 'calibrating': False, 'rejectedModes': 0, 'status': 'live',
    })
    assert result.display_stride == 20
    assert result.mode_period_seconds == 256e-9
    assert np.isnan(result.quadratures['a'][1])
    np.testing.assert_array_equal(result.variances['CH1'], [np.nan, 0.1])


@pytest.mark.parametrize('board', [True, False])
@pytest.mark.parametrize('matching_config', [True, False])
def test_receiver_uses_only_the_selected_calculation_backend(monkeypatch, board, matching_config):
    from apps.native_receiver import stream_controller

    stop = threading.Event()
    frame = object()

    async def receive():
        stop.set()
        return b'raw packet'

    websocket = SimpleNamespace(recv=receive)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=websocket)
    context.__aexit__ = AsyncMock(return_value=False)
    client = Mock()
    client.request.return_value = {'nullifierBackend': 'redpitaya-arm-c' if board else 'host'}
    if matching_config:
        client.request.return_value['config'] = config_to_dict(single_mode_processing_config())
    monkeypatch.setattr(stream_controller.websockets, 'connect', lambda *a, **k: context)
    monkeypatch.setattr(stream_controller, 'EngineClient', Mock(return_value=client))
    monkeypatch.setattr(stream_controller, 'parse_frame', lambda _: frame)
    monkeypatch.setattr(stream_controller, 'start_display_timer', lambda: lambda: None)
    receiver = SimpleNamespace(
        events=queue.Queue(), stop_event=stop, selected_device=SimpleNamespace(id='board'),
        _api_base=lambda: 'http://engine', cluster_lock=threading.Lock(),
        cluster_processor=SimpleNamespace(config=single_mode_processing_config()),
        _native_nullifier_loop=AsyncMock(), _process_cluster_frame=Mock(),
        frame_lock=threading.Lock(), socket_frame_count=0, sample_history=Mock(),
    )
    asyncio.run(stream_controller.StreamControllerMixin._socket_loop(receiver, 'ws://engine/samples'))
    receiver.sample_history.append.assert_called_once_with(frame)
    assert receiver.latest_frame is frame
    assert receiver.cluster_board_backend is board
    assert receiver._process_cluster_frame.call_count == (0 if board else 1)
    assert client.request.call_count == ((2 if matching_config else 3) if board else 1)
    assert sum(call.args[0] == 'POST' for call in client.request.call_args_list) == (
        int(board and not matching_config))
