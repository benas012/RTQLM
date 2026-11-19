"""Install/restore the v0.94 image on the explicitly authorized burner SD."""
import argparse
import hashlib
import json
import shlex
from contextlib import contextmanager

from board_access import ROOT, connect, run

DEST = "/opt/redpitaya/fpga/z10_125_pro_v2/v0.94"
BACKUP = "/root/rtqlm-e1-sync/stock-image"
SOURCE_COMMIT = "c3fc4f6ebebf9b24f008ecb606eb7a3a23f62422"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def remote_sha(client, path):
    return run(client, "sha256sum " + shlex.quote(path)).split()[0]


@contextmanager
def writable_boot(client):
    options = run(client, "findmnt -n -o OPTIONS /opt/redpitaya").strip().split(",")
    readonly = "ro" in options
    if readonly:
        run(client, "mount -o remount,rw /opt/redpitaya")
    try:
        yield
    finally:
        run(client, "sync")
        if readonly:
            run(client, "mount -o remount,ro /opt/redpitaya")


def install(restore=False):
    originals = ROOT / "backup"
    with connect() as client:
        model = run(client, "/opt/redpitaya/bin/profiles -f").strip()
        if model != "z10_125_pro_v2":
            raise RuntimeError(f"Unexpected board: {model}")
        run(client, "mkdir -p " + BACKUP)
        with client.open_sftp() as sftp:
            for name in ("fpga.bin", "fpga.bit", "fpga.dtbo", "git_info.txt"):
                # Never replace the preserved original with an installed custom image.
                try:
                    sftp.stat(BACKUP + "/" + name)
                except FileNotFoundError:
                    sftp.put(str(originals / name), BACKUP + "/" + name)
                if remote_sha(client, BACKUP + "/" + name) != sha(originals / name):
                    raise RuntimeError("Stock backup mismatch: " + name)
        if restore:
            files = {name: originals / name for name in ("fpga.bin", "fpga.bit", "git_info.txt")}
        else:
            build = json.loads((ROOT / "build-results.json").read_text())
            if not build["routedTimingMet"] or build["baseCommit"] != SOURCE_COMMIT:
                raise RuntimeError("A successful routed build is required")
            out = ROOT / "image"
            files = {"fpga.bin": out / "red_pitaya.bin", "fpga.bit": out / "red_pitaya.bit"}
            for path in files.values():
                if not path.is_file() or path.stat().st_size < 100_000:
                    raise RuntimeError("Built image missing: " + str(path))
                if sha(path) != build["artifactsSha256"][path.name]:
                    raise RuntimeError("Built image hash mismatch: " + str(path))
            info = ROOT / "git_info.txt"
            info.write_text("RTQLM E1 synchronized raw acquisition\nCommit " + SOURCE_COMMIT +
                            "\nRelease_2026.1 + RTQLM_E1_SYNC\n")
            files["git_info.txt"] = info
        # Preserve the factory device tree: interfaces and memory map are unchanged.
        if remote_sha(client, DEST + "/fpga.dtbo") != sha(originals / "fpga.dtbo"):
            raise RuntimeError("Unexpected installed device tree")
        hashes = {name: sha(path) for name, path in files.items()}
        with writable_boot(client):
            with client.open_sftp() as sftp:
                for name, path in files.items():
                    target = DEST + "/" + name + ".rtqlm-upload"
                    sftp.put(str(path), target)
                    if remote_sha(client, target) != hashes[name]:
                        raise RuntimeError("Upload hash mismatch: " + name)
            for name in files:
                run(client, "mv " + DEST + "/" + name + ".rtqlm-upload " + DEST + "/" + name)
        try:
            print(run(client, "/opt/redpitaya/sbin/overlay.sh v0.94", timeout=45))
        except Exception:
            with writable_boot(client):
                run(client, "cp " + BACKUP + "/fpga.bin " + DEST + "/fpga.bin\n"
                    "cp " + BACKUP + "/fpga.bit " + DEST + "/fpga.bit\n"
                    "cp " + BACKUP + "/git_info.txt " + DEST + "/git_info.txt")
            run(client, "/opt/redpitaya/sbin/overlay.sh v0.94", timeout=45)
            raise
        print(run(client, "cat /tmp/update_fpga.txt; cat /tmp/loaded_fpga.inf"))
        report = {"restoredStock": restore, "model": model, "sourceCommit": SOURCE_COMMIT,
                  "installed": hashes, "stockBackup": BACKUP}
        (ROOT / "deployment.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore-stock", action="store_true")
    install(parser.parse_args().restore_stock)
