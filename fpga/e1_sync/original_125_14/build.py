"""Build the E1-modified v0.94 image for original STEMlab 125-14 / z10_125.

This creates an isolated Z10 project. It does not connect to or flash a board.
The original board's installed device-tree overlay must be retained on deployment.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent
REVISION = "c3fc4f6ebebf9b24f008ecb606eb7a3a23f62422"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vivado", type=Path, default=Path("C:/Programs/Xilinx/2025.1/Vivado/bin/vivado.bat")
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Reuse completed isolated IP runs")
    args = parser.parse_args()
    # Vivado's generated IP paths must fit Windows' legacy path-length limits.
    source = ROOT.parents[2] / "tmp/o125"
    if not source.exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--no-hardlinks",
                "--no-checkout",
                str(PARENT / "upstream"),
                str(source),
            ],
            check=True,
        )
        subprocess.run(["git", "checkout", "--detach", REVISION], cwd=source, check=True)
    subprocess.run(
        [sys.executable, str(PARENT / "prepare_project.py"), "--source", str(source)], check=True
    )
    if args.prepare_only:
        return
    if not args.vivado.is_file():
        raise RuntimeError("Vivado 2025.1 executable was not found")
    # Same official hardware target for original and Gen 2 Zynq-7010 boards.
    # The z10_125 deployment identity is recorded in the checked build manifest.
    command = [
        str(args.vivado),
        "-mode",
        "batch",
        "-nojournal",
        "-log",
        str(ROOT / "vivado.log"),
        "-source",
        str(ROOT / ("resume.tcl" if args.resume else "build.tcl")),
        "-tclargs",
    ]
    command += [str(source)] if args.resume else ["v0.94", "RTQLM_E1_SYNC"]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (ROOT / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            command,
            cwd=source,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            check=True,
        )
        subprocess.run(
            [
                str(args.vivado),
                "-mode",
                "batch",
                "-nojournal",
                "-log",
                str(ROOT / "verify.log"),
                "-source",
                str(ROOT / "verify.tcl"),
                "-tclargs",
                str(ROOT),
                str(source),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            check=True,
        )
    print("Original 125-14 Z10 build completed; timing and packaging checks are still required.")


if __name__ == "__main__":
    main()
