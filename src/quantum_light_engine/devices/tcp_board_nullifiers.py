"""ARM calculation with authenticated TCP commands/results on the instrument LAN."""

from __future__ import annotations

import logging
import secrets
import shlex
import socket
from contextlib import suppress
from pathlib import Path
from time import sleep
from typing import Any

import paramiko

from .board_nullifiers import BoardNullifierProcessor

LOGGER = logging.getLogger(__name__)


class _WorkerStartupError(RuntimeError):
    """A newly launched command worker failed before it could be configured."""

    def __init__(self, message: str, *, port_busy: bool = False) -> None:
        super().__init__(message)
        self.port_busy = port_busy


class TcpBoardNullifierProcessor(BoardNullifierProcessor):
    """SSH starts the worker; acquisition commands and results use a separate TCP socket."""

    def __init__(
        self,
        host: str,
        executable: str,
        decimation: int,
        channel_mask: int,
        port: int,
        *,
        binary_port: int = 18903,
        known_hosts: Path | None = None,
        ssh_password: str | None = None,
    ):
        super().__init__(host, executable, decimation, channel_mask, port)
        self.variance_only = True
        self.binary_port = binary_port
        self.known_hosts = known_hosts
        self._ssh_password = ssh_password
        self._socket: socket.socket | None = None
        self._launch_stdout: Any = None

    def _connect(self) -> None:
        if self._client is not None:
            return
        for attempt in range(2):
            try:
                self._connect_once()
                return
            except (_WorkerStartupError, OSError, EOFError) as exc:
                if attempt:
                    raise
                if isinstance(exc, _WorkerStartupError) and exc.port_busy:
                    # A previous app can have exited before remembering its PID.
                    # Its unconnected C listener expires after 15 seconds. Allow
                    # that deadline before retrying; never kill an unknown owner.
                    LOGGER.info("Waiting for a previous board TCP listener to release its port")
                    sleep(15.1)
                LOGGER.warning("Retrying board TCP startup after cleaning up its command worker")

    def _connect_once(self) -> None:
        client = paramiko.SSHClient()
        launched = False
        token = ""
        try:
            if self.known_hosts is None:
                client.load_system_host_keys()
            else:
                client.load_host_keys(str(self.known_hosts))
            client.connect(
                self.host, username="root", password=self._ssh_password,
                timeout=10, auth_timeout=10, banner_timeout=10,
            )
            transport = client.get_transport()
            if transport is None:
                raise EOFError("Board SSH connection closed during startup")
            transport.set_keepalive(5)
            if self._remote_command(client, "cat /tmp/loaded_fpga.inf") != "v0.94":
                raise RuntimeError("Buffered TCP acquisition requires the v0.94 FPGA")
            self._ensure_installed(client, "rp-buffered-board")
            self._release_stale_process(client)
            token = secrets.token_hex(32)
            # exec preserves the announced PID/start time, so failures and reconnects
            # can release this worker without terminating another board session.
            _, self._launch_stdout, stderr = client.exec_command(
                "printf 'RTQLM_SESSION %s %s\\n' \"$$\" "
                "\"$(awk '{print $22}' /proc/$$/stat)\"; NF_BUFFER_TOKEN="
                + token
                + " exec "
                + shlex.quote(self.executable)
                + " --listen "
                + str(self.binary_port),
                timeout=15,
            )
            launched = True
            self._launch_stdout.channel.settimeout(15)
            identity = self._launch_stdout.readline(128).split()
            if (len(identity) != 3 or identity[0] != "RTQLM_SESSION"
                    or not identity[1].isdecimal() or not identity[2].isdecimal()
                    or int(identity[1]) <= 1 or int(identity[2]) <= 0):
                raise _WorkerStartupError("Board TCP worker did not identify its startup process")
            self._remote_process = (int(identity[1]), int(identity[2]))
            readiness = stderr.readline(1024).strip()
            if readiness != "READY":
                detail = readiness.replace(token, "[redacted]") or "exited before becoming ready"
                raise _WorkerStartupError(
                    f"Board TCP worker could not listen on port {self.binary_port}: {detail}",
                    port_busy="Address already in use" in readiness,
                )
            peer = transport.getpeername()[0]
            if ":" in peer:
                peer = socket.getaddrinfo(
                    self.host, self.binary_port, socket.AF_INET, socket.SOCK_STREAM
                )[0][4][0]
            self._socket = socket.create_connection((peer, self.binary_port), timeout=30)
            self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            self._socket.sendall(token.encode("ascii") + b"\n")
            self._stdin = self._socket.makefile("w", encoding="utf-8", newline="\n")
            self._stdout = self._socket.makefile("r", encoding="utf-8", newline="\n")
            self._client = client
            self._call({"command": "configure", "config": self._settings(self.config)})
            self._analysis_error = None
        except Exception as exc:
            if self._client is None:
                self._client = client
            self.close()
            if launched and not isinstance(exc, (ValueError, _WorkerStartupError)):
                raise _WorkerStartupError(
                    f"Board TCP startup failed on port {self.binary_port}: "
                    + str(exc).replace(token, "[redacted]")
                ) from exc
            raise

    def _call(self, command: dict[str, object]) -> dict[str, Any]:
        try:
            return super()._call(command)
        finally:
            if self._client is None:
                self._close_socket()

    def prepare_capture(self) -> None:
        with self._lock:
            self._connect()

    def _close_socket(self) -> None:
        if self._socket is not None:
            with suppress(OSError):
                self._socket.shutdown(socket.SHUT_RDWR)
            self._socket.close()
            self._socket = None

    def close(self) -> None:
        with self._lock:
            self._close_socket()
            for stream in (self._stdin, self._stdout):
                if stream is not None:
                    with suppress(OSError):
                        stream.close()
            self._stale_process = self._remote_process or self._stale_process
            self._remote_process = None
            if self._client is not None:
                try:
                    self._release_stale_process(self._client)
                except Exception:
                    # Retain the identity for cleanup on the next SSH session.
                    # Do not replace the original startup/transport error.
                    LOGGER.warning(
                        "Board TCP worker cleanup deferred until reconnect", exc_info=True,
                    )
                finally:
                    self._client.close()
            self._client = self._stdin = self._stdout = self._launch_stdout = None
            self._cached = {}
            self._capture_result = None
