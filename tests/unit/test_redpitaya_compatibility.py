from __future__ import annotations

from redpitaya_acquisition.redpitaya.compatibility import StreamingCompatibility


def test_stop_prefers_the_thread_safe_notify_stop_signal() -> None:
    calls: list[str] = []

    class Client:
        def notifyStop(self) -> None:
            calls.append("notifyStop")

        def stopStreaming(self) -> None:
            calls.append("stopStreaming")

    StreamingCompatibility(Client()).stop()

    assert calls == ["notifyStop"]


def test_stop_falls_back_to_stop_streaming_for_older_clients() -> None:
    calls: list[str] = []

    class Client:
        def stopStreaming(self) -> None:
            calls.append("stopStreaming")

    StreamingCompatibility(Client()).stop()

    assert calls == ["stopStreaming"]
