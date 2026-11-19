import json

from board_access import ROOT, connect, run

with connect() as client:
    details = {}
    for name, command in {
        "model": "/opt/redpitaya/bin/profiles -f",
        "bootMount": "findmnt -n -o SOURCE,TARGET,OPTIONS /opt/redpitaya",
        "memory": "head -3 /proc/meminfo",
        "ethernet": "ethtool eth0 | awk '/Speed:|Duplex:|Link detected:/'",
        "cpuClock": "awk '/cpu_6or4x|cpu_3or2x/ {print}' /sys/kernel/debug/clk/clk_summary",
    }.items():
        details[name] = run(client, command).strip()
    (ROOT / "board-details.json").write_text(json.dumps(details, indent=2) + "\n")
    print(json.dumps(details, indent=2))
