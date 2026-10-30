"""Read extension pins on the normal scope FPGA without resetting the board."""

from __future__ import annotations

import json
import shlex
from typing import Any
from urllib.parse import urlsplit

import paramiko

from .host import web_base_url

# Keep stdout exclusively for replies. Re-check the image on every request: the
# streaming FPGA uses different registers and cannot supply live pin readback.
BOARD_READER = r'''
import json
import sys
from pathlib import Path

def check_image():
    image = Path("/tmp/loaded_fpga.inf").read_text().strip()
    if image != "v0.94":
        raise RuntimeError("External pin polling requires the v0.94 FPGA; loaded image is "
                           + image + ". The stock streaming FPGA has no live GPIO readback.")

try:
    check_image()
    sys.path.insert(0, "/opt/redpitaya/lib/python")
    import rp
    pin = getattr(rp, "RP_" + sys.argv[1])
    if rp.rp_InitReset(False) != 0:
        raise RuntimeError("Could not initialize the digital pin API without reset")
    try:
        result, direction = rp.rp_DpinGetDirection(pin)
        if result != 0 or direction != rp.RP_IN:
            raise RuntimeError("The selected extension pin must already be configured as input")
        for request in sys.stdin:
            check_image()
            result, state = rp.rp_DpinGetState(pin)
            if result != 0:
                raise RuntimeError("Digital pin read failed (code %s)" % result)
            print(json.dumps({"level": int(state)}), flush=True)
    finally:
        rp.rp_Release()
except Exception as exc:
    print(json.dumps({"error": str(exc)}), flush=True)
'''


class DigitalInputReader:
    """One SSH session and one request/reply channel, with bounded blocking reads."""

    def __init__(self, host: str, password: str | None = None) -> None:
        self.host = urlsplit(web_base_url(host)).hostname
        self.password = password
        self._client: Any = None
        self._stdin: Any = None
        self._stdout: Any = None

    def connect(self, pin: str) -> int:
        client = paramiko.SSHClient()
        self._client = client
        client.load_system_host_keys()
        # Reject unknown/changed host keys; never accept them automatically.
        client.connect(
            self.host,
            username="root",
            password=self.password,
            timeout=5,
            auth_timeout=5,
            banner_timeout=5,
        )
        command = "python3 -u -c " + shlex.quote(BOARD_READER) + " " + shlex.quote(pin)
        self._stdin, self._stdout, _stderr = client.exec_command(command, timeout=10)
        level = self.read()
        self._stdout.channel.settimeout(2)
        return level

    def read(self) -> int:
        stdin, stdout = self._stdin, self._stdout
        if stdin is None or stdout is None:
            raise RuntimeError("digital input reader is not connected")
        stdin.write("read\n")
        stdin.flush()
        line = stdout.readline()
        if not line:
            raise ConnectionError("digital input reader closed without a reply")
        reply = json.loads(line)
        if "error" in reply:
            raise RuntimeError(str(reply["error"]))
        level = reply.get("level")
        if type(level) is not int or level not in (0, 1):
            raise ValueError("digital input reader returned an invalid 0/1 level")
        return level

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = self._stdin = self._stdout = None
