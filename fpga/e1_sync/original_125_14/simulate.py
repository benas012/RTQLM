"""Run E1 packing, arithmetic, and original/modified ASG equivalence simulations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parents[2] / "tmp/o125"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vivado-bin", type=Path, default=Path("C:/Programs/Xilinx/2025.1/Vivado/bin")
    )
    args = parser.parse_args()
    sim = ROOT / "simulation"
    sim.mkdir(exist_ok=True)
    result = ROOT / "simulation-results.json"
    result.unlink(missing_ok=True)
    generated = sim / "generated_asg_equivalence.sv"
    subprocess.run(
        [
            sys.executable,
            str(ROOT.parent / "sim/prepare_asg_equivalence.py"),
            "--source",
            str(SOURCE),
            "--output",
            str(generated),
        ],
        check=True,
    )
    files = {
        "e1_tag": [SOURCE / "prj/v0.94/rtl/rp_e1_tag.sv", ROOT.parent / "sim/tb_e1_tag.sv"],
        "fast_add": [
            SOURCE / "prj/v0.94/rtl/rp_e1_fast_add.sv",
            ROOT.parent / "sim/tb_fast_add.sv",
        ],
        "asg_equivalence": [generated],
    }
    cases = []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for name, sources in files.items():
        log_path = sim / (name + ".log")
        snapshot = name + "_sim"
        with log_path.open("w", encoding="utf-8") as log:
            for tool, arguments in (
                ("xvlog", ["-sv", *map(str, sources)]),
                ("xelab", ["tb_" + name, "-s", snapshot]),
                ("xsim", [snapshot, "-runall"]),
            ):
                subprocess.run(
                    [str(args.vivado_bin / (tool + ".bat")), *arguments],
                    cwd=sim,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                    check=True,
                )
        passed = [
            line
            for line in log_path.read_text(errors="replace").splitlines()
            if line.startswith("PASS:")
        ]
        if len(passed) != 1:
            raise RuntimeError("Simulation did not report a unique pass: " + str(log_path))
        cases.append(
            {
                "name": name,
                "passed": True,
                "result": passed[0],
                "inputSha256": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources
                },
            }
        )
        print(passed[0], flush=True)
    result.write_text(
        json.dumps(
            {
                "targetModel": "z10_125",
                "tool": "Vivado Simulator 2025.1",
                "allPassed": True,
                "cases": cases,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
