from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quantum_light_engine.application import create_app
from quantum_light_engine.config.settings import EngineSettings
from quantum_light_engine.devices.session import DeviceSession
from redpitaya_acquisition.streaming.binary_protocol import decode_header


def app_for(tmp_path: Path):  # type: ignore[no-untyped-def]
    return create_app(EngineSettings(_env_file=None, data_dir=tmp_path, log_dir=tmp_path / "logs"))


def device_body(name: str = "Sim", *, default: bool = True) -> dict[str, object]:
    return {
        "name": name,
        "source": "simulator",
        "isDefault": default,
        "channel1Enabled": True,
        "channel2Enabled": True,
        "decimation": 256,
        "blockSizeBytes": 131072,
        "adcBufferSizeBytes": 4194304,
        "simulatorSamplesPerPacket": 32,
        "simulatorSeed": 4,
    }


def test_device_crud_and_lifecycle(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        created = client.post("/api/v1/devices", json=device_body())
        assert created.status_code == 201
        device_id = created.json()["id"]
        assert client.get("/api/v1/devices").json()[0]["id"] == device_id
        assert client.get(f"/api/v1/devices/{device_id}").status_code == 200
        assert client.post(f"/api/v1/devices/{device_id}/connect").json()["state"] == "ready"
        assert client.delete(f"/api/v1/devices/{device_id}").status_code == 409
        assert client.post(f"/api/v1/devices/{device_id}/start").json()["state"] == "streaming"
        update = client.put(f"/api/v1/devices/{device_id}", json=device_body("Changed"))
        assert update.status_code == 409
        assert client.get(f"/api/v1/devices/{device_id}/latest").status_code == 200
        assert client.post(f"/api/v1/devices/{device_id}/stop").json()["state"] == "ready"
        assert (
            client.post(f"/api/v1/devices/{device_id}/disconnect").json()["state"] == "disconnected"
        )
        assert client.delete(f"/api/v1/devices/{device_id}").status_code == 204


def test_test_output_route_delegates_to_the_device_session(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[bool, float, float]] = []

    async def set_test_output(
        self: DeviceSession,
        enabled: bool,
        *,
        frequency_hz: float,
        amplitude_volts: float,
    ) -> dict[str, object]:
        calls.append((enabled, frequency_hz, amplitude_volts))
        return {
            "enabled": enabled,
            "channel": 1,
            "waveform": "sine",
            "frequencyHz": frequency_hz,
            "amplitudeVolts": amplitude_volts,
        }

    monkeypatch.setattr(DeviceSession, "set_test_output1_sine", set_test_output)
    with TestClient(app_for(tmp_path)) as client:
        device_id = client.post("/api/v1/devices", json=device_body()).json()["id"]

        response = client.post(
            f"/api/v1/devices/{device_id}/outputs/1/test-sine",
            json={"enabled": True},
        )

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "channel": 1,
        "waveform": "sine",
        "frequencyHz": 1_000,
        "amplitudeVolts": 0.2,
    }
    assert calls == [(True, 1_000, 0.2)]


def test_profile_update_from_ready_disconnects_instead_of_reconnecting(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        device_id = client.post("/api/v1/devices", json=device_body()).json()["id"]
        assert client.post(f"/api/v1/devices/{device_id}/connect").json()["state"] == "ready"

        body = device_body("CH1 only")
        body["channel2Enabled"] = False
        updated = client.put(f"/api/v1/devices/{device_id}", json=body)

        assert updated.status_code == 200
        payload = updated.json()
        assert payload["channel2Enabled"] is False
        assert payload["runtime"]["state"] == "disconnected"
        assert payload["runtime"]["connected"] is False


def test_device_websocket_preserves_binary_protocol(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        device_id = client.post("/api/v1/devices", json=device_body()).json()["id"]
        with client.websocket_connect(f"/ws/v1/devices/{device_id}/samples") as websocket:
            client.post(f"/api/v1/devices/{device_id}/start")
            message = websocket.receive_bytes()
            assert decode_header(message)["magic"] == b"RPA1"
        status = client.get(f"/api/v1/devices/{device_id}/status").json()
        assert status["state"] == "streaming"
        assert status["webSocketClients"] == 0


def test_unknown_device_is_clear(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        assert client.get("/api/v1/devices/00000000-0000-0000-0000-000000000000").status_code == 404


def test_removed_browser_and_legacy_routes_are_not_registered(tmp_path: Path) -> None:
    paths = {path for route in app_for(tmp_path).routes if (path := getattr(route, "path", None))}
    assert "/receiver" not in paths
    assert "/ws/samples" not in paths
    assert not paths.intersection(
        {"/api/status", "/api/config", "/api/start", "/api/stop", "/api/latest"}
    )
