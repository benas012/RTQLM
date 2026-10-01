"""Sample-clock acquisition controls shared by the nullifier tab and editor."""

from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from tkinter import ttk
from typing import Any

from quantum_light_engine.post_processing import (
    DEFAULT_CLUSTER_P,
    DEFAULT_CLUSTER_X,
    ClusterProcessingConfig,
    config_to_dict,
)

WINDOW_CHOICES = ("Periodic", "Continuous", "Triggered")


def match_builtin_basis(config: ClusterProcessingConfig) -> ClusterProcessingConfig:
    """Switch the single built-in 1D row with its basis, preserving custom formulas."""
    if config.nullifiers in ((DEFAULT_CLUSTER_X,), (DEFAULT_CLUSTER_P,)):
        preset = DEFAULT_CLUSTER_X if config.measurement_basis == "x" else DEFAULT_CLUSTER_P
        return replace(config, nullifiers=(preset,))
    return config


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
    return f"{basis} basis · {detail}"


class AcquisitionWindowControls(ttk.Frame):
    """Edit timing independently from the waveform display trigger."""

    def __init__(self, parent: Any, *, compact: bool = False) -> None:
        super().__init__(parent, style="Toolbar.TFrame")
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
            row=row, column=column, sticky="w", padx=(0, 5), pady=3
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
        widget.grid(row=row, column=column + 1, sticky="w", padx=(0, 10), pady=3)

    def load(self, config: ClusterProcessingConfig) -> None:
        data = config_to_dict(config)
        values = {
            "mode": str(data["windowMode"]).title(),
            "basis": str(data["measurementBasis"]),
            "channel": "CH1" if data["triggerChannel"] == "a" else "CH2",
            "edge": str(data["triggerEdge"]),
            "level": f"{float(data['triggerLevelVolts']):g}",
            "hysteresis": f"{float(data['triggerHysteresisVolts']):g}",
        }
        for name, key in (
            ("lock", "lockSeconds"),
            ("collect", "collectSeconds"),
            ("delay", "triggerDelaySeconds"),
            ("holdoff", "triggerHoldoffSeconds"),
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
        )
