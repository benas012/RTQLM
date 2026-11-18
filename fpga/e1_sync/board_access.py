"""Task-local pinned SSH access to the user's replacement SD card."""
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent
HOST = "rp-f0f9eb.local"
# Replacement SD key observed on the direct Ethernet peer MAC 00:26:32:f0:f9:eb.
KEY = "AAAAC3NzaC1lZDI1NTE5AAAAICfyBy0AQ+Xd9it9oNfKJmSaXaLtPA7oOyt82JVtTLuI"


def connect():
    hosts = ROOT / "known_hosts"
    if not hosts.exists():
        hosts.write_text(f"{HOST} ssh-ed25519 {KEY}\n")
    client = paramiko.SSHClient()
    client.load_host_keys(str(hosts))
    # The burner card uses the documented factory root login; existing keys
    # remain enabled. This does not change the application's authentication.
    client.connect(HOST, username="root", password="root", timeout=10,
                   auth_timeout=10, banner_timeout=10)
    return client


def run(client, command, timeout=30):
    _, out, err = client.exec_command(command, timeout=timeout)
    result, errors = out.read().decode(), err.read().decode()
    if out.channel.recv_exit_status():
        raise RuntimeError(result + errors)
    return result


if __name__ == "__main__":
    with connect() as client:
        print(run(client, "uname -a; cat /opt/redpitaya/version.txt; "
                  "/opt/redpitaya/bin/profiles -f; cat /tmp/loaded_fpga.inf; "
                  "df -h / /opt/redpitaya /root; "
                  "ps -eo pid,comm,args | grep -E 'rp-nullifier|rp-buffered|ring-server|streaming-server' | grep -v grep || true"))
