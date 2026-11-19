import json

from board_access import ROOT, connect, run

with connect() as client:
    with client.open_sftp() as sftp:
        sftp.put(str(ROOT / "board/inspect_state.cpp"), "/root/rtqlm-e1-sync/inspect_state.cpp")
    run(client, "cd /root/rtqlm-e1-sync && g++ -O2 -std=c++20 -Wall -Wextra -Werror "
        "-I/opt/redpitaya/include inspect_state.cpp -L/opt/redpitaya/lib "
        "-Wl,-rpath,/opt/redpitaya/lib -lrp -o inspect-state", timeout=60)
    text = run(client, "/root/rtqlm-e1-sync/inspect-state")
    print(text)
    path = ROOT / "backup/generator-state.json"
    if not path.exists():
        path.write_text(json.dumps([json.loads(line) for line in text.splitlines()], indent=2) + "\n")
