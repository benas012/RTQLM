from __future__ import annotations

import pytest

from examples import native_receiver as nr


def test_engine_health_check_reads_the_response_before_closing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status = 200

        def __init__(self) -> None:
            self.read_calls = 0

        def read(self) -> bytes:
            self.read_calls += 1
            return b'{"status":"live"}'

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return

    response = Response()
    monkeypatch.setattr(nr, "urlopen", lambda *_args, **_kwargs: response)

    running = nr.ReceiverApp._engine_is_running_for_base(object(), "http://127.0.0.1:8080")

    assert running
    assert response.read_calls == 1
