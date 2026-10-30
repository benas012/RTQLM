import threading
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from quantum_light_engine.config.settings import EngineSettings
from quantum_light_engine.devices.board_nullifiers import BoardNullifierProcessor
from quantum_light_engine.devices.buffered_source import BufferedCaptureSource
from quantum_light_engine.devices.models import DeviceProfile, DeviceProfileCreate
from quantum_light_engine.devices.session import DeviceSession, DeviceSessionState
from quantum_light_engine.post_processing import single_mode_processing_config
from redpitaya_acquisition.redpitaya.output_signals import OutputSignalsConfig


def test_buffered_settings_use_the_requested_adc_rate_and_complete_capture():
    board = BoardNullifierProcessor('board.local', '/processor', 1, 3, 18902)
    board.buffered = True
    settings = board._settings(replace(single_mode_processing_config(), mode_samples=None,
                                       mode_shape='identity', mode_width_seconds=308e-9))
    assert settings['captureSamples'] == 625_024
    assert settings['decimation'] == 1
    assert settings['modeSamples'] is None
    assert settings['modeWidthSeconds'] == 308e-9


def test_capture_profile_rejects_unaligned_dma_lengths():
    with pytest.raises(ValueError):
        DeviceProfileCreate(name='board', source='redpitaya', host='board.local',
                            acquisitionMode='buffered', captureSamples=625_025)


@pytest.mark.asyncio
async def test_preview_uses_the_original_timebase_and_keeps_captures_separate():
    board = Mock(decimation=1, channel_mask=3)
    board.capture_preview.return_value = {'preview': {'a': [.1, -.2], 'b': [.2, -.1]}}
    source = BufferedCaptureSource(board)
    await source.connect()
    await source.start()
    frames = source.frames()
    first, second = await anext(frames), await anext(frames)
    assert first.stream_id != second.stream_id
    assert first.sample_rate_hz == 125_000_000
    assert first.fpga_lost_ch1 == first.fpga_lost_ch2 == 0
    np.testing.assert_array_equal(first.channel_1, [819, -1638])
    await source.stop()
    await frames.aclose()
    await source.disconnect()
    board.prepare_capture.assert_called_once()
    board.close.assert_called_once()


@pytest.mark.asyncio
async def test_session_stop_keeps_buffered_reference_and_outputs_until_disconnect():
    profile = DeviceProfile.create(DeviceProfileCreate(
        name='board', source='redpitaya', host='board.local', acquisitionMode='buffered'))
    session = DeviceSession(profile, EngineSettings(_env_file=None))
    board = BoardNullifierProcessor('board.local', '/processor', 1, 3, 18902)
    board.prepare_capture, board.stop_stream = Mock(), Mock()
    board.reset_stream, board.close = Mock(), Mock()
    session.nullifiers = board
    source = session._new_source()
    outputs = OutputSignalsConfig.model_validate({'output1': {'enabled': True}})
    source.outputs = outputs
    session._state = DeviceSessionState.STREAMING
    session.manager.stop = AsyncMock(side_effect=source.disconnect)
    session.trigger.stop = AsyncMock()

    await session.stop()

    assert session.state is DeviceSessionState.READY
    assert session._prepared_source is source
    assert source.outputs is outputs
    board.close.assert_not_called()
    board.reset_stream.assert_called_once_with()
    session.trigger.stop.assert_not_called()
    session.manager.start = AsyncMock()
    await session.start()
    session.trigger.stop.assert_not_called()
    await session.disconnect()
    session.trigger.stop.assert_awaited_once()
    board.close.assert_called_once_with()
    board.reset_stream.assert_called_with(clear_calibration=True)


def test_buffered_capture_demands_compact_results_without_recomputing_them():
    board = BoardNullifierProcessor('board.local', '/processor', 1, 3, 18902)
    board._call = Mock(return_value={'ok': True, 'preview': {}})
    assert board.capture_preview() == {'ok': True, 'preview': {}}
    board._call.assert_called_once_with(
        {'command': 'capture', 'previewPoints': 1024, 'maxPoints': 500})


def test_buffered_latest_reuses_completed_c_results_without_another_board_transfer():
    board = BoardNullifierProcessor('board.local', '/processor', 1, 3, 18902)
    board.buffered = True
    board._client = Mock()
    board._call = Mock(return_value={
        'quadratures': {'a': [1, 2]}, 'preview': {'a': [.1]},
        'modePeriodSeconds': 304e-9, 'displayStride': 33, 'captures': 4,
        'stream': {'droppedPackets': 0, 'lossPackets': 0},
    })
    board.capture_preview()
    first, again = board.latest(), board.latest()
    assert first == again
    assert first['quadratures'] == {'a': [1, 2]}
    assert 'preview' not in first
    assert board._call.call_count == 1
    board.reset_stream()
    assert board.latest() is None


def test_cached_results_do_not_wait_for_the_next_capture_command():
    board = BoardNullifierProcessor('board.local', '/processor', 1, 3, 18902)
    board.buffered = True
    board._client = Mock()
    board._call = Mock(return_value={
        'quadratures': {'a': [1]}, 'modePeriodSeconds': 304e-9, 'captures': 1,
        'stream': {'droppedPackets': 0, 'lossPackets': 0},
    })
    board.capture_preview()
    original = board.config
    board.config = replace(original, measurement_basis='p')
    held, release = threading.Event(), threading.Event()
    def hold_capture_lock():
        with board._lock:
            held.set()
            release.wait(2)
    worker = threading.Thread(target=hold_capture_lock)
    worker.start()
    assert held.wait(1)
    try:
        result = board.latest()
        assert not release.is_set()
        assert worker.is_alive()
        assert result['measurementBasis'] == original.measurement_basis
    finally:
        release.set()
        worker.join(1)


@pytest.mark.asyncio
async def test_buffered_sdk_outputs_report_and_send_the_selected_amplitudes():
    board = Mock()
    source = BufferedCaptureSource(board)
    config = OutputSignalsConfig.model_validate({
        'output1': {'enabled': True, 'frequencyHz': 6_000_000, 'amplitudeVolts': .025},
        'output2': {'enabled': True, 'frequencyHz': 6_000_000, 'amplitudeVolts': .0125},
    })
    await source.set_output_signals(config)
    assert board.set_output.call_count == 2
    assert board.set_output.call_args_list[0].args[0]['amplitude'] == .025
    assert board.set_output.call_args_list[1].args[0]['amplitude'] == .0125
    assert source.output_status()['state'] == 'active'


def test_fpga_restoration_releases_our_stale_process_before_switching_images(monkeypatch):
    board = BoardNullifierProcessor('board.local', '/processor', 1, 3, 18902)
    board._previous_fpga = 'stream_app'
    board._stale_process = (123, 456)
    client = Mock()
    monkeypatch.setattr('quantum_light_engine.devices.board_nullifiers.paramiko.SSHClient',
                        lambda: client)
    order = []
    board._release_stale_process = Mock(side_effect=lambda _client: order.append('release'))
    board._remote_command = Mock(side_effect=lambda _client, _command: order.append('restore'))
    board.close()
    assert order == ['release', 'restore']
    assert board._previous_fpga is None
    client.close.assert_called_once()
