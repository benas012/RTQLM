"""Preview a saved native-rate capture without changing its samples."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if (ROOT / ".plotdeps").exists():
    sys.path.insert(0,str(ROOT / ".plotdeps"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "captures/.mplconfig"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

source = ROOT / "captures/real-50ms.npz"
with np.load(source) as data:
    meta = json.loads(str(data["metadata"]))
    rate = meta["sampleRateHz"]
    # A 5 ms view, displayed at 1 us intervals; NPZ remains full rate.
    selection = slice(0, min(meta["count"], int(rate * .005)), 125)
    signals = [data[name][selection] for name in ("CH1", "CH2", "E1_PIN3")]
t = np.arange(len(signals[0])) * 125 / rate * 1000
fig, axes = plt.subplots(3, 1, figsize=(11, 6), sharex=True, layout="constrained")
for ax, signal, name, color in zip(axes, signals, ("CH1", "CH2", "E1 pin 3"),
                                   ("#2071b8", "#c45d22", "#25824b")):
    ax.plot(t, signal, color=color, linewidth=1)
    ax.set_ylabel(name + ("\nADC counts" if name != "E1 pin 3" else "\nlogic level"))
    ax.grid(alpha=.2)
axes[2].set_yticks([0, 1], ["LOW", "HIGH"])
axes[2].set_ylim(-.15, 1.25)
axes[2].set_xlabel("Shared capture time (ms)")
axes[2].set_xlim(0, t[-1])
fig.suptitle("Red Pitaya: CH1, CH2 and E1 pin 3\n"
             "125 MS/s · first 5 ms shown · E1 held HIGH · physical edge offset unmeasured")
fig.savefig(ROOT / "captures/real-50ms.png", dpi=160)
plt.close(fig)
print(ROOT / "captures/real-50ms.png")
