"""Exercise continuous TCP delivery and C calculations against a deterministic ADC."""
from __future__ import annotations

import json
import math
import socket
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path

BINARY = str(Path(sys.argv.pop(1)).resolve())
MAGIC = bytes([255] * 4 + [160] * 4) * 2
HEADER = struct.Struct("<16sQQQQIBBBBq")


class Streaming(unittest.TestCase):
    def setUp(self) -> None:
        self.adc = socket.socket()
        self.adc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.adc.bind(("127.0.0.1", 18900))
        self.adc.listen()
        self.adc.settimeout(3)
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.port = probe.getsockname()[1]
        probe.close()
        self.process = subprocess.Popen(
            [BINARY, "--replay"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        self.sockets = []
        self.sequence = 0

    def command(self, body: dict) -> dict:
        self.process.stdin.write(json.dumps(body) + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def start(self, **changes: object) -> None:
        self.rate = 125000000 / int(changes.get('decimation', 1))
        configured = self.command({"command": "configure", "config": {
            "modeShape": "identity", "modeSamples": 4, "captureSamples": 64,
            "minSamplesPerMode": 2, "channelBDelaySamples": 0,
            "collectionGuardSeconds": 0, "collectionEndGuardSamples": 0,
            "varianceEstimator": "rolling", "varianceWindowModes": 4,
            "historyModes": 100, "windowMode": "periodic",
            "lockSeconds": 32 / 125e6, "collectSeconds": 64 / 125e6,
            "nullifiers": [{"name": "a", "terms": [{"source": "a"}]}],
            **changes,
        }})
        self.assertTrue(configured["ok"], configured)
        started = self.command({"command": "start_stream", "port": self.port})
        self.assertTrue(started["ok"], started)
        self.downstream = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        self.upstream, _ = self.adc.accept()
        self.sockets += [self.downstream, self.upstream]

    def send(self, start: int, count: int, *, lost: int = 0) -> None:
        raw = struct.pack("<" + "h" * count, *[i // 4 for i in range(start, start + count)])
        packet = b""
        for channel in (0, 1):
            header = HEADER.pack(MAGIC, 128 + len(raw), self.sequence, lost,
                                 (128 + len(raw)) * 2, int(self.rate), 14, channel, 16, 0, 0)
            packet += header.ljust(128, b"\0") + raw
        self.sequence += 1
        for chunk in (packet[:17], packet[17:139], packet[139:]):
            self.upstream.sendall(chunk)
        received = b""
        while len(received) < len(packet):
            received += self.downstream.recv(len(packet) - len(received))
        self.assertEqual(received, packet, "native processing must preserve raw ADC bytes")

    def result(self, minimum: int) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            reply = self.command({"command": "latest"})
            if reply["ok"] and reply["stream"]["results"] >= minimum:
                return reply
            time.sleep(0.005)
        self.fail(str(self.command({"command": "status"})))

    def tearDown(self) -> None:
        if self.process.poll() is None:
            stopped = self.command({"command": "stop_stream"})
            self.assertTrue(stopped["ok"])
            self.process.communicate(timeout=3)
        for connection in self.sockets:
            connection.close()
        self.adc.close()

    def test_packet_boundaries_do_not_split_periodic_collections(self) -> None:
        self.start()
        for start in range(0, 256, 32):
            self.send(start, 32)
        result = self.result(2)
        expected = [i * 2 / math.sqrt(125e6) / 8192 for i in range(32, 48)]
        for actual, wanted in zip(result["nullifiers"]["a"], expected, strict=True):
            self.assertAlmostEqual(actual, wanted, places=14)
        self.assertEqual(result["stream"]["packets"], 8)

    def test_continuous_mode_retains_the_mode_grid_across_packets(self) -> None:
        self.start(windowMode="continuous", lockSeconds=0)
        for start in range(0, 512, 32):
            self.send(start, 32)
        result = self.result(1)
        self.assertEqual(len(result["nullifiers"]["a"]), 100)
        for actual, i in zip(result["nullifiers"]["a"], range(100), strict=True):
            self.assertAlmostEqual(actual, i * 2 / math.sqrt(125e6) / 8192, places=14)

    def test_fractional_bins_keep_phase_across_overlapping_continuous_windows(self) -> None:
        self.start(windowMode="continuous", lockSeconds=0, modeSamples=None,
                   modeWidthSeconds=308e-9, historyModes=103)
        for start in range(0, 12000, 3000):
            self.send(start, 3000)
        result = self.result(5)
        self.assertEqual((result["modeSamples"], result["modeSamplesMax"]), (38, 39))
        self.assertAlmostEqual(result["modePeriodSeconds"], 308e-9)
        first = (result["stream"]["results"] - 1) * 51
        expected = []
        for k in range(first, first + 103):
            start, stop = (k * 77) // 2, ((k + 1) * 77) // 2
            expected.append(sum(i // 4 for i in range(start, stop)) /
                            (8192 * math.sqrt((stop - start) * self.rate)))
        for actual, wanted in zip(result["nullifiers"]["a"], expected, strict=True):
            self.assertAlmostEqual(actual, wanted, places=13)

    def test_connection_close_cannot_publish_queued_old_results(self) -> None:
        self.start()
        for start in range(0, 960, 96):
            self.send(start, 96)
        self.result(1)
        self.upstream.shutdown(socket.SHUT_RDWR)
        self.upstream.close()
        deadline = time.monotonic() + 3
        while self.command({'command': 'status'})['hasResult']:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)
        time.sleep(0.02)
        self.assertFalse(self.command({'command': 'latest'})['ok'])

    def test_new_loss_excludes_the_packet_and_resets_old_results(self) -> None:
        self.start()
        self.send(0, 96)
        self.result(1)
        self.send(96, 32, lost=1)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = self.command({"command": "status"})
            if not status["hasResult"]:
                break
            time.sleep(0.005)
        self.assertFalse(status["hasResult"])
        self.assertEqual(status["stream"]["lossPackets"], 1)

    def test_configuration_changes_keep_the_raw_connection_open(self) -> None:
        self.start()
        self.send(0, 96)
        self.result(1)
        configured = self.command({"command": "configure", "config": {
            "nullifiers": [{"name": "changed", "terms": [{"source": "a", "weight": 2}]}],
        }})
        self.assertTrue(configured["ok"])
        self.send(96, 96)
        result = self.result(2)
        self.assertEqual(set(result["nullifiers"]), {"changed"})

    def test_invalid_stop_command_preserves_stream(self) -> None:
        self.start()
        self.assertFalse(self.command({"command": "stop_stream", "bad": 1})["ok"])
        self.assertTrue(self.command({"command": "status"})["stream"]["running"])

    def test_continuous_reference_accumulates_before_a_large_history_is_full(self) -> None:
        self.start(windowMode='continuous', decimation=1024, historyModes=25000,
                   modeSamples=None, modeWidthSeconds=458.752e-6, lockSeconds=0)
        self.assertTrue(self.command({'command': 'start_calibration'})['ok'])
        self.send(0, 30000)
        deadline = time.monotonic() + 3
        while True:
            status = self.command({'command': 'status'})
            if status['calibrationModeCounts']['a'] >= 100:
                break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.005)
        self.assertLess(status['calibrationModeCounts']['a'], 25000)
        finished = self.command({'command': 'finish_calibration', 'minimumModes': 100})
        self.assertTrue(finished['ok'], finished)
        self.assertTrue(finished['calibrated'])


if __name__ == "__main__":
    unittest.main()
