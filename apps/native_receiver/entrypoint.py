from __future__ import annotations

import argparse

from apps.native_receiver.settings import load_cluster_config, load_settings


def main(argv: list[str] | None = None) -> int:
    """Start the desktop receiver or validate its local startup dependencies."""
    parser = argparse.ArgumentParser(prog="native-receiver")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate settings and dependencies without opening a window",
    )
    args = parser.parse_args(argv)
    if args.check:
        load_settings()
        load_cluster_config()
        print("native receiver startup check passed")
        return 0

    from apps.native_receiver.native_receiver import ReceiverApp

    app = ReceiverApp()
    app.mainloop()
    return 0
