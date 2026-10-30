from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from tests.integration.test_engine_api import app_for, device_body


def config() -> dict[str, object]:
    return {
        "modeShape": "identity", "modeSamples": 4, "minSamplesPerMode": 2,
        "windowMode": "continuous", "varianceEstimator": "rolling",
        "varianceWindowModes": 4, "channelBDelaySamples": 0,
        "collectionGuardSeconds": 0, "collectionEndGuardSamples": 0,
        "nullifiers": [{"name": "difference", "terms": [
            {"source": "a", "weight": 1}, {"source": "b", "weight": -2},
        ]}],
    }


def wait_for_result(client: TestClient, url: str, *, calibrated: bool = False) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"{url}/latest")
        if response.status_code == 200 and response.json()["calibrated"] == calibrated:
            return response.json()
        time.sleep(0.01)
    raise AssertionError(f"no result: {client.get(url).json()}")


def test_commands_process_acquired_samples_without_a_receiver_and_are_per_device(
    tmp_path: Path,
) -> None:
    with TestClient(app_for(tmp_path)) as client:
        first = client.post("/api/v1/devices", json=device_body("One")).json()["id"]
        second = client.post(
            "/api/v1/devices", json=device_body("Two", default=False),
        ).json()["id"]
        url = f"/api/v1/devices/{first}/nullifiers"
        assert client.get(url).json()["enabled"] is False
        assert client.get(f"{url}/latest").status_code == 404
        configured = client.post(
            f"{url}/commands", json={"command": "configure", "config": config()},
        )
        assert configured.status_code == 200
        assert configured.json()["revision"] == 1
        assert client.get(f"/api/v1/devices/{second}/nullifiers").json()["enabled"] is False
        assert client.post(f"/api/v1/devices/{first}/start").status_code == 200
        result = wait_for_result(client, url)
        assert result["sequence"] > 0 and result["revision"] == 1
        assert set(result["nullifiers"]) == {"difference"}
        assert result["nullifiers"]["difference"]
        assert client.get(f"/api/v1/devices/{first}/status").json()["webSocketClients"] == 0

        started = client.post(f"{url}/commands", json={"command": "start_calibration"})
        assert started.status_code == 200
        deadline = time.monotonic() + 5
        while client.get(url).json()["calibrationModeCounts"]["a"] < 100:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        finished = client.post(f"{url}/commands", json={"command": "finish_calibration"})
        assert finished.status_code == 200 and finished.json()["calibrated"]
        result = wait_for_result(client, url, calibrated=True)
        assert result["squeezingDb"]["difference"]
        changed = client.post(f"{url}/commands", json={
            "command": "configure", "config": {"nullifiers": [{
                "name": "changed", "terms": [{"source": "a", "weight": 3}],
            }]},
        })
        assert changed.status_code == 200 and changed.json()["revision"] == 2
        assert not changed.json()["calibrated"]
        result = wait_for_result(client, url)
        assert result["revision"] == 2 and set(result["nullifiers"]) == {"changed"}
        assert client.post(f"/api/v1/devices/{first}/stop").status_code == 200
        assert client.get(f"{url}/latest").status_code == 404


def test_validation_and_failed_calibration_leave_active_settings_intact(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        device = client.post("/api/v1/devices", json=device_body()).json()["id"]
        url = f"/api/v1/devices/{device}/nullifiers"
        rejected = client.post(f"{url}/commands", json={"command": "start_calibration"})
        assert rejected.status_code == 409
        configured = client.post(
            f"{url}/commands", json={"command": "configure", "config": config()},
        )
        assert configured.status_code == 200
        before = client.get(url).json()
        for body in [
            {"command": "unknown"}, {"command": "configure"},
            {"command": "configure", "config": {"nullifiers": []}},
            {"command": "configure", "config": {"typo": 4}},
            {"command": "reset", "config": config()},
        ]:
            assert client.post(f"{url}/commands", json=body).status_code == 422
            assert client.get(url).json() == before
        rejected = client.post(f"{url}/commands", json={"command": "finish_calibration"})
        assert rejected.status_code == 409
        client.post(f"{url}/commands", json={"command": "start_calibration"})
        failed = client.post(f"{url}/commands", json={"command": "finish_calibration"})
        assert failed.status_code == 409
        assert "at least 100" in failed.json()["detail"]
        assert client.get(url).json()["calibrating"]
        for suffix, method, body in [("", "GET", None), ("/latest", "GET", None),
                                     ("/commands", "POST", {"command": "reset"})]:
            response = client.request(
                method, f"/api/v1/devices/{uuid4()}/nullifiers{suffix}", json=body,
            )
            assert response.status_code == 404
