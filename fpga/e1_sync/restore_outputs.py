import json

from board_access import ROOT, connect, run

state = json.loads((ROOT / "backup/generator-state.json").read_text())[:2]
arguments = []
for channel in state:
    if channel["waveform"] != 0:
        raise RuntimeError("Unsupported saved waveform; preserving generator settings")
    arguments += [str(channel[key]) for key in ("frequency", "amplitude", "offset", "phase", "waveform")]
    arguments.append(str(int(channel["enabled"])))
with connect() as client:
    with client.open_sftp() as sftp:
        sftp.put(str(ROOT / "board/restore_outputs.cpp"), "/root/rtqlm-e1-sync/restore_outputs.cpp")
    run(client, "cd /root/rtqlm-e1-sync && g++ -O2 -std=c++20 -Wall -Wextra -Werror "
        "-I/opt/redpitaya/include restore_outputs.cpp -L/opt/redpitaya/lib "
        "-Wl,-rpath,/opt/redpitaya/lib -lrp -o restore-outputs", timeout=60)
    print(run(client, "/root/rtqlm-e1-sync/restore-outputs " + " ".join(arguments)))
