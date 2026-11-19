"""Calibrate on the recorded shot noise and replay the paired squeezing trace."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from quantum_light_engine.post_processing import (
    LAB_CLUSTER_X,
    RealtimeClusterProcessor,
    cluster_squeezing_db,
    compute_nullifier,
    rolling_variance,
    single_mode_processing_config,
)
from quantum_light_engine.recorded_data import COUNT, MEASUREMENT, prepare_recording, read_trace

WINDOW_MODES = 2000


def load_pair(folder: Path, suffix: str, board_approximation: bool):
    if board_approximation:
        payload, sources = prepare_recording(folder, suffix)
        packed = np.frombuffer(payload, dtype="<i2").reshape(2, COUNT)[:, :MEASUREMENT]
        signed = (packed & 8191) - (packed & 8192)
        return signed.astype(np.float64) / 8192, sources
    a, source_a = read_trace(folder / f"C1{suffix}.trc")
    b, source_b = read_trace(folder / f"C2{suffix}.trc")
    return (a, b), [source_a, source_b]


def traces(quadratures):
    return {
        "CH1": quadratures["a"],
        "CH2": quadratures["b"],
        "Cluster X": compute_nullifier(quadratures, LAB_CLUSTER_X),
    }


def replay(folder: Path, rate: int, board_approximation: bool):
    shot, shot_sources = load_pair(folder, "snv100001", board_approximation)
    signal, signal_sources = load_pair(folder, "sqzxv100015", board_approximation)
    config = replace(
        single_mode_processing_config(variance_window_modes=WINDOW_MODES),
        window_mode="continuous",
        history_modes=170_000,
        collection_guard_seconds=0,
        collection_end_guard_samples=0,
    )
    processor = RealtimeClusterProcessor(config)
    processor.start_calibration()
    reference = processor.feed(*shot, rate)
    if reference is None:
        raise ValueError("shot-noise trace contains no complete modes")
    coefficients = processor.finish_calibration()
    shot_variances = {
        name: float(np.var(values))
        for name, values in traces({
            rail: values * coefficients[rail] for rail, values in reference.quadratures.items()
        }).items()
    }
    if not all(np.isfinite(value) and value > 0 for value in shot_variances.values()):
        raise ValueError("shot-noise trace has invalid variance")

    windows = []
    chart = {}
    for start_ms, stop_ms in ((0, 50), (0, 20), (20, 40), (40, 50)):
        start, stop = start_ms * rate // 1000, stop_ms * rate // 1000
        result = processor.feed(*(rail[start:stop] for rail in signal), rate, discontinuity=True)
        if result is None:
            raise ValueError("signal trace contains no complete modes")
        summary = {}
        for name, values in traces(result.quadratures).items():
            variance = rolling_variance(values, WINDOW_MODES, require_full_window=True)
            db = cluster_squeezing_db(variance, shot_variances[name])
            finite = db[np.isfinite(db)]
            summary[name] = {
                "modes": len(values),
                "pooledDb": float(cluster_squeezing_db(np.var(values), shot_variances[name])),
                "rollingMedianDb": float(np.median(finite)),
                "rollingP05Db": float(np.percentile(finite, 5)),
                "rollingP95Db": float(np.percentile(finite, 95)),
                "rollingBelowReferencePercent": float(100 * np.mean(finite < 0)),
            }
            if start_ms == 0 and stop_ms == 50:
                valid = np.flatnonzero(np.isfinite(db))
                indices = valid[np.linspace(0, valid.size - 1, 600, dtype=int)]
                chart[name] = [
                    [float(i * result.mode_period_seconds * 1000), float(db[i])]
                    for i in indices
                ]
        windows.append({"startMs": start_ms, "endMs": stop_ms, "traces": summary})
    return {
        "sampleRateHz": rate,
        "boardApproximation": board_approximation,
        "shotSources": shot_sources,
        "signalSources": signal_sources,
        "calibrationCoefficients": coefficients,
        "normalizedShotVariances": shot_variances,
        "windows": windows,
    }, chart


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, help="Folder containing the four paired LeCroy files")
    parser.add_argument("--output", type=Path, required=True, help="Write JSON replay statistics")
    parser.add_argument("--plot-data", type=Path, help="Also export compact time and dB series")
    args = parser.parse_args()
    cases = []
    charts = {}
    for rate, approximation in ((200_000_000, False), (125_000_000, True)):
        result, chart = replay(args.data, rate, approximation)
        cases.append(result)
        charts[str(rate)] = chart
        for window in result["windows"]:
            levels = ", ".join(f"{k} {v['pooledDb']:+.2f} dB" for k, v in window["traces"].items())
            print(f"{rate / 1e6:g} MS/s, {window['startMs']}-{window['endMs']} ms  {levels}")
    metadata = {
        "modeWidthSeconds": 308e-9,
        "modeShape": "lab_gaussian_linear_cosine",
        "modeFrequencyHz": 6_000_000,
        "channelBDelaySamples": 2,
        "rollingWindowModes": WINDOW_MODES,
        "calibrationDurationMs": 50,
        "gate": "No recorded E1 channel. All complete modes are eligible.",
        "boardConversion": "Fourier low-pass resampling, then signed 14-bit ±1 V quantization.",
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    if args.plot_data:
        args.plot_data.parent.mkdir(parents=True, exist_ok=True)
        args.plot_data.write_text(json.dumps(charts, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
