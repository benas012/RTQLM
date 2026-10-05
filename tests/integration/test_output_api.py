from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantum_light_engine.devices.session import DeviceSession
from redpitaya_acquisition.redpitaya.output_signals import OutputSignalsConfig
from tests.integration.test_engine_api import app_for, device_body


def test_output_api_validates_before_delegating_and_targets_selected_board(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def configure(self, config):  # type: ignore[no-untyped-def]
        calls.append((str(self.profile.id), config))
        return {"state": "running", "config": config.model_dump(mode="json", by_alias=True)}

    monkeypatch.setattr(DeviceSession, "set_output_signals", configure)
    with TestClient(app_for(tmp_path)) as client:
        first = client.post("/api/v1/devices", json=device_body("One")).json()["id"]
        second = client.post("/api/v1/devices", json=device_body("Two", default=False)).json()["id"]
        response = client.put(
            f"/api/v1/devices/{second}/outputs",
            json={
                "output1": {"enabled": True, "frequencyHz": 976562.5},
                "output2": {"enabled": True, "frequencyHz": 976562.5, "phaseDegrees": -90},
            },
        )
        assert response.status_code == 200
        assert calls[0][0] == second
        assert calls[0][1].output2.phase_degrees == -90
        assert (
            client.put(
                f"/api/v1/devices/{first}/outputs",
                json={"output1": {"amplitudeVolts": 0.9, "offsetVolts": 0.9}},
            ).status_code
            == 422
        )
        assert len(calls) == 1
        assert client.get(f"/api/v1/devices/{second}/outputs").status_code == 200
        assert client.post(f"/api/v1/devices/{second}/outputs/stop").status_code == 200
        assert calls[-1] == (second, OutputSignalsConfig())


def test_simulator_and_disconnected_device_cannot_start_physical_outputs(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        sim = client.post("/api/v1/devices", json=device_body()).json()["id"]
        body = device_body("RP", default=False)
        body.update(source="redpitaya", host="rp-test.local")
        board = client.post("/api/v1/devices", json=body).json()["id"]
        config = {"output1": {"enabled": True}}
        assert client.put(f"/api/v1/devices/{sim}/outputs", json=config).status_code == 409
        assert client.put(f"/api/v1/devices/{board}/outputs", json=config).status_code == 409
        assert client.get(f"/api/v1/devices/{board}/outputs").json()["state"] == "unavailable"


def test_output_backend_failure_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(self, config):  # type: ignore[no-untyped-def]
        raise RuntimeError("DAC rejected output buffer")

    monkeypatch.setattr(DeviceSession, "set_output_signals", fail)
    with TestClient(app_for(tmp_path)) as client:
        device = client.post("/api/v1/devices", json=device_body()).json()["id"]
        response = client.put(f"/api/v1/devices/{device}/outputs", json={})
        assert response.status_code == 503
        assert "DAC rejected" in response.json()["detail"]


def test_stop_keeps_waveform_settings_for_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = OutputSignalsConfig.model_validate(
        {
            "output1": {"enabled": True, "frequencyHz": 976562.5},
            "output2": {"enabled": True, "frequencyHz": 976562.5, "phaseDegrees": -90},
        }
    )
    monkeypatch.setattr(
        DeviceSession,
        "output_status",
        lambda self: {"state": "running", "config": config.model_dump(mode="json", by_alias=True)},
    )

    async def configure(self, received):  # type: ignore[no-untyped-def]
        return {"state": "inactive", "config": received.model_dump(mode="json", by_alias=True)}

    monkeypatch.setattr(DeviceSession, "set_output_signals", configure)
    with TestClient(app_for(tmp_path)) as client:
        device = client.post("/api/v1/devices", json=device_body()).json()["id"]
        response = client.post(f"/api/v1/devices/{device}/outputs/stop")
        assert response.status_code == 200
        stopped = response.json()["config"]
        assert not stopped["output1"]["enabled"] and not stopped["output2"]["enabled"]
        assert stopped["output2"]["phaseDegrees"] == -90
        assert stopped["output1"]["frequencyHz"] == 976562.5
