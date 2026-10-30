from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from quantum_light_engine.devices import trigger
from quantum_light_engine.devices.session import DeviceSession
from tests.integration.test_engine_api import app_for, device_body


def board_body() -> dict[str, object]:
    return {**device_body("Board"), "source": "redpitaya", "host": "rp-test.local"}


def test_trigger_configuration_validates_and_targets_one_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def configure(self, config):  # type: ignore[no-untyped-def]
        calls.append((str(self.profile.id), config))
        return {"config": config.model_dump(by_alias=True)}

    monkeypatch.setattr(DeviceSession, "configure_trigger", configure)
    with TestClient(app_for(tmp_path)) as client:
        first = client.post("/api/v1/devices", json=device_body("One")).json()["id"]
        second = client.post(
            "/api/v1/devices", json=device_body("Two", default=False)
        ).json()["id"]
        url = f"/api/v1/devices/{second}/trigger"
        response = client.put(url, json={
            "enabled": True, "pin": "DIO3_N", "edge": "falling", "pollIntervalMs": 20,
        })
        assert response.status_code == 200
        assert calls[0][0] == second and calls[0][1].pin == "DIO3_N"
        assert client.get(f"/api/v1/devices/{first}/trigger").json()["state"] == "disabled"
        assert client.put(url, json={"pin": "DIO8_P"}).status_code == 422
        assert client.put(url, json={"pollIntervalMs": 0}).status_code == 422
        assert len(calls) == 1
        assert client.get(f"/api/v1/devices/{uuid4()}/trigger").status_code == 404
        assert client.put(f"/api/v1/devices/{uuid4()}/trigger", json={}).status_code == 404


def test_simulators_cannot_poll_physical_pins(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        device = client.post("/api/v1/devices", json=device_body()).json()["id"]
        url = f"/api/v1/devices/{device}/trigger"
        assert client.put(url, json={"enabled": True}).status_code == 409
        assert client.put(url, json={"enabled": False}).status_code == 200


def test_unsupported_firmware_returns_503_and_unknown_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reader:
        def __init__(self, *args):  # type: ignore[no-untyped-def]
            pass

        def connect(self, pin: str) -> int:
            raise RuntimeError("stock streaming FPGA has no live GPIO readback")

        def close(self) -> None:
            pass

    monkeypatch.setattr(trigger, "DigitalInputReader", Reader)
    with TestClient(app_for(tmp_path)) as client:
        device = client.post("/api/v1/devices", json=board_body()).json()["id"]
        url = f"/api/v1/devices/{device}/trigger"
        response = client.put(url, json={"enabled": True})
        assert response.status_code == 503
        assert "no live GPIO" in response.json()["detail"]
        state = client.get(url).json()
        assert state["state"] == "error" and state["level"] is None
        assert state["triggerCount"] == 0


def test_disconnect_and_shutdown_release_polling_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    readers = []

    class Reader:
        def __init__(self, *args):  # type: ignore[no-untyped-def]
            self.closed = False
            readers.append(self)

        def connect(self, pin: str) -> int:
            return 1

        def read(self) -> int:
            return 1

        def close(self) -> None:
            self.closed = True

    async def no_board_shutdown(self):  # type: ignore[no-untyped-def]
        pass

    from redpitaya_acquisition.redpitaya.streaming_source import RedPitayaStreamingSource

    monkeypatch.setattr(RedPitayaStreamingSource, "shutdown_remote_service", no_board_shutdown)
    monkeypatch.setattr(trigger, "DigitalInputReader", Reader)
    with TestClient(app_for(tmp_path)) as client:
        device = client.post("/api/v1/devices", json=board_body()).json()["id"]
        url = f"/api/v1/devices/{device}/trigger"
        response = client.put(url, json={"enabled": True})
        assert response.status_code == 200
        assert response.json()["level"] == 1
        assert response.json()["triggerCount"] == 0
        runtime = client.get(f"/api/v1/devices/{device}/status").json()
        assert runtime["externalTrigger"]["level"] == 1
        assert client.post(f"/api/v1/devices/{device}/disconnect").status_code == 200
        assert readers[0].closed
        assert client.get(url).json()["level"] is None
        assert client.put(url, json={"enabled": True}).status_code == 200
        assert client.delete(f"/api/v1/devices/{device}").status_code == 204
        assert readers[1].closed
        # A newly-created board also releases its independent reader at shutdown.
        device = client.post("/api/v1/devices", json=board_body()).json()["id"]
        response = client.put(f"/api/v1/devices/{device}/trigger", json={"enabled": True})
        assert response.status_code == 200
    assert readers[2].closed
