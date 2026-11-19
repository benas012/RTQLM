from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from apps.native_receiver import api_client
from apps.native_receiver.api_client import ApiError, EngineClient


class Response(BytesIO):
    def __init__(self, data: bytes, status: int = 200) -> None:
        super().__init__(data)
        self.status = status


def test_request_sends_json_and_closes_response(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Response(b'{"id":"board"}')
    requests = []

    def open_url(request: Any, *, timeout: float) -> Response:
        requests.append((request, timeout))
        return response

    monkeypatch.setattr(api_client, "urlopen", open_url)

    result = EngineClient(" http://engine.local:8080/ ").request(
        "PUT", "/api/v1/devices/board", {"channel1Enabled": True}, timeout_seconds=3
    )

    assert result == {"id": "board"}
    request, timeout = requests[0]
    assert request.full_url == "http://engine.local:8080/api/v1/devices/board"
    assert request.method == "PUT"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"channel1Enabled": True}
    assert timeout == 3
    assert response.closed


def test_empty_success_does_not_require_json(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Response(b"", status=204)
    monkeypatch.setattr(api_client, "urlopen", lambda *_args, **_kwargs: response)

    assert EngineClient("http://engine").request("DELETE", "/api/v1/devices/board") is None
    assert response.closed


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (HTTPError("http://engine", 409, "Conflict", {}, BytesIO(b'{"detail":"busy"}')), "busy"),
        (HTTPError("http://engine", 502, "Bad gateway", {}, BytesIO(b"not json")), "Bad gateway"),
        (TimeoutError(), "Timed out after 3s"),
        (URLError("connection refused"), "connection refused"),
    ],
)
def test_request_errors_remain_readable(
    monkeypatch: pytest.MonkeyPatch, error: Exception, message: str
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(api_client, "urlopen", fail)

    with pytest.raises(ApiError, match=message):
        EngineClient("http://engine").request("GET", "/api/v1/devices", timeout_seconds=3)


def test_device_list_preserves_filtering_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {"id": "sim", "name": "Simulator", "source": "simulator"},
        {
            "id": "rp",
            "name": "Board",
            "source": "redpitaya",
            "host": "rp.local",
            "runtime": {"state": "ready"},
            "channel1Enabled": True,
        },
    ]
    monkeypatch.setattr(
        api_client, "urlopen", lambda *_args, **_kwargs: Response(json.dumps(payload).encode())
    )

    (device,) = EngineClient("http://engine").devices()

    assert (device.id, device.name, device.host, device.state) == (
        "rp",
        "Board",
        "rp.local",
        "ready",
    )
    assert device.channel_1_enabled and not device.channel_2_enabled
    assert device.decimation == 256
    assert device.block_size_bytes == 131_072


@pytest.mark.parametrize(
    ("base", "scheme"), [("http://engine:8080/", "ws"), ("https://engine:8080/", "wss")]
)
def test_stream_url_uses_the_engine_transport(base: str, scheme: str) -> None:
    assert EngineClient(base).websocket_url("board") == (
        f"{scheme}://engine:8080/ws/v1/devices/board/samples"
    )


def test_readiness_retries_stop_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter([False, True])
    delays = []
    monkeypatch.setattr(EngineClient, "is_running", lambda _self: next(results))
    monkeypatch.setattr(api_client.time, "sleep", delays.append)

    assert EngineClient("http://engine").wait_until_running(attempts=5, delay_seconds=0.5)
    assert delays == [0.5]


def test_failed_readiness_does_not_sleep_after_last_check(monkeypatch: pytest.MonkeyPatch) -> None:
    checks = []
    delays = []
    monkeypatch.setattr(EngineClient, "is_running", lambda self: checks.append(self) or False)
    monkeypatch.setattr(api_client.time, "sleep", delays.append)

    assert not EngineClient("http://engine").wait_until_running(attempts=3, delay_seconds=0.5)
    assert len(checks) == 3
    assert delays == [0.5, 0.5]
