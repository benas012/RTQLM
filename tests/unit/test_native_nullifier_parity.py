"""Numerical parity between the native command executable and the receiver reference."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from quantum_light_engine.post_processing import (
    ClusterProcessingConfig,
    NullifierSpec,
    NullifierTerm,
    RealtimeClusterProcessor,
    config_to_dict,
)


@pytest.fixture(scope="module")
def native_runner(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    supplied = os.environ.get("RTQLM_NATIVE_COMMAND")
    if supplied:
        return shlex.split(supplied)
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("native C compiler or RTQLM_NATIVE_COMMAND is required")
    root = Path(__file__).resolve().parents[2] / "native" / "redpitaya_nullifiers"
    binary = tmp_path_factory.mktemp("native") / "nullifier-replay"
    subprocess.run(
        [compiler, "-std=c11", "-O2", "-DCJSON_NESTING_LIMIT=64",
         str(root / "commands.c"), str(root / "nullifier.c"), str(root / "acquire_stub.c"),
         str(root / "stream.c"), str(root / "vendor" / "cJSON.c"),
         "-lm", "-pthread", "-o", str(binary)],
        check=True, capture_output=True,
    )
    return [str(binary), "--replay"]


@pytest.mark.parametrize("shape", ["identity", "gaussian_linear", "gaussian_linear_cosine",
                                   "lab_gaussian_linear_cosine"])
@pytest.mark.parametrize("width", [256e-9, 308e-9])
def test_custom_expression_native_projection_matches_python(native_runner, shape, width):
    from quantum_light_engine.devices.board_nullifiers import BoardNullifierProcessor
    from quantum_light_engine.post_processing import single_mode_processing_config

    config = replace(
        single_mode_processing_config(), mode_shape=shape, mode_width_seconds=width,
        mode_expression="exp(-0.5*(t/(T/5))**2) * cos(2*pi*f*t) + 0.1",
        history_modes=1000, variance_window_modes=5, window_mode="continuous",
        collection_guard_seconds=0, collection_end_guard_samples=0,
    )
    board = BoardNullifierProcessor("board.local", "/root/processor", 1, 3, 18902)
    settings = board._settings(config)
    settings["captureSamples"] = 4096
    rng = np.random.default_rng(612)
    a, b = rng.normal(0, 0.2, (2, 4096)).astype(np.float32)
    commands = [
        {"command": "configure", "config": settings},
        {"command": "capture", "samples": {"a": a.tolist(), "b": b.tolist()}},
    ]
    reply = subprocess.run(
        native_runner, input="\n".join(json.dumps(command) for command in commands) + "\n",
        text=True, capture_output=True, timeout=30, check=True,
    )
    configured, actual = map(json.loads, reply.stdout.splitlines())
    assert configured["ok"], configured
    assert actual["ok"], actual
    expected = RealtimeClusterProcessor(config).feed(a, b, 125e6)
    for channel in ("a", "b"):
        values = actual["quadratures"][channel]
        np.testing.assert_allclose(values, expected.quadratures[channel][:len(values)],
                                   rtol=2e-11, atol=1e-16)


@pytest.mark.parametrize("shape", ["identity", "identity_width", "gaussian_linear",
                                   "lab_gaussian_linear_cosine"])
@pytest.mark.parametrize("delay", [-2, 0, 2, 100])
@pytest.mark.parametrize("threshold", [None, 0.8])
def test_native_projection_and_rolling_variance_match_python_reference(
    native_runner: list[str], shape: str, delay: int, threshold: float | None,
) -> None:
    formula = NullifierSpec("custom", (
        NullifierTerm("a", 0, 2), NullifierTerm("a", 1, -0.5),
        NullifierTerm("b", 2, -3),
    ), start=1)
    config = ClusterProcessingConfig(
        mode_width_seconds=308e-9, measurement_frequency_hz=6e6,
        mode_shape="identity" if shape == "identity_width" else shape,
        mode_samples=8 if shape == "identity" else None,
        min_samples_per_mode=4, mode_oversample=1000, channel_b_delay_samples=delay,
        variance_window_modes=5, history_modes=100, collection_guard_seconds=8e-9,
        collection_end_guard_samples=3, nullifiers=(formula,), gate_absolute_threshold=threshold,
    )
    rng = np.random.default_rng(918)
    a = rng.normal(0, 0.2, 4096).astype(np.float32)
    b = rng.normal(0, 0.3, 4096).astype(np.float32)
    native_keys = {
        "modeWidthSeconds", "measurementFrequencyHz", "modeShape", "modeSamples",
        "minSamplesPerMode", "modeOversample", "channelBDelaySamples", "varianceWindowModes",
        "historyModes", "collectionGuardSeconds", "collectionEndGuardSamples", "nullifiers",
        "gateAbsoluteThreshold",
    }
    settings = {key: value for key, value in config_to_dict(config).items() if key in native_keys}
    settings.update({"captureSamples": 4096, "varianceEstimator": "rolling"})
    commands = [{"command": "configure", "config": settings},
                {"command": "capture", "samples": {"a": a.tolist(), "b": b.tolist()}}]
    reply = subprocess.run(
        native_runner, input="\n".join(json.dumps(command) for command in commands) + "\n",
        text=True, capture_output=True, timeout=30, check=True,
    )
    configured, actual = map(json.loads, reply.stdout.splitlines())
    assert configured["ok"], configured
    assert actual["ok"], actual
    # A native capture is a complete guarded segment. Feed only that admitted
    # span to the continuous reference, which intentionally has no segment end.
    reference = RealtimeClusterProcessor(replace(config, history_modes=1000)).feed(
        a[1:-3], b[1:-3], 125_000_000,
    )
    assert reference is not None
    expected = reference.nullifiers["custom"][:len(actual["nullifiers"]["custom"])]
    np.testing.assert_allclose(np.asarray(actual["nullifiers"]["custom"], dtype=np.float64),
                               expected, rtol=2e-11, atol=1e-16)
    # The native prefix is bounded, so its final centered variance window is
    # intentionally clipped at that boundary.
    from quantum_light_engine.post_processing import rolling_variance

    np.testing.assert_allclose(np.asarray(actual["variances"]["custom"], dtype=np.float64),
                               rolling_variance(expected, 5),
                               rtol=2e-10, atol=1e-20)
