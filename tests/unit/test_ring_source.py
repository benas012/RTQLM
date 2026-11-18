import io
import json
from types import SimpleNamespace

import pytest

from quantum_light_engine.devices.ring_source import RingStreamingSource


def packet(**changes):
    metadata = dict(sequence=0, firstSample=0, timestampNs=10**9,
                    count=312500, rate=62500000, lost=0,
                    zero=[0, 0], scale=[1/8192, 1/8192], anchorUncertaintyNs=100)
    metadata.update(changes)
    return json.dumps(metadata).encode() + b'\n' + bytes(1250000)


def source(data):
    result = RingStreamingSource(SimpleNamespace(host='board', decimation=2))
    result.reader = io.BytesIO(data)
    return result


def test_sample_index_skip_is_marked_and_channels_stay_paired():
    reader = source(packet() + packet(sequence=1, firstSample=400000, lost=87500))
    first, second = reader._read(), reader._read()
    assert first.samples_per_channel == len(first.channel_1) == len(first.channel_2) == 312500
    assert not first.packet_gap_detected
    assert second.packet_gap_detected
    assert second.fpga_lost_ch1 == second.fpga_lost_ch2 == 87500


@pytest.mark.parametrize('changes', [
    {'anchorUncertaintyNs': -1}, {'anchorUncertaintyNs': 1000001},
    {'scale': [float('nan'), 1]}, {'scale': [0, 1]}, {'zero': [0]},
    {'firstSample': -1}, {'sequence': .5}, {'count': 1},
])
def test_invalid_metadata_fails_closed(changes):
    with pytest.raises(ValueError):
        source(packet(**changes))._read()


def test_truncated_payload_fails_closed():
    with pytest.raises(ConnectionError):
        source(packet()[:-1])._read()


def test_repeated_sample_timeline_fails_closed():
    reader = source(packet() + packet(sequence=1))
    reader._read()
    with pytest.raises(ValueError):
        reader._read()
