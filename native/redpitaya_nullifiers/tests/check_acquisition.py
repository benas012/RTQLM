"""Exercise the real hardware bridge against an SDK test double, without a board."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

BINARY = Path(sys.argv.pop(1)).resolve()
IMAGE = BINARY.parent / "fpga.inf"
LOG = BINARY.parent / "calls.log"
SETTINGS = {"command": "configure", "config": {
    "modeShape": "identity", "modeSamples": 4, "captureSamples": 64,
    "channelBDelaySamples": 0, "collectionGuardSeconds": 0,
    "collectionEndGuardSamples": 0, "triggerTimeoutSeconds": 0.01,
}}


def run(mode: str = "", image: str = "v0.94", captures: int = 1) -> tuple[list, list[str]]:
    IMAGE.write_text(image)
    LOG.write_text("")
    commands = [SETTINGS] + [{"command": "capture"}] * captures
    result = subprocess.run(
        [str(BINARY)], input="\n".join(json.dumps(item) for item in commands) + "\n",
        text=True, capture_output=True, timeout=10, check=True,
        env={**os.environ, "NF_MOCK_MODE": mode, "NF_MOCK_LOG": str(LOG)},
    )
    return [json.loads(line) for line in result.stdout.splitlines()], LOG.read_text().splitlines()


class Acquisition(unittest.TestCase):
    def test_both_channels_are_captured_and_resources_are_released(self) -> None:
        replies, calls = run()
        self.assertTrue(replies[-1]["ok"], replies)
        self.assertIn("fill 0 -1", calls)
        self.assertIn("fill 1 -1", calls)
        self.assertIn("read 0 100", calls)
        self.assertIn("read 1 101", calls)
        self.assertIn("factory_calibration -1 -1", calls)
        self.assertEqual(calls[-3:], ["enable 0 0", "enable 1 0", "release -1 -1"])

    def test_every_capture_rearms_the_hardware(self) -> None:
        replies, calls = run(captures=2)
        self.assertTrue(all(reply["ok"] for reply in replies))
        self.assertEqual(calls.count("reset -1 -1"), 2)
        self.assertEqual(calls.count("init -1 0"), 1)
        self.assertEqual(calls.count("factory_calibration -1 -1"), 1)
        self.assertEqual(calls.count("release -1 -1"), 1)

    def test_wrong_image_does_not_touch_sdk(self) -> None:
        replies, calls = run(image="stream_app")
        self.assertFalse(replies[-1]["ok"])
        self.assertIn("v0.94", replies[-1]["error"])
        self.assertEqual(calls, [])

    def test_wrong_board_model_is_rejected_before_acquisition(self) -> None:
        replies, calls = run(mode="wrong_model")
        self.assertFalse(replies[-1]["ok"])
        self.assertNotIn("start -1 -1", calls)
        self.assertEqual(calls[-1], "release -1 -1")

    def test_dma_capacity_checked_before_channels_enabled(self) -> None:
        replies, calls = run(mode="small_memory")
        self.assertFalse(replies[-1]["ok"])
        self.assertNotIn("enable 0 1", calls)
        self.assertEqual(calls[-1], "release -1 -1")

    def test_second_channel_enable_failure_cleans_up_first(self) -> None:
        replies, calls = run(mode="enable_b_failure")
        self.assertFalse(replies[-1]["ok"])
        self.assertEqual(calls[-2:], ["enable 0 0", "release -1 -1"])

    def test_second_channel_timeout_stops_and_disables_both(self) -> None:
        replies, calls = run(mode="timeout")
        self.assertFalse(replies[-1]["ok"])
        self.assertIn("timeout", replies[-1]["error"])
        self.assertEqual(calls[-4:], ["stop -1 -1", "enable 0 0", "enable 1 0", "release -1 -1"])

    def test_short_capture_is_excluded(self) -> None:
        replies, calls = run(mode="short_read")
        self.assertFalse(replies[-1]["ok"])
        self.assertIn("short DMA", replies[-1]["error"])
        self.assertEqual(calls[-1], "release -1 -1")

    def test_termination_during_capture_cleans_up_both_channels(self) -> None:
        IMAGE.write_text("v0.94")
        LOG.write_text("")
        settings = {"command": "configure", "config": {
            **SETTINGS["config"], "triggerTimeoutSeconds": 5,
        }}
        process = subprocess.Popen(
            [str(BINARY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={**os.environ, "NF_MOCK_MODE": "timeout", "NF_MOCK_LOG": str(LOG)},
        )
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps(settings) + '\n{"command":"capture"}\n')
            process.stdin.flush()
            deadline = time.monotonic() + 3
            while "start -1 -1" not in LOG.read_text():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.005)
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=3)
            calls = LOG.read_text().splitlines()
            self.assertEqual(calls[-4:], ["stop -1 -1", "enable 0 0",
                                         "enable 1 0", "release -1 -1"])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
