import numpy as np
from quantum_light_engine.devices.gated_streaming import GatedStreamingProcessor
from quantum_light_engine.devices.gate_timeline import GateObservation
from quantum_light_engine.devices.nullifiers import NullifierCommand
from quantum_light_engine.devices.models import DeviceProfile,DeviceProfileCreate
from quantum_light_engine.devices.session import acquisition_settings
from quantum_light_engine.config.settings import EngineSettings
from redpitaya_acquisition.domain.frame import RawSampleFrame


def processor(high):
    p=GatedStreamingProcessor('board',5)
    p.execute(NullifierCommand(command='configure',config={
        'modeShape':'identity','modeSamples':4,'minSamplesPerMode':2,
        'collectionGuardSeconds':0,'collectionEndGuardSamples':0,
        'channelBDelaySamples':0,'varianceWindowModes':10}))
    p._gate_error=None
    p._observations.extend(GateObservation(10**9+i*10**6,10**9+i*10**6,high)
                           for i in range(21))
    return p


def frame(timestamp=1005000000):
    values=np.arange(10000,dtype='<i2')
    return RawSampleFrame(stream_id=1,sequence=0,source_pack_id=0,
        captured_at_unix_ns=2*10**9,adc_capture_unix_ns=timestamp,
        base_rate_hz=125000000,decimation=125,sample_rate_hz=1000000,
        samples_per_channel=10000,channel_mask=3,channel_1=values,channel_2=values.copy())


def test_high_feeds_engine_and_low_clears_results():
    p=processor(True); p.feed(frame())
    assert p.status()['gateAcceptedSamples']==10000
    assert p.latest() is not None
    p._observations.clear()
    p._observations.extend(processor(False)._observations)
    p.feed(frame())
    assert p.latest() is None
    assert p.status()['gateDiscardedSamples']==10000


def test_missing_hardware_timestamps_fail_closed():
    p=processor(True); p.feed(frame(0))
    assert p.latest() is None
    assert p.status()['gateAcceptedSamples']==0


def test_server_stream_cannot_autoload_an_fpga_image_or_select_arm_relay():
    profile=DeviceProfile.create(DeviceProfileCreate(name='board',source='redpitaya',host='board',
        acquisitionMode='streaming',serverStreamProcessing=True,decimation=2))
    settings=acquisition_settings(profile,EngineSettings(_env_file=None,native_nullifier_enabled=True))
    assert settings.native_nullifier_port==0
    assert not settings.auto_start_streaming_app
    assert settings.require_adc_timestamps
