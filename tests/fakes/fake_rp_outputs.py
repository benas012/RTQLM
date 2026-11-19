from __future__ import annotations

from typing import Any

from tests.fakes.fake_rp_stream import ADCCallback
from tests.fakes.fake_rp_stream import ADCStreamClient as BaseADC

__all__ = ["ADCCallback", "ADCStreamClient", "ConfigStreamClient", "DACStreamClient"]


class ConfigStreamClient:
    def __init__(self) -> None:
        self.configuration: dict[str, Any] = {}
        self.dac_stops = 0

    def connect(self, hosts: list[str]) -> bool:
        return bool(hosts)

    def sendConfig(self, host: str, key: str, value: str) -> bool:
        self.configuration[key] = value
        return True

    def requestDACServerStop(self, host: str) -> bool:
        self.dac_stops += 1
        return True


class ADCStreamClient(BaseADC):
    def __init__(self, config: ConfigStreamClient) -> None:
        super().__init__()
        self.configuration = config.configuration


class DACStreamClient:
    def __init__(self, config: ConfigStreamClient) -> None:
        self.buffers: dict[int, list[int]] = {}
        self.started = False
        self.notified = False

    def setMemory16Bit(self, channel: int, samples: list[int]) -> bool:
        self.buffers[channel] = samples
        return len(samples) % 64 == 0

    def setRepeatInf(self, enabled: bool) -> None:
        assert enabled

    def startStreamingFromMemory(self, host: str) -> bool:
        assert len(self.buffers[1]) == len(self.buffers[2])
        self.started = True
        return True

    def stopStreaming(self) -> None:
        self.started = False

    def notifyStop(self) -> None:
        self.notified = True

    def wait(self) -> None:
        assert self.notified
