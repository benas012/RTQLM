from __future__ import annotations

from types import ModuleType

import pytest

import redpitaya_acquisition.redpitaya.streaming_source as streaming_source_module
from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.redpitaya.library_loader import LoadedStreamingLibrary
from redpitaya_acquisition.redpitaya.streaming_source import RedPitayaStreamingSource


@pytest.mark.asyncio
async def test_connect_keeps_an_available_streaming_app_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    starts: list[tuple[str, float]] = []

    monkeypatch.setattr(
        streaming_source_module,
        "request_streaming_app_start",
        lambda host, delay: starts.append((host, delay)),
    )
    source = RedPitayaStreamingSource(
        Settings(
            _env_file=None,
            source="redpitaya",
            host="explicit-host",
            streaming_module="tests.fakes.fake_rp_stream",
            auto_start_streaming_app=True,
        )
    )

    await source.connect()

    assert starts == []
    await source.disconnect()


@pytest.mark.asyncio
async def test_connect_uses_version_3_split_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(streaming_source_module, "_streaming_service_ready", lambda _host: True)

    class Callback:
        pass

    class ConfigClient:
        def connect(self, hosts: list[str]) -> bool:
            calls.append(("connect", hosts))
            return True

        def sendConfig(self, host: str, key: str, value: str) -> bool:
            calls.append(("config", host, key, value))
            return True

        def setVerbose(self, enabled: bool) -> None:
            calls.append(("config-verbose", enabled))

    class Client:
        def __init__(self, config_client: ConfigClient) -> None:
            calls.append(("construct", config_client))

        def setCallback(self, callback: Callback) -> bool:
            calls.append(("callback", callback))
            return True

        def setVerbose(self, enabled: bool) -> None:
            calls.append(("adc-verbose", enabled))

    module = ModuleType("test_streaming_module_v3")
    module.ConfigStreamClient = ConfigClient  # type: ignore[attr-defined]
    module.ADCStreamClient = Client  # type: ignore[attr-defined]
    module.ADCCallback = Callback  # type: ignore[attr-defined]
    loaded = LoadedStreamingLibrary(module=module, name=module.__name__, path=None)
    monkeypatch.setattr(streaming_source_module, "load_streaming_library", lambda _name: loaded)
    source = RedPitayaStreamingSource(
        Settings(_env_file=None, source="redpitaya", host="rp-test.local")
    )

    await source.connect()

    assert calls[0][0] == "construct"
    assert ("connect", ["rp-test.local"]) in calls
    assert any(call[0] == "callback" for call in calls)
    await source.disconnect()


@pytest.mark.asyncio
async def test_version_3_starts_board_app_before_entering_native_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    readiness = iter((False, True))

    class Callback:
        pass

    class ConfigClient:
        def connect(self, hosts: list[str]) -> bool:
            calls.append(("connect", hosts))
            return True

        def sendConfig(self, host: str, key: str, value: str) -> bool:
            return True

    class Client:
        def __init__(self, _config_client: ConfigClient) -> None:
            calls.append(("construct",))

        def setCallback(self, _callback: Callback) -> bool:
            return True

    module = ModuleType("test_streaming_module_v3")
    module.ConfigStreamClient = ConfigClient  # type: ignore[attr-defined]
    module.ADCStreamClient = Client  # type: ignore[attr-defined]
    module.ADCCallback = Callback  # type: ignore[attr-defined]
    loaded = LoadedStreamingLibrary(module=module, name=module.__name__, path=None)
    monkeypatch.setattr(streaming_source_module, "load_streaming_library", lambda _name: loaded)
    monkeypatch.setattr(
        streaming_source_module,
        "_streaming_service_ready",
        lambda _host: next(readiness),
    )
    monkeypatch.setattr(
        streaming_source_module,
        "request_streaming_app_start",
        lambda host, delay: calls.append(("start-app", host, delay)),
    )
    source = RedPitayaStreamingSource(
        Settings(_env_file=None, source="redpitaya", host="rp-test.local")
    )

    await source.connect()

    assert calls[0][0] == "start-app"
    assert calls[1][0] == "construct"
    assert calls[2] == ("connect", ["rp-test.local"])
    await source.disconnect()


@pytest.mark.asyncio
async def test_connect_restarts_the_app_only_after_native_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_results = iter((False, True))
    starts: list[tuple[str, float]] = []

    class Callback:
        pass

    class Client:
        def __init__(self) -> None:
            self.connect_result = next(connection_results)

        def connect(self, _hosts: list[str]) -> bool:
            return self.connect_result

        def disconnect(self) -> None:
            return

        def setReceiveDataCallback(self, _callback: Callback) -> bool:
            return True

        def setVerbose(self, _enabled: bool) -> None:
            return

        def sendConfig(self, _host: str, _key: str, _value: str) -> bool:
            return True

    module = ModuleType("test_streaming_module")
    module.ADCStreamClient = Client  # type: ignore[attr-defined]
    module.ADCCallback = Callback  # type: ignore[attr-defined]
    loaded = LoadedStreamingLibrary(module=module, name=module.__name__, path=None)
    monkeypatch.setattr(streaming_source_module, "load_streaming_library", lambda _name: loaded)
    monkeypatch.setattr(
        streaming_source_module,
        "request_streaming_app_start",
        lambda host, delay: starts.append((host, delay)),
    )
    source = RedPitayaStreamingSource(
        Settings(
            _env_file=None,
            source="redpitaya",
            host="rp-test.local",
            auto_start_streaming_app=True,
            streaming_app_startup_delay_seconds=2.5,
        )
    )

    await source.connect()

    assert starts == [("rp-test.local", 2.5)]
    await source.disconnect()


@pytest.mark.asyncio
async def test_connect_retries_after_streaming_app_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_results = iter((False, False, False, True))
    starts: list[tuple[str, float]] = []
    sleeps: list[float] = []

    class Callback:
        pass

    class Client:
        def __init__(self) -> None:
            self.connect_result = next(connection_results)

        def connect(self, _hosts: list[str]) -> bool:
            return self.connect_result

        def disconnect(self) -> None:
            return

        def setReceiveDataCallback(self, _callback: Callback) -> bool:
            return True

        def setVerbose(self, _enabled: bool) -> None:
            return

        def sendConfig(self, _host: str, _key: str, _value: str) -> bool:
            return True

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    module = ModuleType("test_streaming_module")
    module.ADCStreamClient = Client  # type: ignore[attr-defined]
    module.ADCCallback = Callback  # type: ignore[attr-defined]
    loaded = LoadedStreamingLibrary(module=module, name=module.__name__, path=None)
    monkeypatch.setattr(streaming_source_module, "load_streaming_library", lambda _name: loaded)
    monkeypatch.setattr(
        streaming_source_module,
        "request_streaming_app_start",
        lambda host, delay: starts.append((host, delay)),
    )
    monkeypatch.setattr(streaming_source_module.asyncio, "sleep", record_sleep)
    source = RedPitayaStreamingSource(
        Settings(
            _env_file=None,
            source="redpitaya",
            host="rp-test.local",
            auto_start_streaming_app=True,
            streaming_app_startup_delay_seconds=2.5,
        )
    )

    await source.connect()

    assert starts == [("rp-test.local", 2.5)]
    assert sleeps == [streaming_source_module.POST_RESTART_CONNECT_INTERVAL_SECONDS] * 2
    await source.disconnect()


@pytest.mark.asyncio
async def test_connect_restarts_streaming_app_again_when_retries_keep_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_results = iter((False, False, True))
    starts: list[tuple[str, float]] = []

    class Callback:
        pass

    class Client:
        def __init__(self) -> None:
            self.connect_result = next(connection_results)

        def connect(self, _hosts: list[str]) -> bool:
            return self.connect_result

        def disconnect(self) -> None:
            return

        def setReceiveDataCallback(self, _callback: Callback) -> bool:
            return True

        def setVerbose(self, _enabled: bool) -> None:
            return

        def sendConfig(self, _host: str, _key: str, _value: str) -> bool:
            return True

    module = ModuleType("test_streaming_module")
    module.ADCStreamClient = Client  # type: ignore[attr-defined]
    module.ADCCallback = Callback  # type: ignore[attr-defined]
    loaded = LoadedStreamingLibrary(module=module, name=module.__name__, path=None)
    monkeypatch.setattr(streaming_source_module, "load_streaming_library", lambda _name: loaded)
    monkeypatch.setattr(
        streaming_source_module,
        "request_streaming_app_start",
        lambda host, delay: starts.append((host, delay)),
    )
    monkeypatch.setattr(streaming_source_module, "POST_RESTART_CONNECT_TIMEOUT_SECONDS", 0)
    source = RedPitayaStreamingSource(
        Settings(
            _env_file=None,
            source="redpitaya",
            host="rp-test.local",
            auto_start_streaming_app=True,
            streaming_app_startup_delay_seconds=2.5,
        )
    )

    await source.connect()

    assert starts == [("rp-test.local", 2.5), ("rp-test.local", 2.5)]
    await source.disconnect()
