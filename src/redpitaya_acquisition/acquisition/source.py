from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from redpitaya_acquisition.domain.frame import RawSampleFrame


class AcquisitionSource(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    def frames(self) -> AsyncIterator[RawSampleFrame]: ...

    @property
    def module_name(self) -> str | None:
        return None

    @property
    def module_path(self) -> str | None:
        return None

    def take_dropped_frames(self) -> int:
        return 0

    def take_malformed_packets(self) -> int:
        return 0

    async def set_test_output1_sine(
        self,
        enabled: bool,
        *,
        frequency_hz: float,
        amplitude_volts: float,
    ) -> None:
        raise RuntimeError("test output is not supported by this acquisition source")

    async def shutdown_remote_service(self) -> None:
        """Stop any device-side service owned by this source."""

        return
