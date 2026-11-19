"""Package only a routed, simulated original-125-14 implementation."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parents[2] / "tmp/o125"
OUT = SOURCE / "prj/v0.94/out"
REVISION = "c3fc4f6ebebf9b24f008ecb606eb7a3a23f62422"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip()
    if revision != REVISION:
        raise RuntimeError("Original-board sources differ from the pinned release")
    patch = subprocess.check_output(["git", "diff", "HEAD", "--"], cwd=SOURCE, text=True)
    for name in ("rp_e1_tag.sv", "rp_e1_fast_add.sv"):
        relative = "prj/v0.94/rtl/" + name
        patch += "diff --git a/" + relative + " b/" + relative + "\nnew file mode 100644\n"
        patch += "".join(
            difflib.unified_diff(
                [],
                (SOURCE / relative).read_text().splitlines(True),
                fromfile="/dev/null",
                tofile="b/" + relative,
            )
        )
    if patch != (ROOT.parent / "upstream.patch").read_text():
        raise RuntimeError("Original-board sources contain changes outside the checked E1 patch")
    (ROOT / "source.patch").write_text(patch, newline="\n")
    timing = OUT / "red_pitaya_top_timing_summary_postroute_physopted.rpt"
    text = timing.read_text()
    if "All user specified timing constraints are met." not in text:
        raise RuntimeError("Routed design failed timing")
    match = re.search(
        r"WNS\(ns\).*?\n\s*-+.*?\n\s*([-\d.]+)\s+([-\d.]+)\s+(\d+)\s+(\d+)\s+([-\d.]+)",
        text,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("Final timing summary could not be parsed")
    wns, tns, failing, endpoints, whs = match.groups()
    if min(float(wns), float(tns), float(whs)) < 0 or int(failing):
        raise RuntimeError("Original-board routed design has setup/hold violations")
    proof_file = OUT / "original_125_14_implementation.txt"
    proof = dict(line.split("=", 1) for line in proof_file.read_text().splitlines())
    if (
        proof.get("targetModel") != "z10_125"
        or proof.get("part") != "xc7z010clg400-1"
        or set(proof.get("defines", "").split()) != {"Z10", "RTQLM_E1_SYNC"}
        or proof.get("e1PackagePin") != "G17"
        or proof.get("e1IOStandard") != "LVCMOS33"
        or any(
            proof.get(key, "").upper() not in {"TRUE", "1"}
            for key in ("e1MetaAsync", "e1SyncAsync")
        )
        or float(proof.get("e1SynchronizerSlackNs", "-1")) < 0
    ):
        raise RuntimeError("Routed target/pin/synchronizer proof does not match original 125-14")
    simulation = json.loads((ROOT / "simulation-results.json").read_text())
    if simulation.get("targetModel") != "z10_125" or simulation.get("allPassed") is not True:
        raise RuntimeError("Fresh original-board simulations have not passed")
    generated = ROOT / "simulation/generated_asg_equivalence.sv"
    generated.parent.mkdir(exist_ok=True)
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
    simulated_inputs = {
        "rp_e1_tag.sv": SOURCE / "prj/v0.94/rtl/rp_e1_tag.sv",
        "rp_e1_fast_add.sv": SOURCE / "prj/v0.94/rtl/rp_e1_fast_add.sv",
        "tb_e1_tag.sv": ROOT.parent / "sim/tb_e1_tag.sv",
        "tb_fast_add.sv": ROOT.parent / "sim/tb_fast_add.sv",
        "generated_asg_equivalence.sv": generated,
    }
    if {case["name"] for case in simulation["cases"]} != {
        "e1_tag", "fast_add", "asg_equivalence"
    }:
        raise RuntimeError("Required simulation cases are missing")
    for case in simulation["cases"]:
        if case["passed"] is not True:
            raise RuntimeError("Simulation failed: " + case["name"])
        for name, expected in case["inputSha256"].items():
            if name not in simulated_inputs or sha(simulated_inputs[name]) != expected:
                raise RuntimeError("Simulation input changed since the passing run: " + name)
    dest = ROOT / "image"
    dest.mkdir(exist_ok=True)
    for source_name, target_name in (
        ("red_pitaya.bit", "fpga.bit"),
        ("red_pitaya.bin", "fpga.bin"),
    ):
        path = OUT / source_name
        if path.stat().st_mtime < timing.stat().st_mtime:
            raise RuntimeError("Image predates final timing: " + source_name)
        if proof_file.stat().st_mtime < path.stat().st_mtime:
            raise RuntimeError("Image was not verified after generation: " + source_name)
        shutil.copy2(path, dest / target_name)
    info = (
        "RTQLM E1 synchronized raw acquisition for original STEMlab 125-14\n"
        f"Commit {REVISION}\nRelease_2026.1 + RTQLM_E1_SYNC\n"
        "Target z10_125; Vivado 2025.1; hardware validation pending\n"
    )
    (dest / "git_info.txt").write_text(info, newline="\n")
    names = [
        timing.name,
        "red_pitaya_top_utilization_placed.rpt",
        "red_pitaya_top_drc_routed.rpt",
        "e1_synchronizer_timing.rpt",
        "red_pitaya_cdc.rpt",
        "original_125_14_io.rpt",
        proof_file.name,
    ]
    for name in names:
        shutil.copy2(OUT / name, dest / name)
    modified = subprocess.check_output(
        ["git", "diff", "--name-only", "HEAD"], cwd=SOURCE, text=True
    ).splitlines()
    modified += ["prj/v0.94/rtl/rp_e1_tag.sv", "prj/v0.94/rtl/rp_e1_fast_add.sv"]
    hashes = {name: sha(SOURCE / name) for name in sorted(set(modified))}
    (dest / "source-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    smoke_file = ROOT / "sd-install-smoke-results.json"
    smoke = json.loads(smoke_file.read_text()) if smoke_file.exists() else None
    payload_hashes = {name: sha(dest / name) for name in ("fpga.bin", "fpga.bit", "git_info.txt")}
    if smoke is not None:
        if (
            smoke.get("passed") is not True
            or smoke.get("payloadSha256") != payload_hashes
            or smoke.get("installerSha256") != sha(ROOT / "install_sd.py")
        ):
            raise RuntimeError("Standalone SD installer evidence is stale or failed")
        shutil.copy2(smoke_file, dest / smoke_file.name)
        names.append(smoke_file.name)
    manifest = {
        "targetModel": "z10_125",
        "targetBoard": "Original STEMlab 125-14, Zynq 7010",
        "baseRelease": "Release_2026.1",
        "baseCommit": REVISION,
        "sourcePatchSha256": sha(ROOT / "source.patch"),
        "tool": "Vivado 2025.1",
        "part": "xc7z010clg400-1",
        "defines": ["Z10", "RTQLM_E1_SYNC"],
        "routedTimingMet": True,
        "wnsNs": float(wns),
        "tnsNs": float(tns),
        "whsNs": float(whs),
        "setupFailingEndpoints": int(failing),
        "setupEndpoints": int(endpoints),
        "simulationPassed": True,
        "sdInstallerSmokePassed": smoke is not None,
        "hardwareTested": False,
        "physicalE1TimingOffsetVerified": False,
        "e1SynchronizerSlackNs": float(proof["e1SynchronizerSlackNs"]),
        "e1Pin": {
            "header": "E1",
            "physicalPin": 3,
            "signal": "DIO0_P",
            "fpgaPad": "G17",
            "ioStandard": "LVCMOS33",
        },
        "wordFormat": {"adcBits": [0, 13], "e1Bit": 14, "markerBit": 15},
        "disabledFeatures": ["DDR waveform playback to DAC outputs"],
        "taggedAcquisitionUsesRawCounts": True,
        "deviceTree": "Keep the original board's factory fpga.dtbo; no device tree supplied",
        "payloadSha256": payload_hashes,
        "evidenceSha256": {name: sha(dest / name) for name in [*names, "source-sha256.json"]},
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.copy2(ROOT / "simulation-results.json", dest / "simulation-results.json")
    archive = ROOT / "rtqlm-e1-original-125-14.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(dest.iterdir()):
            if path.is_file():
                bundle.write(path, "image/" + path.name)
        for name in (
            "README.md",
            "install_sd.py",
            "build.py",
            "build.tcl",
            "resume.tcl",
            "simulate.py",
            "verify.tcl",
            "package.py",
        ):
            bundle.write(ROOT / name, name)
        bundle.write(ROOT / "source.patch", "source/upstream.patch")
        for name in ("rp_e1_tag.sv", "rp_e1_fast_add.sv"):
            bundle.write(ROOT.parent / "rtl" / name, "source/" + name)
    print(json.dumps(manifest, indent=2))
    print("Packaged:", archive)


if __name__ == "__main__":
    main()
