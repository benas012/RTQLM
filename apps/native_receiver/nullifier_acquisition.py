"""Sample-clock acquisition controls shared by the nullifier tab and editor."""

from __future__ import annotations

import tkinter as tk
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
    if config.mode_shape == "lab_gaussian_linear_cosine" and config.nullifiers in (
        (LAB_CLUSTER_X,), (LAB_CLUSTER_P,), (DEFAULT_CLUSTER_X,), (DEFAULT_CLUSTER_P,)
    ):
        preset = LAB_CLUSTER_X if config.measurement_basis == "x" else LAB_CLUSTER_P
        return replace(config, nullifiers=(preset,))
    if config.nullifiers in ((DEFAULT_CLUSTER_X,), (DEFAULT_CLUSTER_P,)):
        preset = DEFAULT_CLUSTER_X if config.measurement_basis == "x" else DEFAULT_CLUSTER_P
        return replace(config, nullifiers=(preset,))
    return config


class AnalysisControls(ttk.Frame):
    """Choose the calculation and explicit point counts without changing timing."""

    def __init__(self, parent: Any, *, compact: bool = False) -> None:
        super().__init__(parent, style="Toolbar.TFrame")
        self.compact = compact
        self.variables = {
            name: tk.StringVar(self)
            for name in ("calculation", "mode_samples", "window_modes", "calibration", "channel")
        }
        self._field("Calculation", "calculation", 0, 0, values=CALCULATION_CHOICES, width=12)
        self._field("N_mode (samples)", "mode_samples", 0, 2, width=7)
        self._field("Rolling window (modes)", "window_modes", 0, 4, width=7)
        if not compact:
            self._field(
                "Single-mode channel", "channel", 1, 0,
                values=("Both", "CH1", "CH2"), width=12,
            )
            self._field(
                "Shot-noise variance", "calibration", 1, 2,
                values=tuple(CALIBRATION_CHOICES), width=26, columnspan=3,
            )
            ttk.Label(
                self,
                text="Blank point counts use the mode width and variance time.",
                style="Toolbar.TLabel",
            ).grid(row=2, column=0, columnspan=6, sticky="w")
        self.columnconfigure(6, weight=1)

    def _field(
        self, title: str, name: str, row: int, column: int, *,
        values: tuple[str, ...] | None = None, width: int = 8, columnspan: int = 1,
    ) -> None:
        ttk.Label(self, text=title, style="Toolbar.TLabel").grid(
            row=row, column=column, sticky="w", padx=(0, 5), pady=0 if self.compact else 3,
        )
        if values is None:
            widget: Any = ttk.Entry(self, textvariable=self.variables[name], width=width)
        else:
            widget = ttk.Combobox(
                self, textvariable=self.variables[name], values=values,
                state="readonly", width=width,
            )
        widget.grid(
            row=row, column=column + 1, columnspan=columnspan,
            sticky="w", padx=(0, 10), pady=0 if self.compact else 3,
        )

    def load(self, config: ClusterProcessingConfig) -> None:
        self.variables["calculation"].set(
            "Single-mode" if config.analysis_mode == "single" else "Cluster"
        )
        self.variables["mode_samples"].set(
            "" if config.mode_samples is None else str(config.mode_samples)
        )
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

    def apply_to(self, data: dict[str, object]) -> None:
        mode_samples = self.variables["mode_samples"].get().strip()
        window_modes = self.variables["window_modes"].get().strip()
        data.update(
            analysisMode=(
                "single" if self.variables["calculation"].get() == "Single-mode" else "cluster"
            ),
            modeSamples=None if not mode_samples else int(mode_samples),
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
        self._field("Measured basis", "basis", 0, 2, values=("x", "p"), width=4)
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
        self.columnconfigure(8, weight=1)

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
        ttk.Label(self, text=title, style="Toolbar.TLabel").grid(
            row=row, column=column, sticky="w", padx=(0, 5), pady=0 if self.compact else 3
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
            row=row, column=column + 1, sticky="w", padx=(0, 10),
            pady=0 if self.compact else 3,
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
