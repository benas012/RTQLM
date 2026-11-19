"""Stage the checked original-125-14 image on an offline SD boot partition.

Default is preview only. --install changes only fpga/z10_125/v0.94 image files,
after a verified backup. It does not load an FPGA or replace a device tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL = "z10_125"
PAYLOAD = ("fpga.bin", "fpga.bit", "git_info.txt")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_bundle(bundle: Path) -> dict:
    manifest = json.loads((bundle / "manifest.json").read_text())
    if (
        manifest.get("targetModel") != MODEL
        or manifest.get("part") != "xc7z010clg400-1"
        or manifest.get("routedTimingMet") is not True
        or manifest.get("simulationPassed") is not True
    ):
        raise RuntimeError(
            "An original-125-14 bundle with passing build/simulation checks is required"
        )
    for name in PAYLOAD:
        if sha(bundle / name) != manifest["payloadSha256"][name]:
            raise RuntimeError("Bundle checksum mismatch: " + name)
    return manifest


def durable_copy(source: Path, target: Path) -> None:
    with source.open("rb") as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())
    if sha(source) != sha(target):
        raise RuntimeError("SD copy verification failed: " + str(target))


def stage(boot_root: Path, bundle: Path, *, install: bool = False) -> dict:
    manifest = checked_bundle(bundle)
    boot = boot_root.resolve(strict=True)
    expected = boot / "fpga" / MODEL / "v0.94"
    target = expected.resolve(strict=True)
    if target != expected or not target.is_relative_to(boot):
        raise RuntimeError("Original-board FPGA target is redirected or escapes the boot partition")
    for name in (*PAYLOAD, "fpga.dtbo"):
        path = target / name
        if not path.is_file() or not path.resolve().is_relative_to(target):
            raise RuntimeError(
                "Expected original-board file is missing or redirected: " + str(path)
            )
    dtbo_hash = sha(target / "fpga.dtbo")
    changes = [name for name in PAYLOAD if sha(target / name) != manifest["payloadSha256"][name]]
    result = {
        "targetModel": MODEL,
        "target": str(target),
        "filesToChange": changes,
        "deviceTreePreserved": True,
        "hardwareTested": False,
        "installed": False,
    }
    if not install or not changes:
        result["installed"] = install
        return result
    identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backup_root = (boot / "rtqlm-backups").resolve()
    if not backup_root.is_relative_to(boot):
        raise RuntimeError("Backup target escapes the selected boot partition")
    backup = (backup_root / MODEL / identifier).resolve()
    if not backup.is_relative_to(boot):
        raise RuntimeError("Model backup target escapes the selected boot partition")
    backup.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name in (*PAYLOAD, "fpga.dtbo"):
        durable_copy(target / name, backup / name)
        hashes[name] = sha(backup / name)
    (backup / "backup.json").write_text(
        json.dumps({"target": str(target), "sha256": hashes}, indent=2) + "\n"
    )
    staged: dict[str, Path] = {}
    replaced: list[str] = []
    try:
        for name in changes:
            temporary = target / (name + ".rtqlm-new-" + identifier)
            staged[name] = temporary
            durable_copy(bundle / name, temporary)
        for name in changes:
            staged[name].replace(target / name)
            replaced.append(name)
        for name in PAYLOAD:
            if sha(target / name) != manifest["payloadSha256"][name]:
                raise RuntimeError("Installed file checksum mismatch: " + name)
        if sha(target / "fpga.dtbo") != dtbo_hash:
            raise RuntimeError("Original-board device tree changed during installation")
    except Exception:
        for name in replaced:
            rollback = target / (name + ".rtqlm-rollback-" + identifier)
            durable_copy(backup / name, rollback)
            rollback.replace(target / name)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
    result.update(installed=True, backup=str(backup))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--boot-root",
        type=Path,
        required=True,
        help="Mounted SD boot partition, e.g. E:/ or /opt/redpitaya",
    )
    parser.add_argument("--bundle", type=Path, default=ROOT / "image")
    parser.add_argument("--install", action="store_true", help="Apply the previewed changes")
    args = parser.parse_args()
    print(json.dumps(stage(args.boot_root, args.bundle, install=args.install), indent=2))


if __name__ == "__main__":
    main()
