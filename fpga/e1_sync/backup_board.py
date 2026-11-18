import hashlib
import json
from pathlib import Path

from board_access import ROOT, connect, run

target = ROOT / "backup"
target.mkdir(exist_ok=True)
if (target / "sha256.json").exists():
    raise SystemExit("An original image backup already exists; preserving it unchanged.")
base = "/opt/redpitaya/fpga/z10_125_pro_v2/v0.94"
with connect() as client:
    info = run(client, "cat /opt/redpitaya/version.txt; cat /tmp/loaded_fpga.inf; "
               "/opt/redpitaya/bin/profiles -f; cat /proc/cmdline")
    (target / "board.txt").write_text(info)
    with client.open_sftp() as sftp:
        hashes = {}
        for name in ("fpga.bin", "fpga.bit", "fpga.dtbo", "fpga.dtso", "git_info.txt"):
            sftp.get(base + "/" + name, str(target / name))
            hashes[name] = hashlib.sha256((target / name).read_bytes()).hexdigest()
        sftp.get("/opt/redpitaya/sbin/overlay.sh", str(target / "overlay.sh"))
    (target / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    print(info)
    print("Saved and hashed all factory FPGA files in", target)
