from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from redpitaya_acquisition.redpitaya import output_control


def test_stock_web_stop_targets_dac_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class Socket:
        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter(())

        def close(self):  # type: ignore[no-untyped-def]
            calls.append("closed")

        def send(self, value):  # type: ignore[no-untyped-def]
            calls.append(json.loads(value))

    def connect(url, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(url)
        return Socket()

    monkeypatch.setattr(output_control, "connect", connect)
    monkeypatch.setattr(output_control.time, "sleep", lambda duration: None)
    client = SimpleNamespace()
    output_control.stop_board_outputs(client, "https://rp.local:8443")
    assert calls == ["wss://rp.local:8443/wss", {"parameters": {"SS_DAC_START": {"value": False}}}]
    output_control.stop_board_outputs(client, "https://rp.local:8443")
    assert calls[-1] == {"parameters": {"SS_DAC_START": {"value": False}}}
    assert calls.count("wss://rp.local:8443/wss") == 1
    output_control.close_board_output_control(client)
    assert calls[-1] == "closed"
    assert id(client) not in output_control._web_controls


def test_native_stop_request_preferred_and_failure_reported() -> None:
    calls = []
    output_control.stop_board_outputs(
        SimpleNamespace(requestDACServerStop=lambda host: calls.append(host) or True), "rp.local"
    )
    assert calls == ["rp.local"]
    with pytest.raises(RuntimeError, match="stop board DAC"):
        output_control.stop_board_outputs(
            SimpleNamespace(requestDACServerStop=lambda host: False), "rp.local"
        )


def test_web_stop_failure_is_not_silently_reported_as_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ConnectionError("no web app")

    monkeypatch.setattr(output_control, "connect", fail)
    with pytest.raises(RuntimeError, match="stock Data stream control"):
        output_control.stop_board_outputs(SimpleNamespace(), "rp.local")
