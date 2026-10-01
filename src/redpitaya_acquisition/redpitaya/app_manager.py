from __future__ import annotations

import logging
import subprocess
import time
from http.client import RemoteDisconnected
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .host import web_base_url

logger = logging.getLogger(__name__)


def _request_streaming_app_operation(host: str, operation: str) -> None:
    url = f"{web_base_url(host)}/bazaar?{operation}=streaming_manager&type=run"
    try:
        with urlopen(url, timeout=5) as response:
            response.read(512)
    except (RemoteDisconnected, HTTPError, URLError, TimeoutError, OSError) as exc:
        logger.debug("streaming app %s request did not return cleanly: %s", operation, exc)


def request_streaming_app_start(host: str, startup_delay_seconds: float) -> None:
    """Ask the Red Pitaya web app manager to start Data stream control.

    Some Red Pitaya builds close the HTTP connection while launching an app.
    That close is not a reliable failure signal, so this helper logs it and lets
    the native streaming client perform the authoritative connection check.
    """

    for operation in ("stop", "start"):
        _request_streaming_app_operation(host, operation)
        if operation == "stop":
            time.sleep(3.0)
    if startup_delay_seconds:
        time.sleep(startup_delay_seconds)


def request_streaming_app_stop(host: str) -> None:
    """Stop the board-side Data stream control app during engine disconnect."""

    _request_streaming_app_operation(host, "stop")


def request_legacy_streaming_app_start(host: str) -> None:
    command = """
export LD_LIBRARY_PATH=/opt/redpitaya/lib

cat /opt/redpitaya/fpga/fpga_streaming.bit > /dev/xdevcfg

/opt/redpitaya/sbin/mkoverlay.sh stream_app

pkill -f '/opt/redpitaya/bin/streaming-server' 2>/dev/null || true

/opt/redpitaya/bin/streaming-server \
    -c /root/.streaming_config \
    -b
"""

    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            f"root@{host}",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Could not start legacy Red Pitaya streaming server. "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
