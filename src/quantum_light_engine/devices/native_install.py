"""Install a missing ARM worker using sources shipped with the engine."""

from __future__ import annotations

import hashlib
import io
import logging
import shlex
import tarfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

LOGGER = logging.getLogger(__name__)
SOURCES = (
    "Makefile", "nullifier.h", "nullifier.c", "commands.c", "stream.h", "stream.c",
    "acquire_rp.cpp", "buffered_worker.c", "vendor/cJSON.c", "vendor/cJSON.h",
)


def _run(client: Any, script: str, timeout: int = 300) -> str:
    # Merge compiler diagnostics into stdout to drain both without an SSH window deadlock.
    _, stdout, _ = client.exec_command(
        "bash -c " + shlex.quote("exec 2>&1\nset -eu\n" + script), timeout=timeout,
    )
    stdout.channel.settimeout(timeout)
    output = stdout.read().decode("utf-8", errors="replace")
    if stdout.channel.recv_exit_status():
        raise RuntimeError("Red Pitaya C processor setup failed: " + output[-4000:].strip())
    return str(output).strip()


def source_directory() -> Path:
    bundled = Path(__file__).resolve().parents[1] / "native_sources"
    checkout = Path(__file__).resolve().parents[3] / "native" / "redpitaya_nullifiers"
    for directory in (bundled, checkout):
        if all((directory / name).is_file() for name in SOURCES):
            return directory
    raise RuntimeError("ARM processor sources are missing; reinstall the engine package")


def ensure_native_worker(
    client: Any, executable: str, target: str, *, auto_install: bool = True,
) -> str:
    """Check on every new SSH session; preserve every existing executable."""
    if target not in {"rp-nullifierd", "rp-buffered-board"}:
        raise ValueError("Unsupported ARM processor build target")
    path = PurePosixPath(executable)
    if not path.is_absolute() or ".." in path.parts or path.name in {"", "/"}:
        raise ValueError("ARM processor executable must be an absolute file path")
    quoted = shlex.quote(str(path))
    if _run(client, f"if test -x {quoted}; then printf PRESENT; else printf MISSING; fi", 15) == (
        "PRESENT"
    ):
        return "present"
    if not auto_install:
        raise RuntimeError(f"C processor is missing at {executable}; automatic installation is off")

    directory = source_directory()
    files = {name: (directory / name).read_bytes() for name in SOURCES}
    digest = hashlib.sha256()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        for name, data in files.items():
            digest.update(name.encode() + b"\0" + data)
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            bundle.addfile(info, io.BytesIO(data))
    version = digest.hexdigest()[:24]
    installation_id = uuid.uuid4().hex
    upload = f"/tmp/rtqlm-native-{installation_id}.tar.gz"
    link = shlex.quote(executable + '.install-' + installation_id)
    release = f"/root/rtqlm-nullifiers/engine-builds/{version}/{installation_id}"
    LOGGER.info("Installing missing C processor on Red Pitaya: %s", executable)
    with client.open_sftp() as sftp:
        archive.seek(0)
        sftp.putfo(archive, upload)
    # Serialize installers from different engine sessions. Build away from the live
    # executable and publish only after a successful link; never restart acquisition here.
    script = f"""
trap {shlex.quote('rm -f -- ' + shlex.quote(upload) + ' ' + link)} EXIT
for tool in make gcc g++ tar flock; do
    command -v "$tool" >/dev/null || {{ echo "Missing build tool: $tool"; exit 1; }}
done
test -f /opt/redpitaya/include/rp.h || {{ echo 'Red Pitaya SDK headers are missing'; exit 1; }}
mkdir -p /root/rtqlm-nullifiers/engine-builds
exec 9>/root/rtqlm-nullifiers/engine-builds/install.lock
flock -w 240 9
if test -x {quoted}; then exit 0; fi
mkdir -p {shlex.quote(release)}
tar -xzf {shlex.quote(upload)} -C {shlex.quote(release)}
make -C {shlex.quote(release)} -j2 BENCHMARKS=/nonexistent build/{target}
test -x {shlex.quote(release + '/build/' + target)}
mkdir -p {shlex.quote(str(path.parent))}
ln -s {shlex.quote(release + '/build/' + target)} {link}
mv -Tf {link} {quoted}
"""
    _run(client, script)
    LOGGER.info("C processor installed successfully: %s (source %s)", executable, version)
    return "installed"
