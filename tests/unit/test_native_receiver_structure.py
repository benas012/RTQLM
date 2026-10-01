from __future__ import annotations

from pathlib import Path

MAX_RECEIVER_FILE_LINES = 400
RECEIVER_PACKAGE = Path(__file__).resolve().parents[2] / "apps" / "native_receiver"


def test_native_receiver_modules_remain_focused() -> None:
    oversized = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in RECEIVER_PACKAGE.glob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > MAX_RECEIVER_FILE_LINES
    }

    assert oversized == {}, f"Split receiver modules that exceed {MAX_RECEIVER_FILE_LINES} lines"
