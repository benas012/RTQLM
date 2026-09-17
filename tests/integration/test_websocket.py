from fastapi.testclient import TestClient

from redpitaya_acquisition.application import create_app
from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.streaming.binary_protocol import decode_header


def test_websocket_receives_binary_frame() -> None:
    settings = Settings(_env_file=None, simulator_samples_per_packet=32)
    app = create_app(settings)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/samples") as websocket:
            assert client.post("/api/start").status_code == 200
            message = websocket.receive_bytes()
            header = decode_header(message)
            assert header["magic"] == b"RPA1"
            assert len(message) == 80 + header["payload_size_bytes"]
