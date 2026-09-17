from fastapi.testclient import TestClient

from redpitaya_acquisition.application import create_app
from redpitaya_acquisition.config.settings import Settings


def test_rest_status_start_latest_stop() -> None:
    app = create_app(Settings(_env_file=None, simulator_samples_per_packet=32))
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/latest").status_code == 404
        started = client.post("/api/start")
        assert started.status_code == 200
        assert started.json()["state"] == "streaming"
        assert client.get("/api/latest").status_code == 200
        conflict = client.put("/api/config", json=client.get("/api/config").json())
        assert conflict.status_code == 409
        assert client.post("/api/stop").json()["state"] == "stopped"


def test_simulator_does_not_require_hardware_module() -> None:
    app = create_app(Settings(_env_file=None, source="simulator"))
    with TestClient(app) as client:
        assert client.post("/api/start").status_code == 200


def test_hardware_missing_module_returns_clear_error() -> None:
    settings = Settings(
        _env_file=None,
        source="redpitaya",
        host="rp.local",
        streaming_module="missing_vendor_module",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/start")
        assert response.status_code == 503
        assert "matching the board OS" in response.json()["detail"]
