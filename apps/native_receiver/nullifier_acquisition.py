"""Sample-clock acquisition controls shared by the nullifier tab and editor."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import replace
from tkinter import ttk
from typing import Any

from quantum_light_engine.post_processing import (
    DEFAULT_CLUSTER_P,
    DEFAULT_CLUSTER_X,
    LAB_CLUSTER_P,
    LAB_CLUSTER_X,
    ClusterProcessingConfig,
    config_to_dict,
    mode_sample_count,
)

WINDOW_CHOICES = ("Periodic", "Continuous", "Triggered")
CALCULATION_CHOICES = ("Cluster", "Single-mode")
CALIBRATION_CHOICES = {
    "Automatic": "auto",
    "Whole accepted trace": "pooled",
    "Average collection variances": "segment_mean",
}


def match_builtin_basis(config: ClusterProcessingConfig) -> ClusterProcessingConfig:
    """Switch the single built-in 1D row with its basis, preserving custom formulas."""
    if config.analysis_mode == "single":
        return config
    if config.nullifiers in ((LAB_CLUSTER_X,), (LAB_CLUSTER_P,)) or (
        config.mode_shape == "lab_gaussian_linear_cosine"
        and config.nullifiers in ((DEFAULT_CLUSTER_X,), (DEFAULT_CLUSTER_P,))
    ):
        preset = LAB_CLUSTER_X if config.measurement_basis == "x" else LAB_CLUSTER_P
        return replace(config, nullifiers=(preset,))
    if config.nullifiers in ((DEFAULT_CLUSTER_X,), (DEFAULT_CLUSTER_P,)):
        preset = DEFAULT_CLUSTER_X if config.measurement_basis == "x" else DEFAULT_CLUSTER_P
        return replace(config, nullifiers=(preset,))
    return config


class AnalysisControls(ttk.Frame):
    """Edit temporal width; report the sample count determined by the ADC rate."""

    def __init__(
        self, parent: Any, *, compact: bool = False,
        sample_rate_provider: Callable[[], float | None] | None = None,
    ) -> None:
        super().__init__(parent, style="Toolbar.TFrame")
        self.compact = compact
        self.sample_rate_provider = sample_rate_provider or (lambda: None)
        self._legacy_points: int | None = None
        self._loading = False
        self._mode_shape = "lab_gaussian_linear_cosine"
        self.sampling_readout = tk.StringVar(self, value="Samples / mode: select a device")
        self.variables = {
            name: tk.StringVar(self)
            for name in ("calculation", "mode_width_us", "window_modes", "calibration", "channel")
        }
        self._field("Calculation", "calculation", 0, 0, values=CALCULATION_CHOICES, width=12)
        self._field("T width (µs)", "mode_width_us", 0, 2, width=12)
        self._field("Variance window · modes", "window_modes", 0, 4, width=7)
        if not compact:
            self._field(
                "Single-mode channel", "channel", 1, 0,
                values=("Both", "CH1", "CH2"), width=12,
            )
            self._field(
                "Shot-noise variance", "calibration", 1, 2,
                values=tuple(CALIBRATION_CHOICES), width=26, columnspan=3,
            )
        ttk.Label(self, textvariable=self.sampling_readout, style="Toolbar.TLabel",
                   wraplength=470).grid(row=2 if compact else 4, column=0, columnspan=3,
                                        sticky="w", pady=(4, 4))
        self.variables["mode_width_us"].trace_add("write", self._width_changed)

    def _width_changed(self, *_args: Any) -> None:
        if not self._loading:
            self._legacy_points = None
            self.refresh_sampling()

    def set_mode_shape(self, shape: str) -> None:
        self._mode_shape = shape
        self.refresh_sampling()

    def refresh_sampling(self) -> None:
        if self._loading:
            return
        rate = self.sample_rate_provider()
        if rate is None:
            self.sampling_readout.set("Samples / mode: select a device")
            return
        if self._legacy_points is not None:
            self._loading = True
            self.variables["mode_width_us"].set(f"{self._legacy_points / rate * 1e6:.12g}")
            self._loading = False
            self._legacy_points = None
        try:
            width = float(self.variables["mode_width_us"].get()) * 1e-6
            points = mode_sample_count(width, rate, self._mode_shape)
            actual_us = points / rate * 1e6
            self.sampling_readout.set(
                f"Samples / mode: {points} · actual width: {actual_us:.9g} µs "
                f"at {rate / 1000:.6g} kS/s"
            )
        except ValueError:
            self.sampling_readout.set("Enter a positive T width to determine samples / mode")

    def _field(
        self, title: str, name: str, row: int, column: int, *,
        values: tuple[str, ...] | None = None, width: int = 8, columnspan: int = 1,
    ) -> None:
        self.columnconfigure(column // 2, weight=1, uniform="analysis_fields")
        ttk.Label(self, text=title, style="Toolbar.TLabel").grid(
            row=row * 2, column=column // 2, sticky="w", padx=(0, 12), pady=(0, 5),
        )
        if values is None:
            widget: Any = ttk.Entry(self, textvariable=self.variables[name], width=width)
        else:
            widget = ttk.Combobox(
                self, textvariable=self.variables[name], values=values,
                state="readonly", width=width,
            )
        widget.grid(
            row=row * 2 + 1, column=column // 2, columnspan=(columnspan + 1) // 2,
            sticky="ew", padx=(0, 12), pady=(0, 8),
        )

    def load(self, config: ClusterProcessingConfig) -> None:
        self._loading = True
        self._mode_shape = config.mode_shape
        self._legacy_points = config.mode_samples
        self.variables["calculation"].set(
            "Single-mode" if config.analysis_mode == "single" else "Cluster"
        )
        self.variables["mode_width_us"].set(f"{config.mode_width_seconds * 1e6:.12g}")
        self.variables["window_modes"].set(
            "" if config.variance_window_modes is None else str(config.variance_window_modes)
        )
        self.variables["calibration"].set(next(
            title for title, estimator in CALIBRATION_CHOICES.items()
            if estimator == config.calibration_estimator
        ))
        self.variables["channel"].set(
            {"both": "Both", "a": "CH1", "b": "CH2"}[config.single_channel]
        )
        self._loading = False
        self.refresh_sampling()

    def apply_to(self, data: dict[str, object]) -> None:
        self.refresh_sampling()
        if self._legacy_points is not None:
            raise ValueError("Select a device to convert the saved sample count to a T width")
        width = float(self.variables["mode_width_us"].get()) * 1e-6
        rate = self.sample_rate_provider()
        points = mode_sample_count(width, rate or 1.0, str(data["modeShape"]))
        if rate is not None:
            minimum = int(data["minSamplesPerMode"])
            if points < minimum or points > 4096:
                raise ValueError(f"T width gives {points} samples / mode at {rate:g} S/s; "
                                 f"{minimum}-4096 are required. "
                                 "Change T width or the sampling rate.")
            template = data.get("explicitModeTemplate")
            if template and len(template) != points:
                raise ValueError(f"The explicit template must contain {points} weights for "
                                 "the selected T width and sample rate")
        window_modes = self.variables["window_modes"].get().strip()
        data.update(
            analysisMode=(
                "single" if self.variables["calculation"].get() == "Single-mode" else "cluster"
            ),
            modeSamples=None,
            modeWidthSeconds=width,
            varianceWindowModes=None if not window_modes else int(window_modes),
            calibrationEstimator=CALIBRATION_CHOICES[self.variables["calibration"].get()],
            singleChannel={"Both": "both", "CH1": "a", "CH2": "b"}[self.variables["channel"].get()],
        )
        if data["analysisMode"] == "single":
            data["varianceEstimator"] = "rolling"
            if data["varianceWindowModes"] is None:
                data["varianceWindowModes"] = 100


def acquisition_description(config: ClusterProcessingConfig) -> str:
    data = config_to_dict(config)
    mode = str(data["windowMode"])
    basis = str(data["measurementBasis"])
    if mode == "periodic":
        detail = (
            f"{float(data['lockSeconds']) * 1e3:g} ms lock → "
            f"{float(data['collectSeconds']) * 1e3:g} ms collect; repeat"
        )
    elif mode == "triggered":
        channel = "CH1" if data["triggerChannel"] == "a" else "CH2"
        detail = (
            f"{channel} {data['triggerEdge']} at {float(data['triggerLevelVolts']):g} V; "
            f"delay {float(data['triggerDelaySeconds']) * 1e3:g} ms, "
            f"collect {float(data['collectSeconds']) * 1e3:g} ms"
        )
    else:
        detail = "Collect continuously"
    calculation = "Cluster"
    if config.analysis_mode == "single":
        channel = {"a": "CH1", "b": "CH2", "both": "CH1 / CH2"}[config.single_channel]
        calculation = f"Single-mode {channel}"
    return f"{calculation} · {basis} basis · {detail}"


class AcquisitionWindowControls(ttk.Frame):
    """Edit timing independently from the waveform display trigger."""

    def __init__(self, parent: Any, *, compact: bool = False) -> None:
        super().__init__(parent, style="Toolbar.TFrame")
        self.compact = compact
        self.variables = {
            name: tk.StringVar(self)
            for name in (
                "mode",
                "basis",
                "lock",
                "collect",
                "channel",
                "edge",
                "level",
                "delay",
                "holdoff",
                "hysteresis",
                "guard",
                "end_guard",
            )
        }
        self._field("Window", "mode", 0, 0, values=WINDOW_CHOICES, width=11)
        self._field("Basis", "basis", 0, 2, values=("x", "p"), width=4)
        self._field("Lock (ms)", "lock", 0, 4, width=8)
        self._field("Collect (ms)", "collect", 0, 6, width=8)
        if not compact:
            self._field("Trigger channel", "channel", 1, 0, values=("CH1", "CH2"), width=11)
            self._field("Edge", "edge", 1, 2, values=("rising", "falling"), width=9)
            self._field("Level (V)", "level", 1, 4, width=8)
            self._field("Delay (ms)", "delay", 1, 6, width=8)
            self._field("Holdoff (ms)", "holdoff", 2, 0, width=11)
            self._field("Hysteresis (V)", "hysteresis", 2, 2, width=9)
            self._field("Collect guard (ms)", "guard", 2, 4, width=8)
            self._field("End guard (samples)", "end_guard", 2, 6, width=8)

    def _field(
        self,
        title: str,
        name: str,
        row: int,
        column: int,
        *,
        values: tuple[str, ...] | None = None,
        width: int = 8,
    ) -> None:
        self.columnconfigure(column // 2, weight=1, uniform="timing_fields")
        ttk.Label(self, text=title, style="Toolbar.TLabel").grid(
            row=row * 2, column=column // 2, sticky="w", padx=(0, 12), pady=(0, 5),
        )
        if values is None:
            widget: Any = ttk.Entry(self, textvariable=self.variables[name], width=width)
        else:
            widget = ttk.Combobox(
                self,
                textvariable=self.variables[name],
                values=values,
                state="readonly",
                width=width,
            )
        widget.grid(
            row=row * 2 + 1, column=column // 2, sticky="ew", padx=(0, 12), pady=(0, 8),
        )

    def load(self, config: ClusterProcessingConfig) -> None:
        data = config_to_dict(config)
        values = {
            "mode": str(data["windowMode"]).title(),
            "basis": str(data["measurementBasis"]),
            "channel": "CH1" if data["triggerChannel"] == "a" else "CH2",
            "edge": str(data["triggerEdge"]),
            "level": f"{float(data['triggerLevelVolts']):g}",
            "hysteresis": f"{float(data['triggerHysteresisVolts']):g}",
            "end_guard": str(data["collectionEndGuardSamples"]),
        }
        for name, key in (
            ("lock", "lockSeconds"),
            ("collect", "collectSeconds"),
            ("delay", "triggerDelaySeconds"),
            ("holdoff", "triggerHoldoffSeconds"),
            ("guard", "collectionGuardSeconds"),
        ):
            values[name] = f"{float(data[key]) * 1e3:g}"
        for name, value in values.items():
            self.variables[name].set(value)

    def apply_to(self, data: dict[str, object]) -> None:
        def get(name: str) -> str:
            return self.variables[name].get().strip()

        data.update(
            windowMode=get("mode").lower(),
            measurementBasis=get("basis"),
            lockSeconds=float(get("lock")) * 1e-3,
            collectSeconds=float(get("collect")) * 1e-3,
            triggerChannel="a" if get("channel") == "CH1" else "b",
            triggerEdge=get("edge"),
            triggerLevelVolts=float(get("level")),
            triggerDelaySeconds=float(get("delay")) * 1e-3,
            triggerHoldoffSeconds=float(get("holdoff")) * 1e-3,
            triggerHysteresisVolts=float(get("hysteresis")),
            collectionGuardSeconds=float(get("guard")) * 1e-3,
            collectionEndGuardSamples=int(get("end_guard")),
        )
