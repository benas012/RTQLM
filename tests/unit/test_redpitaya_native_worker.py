from __future__ import annotations

import asyncio

import pytest

from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.redpitaya.native_worker import (
    NativeStreamingWorker,
    NativeWorkerCrashed,
    _stop_test_output,
    isolated_client_is_version_3,
    should_isolate_native_client,
)


@pytest.mark.asyncio
async def test_native_worker_streams_frames_from_an_isolated_process() -> None:
    frames = []
    events = []
    worker = NativeStreamingWorker(
        Settings(
            _env_file=None,
            source="redpitaya",
            host="explicit-host",
            streaming_module="tests.fakes.fake_rp_stream",
        ),
        frames.append,
        lambda event, detail: events.append((event, detail)),
    )

    await worker.connect("explicit-host")
    await worker.start()
    for _attempt in range(100):
        if frames:
            break
        await asyncio.sleep(0.01)

    assert frames
    assert frames[0].source_pack_id == 1
    assert ("connected", None) in events
    await worker.stop()
    await worker.close()


@pytest.mark.asyncio
async def test_native_worker_contains_a_vendor_process_abort() -> None:
    worker = NativeStreamingWorker(
        Settings(
            _env_file=None,
            source="redpitaya",
            host="explicit-host",
            streaming_module="tests.fakes.crashing_rp_stream",
        ),
        lambda _frame: None,
        lambda _event, _detail: None,
    )

    with pytest.raises(NativeWorkerCrashed, match="engine stayed alive"):
        await worker.connect("explicit-host")

    await worker.close()


def test_binary_vendor_client_is_detected_for_isolation(tmp_path) -> None:
    client = tmp_path / "rpsa-client-3.00" / "python_lib"
    client.mkdir(parents=True)
    (client / "_python_lib3_12.pyd").write_bytes(b"")

    assert should_isolate_native_client(str(client))
    assert isolated_client_is_version_3(str(client))


def test_pure_python_streaming_client_stays_in_process() -> None:
    assert not should_isolate_native_client("tests.fakes.fake_rp_stream")


def test_test_output_stop_releases_the_vendor_wait_loop() -> None:
    calls: list[str] = []

    class DACClient:
        def __init__(self) -> None:
            self.notified = False

        def stopStreaming(self) -> None:
            calls.append("board-stop")

        def notifyStop(self) -> None:
            calls.append("notify-stop")
            self.notified = True

        def wait(self) -> None:
            assert self.notified
            calls.append("wait")

    _stop_test_output(DACClient())

    assert calls == ["board-stop", "notify-stop", "wait"]
