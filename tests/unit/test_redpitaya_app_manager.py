from __future__ import annotations

from typing import Any

import redpitaya_acquisition.redpitaya.app_manager as app_manager


def test_stop_requests_the_board_streaming_app_to_stop(monkeypatch: Any) -> None:
    urls: list[str] = []

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return

        def read(self, _limit: int) -> bytes:
            return b""

    def open_url(url: str, timeout: float) -> Response:
        assert timeout == 5
        urls.append(url)
        return Response()

    monkeypatch.setattr(app_manager, "urlopen", open_url)

    app_manager.request_streaming_app_stop("http://rp-test.local/")

    assert urls == ["http://rp-test.local/bazaar?stop=streaming_manager&type=run"]
