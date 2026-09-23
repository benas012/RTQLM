from __future__ import annotations

from typing import Any

from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.redpitaya.compatibility import StreamingCompatibility


class CurrentVendorClient:
    def __init__(self) -> None:
        self.hosts: list[str] = []
        self.callback: Any = None
        self.config: list[tuple[str, str, str]] = []
        self.verbose: bool | None = None

    def connect(self, hosts: list[str]) -> bool:
        self.hosts = hosts
        return True

    def setReciveDataFunction(self, callback: Any) -> None:
        self.callback = callback

    def sendConfig(self, host: str, key: str, value: str) -> bool:
        self.config.append((host, key, value))
        return True

    def setVerbose(self, enabled: bool) -> None:
        self.verbose = enabled


def test_current_official_misspelled_wrapper_surface() -> None:
    client = CurrentVendorClient()
    adapter = StreamingCompatibility(client)
    callback = object()
    adapter.connect("rp-test.local")
    adapter.register_callback(callback)
    adapter.set_verbose(False)
    adapter.configure(Settings(_env_file=None))
    assert client.hosts == ["rp-test.local"]
    assert client.callback is callback
    assert client.verbose is False
    assert ("rp-test.local", "adc_pass_mode", "NET") in client.config
    assert ("rp-test.local", "channel_state_1", "ON") in client.config


class SingleBoardVendorClient(CurrentVendorClient):
    def connect(self) -> bool:  # type: ignore[override]
        self.hosts = ["auto"]
        return True

    def setReceiveDataFunction(self, callback: Any) -> None:
        self.callback = callback

    def sendConfig(self, key: str, value: str) -> bool:  # type: ignore[override]
        self.config.append(("single-board", key, value))
        return True


def test_single_board_wrapper_surface_after_type_error_fallback() -> None:
    client = SingleBoardVendorClient()
    adapter = StreamingCompatibility(client)
    callback = object()
    adapter.connect("rp-test.local")
    adapter.register_callback(callback)
    adapter.configure(Settings(_env_file=None))
    assert client.hosts == ["auto"]
    assert client.callback is callback
    assert ("single-board", "adc_decimation", "256") in client.config


def test_version_3_split_config_and_adc_clients() -> None:
    calls: list[tuple[Any, ...]] = []

    class Callback:
        def __disown__(self) -> object:
            calls.append(("disown",))
            return object()

    class ConfigClient:
        def connect(self, hosts: list[str]) -> bool:
            calls.append(("connect", hosts))
            return True

        def sendConfig(self, host: str, key: str, value: str) -> bool:
            calls.append(("config", host, key, value))
            return True

        def setVerbose(self, enabled: bool) -> None:
            calls.append(("config-verbose", enabled))

    class ADCClient:
        def setCallback(self, callback: Any) -> bool:
            calls.append(("callback", callback))
            return True

        def setVerbose(self, enabled: bool) -> None:
            calls.append(("adc-verbose", enabled))

    callback = Callback()
    config_client = ConfigClient()
    adapter = StreamingCompatibility(ADCClient(), config_client)

    adapter.connect("192.0.2.10")
    adapter.register_callback(callback)
    adapter.configure(Settings(_env_file=None))
    adapter.set_verbose(True)

    assert ("connect", ["192.0.2.10"]) in calls
    assert ("callback", callback) in calls
    assert ("disown",) not in calls
    assert ("config", "192.0.2.10", "adc_decimation", "256") in calls
    assert ("config-verbose", True) in calls
    assert ("adc-verbose", True) in calls
