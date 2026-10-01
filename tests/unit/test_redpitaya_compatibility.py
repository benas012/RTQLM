from __future__ import annotations

from redpitaya_acquisition.redpitaya.compatibility import StreamingCompatibility


def test_stop_prefers_the_board_side_stop_operation() -> None:
    calls: list[str] = []

    class Client:
        def notifyStop(self) -> None:
            calls.append("notifyStop")

        def stopStreaming(self) -> None:
            calls.append("stopStreaming")

    StreamingCompatibility(Client()).stop()

    assert calls == ["stopStreaming"]


def test_stop_falls_back_to_notify_stop_for_older_clients() -> None:
    calls: list[str] = []

    class Client:
        def notifyStop(self) -> None:
            calls.append("notifyStop")

    StreamingCompatibility(Client()).stop()

    assert calls == ["notifyStop"]


def test_remote_stop_clears_stale_adc_stream_for_the_connected_host() -> None:
    calls: list[str] = []

    class ConfigClient:
        def requestADCServerStop(self, host: str) -> bool:
            calls.append(host)
            return True

    adapter = StreamingCompatibility(object(), ConfigClient())
    adapter.host = "rp-test.local"

    adapter.stop_remote_stream()

    assert calls == ["rp-test.local"]
