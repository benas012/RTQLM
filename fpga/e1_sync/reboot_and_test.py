"""Verify the authorized SD installation survives a real board reboot."""
import json
import subprocess
import sys
import time

import numpy as np
import paramiko

from board_access import ROOT, connect, run
from capture import capture

with connect() as client:
    previous = run(client, "cat /proc/sys/kernel/random/boot_id").strip()
    run(client, "nohup /bin/sh -c 'sleep 1; sync; /sbin/shutdown -r now' "
        ">/root/rtqlm-e1-sync/reboot.log 2>&1 </dev/null &", timeout=5)
print("Reboot requested", flush=True)
deadline = time.monotonic() + 150
time.sleep(3)
while True:
    try:
        with connect() as client:
            current = run(client, "cat /proc/sys/kernel/random/boot_id", timeout=5).strip()
            if current == previous:
                raise ConnectionError("Waiting for a new boot")
            state = [json.loads(line) for line in run(client, "/root/rtqlm-e1-sync/inspect-state").splitlines()]
            if state[-1] != {"fpgaSignature": 0x45513101, "e1Control": 0x500}:
                raise RuntimeError("Unexpected FPGA signature/default mode after reboot")
            hashes = run(client, "sha256sum /opt/redpitaya/fpga/z10_125_pro_v2/v0.94/fpga.bin "
                         "/opt/redpitaya/fpga/z10_125_pro_v2/v0.94/fpga.bit")
            expected = json.loads((ROOT / "deployment.json").read_text())["installed"]
            for line in hashes.splitlines():
                digest, name = line.split()
                if digest != expected[name.rsplit("/", 1)[-1]]:
                    raise RuntimeError("Installed SD image hash changed after reboot")
        break
    except (OSError, EOFError, ConnectionError, paramiko.SSHException) as error:
        if time.monotonic() >= deadline:
            raise TimeoutError("Red Pitaya did not reconnect after reboot") from error
        print("Waiting for reboot:", type(error).__name__, flush=True)
        time.sleep(3)
print("Reconnected with a new boot ID and custom FPGA signature", flush=True)
subprocess.run([sys.executable, str(ROOT / "restore_outputs.py")], check=True)
a, b, e, diagnostic = capture(6250000, diagnostic=True)
del a, b, e
a, b, e, metadata = capture()
if not np.all(e == 1):
    raise RuntimeError("E1 pin 3 was not continuously HIGH after reboot")
metadata["e1High"] = int(e.sum())
metadata["e1Low"] = int(len(e)-e.sum())
for name, signal in (("CH1",a),("CH2",b)):
    reduced=signal[::1250].astype(np.float64)
    spectrum=np.abs(np.fft.rfft((reduced-reduced.mean())*np.hanning(len(reduced))))
    frequency=float(np.argmax(spectrum[1:])+1)*100000/len(reduced)
    metadata[name+"Range"]=[int(signal.min()),int(signal.max())]
    metadata[name+"ApproxFrequencyHz"]=frequency
    if np.ptp(signal.astype(np.int32)) < 100 or not 900 <= frequency <= 1100:
        raise RuntimeError("Missing physical loopback waveform after reboot: "+name)
np.savez(ROOT / "captures/real-50ms-after-reboot.npz", CH1=a, CH2=b, E1_PIN3=e,
         metadata=json.dumps(metadata))
with connect() as client:
    final = [json.loads(line) for line in run(client, "/root/rtqlm-e1-sync/inspect-state").splitlines()]
    mount = run(client,"findmnt -n -o OPTIONS /opt/redpitaya").strip()
if final[-1] != {"fpgaSignature":0x45513101,"e1Control":0x500}:
    raise RuntimeError("Capture did not restore normal acquisition mode")
if final[-2] != {"e1Pin3State":1,"e1Pin3Direction":0}:
    raise RuntimeError("E1 pin state or direction changed")
for output in final[:2]:
    if (not output["enabled"] or abs(output["frequency"]-1000)>.1
            or abs(output["amplitude"]-1)>.0001 or abs(output["offset"])>.0001):
        raise RuntimeError("Saved generator settings were not restored")
report = {"bootIdBefore": previous, "bootIdAfter": current, "stateOnBoot": state,
          "installedSha256": hashes, "diagnostic": diagnostic, "realCapture": metadata,
          "finalState": final, "bootMountOptions":mount, "passed": True}
(ROOT / "reboot-results.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
