from unittest.mock import Mock

import pytest

from quantum_light_engine.config.settings import EngineSettings
from quantum_light_engine.devices.models import DeviceProfile, DeviceProfileCreate
from quantum_light_engine.devices.session import DeviceSession
from quantum_light_engine.devices.server_nullifiers import ServerNullifierProcessor


def test_server_profile_selects_server_processor_and_retains_arm_default():
    profile = DeviceProfile.create(DeviceProfileCreate(
        name='board', source='redpitaya', host='board.local', acquisitionMode='buffered',
        bufferedProcessing='server', decimation=2, captureSamples=312512))
    session = DeviceSession(profile, EngineSettings(_env_file=None))
    assert isinstance(session.nullifiers, ServerNullifierProcessor)
    assert session.nullifiers.capture_samples == 312512
    assert session._new_source().processor is session.nullifiers
    assert DeviceProfileCreate(name='board',source='redpitaya',host='board').buffered_processing == 'arm'


@pytest.mark.parametrize('changes', [dict(decimation=4), dict(channel2Enabled=False),
                                     dict(captureSamples=625024),dict(acquisitionMode='streaming')])
def test_invalid_server_profile_rejected(changes):
    config = dict(name='board',source='redpitaya',host='board',acquisitionMode='buffered',
                  bufferedProcessing='server',decimation=2,captureSamples=312512)
    config.update(changes)
    with pytest.raises(ValueError): DeviceProfileCreate(**config)


def test_failed_binary_session_is_closed_and_requires_recalibration():
    processor = ServerNullifierProcessor('board','worker',2,3,18902,server_command=['worker'])
    processor.close = Mock()
    processor._call_connected = Mock(side_effect=ConnectionError('short payload'))
    with pytest.raises(ConnectionError): processor._call({'command':'capture'})
    processor.close.assert_called_once()
    assert 'recalibrate' in processor._analysis_error


@pytest.mark.parametrize('duration,samples', [(2,125056),(5,312512),(10,625024),(50,3125056)])
def test_measurement_duration_is_configurable(duration,samples):
    profile = DeviceProfile.create(DeviceProfileCreate(
        name='board',source='redpitaya',host='board',acquisitionMode='buffered',
        bufferedProcessing='server',decimation=2,captureSamples=samples,
        measurementWindowMs=duration))
    session = DeviceSession(profile,EngineSettings(_env_file=None))
    assert session.nullifiers.measurement_window_ms == duration
    assert session.nullifiers.capture_samples == samples


def test_50ms_full_rate_arm_uses_tcp_and_retains_the_complete_measurement():
    from quantum_light_engine.devices.tcp_board_nullifiers import TcpBoardNullifierProcessor

    profile = DeviceProfile.create(DeviceProfileCreate(
        name='board',source='redpitaya',host='board',acquisitionMode='buffered',
        decimation=1,captureSamples=6250048,measurementWindowMs=50))
    session = DeviceSession(profile, EngineSettings(_env_file=None, native_nullifier_enabled=True))
    processor = session.nullifiers
    assert isinstance(processor, TcpBoardNullifierProcessor)
    settings = processor._settings(processor.config)
    assert settings['captureSamples'] == 6250048
    assert settings['measurementSamples'] == 6250000
    assert settings['historyModes'] >= 162338
    assert settings['collectSeconds'] == .05


def test_oversized_capture_is_rejected():
    with pytest.raises(ValueError):
        DeviceProfileCreate(name='board',source='redpitaya',host='board',captureSamples=8388672)
