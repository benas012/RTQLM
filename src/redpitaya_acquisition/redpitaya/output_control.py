"""Stop the board DAC through the stock Streaming web application."""

from __future__ import annotations

import json
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from websockets.sync.client import connect

from .host import web_base_url

# Retain the web connection for the lifetime of the native configuration client.
# On some board builds a transient web session disrupts the streaming service.
_web_controls: dict[int, tuple[Any, Any]] = {}


def close_board_output_control(config_client: Any) -> None:
    entry = _web_controls.pop(id(config_client), None)
    if entry is not None:
        entry[1].close()


def _drain_control(socket: Any) -> None:
    try:
        for _message in socket:
            pass
    except Exception:
        pass


def stop_board_outputs(config_client: Any, host: str) -> None:
    """The pinned DACStreamClient.stopStreaming only joins the local sender.

    Small repeated buffers can continue on the board after that sender exits.
    A wrapper exposing the protocol's DAC stop request takes precedence. The
    unmodified 3.00 wrapper instead needs the web app's SS_DAC_START control.
    This doesn't send the ADC stop control or load another FPGA image.
    """
    method = getattr(config_client, "requestDACServerStop", None)
    if callable(method):
        from .output_signals import require_success

        require_success("stop board DAC", method(urlsplit(web_base_url(host)).hostname))
        return
    base = urlsplit(web_base_url(host))
    scheme = "wss" if base.scheme == "https" else "ws"
    url = f"{scheme}://{base.netloc}/wss"
    try:
        entry = _web_controls.get(id(config_client))
        if entry is None:
            socket = connect(url, open_timeout=3, close_timeout=1, max_size=2_000_000)
            _web_controls[id(config_client)] = (config_client, socket)
            threading.Thread(target=_drain_control, args=(socket,), daemon=True).start()
        else:
            socket = entry[1]
        socket.send(json.dumps({"parameters": {"SS_DAC_START": {"value": False}}}))
        # Parameter application is asynchronous; analog readback is separate.
        time.sleep(0.25)
    except Exception as exc:
        close_board_output_control(config_client)
        raise RuntimeError(
            "Could not send the board DAC stop control. Run the stock Data stream control "
            "web application, or use a client exposing requestDACServerStop. "
            f"Details: {exc}"
        ) from exc
