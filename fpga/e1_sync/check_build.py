"""Gate deployment on routed timing and preserve independently usable images."""
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "upstream/prj/v0.94/out"
final_timing = OUT / "red_pitaya_top_timing_summary_postroute_physopted.rpt"
timing = final_timing if final_timing.exists() else OUT / "red_pitaya_top_timing_summary_routed.rpt"
text = timing.read_text()
if "All user specified timing constraints are met." not in text:
    raise RuntimeError("Routed design has not met timing")
match = re.search(r"WNS\(ns\).*?\n\s*-+.*?\n\s*([-\d.]+)\s+([-\d.]+)\s+(\d+)\s+(\d+)\s+([-\d.]+)",
                  text, re.DOTALL)
if not match:
    raise RuntimeError("Could not parse routed timing summary")
wns, tns, failing, endpoints, whs = match.groups()
if float(wns) < 0 or float(tns) < 0 or int(failing) or float(whs) < 0:
    raise RuntimeError("Routed timing contains setup/hold violations")
dest = ROOT / "image"
dest.mkdir(exist_ok=True)
hashes = {}
for name in ("red_pitaya.bit", "red_pitaya.bin", timing.name,
             "red_pitaya_top_utilization_placed.rpt", "red_pitaya_top_drc_routed.rpt"):
    source = OUT / name
    if not source.is_file():
        raise RuntimeError("Build artifact missing: " + name)
    if source.suffix in (".bin", ".bit") and source.stat().st_mtime < timing.stat().st_mtime:
        raise RuntimeError("Image predates the final timing report: " + name)
    shutil.copy2(source, dest / name)
    hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
for name in ("e1_synchronizer_timing.rpt", "red_pitaya_cdc.rpt"):
    if (OUT / name).exists():
        shutil.copy2(OUT / name, dest / name)
report = {"baseRelease": "Release_2026.1", "baseCommit": "c3fc4f6ebebf9b24f008ecb606eb7a3a23f62422",
          "tool": "Vivado 2025.1", "part": "xc7z010clg400-1", "define": "RTQLM_E1_SYNC",
          "disabledFeatures": ["DDR waveform playback to DAC outputs"],
          "taggedAcquisitionUsesRawCounts": True,
          "sourcePatchSha256": hashlib.sha256((ROOT / "upstream.patch").read_bytes()).hexdigest(),
          "timingReport": timing.name,
          "routedTimingMet": True, "wnsNs": float(wns), "tnsNs": float(tns),
          "setupFailingEndpoints": int(failing), "setupEndpoints": int(endpoints),
          "whsNs": float(whs), "artifactsSha256": hashes}
(ROOT / "build-results.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
