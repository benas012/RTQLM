"""Read stock 3.x ADC packets without the vendor's per-sample Python lists."""

from __future__ import annotations

import socket
import struct
import threading
from types import SimpleNamespace
from typing import Any

import numpy as np

ADC_PORT = 18900
HEADER_BYTES = 128
ADC_MAGIC = b"\xff\xff\xff\xff\xa0\xa0\xa0\xa0" * 2
PACK_HEADER = struct.Struct("<16sQQQQIBBBBq")
MAX_BLOCK_BYTES = 2_097_152 + HEADER_BYTES


def read_exact(connection: Any, size: int) -> bytearray:
    data = bytearray(size)
    view = memoryview(data)
    offset = 0
    while offset < size:
        received = connection.recv_into(view[offset:])
        if not received:
            raise ConnectionError("Red Pitaya ADC connection closed")
        offset += received
    return data


def read_channel(connection: Any, block_size: int) -> tuple[int, Any]:
    header = read_exact(connection, HEADER_BYTES)
    magic, size, pack_id, lost, total, rate, base_bits, channel, bits, mode, captured = (
        PACK_HEADER.unpack_from(header)
    )
    if magic != ADC_MAGIC or size != block_size + HEADER_BYTES or size > MAX_BLOCK_BYTES:
        raise ValueError("Invalid Red Pitaya ADC packet header or block size")
    if channel not in (0, 1) or bits not in (8, 16) or not rate or not total:
        raise ValueError("Invalid Red Pitaya ADC channel metadata")
    data = read_exact(connection, size - HEADER_BYTES)
    raw = np.frombuffer(data, dtype="<i2" if bits == 16 else "i1")
    return channel + 1, SimpleNamespace(
        raw=raw, samples=raw.size, bitsPerSample=bits, baseRate=rate,
        adcBaseBits=base_bits, packId=pack_id, fpgaLost=lost,
        attenuator_1_20=bool(mode), timeCapture=captured,
    )


class BinaryADCClient:
    """Use the vendor configuration protocol and the stock ADC TCP data format."""

    def __init__(self, module: Any, config: Any, settings: Any) -> None:
        self._module = module
        self._config = config
        self._settings = settings
        self._callback: Any = None
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def setCallback(self, callback: Any) -> None:
        self._callback = callback

    def setVerbose(self, _enabled: bool) -> None:
        pass

    def _request(self, host: str, method: str, expected: str) -> None:
        done = threading.Event()
        errors: list[str] = []

        class Observer(self._module.ConfigCallback):  # type: ignore[name-defined, misc]
            def adcServerStopped(self, *_args: Any) -> None:
                if expected == "adcServerStopped":
                    done.set()

            def adcServerStartedTCP(self, *_args: Any) -> None:
                if expected == "adcServerStartedTCP":
                    done.set()

            def adcServerStartedFPGA(self, *_args: Any) -> None:
                if expected == "adcServerStartedFPGA":
                    done.set()

            def failed(self, *_args: Any) -> None:
                errors.append(f"Red Pitaya rejected {method}")
                done.set()

            configError = failed
            configErrorTimeout = failed
            adcServerStoppedMemError = failed
            adcServerStoppedMemModify = failed
            adcServerStoppedNoActiveChannels = failed

        observer = Observer()
        self._config.addCallback(observer)
        try:
            if getattr(self._config, method)(host) is False:
                raise ConnectionError(f"Red Pitaya rejected {method}")
            if not done.wait(5):
                raise ConnectionError(f"Red Pitaya did not acknowledge {method}")
            if errors:
                raise ConnectionError(errors[0])
        finally:
            self._config.removeCallback(observer)

    def startStreaming(self) -> bool:
        self.stopStreaming()
        host = self._config.getHosts()[0]
        self._request(host, "requestADCServerStop", "adcServerStopped")
        self._request(host, "requestADCServerStart", "adcServerStartedTCP")
        connection = socket.create_connection((host, ADC_PORT), timeout=5)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # Match the vendor client's large receive buffer. Windows' default can
        # throttle the board while callback/IPC work temporarily delays reads.
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024 * 1024)
        # A valid low-rate block can take tens of seconds to fill (8-bit mode
        # has the largest sample count). Shutdown still interrupts this wait.
        connection.settimeout(
            max(5, 2 * self._settings.block_size_bytes / self._settings.sample_rate_hz)
        )
        self._socket = connection
        self._stopping.clear()
        try:
            self._request(host, "requestADCServerFPGAStart", "adcServerStartedFPGA")
        except BaseException:
            self.stopStreaming()
            raise
        self._thread = threading.Thread(target=self._read, daemon=True, name="redpitaya-binary-adc")
        self._thread.start()
        return True

    def _read(self) -> None:
        try:
            assert self._socket is not None
            connection = self._socket
            self._callback.connected(self)
            enabled = tuple(number for number, active in (
                (1, self._settings.channel_1_enabled), (2, self._settings.channel_2_enabled)
            ) if active)
            while not self._stopping.is_set():
                channels = {}
                for expected in enabled:
                    number, channel = read_channel(connection, self._settings.block_size_bytes)
                    if number != expected:
                        raise ValueError("Red Pitaya ADC channels arrived out of order")
                    channels[f"channel{number}"] = channel
                self._callback.receivePack(self, SimpleNamespace(**channels))
        except (OSError, ValueError, ConnectionError) as exc:
            if not self._stopping.is_set():
                self._callback.error(self, str(exc))
        finally:
            if not self._stopping.is_set():
                self._callback.disconnected(self)

    def stopStreaming(self) -> None:
        self._stopping.set()
        connection, self._socket = self._socket, None
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.wait()

    def wait(self) -> None:
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()

    def notifyStop(self) -> None:
        self.stopStreaming()
