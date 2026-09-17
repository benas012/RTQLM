from __future__ import annotations

import threading
from typing import Any

import numpy as np


class ADCCallback:
    def __disown__(self) -> ADCCallback:
        return self


class ADCChannel:
    def __init__(
        self,
        raw: np.ndarray[Any, Any],
        pack_id: int,
        fpga_lost: int = 0,
        base_rate: int = 488_281,
    ) -> None:
        self.raw = raw
        self.packId = pack_id
        self.fpgaLost = fpga_lost
        self.baseRate = base_rate


class ADCPack:
    def __init__(
        self,
        pack_id: int,
        count: int = 64,
        *,
        mismatch: bool = False,
        channel_pack_mismatch: bool = False,
    ) -> None:
        self.channel1 = ADCChannel(np.arange(count, dtype=np.int16), pack_id)
        channel_2_pack_id = pack_id - int(channel_pack_mismatch)
        self.channel2 = ADCChannel(
            np.arange(count - int(mismatch), dtype=np.int16), channel_2_pack_id
        )


class ADCStreamClient:
    packet_count = 4
    packet_gap_at: int | None = None
    fpga_loss_at: int | None = None
    malformed_at: int | None = None
    reject_key: str | None = None

    def __init__(self) -> None:
        self.callback: Any = None
        self.hosts: list[str] = []
        self.configuration: dict[str, Any] = {}
        self._stopped = threading.Event()

    def connect(self, hosts: list[str]) -> bool:
        self.hosts = hosts
        return bool(hosts)

    def setReceiveDataCallback(self, callback: Any) -> bool:
        self.callback = callback
        return True

    def setConfig(self, key: str, value: Any) -> bool:
        self.configuration[key] = value
        return key != self.reject_key

    def startStreaming(self) -> bool:
        self._stopped.clear()
        if self.callback:
            self.callback.connected()
        return True

    def wait(self) -> None:
        pack_id = 0
        lost = 0
        for index in range(1, self.packet_count + 1):
            if self._stopped.wait(0.002):
                break
            pack_id += 2 if index == self.packet_gap_at else 1
            lost += 1 if index == self.fpga_loss_at else 0
            pack = ADCPack(pack_id, mismatch=index == self.malformed_at)
            rate = 125_000_000 // int(self.configuration.get("adc_decimation", 256))
            pack.channel1.baseRate = rate
            pack.channel2.baseRate = rate
            pack.channel1.fpgaLost = lost
            pack.channel2.fpgaLost = lost
            self.callback.receivePack(pack)
        self._stopped.wait()

    def stopStreaming(self) -> bool:
        self._stopped.set()
        if self.callback:
            self.callback.stopped()
        return True

    def disconnect(self) -> None:
        if self.callback:
            self.callback.disconnected()
