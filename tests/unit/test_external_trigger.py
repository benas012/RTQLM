from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from quantum_light_engine.config.settings import EngineSettings
from quantum_light_engine.devices import trigger
from quantum_light_engine.devices.board_nullifiers import BoardNullifierProcessor
from quantum_light_engine.devices.models import DeviceProfile, DeviceProfileCreate
from quantum_light_engine.devices.session import DeviceSession, DeviceSessionState
from quantum_light_engine.devices.trigger import ExternalTrigger, TriggerConfig
from redpitaya_acquisition.redpitaya import digital_input


@pytest.mark.parametrize(
    ("edge", "levels", "expected"),
    [
        ("rising", [1, 1, 0, 1, 1, 0, 1], 2),
        ("falling", [0, 0, 1, 0, 0, 1, 0], 2),
        ("either", [1, 1, 0, 1, 1, 0, 1], 4),
    ],
)
def test_edges_require_a_transition_and_initial_value_is_only_a_baseline(
    edge: str, levels: list[int], expected: int
) -> None:
    monitor = ExternalTrigger()
    monitor.config = TriggerConfig.model_validate({"enabled": True, "edge": edge})
    for index, level in enumerate(levels):
        monitor._observe(level)
        if index == 0:
            assert monitor.trigger_count == 0
            assert monitor.last_trigger_at is None
    assert monitor.trigger_count == expected
    assert monitor.level == levels[-1]
    assert monitor.last_trigger_at is not None
    assert monitor.last_poll_interval_ms is not None


@pytest.mark.parametrize(
    "body",
    [
        {"pin": "DIO8_P"}, {"pin": "LED0"}, {"pin": "DIO0_P; reboot"},
        {"edge": "both"}, {"pollIntervalMs": 0}, {"pollIntervalMs": 1001},
        {"pollIntervalMs": float("nan")}, {"pollIntervalMs": float("inf")},
        {"pollIntervalMs": 10, "unknown": True},
    ],
)
def test_invalid_trigger_settings_are_rejected(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TriggerConfig.model_validate(body)


class Reader:
    def __init__(self, host: str, password: str | None = None) -> None:
        self.closed = False
        self.reads = 0
        self.fail = False

    def connect(self, pin: str) -> int:
        return 0

    def read(self) -> int:
        self.reads += 1
        if self.fail:
            raise ConnectionError("pin read timed out")
        return self.reads % 2

    def close(self) -> None:
        self.closed = True


async def wait_until(predicate) -> None:  # type: ignore[no-untyped-def]
    async with asyncio.timeout(2):
        while not predicate():  # noqa: ASYNC110 - observe a background task in this test
            await asyncio.sleep(0.001)


async def test_poller_releases_connection_on_failure_and_rearms_from_a_new_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readers: list[Reader] = []

    def make_reader(host: str, password: str | None = None) -> Reader:
        reader = Reader(host, password)
        readers.append(reader)
        return reader

    monkeypatch.setattr(trigger, "DigitalInputReader", make_reader)
    monitor = ExternalTrigger()
    config = TriggerConfig(enabled=True, poll_interval_ms=2)
    try:
        initial = await monitor.configure("rp.local", config)
        assert initial["level"] == 0 and initial["triggerCount"] == 0
        await wait_until(lambda: monitor.trigger_count >= 2)
        readers[0].fail = True
        await wait_until(lambda: monitor.state == "error" and readers[0].closed)
        count = monitor.trigger_count
        assert monitor.level is None
        assert "timed out" in (monitor.last_error or "")
        assert count >= 2
        await monitor.configure("rp.local", config)
        assert monitor.trigger_count == 0
        assert monitor.last_error is None
        assert monitor.level == 0
    finally:
        await monitor.stop()
    assert all(reader.closed for reader in readers)
    assert monitor.status()["state"] == "disabled"
    assert monitor.status()["level"] is None


async def test_startup_failure_is_not_reported_as_a_low_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingReader(Reader):
        def connect(self, pin: str) -> int:
            raise RuntimeError("stock streaming FPGA has no live GPIO readback")

    reader = FailingReader("rp.local")
    monkeypatch.setattr(trigger, "DigitalInputReader", lambda *args: reader)
    monitor = ExternalTrigger()
    with pytest.raises(RuntimeError, match="no live GPIO"):
        await monitor.configure("rp.local", TriggerConfig(enabled=True))
    assert monitor.level is None and monitor.trigger_count == 0
    assert monitor.state == "error" and reader.closed


async def test_disable_stops_polling_and_keeps_the_last_event_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = Reader("rp.local")
    monkeypatch.setattr(trigger, "DigitalInputReader", lambda *args: reader)
    monitor = ExternalTrigger()
    await monitor.configure("rp.local", TriggerConfig(enabled=True, poll_interval_ms=2))
    await wait_until(lambda: monitor.trigger_count > 0)
    await monitor.stop()
    count, reads = monitor.trigger_count, reader.reads
    await asyncio.sleep(0.02)
    assert monitor.trigger_count == count and reader.reads == reads
    assert reader.closed and not monitor.config.enabled


@pytest.mark.parametrize("mode", ["buffered", "streaming"])
async def test_only_buffered_adc_acquisition_allows_concurrent_e1_reads(monkeypatch, mode):
    monkeypatch.setattr(trigger, "DigitalInputReader", Reader)
    profile = DeviceProfile.create(DeviceProfileCreate(
        name="RP", source="redpitaya", host="rp.local", acquisition_mode=mode))
    session = DeviceSession(profile, EngineSettings(_env_file=None))
    session.nullifiers = BoardNullifierProcessor("rp.local", "/processor", 1, 3, 18902)
    session._state = DeviceSessionState.STREAMING
    try:
        if mode == "buffered":
            state = await session.configure_trigger(TriggerConfig(enabled=True))
            assert state["state"] == "watching" and state["level"] == 0
        else:
            with pytest.raises(RuntimeError, match="stop ADC acquisition"):
                await session.configure_trigger(TriggerConfig(enabled=True))
    finally:
        await session.trigger.stop()


@pytest.mark.parametrize("reply", ['{"level": 2}', '{"level": true}', '{}', '', 'garbage'])
def test_reader_rejects_invalid_or_missing_replies(reply: str) -> None:
    reader = digital_input.DigitalInputReader("http://rp.local/")
    reader._stdin = io.StringIO()
    reader._stdout = io.StringIO(reply)
    with pytest.raises((ValueError, ConnectionError)):
        reader.read()


def test_reader_uses_one_connection_and_propagates_firmware_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []
    stdout = io.StringIO('{"level": 0}\n{"level": 1}\n{"error": "wrong FPGA"}\n')
    stdout.channel = SimpleNamespace(settimeout=lambda value: None)  # type: ignore[attr-defined]
    client = SimpleNamespace(
        load_system_host_keys=lambda: None,
        connect=lambda *args, **kwargs: commands.append(kwargs),
        exec_command=lambda command, **kwargs: (
            commands.append(command) or io.StringIO(),
            stdout,
            io.StringIO(),
        ),
        close=lambda: commands.append("closed"),
    )
    monkeypatch.setattr(digital_input.paramiko, "SSHClient", lambda: client)
    reader = digital_input.DigitalInputReader("http://rp.local/", "test-password")
    assert reader.connect("DIO0_P") == 0
    assert reader.read() == 1
    with pytest.raises(RuntimeError, match="wrong FPGA"):
        reader.read()
    reader.close()
    assert len(commands) == 3
    assert commands[0]["username"] == "root"
    assert "rp_InitReset(False)" in commands[1]


def test_board_reader_checks_image_before_touching_the_pin_api() -> None:
    import sys
    from unittest.mock import patch

    output = io.StringIO()
    with (
        patch("pathlib.Path.read_text", return_value="stream_app"),
        patch.object(sys, "stdout", output),
    ):
        # No rp module exists on this host: an attempted import would change
        # this expected error, so this also checks validation comes first.
        exec(digital_input.BOARD_READER, {})
    assert "stock streaming FPGA has no live GPIO readback" in output.getvalue()
