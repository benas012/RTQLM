"""Run the actual native command executable against independent numeric expectations."""
from __future__ import annotations

import json
import math
import os
import shlex
import statistics
import struct
import subprocess
import sys
import unittest
from pathlib import Path

BINARY = str(Path(sys.argv.pop(1)).resolve())
RUNNER = shlex.split(os.environ.get("NF_RUNNER", ""))
RATE = 125_000_000


def configure(**changes: object) -> dict:
    return {"command": "configure", "config": {
        "modeShape": "identity", "modeSamples": 4, "minSamplesPerMode": 2,
        "captureSamples": 256, "channelBDelaySamples": 0,
        "collectionGuardSeconds": 0, "collectionEndGuardSamples": 0,
        "varianceEstimator": "rolling", "varianceWindowModes": 4,
        "calibrationEstimator": "pooled",
        "nullifiers": [{"name": "custom", "terms": [{"source": "a"}]}],
        **changes,
    }}


def capture(a: list, b: list | None = None) -> dict:
    return {"command": "capture", "samples": {"a": a, "b": b if b is not None else a}}


def modes(values: list[float], width: int = 4) -> list[float]:
    return [value for value in values for _ in range(width)]


def invoke(*commands: dict | str) -> list[dict]:
    payload = "\n".join(command if isinstance(command, str) else json.dumps(command)
                        for command in commands) + "\n"
    result = subprocess.run(
        [*RUNNER, BINARY, "--replay"], input=payload, text=True,
        capture_output=True, timeout=30, check=True,
    )
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(replies) == len(commands), (result.stdout, result.stderr)
    return replies


class NativeCommands(unittest.TestCase):
    def test_variance_only_preserves_values_and_excludes_samples(self) -> None:
        samples = modes([float(i % 7) for i in range(40)])
        full = invoke(configure(), capture(samples))[-1]
        request = {**capture(samples), "resultFields":"variances"}
        reduced = invoke(configure(), request,
                         {"command":"latest", "resultFields":"variances"})
        for reply in reduced[1:]:
            self.assertTrue(reply["ok"], reply)
            self.assertEqual(reply["resultFields"], "variances")
            for key in ("quadratures", "nullifiers", "preview"):
                self.assertNotIn(key, reply)
            for key in ("variances", "varianceCounts", "squeezingDb", "displayStride"):
                self.assertEqual(reply[key], full[key])

    def test_variance_only_rejects_preview_and_invalid_fields(self) -> None:
        for extra in ({"resultFields":"variances", "previewPoints":16},
                      {"resultFields":"invalid"}, {"resultFields":True}):
            reply = invoke(configure(), {**capture(modes([1.,2.,3.,4.])), **extra})[-1]
            self.assertFalse(reply["ok"])

    def test_serialization_buffer_grows_reuses_and_handles_smaller_results(self) -> None:
        rows = [{"name": f"row_{i}", "terms": [{"source": "a"}]} for i in range(16)]
        signal = [float(i % 251) for i in range(16384)]
        replies = invoke(configure(modeSamples=2, historyModes=16384, nullifiers=rows),
                         capture(signal), capture(signal), capture(signal[:16]))
        self.assertTrue(all(reply["ok"] for reply in replies), replies[0])
        self.assertGreater(len(json.dumps(replies[1])), 1_000_000)
        for field in ("quadratures", "nullifiers", "variances", "varianceCounts"):
            self.assertEqual(replies[1][field], replies[2][field])
        self.assertEqual(len(replies[1]["quadratures"]["a"]), 8192)
        self.assertEqual(len(replies[3]["quadratures"]["a"]), 8)
        self.assertAlmostEqual(replies[3]["quadratures"]["a"][0], 1 / math.sqrt(2 * RATE))

    def test_acquisition_gate_and_readout_settings_are_validated(self) -> None:
        good = invoke(configure(acquisitionGatePin="DIO3_N", acquisitionGatePollUs=100,
                                rawReadout="ram_copy"))[0]
        self.assertTrue(good["ok"], good)
        self.assertEqual(good["config"]["acquisitionGatePin"], "DIO3_N")
        self.assertEqual(good["config"]["rawReadout"], "ram_copy")
        for settings in ({"acquisitionGatePin": "3"}, {"acquisitionGatePin": False},
                         {"acquisitionGatePollUs": 0}, {"acquisitionGatePollUs": 49},
                         {"acquisitionGatePollUs": 500.5}, {"acquisitionGatePollUs": 1000001},
                         {"rawReadout": "unknown"}):
            with self.subTest(settings=settings):
                self.assertFalse(invoke(configure(**settings))[0]["ok"])

    def test_50ms_measurement_configuration_and_limits(self) -> None:
        reply = invoke(configure(captureSamples=6250048, measurementSamples=6250000,
                                 historyModes=200000, acquisitionGatePin="DIO0_P"))[0]
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["config"]["measurementSamples"], 6250000)
        for update in ({"captureSamples":8388672}, {"historyModes":262145},
                       {"measurementSamples":6250050, "captureSamples":6250048}):
            self.assertFalse(invoke(configure(**update))[0]["ok"])

    def assert_close(self, actual: list, expected: list, places: int = 12) -> None:
        self.assertEqual(len(actual), len(expected))
        for observed, wanted in zip(actual, expected, strict=True):
            self.assertAlmostEqual(observed, wanted, places=places)

    def test_time_width_derives_flat_mode_samples_from_acquisition_rate(self) -> None:
        for decimation, width, expected in ((1, 320e-9, 40), (8, 320e-9, 5),
                                             (1024, 458.752e-6, 56)):
            reply = invoke(configure(decimation=decimation, modeSamples=None,
                                     modeWidthSeconds=width))[0]
            self.assertTrue(reply['ok'], reply)
            self.assertEqual(reply['modeSamples'], expected)
            self.assertAlmostEqual(reply['modePeriodSeconds'], expected * decimation / RATE)

    def test_buffered_preview_does_not_shorten_processing_or_change_its_timebase(self) -> None:
        signal = modes(list(range(100)))
        replies = invoke(configure(), capture(signal),
                         capture(signal) | {'previewPoints': 16, 'maxPoints': 16},
                         {'command': 'start_calibration'},
                         capture(signal) | {'previewPoints': 16, 'maxPoints': 16})
        full, compact, calibrated = replies[1], replies[2], replies[4]
        self.assertEqual(compact['preview']['a'], signal[:16])
        self.assertEqual(compact['modePeriodSeconds'], full['modePeriodSeconds'])
        self.assertEqual(len(full['quadratures']['a']), 100)
        self.assert_close(compact['quadratures']['a'], full['quadratures']['a'][::7], places=15)
        self.assertEqual(calibrated['calibrationModeCounts']['a'], 100)
        self.assertEqual(calibrated['preview']['b'], signal[:16])

    def test_time_width_with_too_few_samples_is_rejected(self) -> None:
        reply = invoke(configure(decimation=1024, modeSamples=None,
                                 modeWidthSeconds=308e-9))[0]
        self.assertFalse(reply['ok'])
        self.assertIn('samples', reply['error'])

    def test_compact_snapshot_keeps_the_full_statistics_and_mode_period(self) -> None:
        replies = invoke(configure(), capture(modes(list(range(100)))),
                         {"command": "latest", "maxPoints": 16}, {"command": "latest"})
        full, compact, again = replies[1:]
        self.assertEqual(compact['displayStride'], 7)
        self.assertEqual(compact['modePeriodSeconds'], full['modePeriodSeconds'])
        for key in ('nullifiers', 'variances', 'varianceCounts'):
            self.assert_close(compact[key]['custom'], full[key]['custom'][::7], places=15)
            self.assertEqual(again[key], full[key])

    def test_repeated_snapshots_preserve_owned_spare_mode_buffers(self) -> None:
        signal = modes([1, 2, 4, 8, 16, 32, 16, 8] * 4)
        replies = invoke(configure(), capture(signal), capture(signal),
                         {"command": "latest"}, {"command": "latest"},
                         capture(signal), {"command": "reset"},
                         capture(signal), {"command": "latest"})
        self.assertTrue(all(reply["ok"] for reply in replies), replies)
        # A second capture populates the reusable spare buffers. Snapshots own
        # their result copies; releasing them must leave those live spares usable.
        expected = replies[2]
        for actual in (replies[3], replies[4], replies[5], replies[7], replies[8]):
            for key in ("quadratures", "nullifiers", "variances", "varianceCounts"):
                self.assertEqual(actual[key], expected[key])

    def test_stable_sliding_variance_with_a_large_offset(self) -> None:
        signal = [struct.unpack('f', struct.pack('f', 10000 + math.sin(i * 0.3)))[0]
                  for i in range(400)]
        for window in (4, 5, 100):
            actual = invoke(configure(varianceWindowModes=window), capture(modes(signal)))[-1]
            expected = [statistics.pvariance(signal[max(0, i-window//2):
                                                   min(len(signal), i+window-window//2)]) * 4 / RATE
                        for i in range(len(signal))]
            self.assert_close(actual['variances']['custom'], expected, places=13)

    def test_variable_formulas_offsets_start_and_channel_selection(self) -> None:
        formula = [{"name": "difference", "start": 1, "terms": [
            {"source": "a", "weight": 2}, {"source": "b", "offset": 1, "weight": -3},
        ]}]
        result = invoke(configure(nullifiers=formula),
                        capture(modes([1, 2, 4, 8]), modes([10, 20, 40, 80])))[-1]
        self.assertTrue(result["ok"])
        self.assert_close(result["nullifiers"]["difference"],
                          [(2 * 2 - 3 * 40) * 2 / math.sqrt(RATE),
                           (2 * 4 - 3 * 80) * 2 / math.sqrt(RATE)])
        self.assertEqual(result["indexStarts"]["difference"], 1)

    def test_exact_width_rolling_population_variance(self) -> None:
        signal = [1, 2, 4, 8, 16]
        result = invoke(configure(), capture(modes(signal)))[-1]
        expected = [statistics.pvariance(signal[max(0, i - 2): min(5, i + 2)]) * 4 / RATE
                    for i in range(5)]
        self.assert_close(result["variances"]["custom"], expected)

    def test_each_capture_is_a_separate_collection(self) -> None:
        pair = [{"name": "pair", "terms": [{"source": "a"}, {"source": "a", "offset": 1}]}]
        replies = invoke(configure(nullifiers=pair), capture([10] * 7), capture([20] * 7))
        self.assertEqual(replies[-1]["nullifiers"]["pair"], [])

    def test_change_formula_clears_statistics_and_calibration(self) -> None:
        replies = invoke(configure(), capture(modes([1, 2, 3])), configure(nullifiers=[{
            "name": "changed", "terms": [{"source": "b", "weight": -2}],
        }]), {"command": "latest"}, capture(modes([1, 2, 3])))
        self.assertEqual(replies[2]["revision"], 2)
        self.assertFalse(replies[2]["hasResult"])
        self.assertFalse(replies[3]["ok"])
        self.assertEqual(set(replies[4]["nullifiers"]), {"changed"})

    def test_invalid_configure_is_atomic(self) -> None:
        invalid = [
            {"unknown": 1}, {"modeSamples": True}, {"captureSamples": 65},
            {"modeShape": "eval"}, {"varianceEstimator": "unsupported"},
            {"explicitModeTemplate": [0, 0, 0, 0]}, {"nullifiers": []},
            {"nullifiers": [{"name": "x", "terms": [{"source": "a", "offset": -1}]}]},
            {"nullifiers": [{"name": "x", "terms": [{"source": "a", "wieght": 1}]}]},
            {"nullifiers": [{"name": "x", "terms": [
                {"source": "a", "weight": 1}, {"source": "a", "weight": -1},
            ]}]},
        ]
        commands = [configure(), capture(modes([1, 2, 3]))]
        for bad in invalid:
            commands.extend([{"command": "configure", "config": bad}, {"command": "status"}])
        replies = invoke(*commands)
        for i in range(2, len(replies), 2):
            self.assertFalse(replies[i]["ok"], invalid[(i - 2) // 2])
            self.assertEqual(replies[i + 1]["revision"], 1)
            self.assertTrue(replies[i + 1]["hasResult"])

    def test_duplicate_keys_trailing_json_and_unknown_commands_are_rejected(self) -> None:
        replies = invoke('{"command":"status","command":"reset"}',
                         '{"command":"status"} {}', {"command": "eval"},
                         {"command": "status", "config": {}}, {"command": "status"})
        self.assertTrue(all(not item["ok"] for item in replies[:-1]))
        self.assertTrue(replies[-1]["ok"])

    def test_calibration_normalizes_vacuum_and_custom_reference(self) -> None:
        signal = [-1, 1, -2, 2]
        replies = invoke(configure(), {"command": "start_calibration"}, capture(modes(signal)),
                         {"command": "finish_calibration", "minimumModes": 4},
                         capture(modes(signal)))
        self.assertTrue(replies[-1]["calibrated"])
        coefficient = 1 / math.sqrt(2 * statistics.pvariance(signal) * 4 / RATE)
        self.assertAlmostEqual(replies[-1]["shotNoiseCoefficients"]["a"], coefficient)
        self.assert_close(replies[-1]["nullifiers"]["custom"],
                          [value * 2 / math.sqrt(RATE) * coefficient for value in signal])

    def test_failed_calibration_can_collect_more_modes(self) -> None:
        replies = invoke(configure(), {"command": "start_calibration"},
                         capture(modes([-1, 1])),
                         {"command": "finish_calibration", "minimumModes": 4},
                         capture(modes([-1, 1])),
                         {"command": "finish_calibration", "minimumModes": 4})
        self.assertFalse(replies[3]["ok"])
        self.assertTrue(replies[4]["calibrating"])
        self.assertTrue(replies[5]["calibrated"])

    def test_ensemble_variance_accumulates_once_per_index_per_capture(self) -> None:
        replies = invoke(configure(varianceEstimator="ensemble"), capture(modes([1, 2, 3])),
                         capture(modes([3, 6, 9])), capture(modes([5, 10])))
        self.assertEqual(replies[1]["variances"]["custom"], [None, None, None])
        self.assert_close(replies[2]["variances"]["custom"], [4 / RATE, 16 / RATE, 36 / RATE])
        self.assertEqual(replies[3]["varianceCounts"]["custom"], [3, 3, 2])
        self.assertIsNone(replies[3]["nullifiers"]["custom"][2])

    def test_lab_rolling_averages_db_per_capture(self) -> None:
        signal = [-1, 1, -2, 2]
        replies = invoke(configure(varianceEstimator="lab_rolling"),
                         {"command": "start_calibration"}, capture(modes(signal)),
                         {"command": "finish_calibration", "minimumModes": 4},
                         capture(modes(signal)), capture(modes([value * 2 for value in signal])))
        first = replies[-2]["squeezingDb"]["custom"]
        final = replies[-1]["squeezingDb"]["custom"]
        self.assert_close(final, [value + 10 * math.log10(2) for value in first])
        self.assertEqual(replies[-1]["varianceCounts"]["custom"], [2] * 4)

    def test_explicit_template_delay_guards_and_tail(self) -> None:
        a = [999, 1, 3, 2, 6, 5, 10, 999, 999]
        b = [999, 999, 999, 2, 4, 3, 8, 5, 11]
        formula = [{"name": "sum", "terms": [{"source": "a"}, {"source": "b"}]}]
        result = invoke(configure(modeSamples=2, explicitModeTemplate=[1, -1],
                                  channelBDelaySamples=2, collectionGuardSeconds=1 / RATE,
                                  nullifiers=formula), capture(a, b))[-1]
        self.assert_close(result["nullifiers"]["sum"],
                          [-4 / math.sqrt(2 * RATE), -9 / math.sqrt(2 * RATE),
                           -11 / math.sqrt(2 * RATE)])

    def test_amplitude_rejection_preserves_mode_positions(self) -> None:
        result = invoke(configure(gateAbsoluteThreshold=5), capture(modes([1, 10, 2])))[-1]
        self.assertIsNone(result["nullifiers"]["custom"][1])
        self.assertEqual(len(result["nullifiers"]["custom"]), 3)

    def test_bounded_history_reports_prefix_truncation(self) -> None:
        result = invoke(configure(historyModes=100), capture(modes(list(range(150)))))[-1]
        self.assertEqual(len(result["nullifiers"]["custom"]), 100)
        self.assertTrue(result["prefixTruncated"])

    def test_disable_requires_reconfiguration(self) -> None:
        replies = invoke(configure(), {"command": "disable"}, capture(modes([1, 2, 3])))
        self.assertFalse(replies[1]["enabled"])
        self.assertFalse(replies[-1]["ok"])

    def test_shape_is_validated_before_configuration_commit(self) -> None:
        replies = invoke({"command": "configure", "config": {"modeShape": "unknown"}},
                         {"command": "status"})
        self.assertFalse(replies[0]["ok"])
        self.assertEqual(replies[1]["revision"], 0)

    def test_basis_only_changes_builtin_formula_and_preserves_custom_rows(self) -> None:
        replies = invoke({"command": "configure", "config": {"measurementBasis": "p"}},
                         {"command": "configure", "config": {"measurementBasis": "x"}},
                         configure(), {"command": "configure", "config": {"measurementBasis": "p"}})
        p_row = replies[0]["config"]["nullifiers"][0]
        self.assertEqual(p_row["name"], "cluster_p")
        self.assertEqual(p_row["start"], 1)
        self.assertEqual([term["weight"] for term in p_row["terms"]], [1, -1, 1, 1])
        self.assertEqual(replies[1]["config"]["nullifiers"][0]["name"], "cluster_x")
        self.assertEqual(replies[3]["config"]["nullifiers"][0]["name"], "custom")

    def test_segment_calibration_weights_captures_equally(self) -> None:
        replies = invoke(configure(calibrationEstimator="segment_mean"),
                         {"command": "start_calibration"}, capture(modes([-1, 1])),
                         capture(modes([-3, 3, -3, 3])),
                         {"command": "finish_calibration", "minimumModes": 6})
        self.assertTrue(replies[-1]["calibrated"])
        self.assertAlmostEqual(replies[-1]["shotNoiseCoefficients"]["a"],
                               1 / math.sqrt(2 * ((4 / RATE + 36 / RATE) / 2)))

    def test_oversized_command_does_not_desynchronize_the_channel(self) -> None:
        replies = invoke(" " * (1024 * 1024 + 1), {"command": "status"})
        self.assertFalse(replies[0]["ok"])
        self.assertTrue(replies[1]["ok"])

    def test_configuration_field_separators_cannot_hide_unknown_keys(self) -> None:
        result = invoke({"command": "configure", "config": {
            "modeShape||varianceEstimator": "ignored",
        }})[0]
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
