"""Exercise the real hardware bridge against an SDK test double, without a board."""
from __future__ import annotations

import json
import math
import os
import signal
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path

BINARY = Path(sys.argv.pop(1)).resolve()
IMAGE = BINARY.parent / "fpga.inf"
LOG = BINARY.parent / "calls.log"
HOUSEKEEPING = BINARY.parent / "housekeeping.bin"
SETTINGS = {"command": "configure", "config": {
    "modeShape": "identity", "modeSamples": 4, "captureSamples": 64,
    "channelBDelaySamples": 0, "collectionGuardSeconds": 0,
    # The SDK double logs through a filesystem, which can be delayed on WSL mounts.
    "collectionEndGuardSamples": 0, "triggerTimeoutSeconds": 0.25,
}}


def run(mode: str = "", image: str = "v0.94", captures: int = 1,
        preview_points: int | None = None, config: dict | None = None,
        calibrate: bool = False, status_after: bool = False,
        max_points: int | None = None, timeout_seconds: int = 10,
        finish_and_measure: bool = False) -> tuple[list, list[str]]:
    IMAGE.write_text(image)
    LOG.write_text("")
    registers = bytearray(4096)
    struct.pack_into("<II", registers, 0x110, 0 if mode == "tagged_no_signature" else 0x45513101, 0x502)
    HOUSEKEEPING.write_bytes(registers)
    settings = {"command": "configure", "config": {**SETTINGS["config"], **(config or {})}}
    capture = {"command": "capture"}
    if max_points is not None:
        capture["maxPoints"] = max_points
    if preview_points is not None:
        capture["previewPoints"] = preview_points
    commands = [settings] + ([{"command": "start_calibration"}] if calibrate else []) + [capture] * captures
    if finish_and_measure:
        commands.extend([{"command": "finish_calibration", "minimumModes": 4}, capture])
    if status_after:
        commands.append({"command": "status"})
    result = subprocess.run(
        [str(BINARY)], input="\n".join(json.dumps(item) for item in commands) + "\n",
        text=True, capture_output=True, timeout=timeout_seconds, check=True,
        env={**os.environ, "NF_MOCK_MODE": mode, "NF_MOCK_LOG": str(LOG)},
    )
    return [json.loads(line) for line in result.stdout.splitlines()], LOG.read_text().splitlines()


class Acquisition(unittest.TestCase):
    def test_shot_noise_ignores_e1_and_measurement_resumes_masking(self) -> None:
        for readout in ("e1_direct", "e1_tile64x2"):
            config = {"rawReadout": readout, "captureSamples": 256}
            baseline, _ = run("tagged", calibrate=True, captures=2,
                              finish_and_measure=True, config=config)
            reference = baseline[-2]
            self.assertTrue(reference["calibrated"], reference)
            for mode in ("tagged_one_high", "tagged_all_high"):
                with self.subTest(readout=readout, mode=mode):
                    replies, _ = run(mode, calibrate=True, captures=2,
                                     finish_and_measure=True, config=config)
                    self.assertTrue(all(r["ok"] for r in replies), replies)
                    self.assertEqual(replies[-2]["calibrationModeCounts"],
                                     reference["calibrationModeCounts"])
                    self.assertEqual(replies[-2]["shotNoiseCoefficients"], reference["shotNoiseCoefficients"])
                    self.assertGreater(replies[-1]["excludedModes"], 0)
                    self.assertIn(None, replies[-1]["quadratures"]["a"])

    def test_shot_noise_still_rejects_invalid_packed_records(self) -> None:
        for mode in ("tagged_marker", "tagged_disagree"):
            replies, _ = run(mode, calibrate=True, status_after=True,
                             config={"rawReadout": "e1_tile64x2"})
            self.assertFalse(replies[-2]["ok"], replies)
            self.assertEqual(replies[-1]["calibrationModeCounts"], {"a": 0, "b": 0})

    def test_tagged_preview_uses_measurement_timebase_and_calibrates_raw_words(self) -> None:
        replies, calls = run("tagged", preview_points=16,
                             config={"measurementSamples": 60, "rawReadout": "e1_tile64x2"})
        self.assertTrue(replies[-1]["ok"], replies[-1])
        preview = replies[-1]["preview"]
        self.assertEqual(preview["sampleStride"], 3)
        for channel, name in enumerate(("a", "b")):
            for i, got in enumerate(preview[name]):
                bin_index = i * 3 // 4
                count = (bin_index + 1 + channel) * (-1 if bin_index % 2 else 1)
                self.assertAlmostEqual(got, (count - (3 + channel)) / 8192)
        self.assertFalse(any(call.startswith("read ") for call in calls))

    def test_tagged_strategies_match_ram_copy_on_wrapped_weighted_modes(self) -> None:
        config = {"captureSamples": 65536, "measurementSamples": 65529,
                  "modeSamples": 39, "modeShape": "lab_gaussian_linear_cosine",
                  "channelBDelaySamples": -5, "collectionEndGuardSamples": 13,
                  "collectionGuardSeconds": 96e-9}
        reference, _ = run("software_calibration", captures=2, config={**config, "rawReadout": "ram_copy"})
        for strategy in ("e1_direct", "e1_bulk", "e1_tile16", "e1_tile64", "e1_tile256", "e1_tile64x2"):
            with self.subTest(strategy=strategy):
                replies, calls = run("tagged", captures=2, config={**config, "rawReadout": strategy})
                for reply, expected in zip(replies[1:], reference[1:], strict=True):
                    self.assertTrue(reply["ok"], reply)
                    self.assertEqual(reply["quadratures"], expected["quadratures"])
                    self.assert_results_close(reply, expected)
                    self.assertEqual(reply["captureTimingMs"]["gateChecks"], 0)
                self.assertFalse(any(call.startswith("read ") for call in calls))
                self.assertEqual(struct.unpack_from("<I", HOUSEKEEPING.read_bytes(), 0x114)[0], 0x502)

    def test_tagged_capture_rejects_missing_signature_misalignment_and_bad_padding(self) -> None:
        for mode, error in (("tagged_no_signature", "signature"), ("tagged_misaligned", "positions"),
                            ("tagged_marker", "marker"), ("tagged_disagree", "tags disagree")):
            with self.subTest(mode=mode):
                replies, calls = run(mode, config={"measurementSamples": 60, "rawReadout": "e1_tile64x2"})
                self.assertIn(error, replies[-1]["error"])
                self.assertEqual(struct.unpack_from("<I", HOUSEKEEPING.read_bytes(), 0x114)[0], 0x502)
                if mode == "tagged_no_signature":
                    self.assertNotIn("start -1 -1", calls)

    def test_tagged_high_masks_modes_without_rejecting_capture(self) -> None:
        replies, _ = run("tagged_second_high", captures=3, status_after=True,
                         config={"rawReadout": "e1_tile64x2", "collectionGuardSeconds": 24e-9})
        self.assertTrue(replies[1]["ok"], replies[1])
        self.assertTrue(replies[2]["ok"], replies[2])
        self.assertGreater(replies[2]["excludedModes"], 0)
        self.assertIn(None, replies[2]["quadratures"]["a"])
        self.assertEqual(replies[3]["captures"], 3)
        self.assertEqual(struct.unpack_from("<I", HOUSEKEEPING.read_bytes(), 0x114)[0], 0x502)

    def test_tagged_all_high_is_a_valid_empty_calculation_record(self) -> None:
        replies, calls = run("tagged_all_high", config={"rawReadout":"e1_tile64x2"})
        result = replies[-1]
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["includedModes"], 0)
        self.assertEqual(result["e1MeasurementLevel"], "low")
        self.assertEqual(result["excludedModes"], result["capturedModes"])
        self.assertTrue(all(v is None for v in result["quadratures"]["a"]))
        self.assertTrue(any(call.startswith("start") for call in calls))
        self.assertFalse(any(call.startswith("gate_read") for call in calls))

    def test_20ms_record_preserves_5ms_high_low_gaps_in_display(self) -> None:
        replies, _ = run("tagged_alternating", max_points=2000, timeout_seconds=60, config={
            "captureSamples":2500032, "measurementSamples":2500000, "modeSamples":40,
            "historyModes":70000, "rawReadout":"e1_tile64x2", "collectSeconds":.020,
        })
        result = replies[-1]
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["capturedModes"], 62500)
        self.assertEqual(result["includedModes"], 31250)
        self.assertEqual(result["excludedModes"], 31250)
        values = result["quadratures"]["a"]
        stride = result["displayStride"]
        self.assertIsNone(values[0])
        self.assertIsNotNone(values[15625 // stride + 1])
        self.assertIsNone(values[31250 // stride + 1])
        self.assertIsNotNone(values[46875 // stride + 1])

    def test_display_downsampling_cannot_hide_a_short_high_gap(self) -> None:
        replies, _ = run("tagged_second_high", captures=2, max_points=16,
                         config={"rawReadout":"e1_tile64x2", "captureSamples":256})
        self.assertTrue(replies[-1]["ok"], replies[-1])
        self.assertIsNotNone(replies[-1]["quadratures"]["a"][0])
        self.assertIsNone(replies[-1]["quadratures"]["a"][1])
        self.assertIsNotNone(replies[-1]["quadratures"]["a"][2])

    def test_measurement_excludes_alignment_padding_for_both_readout_paths(self) -> None:
        for readout in ("direct_dma", "ram_copy"):
            replies, _ = run(config={"measurementSamples":60, "rawReadout":readout})
            self.assertTrue(replies[-1]["ok"], replies)
            self.assertEqual(len(replies[-1]["quadratures"]["a"]), 15)
            self.assertEqual(len(replies[-1]["quadratures"]["b"]), 15)

    def assert_results_close(self, actual: dict, expected: dict) -> None:
        for field in ("quadratures", "nullifiers", "variances", "varianceCounts", "squeezingDb"):
            self.assertEqual(actual[field].keys(), expected[field].keys())
            for name in actual[field]:
                for got, wanted in zip(actual[field][name], expected[field][name], strict=True):
                    if wanted is None:
                        self.assertIsNone(got)
                    else:
                        self.assertIsNotNone(got)
                        self.assertAlmostEqual(got, wanted, delta=1e-15 + abs(wanted) * 1e-10)

    def test_gate_defaults_high_and_reports_checks_and_capture_time(self) -> None:
        replies, calls = run(captures=2)
        self.assertTrue(all(reply["ok"] for reply in replies), replies)
        self.assertEqual(replies[0]["config"]["acquisitionGatePin"], "DIO0_P")
        self.assertEqual(replies[0]["config"]["acquisitionGatePollUs"], 500)
        self.assertTrue(any(call.startswith("gate_direction ") for call in calls))
        for reply in replies[1:]:
            timing = reply["captureTimingMs"]
            self.assertEqual(timing["gateChecks"], 3)
            self.assertGreaterEqual(timing["gateMaxInterval"], 0)
            self.assertEqual(sum(timing["gateIntervalHistogram"]), timing["gateIntervalCount"])
            self.assertEqual(timing["gateIntervalCount"], timing["gateChecks"] - 1)
            self.assertLessEqual(timing["gateMeanInterval"], timing["gateMaxInterval"])
            self.assertAlmostEqual(timing["sampleDuration"], 64 / 125_000)
        self.assertEqual(replies[1]["captureTimingMs"]["startInterval"], 0)
        self.assertGreater(replies[2]["captureTimingMs"]["startInterval"], 0)

    def test_acquisition_only_shutdown_preserves_generator_outputs(self) -> None:
        for mode in ("", "gate_low", "raw_failure"):
            _, calls = run(mode=mode)
            self.assertFalse(any(call.startswith("gen_") for call in calls))

    def test_poll_sleep_is_bounded_by_wait_timeout(self) -> None:
        replies, _ = run(mode="gate_low", config={"acquisitionGatePollUs": 1000000})
        self.assertFalse(replies[-1]["ok"])
        # A one-second configured sleep must not extend a 250-ms wait by 750 ms.
        self.assertLess(replies[-1]["captureTimingMs"]["gateWait"], 750)

    def test_invalid_gpio_state_is_fail_closed(self) -> None:
        replies, calls = run(mode="gate_invalid")
        self.assertFalse(replies[-1]["ok"])
        self.assertNotIn("start -1 -1", calls)

    def test_low_gate_never_starts_or_reads_adc(self) -> None:
        replies, calls = run(mode="gate_low")
        self.assertFalse(replies[-1]["ok"])
        self.assertIn("HIGH wait timeout", replies[-1]["error"])
        self.assertGreater(replies[-1]["captureTimingMs"]["gateChecks"], 1)
        self.assertGreaterEqual(replies[-1]["captureTimingMs"]["gateWait"], 250)
        self.assertNotIn("start -1 -1", calls)
        self.assertFalse(any(call.startswith("raw ") for call in calls))
        self.assertIn("enable 0 0", calls)
        self.assertIn("enable 1 0", calls)

    def test_gate_can_rise_after_waiting(self) -> None:
        replies, calls = run(mode="gate_rises")
        self.assertTrue(replies[-1]["ok"], replies)
        reads = [call for call in calls if call.startswith("gate_read ")]
        self.assertEqual([int(call.split()[-1]) for call in reads[:3]], [0, 0, 1])
        self.assertLess(calls.index(reads[2]), calls.index("start -1 -1"))
        self.assertGreaterEqual(replies[-1]["captureTimingMs"]["gateWait"], 0.9)
        self.assertEqual(replies[-1]["captureTimingMs"]["gateChecks"], 5)

    def test_low_or_gpio_failure_discards_active_capture(self) -> None:
        for mode in ("gate_falls", "gate_falls_at_fill", "gate_read_failure_active"):
            with self.subTest(mode=mode):
                replies, calls = run(mode=mode)
                self.assertFalse(replies[-1]["ok"], replies)
                self.assertIn("acquisition gate", replies[-1]["error"])
                self.assertIn("start -1 -1", calls)
                self.assertFalse(any(call.startswith("raw ") for call in calls))
                self.assertEqual(calls.count("stop -1 -1"), 2)
                self.assertIn("enable 0 0", calls)
                self.assertIn("enable 1 0", calls)

    def test_gpio_setup_or_read_failure_never_starts(self) -> None:
        for mode in ("gate_direction_failure", "gate_read_failure"):
            replies, calls = run(mode=mode)
            self.assertFalse(replies[-1]["ok"])
            self.assertNotIn("start -1 -1", calls)

    def test_rejected_capture_does_not_enter_calibration_or_statistics(self) -> None:
        replies, _ = run(mode="gate_falls", calibrate=True, status_after=True)
        self.assertFalse(replies[-2]["ok"])
        status = replies[-1]
        self.assertTrue(status["calibrating"])
        self.assertFalse(status["hasResult"])
        self.assertEqual(status["captures"], 0)
        self.assertEqual(status["calibrationModeCounts"], {"a": 0, "b": 0})

    def test_rejection_preserves_previous_statistics_and_calibration(self) -> None:
        for calibrate in (False, True):
            replies, _ = run(mode="gate_second_low", captures=3, calibrate=calibrate, status_after=True)
            self.assertTrue(replies[-4]["ok"])
            self.assertFalse(replies[-3]["ok"])
            self.assertTrue(replies[-2]["ok"])
            self.assertEqual(replies[-1]["captures"], 2)
            if calibrate:
                self.assertEqual(replies[-1]["calibrationModeCounts"], {"a": 32, "b": 32})
            else:
                reference, _ = run(captures=2)
                self.assert_results_close(replies[-2], reference[-1])

    def test_explicitly_disabled_gate_preserves_ungated_capture(self) -> None:
        replies, calls = run(mode="gate_low", config={"acquisitionGatePin": None})
        self.assertTrue(replies[-1]["ok"], replies)
        self.assertFalse(any(call.startswith("gate_") for call in calls))
        self.assertEqual(replies[-1]["captureTimingMs"]["gateChecks"], 0)

    def test_ram_copy_preserves_wrapped_projection_and_preview(self) -> None:
        for shape in ("identity", "gaussian_linear", "lab_gaussian_linear_cosine"):
            for threshold in (None, 0.001):
                config = {"modeShape": shape, "gateAbsoluteThreshold": threshold}
                direct, _ = run(config=config, preview_points=16)
                copied, _ = run(config={**config, "rawReadout": "ram_copy"}, preview_points=16)
                self.assertTrue(copied[-1]["ok"], copied)
                self.assertEqual(copied[-1]["preview"], direct[-1]["preview"])
                self.assert_results_close(copied[-1], direct[-1])
                self.assertEqual(copied[-1]["captureTimingMs"]["rawCopyBytes"], 64 * 2 * 2)
                self.assertEqual(direct[-1]["captureTimingMs"]["rawCopyBytes"], 0)

    def test_ram_copy_fractional_bins_delay_calibration_and_reuse(self) -> None:
        for shape in ("identity", "gaussian_linear", "lab_gaussian_linear_cosine"):
            for delay in (-2, 2):
                for threshold in (None, 0.001):
                    with self.subTest(shape=shape, delay=delay, threshold=threshold):
                        config = {"modeShape": shape, "modeSamples": None, "captureSamples": 4096,
                                  "historyModes": 100, "channelBDelaySamples": delay,
                                  "collectionGuardSeconds": 8e-9, "collectionEndGuardSamples": 3,
                                  "varianceWindowModes": 5, "gateAbsoluteThreshold": threshold}
                        direct, _ = run(mode="software_calibration", config=config, captures=2)
                        copied, _ = run(mode="software_calibration", captures=2,
                                        config={**config, "rawReadout": "ram_copy"})
                        for got, wanted in zip(copied[1:], direct[1:], strict=True):
                            self.assertTrue(got["ok"], got)
                            self.assertTrue(wanted["ok"], wanted)
                            self.assert_results_close(got, wanted)


    def test_both_channels_are_captured_and_resources_are_released(self) -> None:
        replies, calls = run()
        self.assertTrue(replies[-1]["ok"], replies)
        self.assertIn("fill 0 -1", calls)
        self.assertIn("fill 1 -1", calls)
        self.assertIn("raw 0 100", calls)
        self.assertIn("raw 1 101", calls)
        self.assertFalse(any(call.startswith("read ") for call in calls))
        self.assertIn("factory_calibration -1 -1", calls)
        self.assertEqual(calls[-3:], ["enable 0 0", "enable 1 0", "release -1 -1"])
        self.assertEqual(replies[-1]["captureTimingMs"]["directChannels"], 2)
        self.assertEqual(replies[-1]["inputUnits"], "adcCounts")
        for channel, name in enumerate(("a", "b")):
            expected = [(i + 1 + channel) * (-1 if i & 1 else 1) * 2 / math.sqrt(125_000_000)
                        for i in range(16)]
            for actual, wanted in zip(replies[-1]["quadratures"][name], expected, strict=True):
                self.assertAlmostEqual(actual, wanted, places=15)

    def test_preview_lookup_matches_sdk_and_reuses_capture_calibration(self) -> None:
        replies, calls = run(captures=2, preview_points=16)
        self.assertTrue(all(reply["ok"] for reply in replies), replies)
        for channel, name in enumerate(("a", "b")):
            expected = [(i // 4 + 1 + channel) * (-1 if i // 4 & 1 else 1) / 8192
                        for i in range(16)]
            self.assertEqual(replies[-1]["preview"][name], expected)
            # SDK oracle verifies only the first lookup table. Preview + raw integration
            # must query the capture's format/calibration once per channel, not twice.
            self.assertEqual(calls.count(f"read {channel} {100 + channel}"), 1)
            self.assertEqual(calls.count(f"parameters {channel} -1"), 2)
            self.assertEqual(calls.count(f"raw {channel} {100 + channel}"), 4)

    def test_software_calibration_offset_is_preserved_in_raw_units(self) -> None:
        replies, _ = run(mode="software_calibration", preview_points=16)
        self.assertTrue(replies[-1]["ok"], replies)
        for channel, name in enumerate(("a", "b")):
            expected = [((i + 1 + channel) * (-1 if i & 1 else 1) - 3 - channel)
                        * 2 / math.sqrt(125_000_000) for i in range(16)]
            for actual, wanted in zip(replies[-1]["quadratures"][name], expected, strict=True):
                self.assertAlmostEqual(actual, wanted, places=15)

    def test_voltage_gate_without_preview_still_builds_the_sdk_lookup(self) -> None:
        replies, calls = run(config={"gateAbsoluteThreshold": 0.001})
        self.assertTrue(replies[-1]["ok"], replies)
        self.assertIn("read 0 100", calls)
        self.assertIn("read 1 101", calls)
        values = replies[-1]["quadratures"]["a"]
        self.assertTrue(all(value is not None for value in values[:7]))
        self.assertEqual(values[7:], [None] * 9)

    def test_unsupported_adc_format_is_rejected_before_raw_projection(self) -> None:
        for mode in ("wide_adc", "unsigned_adc"):
            replies, calls = run(mode=mode)
            self.assertFalse(replies[-1]["ok"])
            self.assertIn("signed 14-bit", replies[-1]["error"])
            self.assertFalse(any(call.startswith("raw ") for call in calls))

    def test_direct_raw_sdk_failure_cleans_up_both_mappings(self) -> None:
        replies, calls = run(mode="raw_failure")
        self.assertFalse(replies[-1]["ok"])
        self.assertIn("raw ADC regions", replies[-1]["error"])
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
        self.assertIn("short raw ADC regions", replies[-1]["error"])
        self.assertEqual(calls[-1], "release -1 -1")

    def test_termination_during_capture_cleans_up_both_channels(self) -> None:
        self._termination_cleans_up("timeout", "start -1 -1", True)

    def test_termination_while_gate_is_low_never_starts(self) -> None:
        self._termination_cleans_up("gate_low", "gate_read ", False)

    def _termination_cleans_up(self, mode: str, marker: str, started: bool) -> None:
        IMAGE.write_text("v0.94")
        LOG.write_text("")
        settings = {"command": "configure", "config": {
            **SETTINGS["config"], "triggerTimeoutSeconds": 5,
        }}
        process = subprocess.Popen(
            [str(BINARY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={**os.environ, "NF_MOCK_MODE": mode, "NF_MOCK_LOG": str(LOG)},
        )
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps(settings) + '\n{"command":"capture"}\n')
            process.stdin.flush()
            deadline = time.monotonic() + 3
            while marker not in LOG.read_text():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.005)
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=3)
            calls = LOG.read_text().splitlines()
            self.assertEqual(calls[-3:], ["enable 0 0", "enable 1 0", "release -1 -1"])
            self.assertEqual(calls.count("stop -1 -1"), 2 if started else 1)
            self.assertEqual("start -1 -1" in calls, started)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
