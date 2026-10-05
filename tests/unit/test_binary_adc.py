from __future__ import annotations

import socket
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.redpitaya.binary_adc import (
    ADC_MAGIC,
    HEADER_BYTES,
    PACK_HEADER,
    BinaryADCClient,
    read_channel,
)


def packet(channel=0, bits=16, magic=ADC_MAGIC):
    data = np.arange(1024, dtype="<i2" if bits == 16 else "i1").tobytes()
    header = PACK_HEADER.pack(
        magic, HEADER_BYTES + len(data), 7, 13, len(data) * 2,
        15_625_000, 14, channel, bits, 0, 999,
    ).ljust(HEADER_BYTES, b"\0")
    return header + data


class FragmentedSocket:
    def __init__(self, data):
        self.data = data

    def recv_into(self, view):
        count = min(3, len(view), len(self.data))
        view[:count] = self.data[:count]
        self.data = self.data[count:]
        return count


@pytest.mark.parametrize("bits", [8, 16])
def test_fragmented_wire_samples_and_metadata(bits):
    number, channel = read_channel(FragmentedSocket(packet(bits=bits)), 1024 * bits // 8)
    assert number == 1
    np.testing.assert_array_equal(channel.raw, np.arange(1024, dtype=channel.raw.dtype))
    assert channel.packId == 7 and channel.fpgaLost == 13
    assert channel.baseRate == 15_625_000 and channel.adcBaseBits == 14
    assert channel.timeCapture == 999


def test_invalid_or_truncated_packet_is_rejected():
    with pytest.raises(ValueError, match="header"):
        read_channel(FragmentedSocket(packet(magic=b"x" * 16)), 2048)
    with pytest.raises(ValueError, match="block size"):
        read_channel(FragmentedSocket(packet()), 4096)
    with pytest.raises(ConnectionError, match="closed"):
        read_channel(FragmentedSocket(packet()[:-1]), 2048)


@pytest.mark.parametrize("enabled", [(True, True), (False, True)])
def test_binary_client_acknowledges_start_reads_enabled_channels_and_stops(monkeypatch, enabled):
    receiver, sender = socket.socketpair()
    frames = []
    errors = []
    received = threading.Event()
    observers = []

    class Config:
        def getHosts(self):
            return ["rp-test"]

        def addCallback(self, callback):
            observers.append(callback)

        def removeCallback(self, callback):
            observers.remove(callback)

        def requestADCServerStop(self, host):
            observers[0].adcServerStopped(self, host)
            return True

        def requestADCServerStart(self, host):
            observers[0].adcServerStartedTCP(self, host)
            return True

        def requestADCServerFPGAStart(self, host):
            observers[0].adcServerStartedFPGA(self, host)
            sender.sendall(b"".join(packet(ch) for ch, active in enumerate(enabled) if active))
            return True

    class Callback:
        def connected(self, *_args):
            pass

        def disconnected(self, *_args):
            pass

        def error(self, *_args):
            errors.append(_args[-1])

        def receivePack(self, client, pack):
            frames.append(pack)
            received.set()

    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: receiver)
    settings = Settings(_env_file=None, block_size_bytes=2048,
                        channel_1_enabled=enabled[0], channel_2_enabled=enabled[1])
    client = BinaryADCClient(SimpleNamespace(ConfigCallback=object), Config(), settings)
    client.setCallback(Callback())
    try:
        assert client.startStreaming()
        assert received.wait(1)
        assert hasattr(frames[0], "channel1") == enabled[0]
        assert frames[0].channel2.samples == 1024
    finally:
        client.stopStreaming()
        sender.close()
    assert not errors and not observers
    assert not client._thread.is_alive()
