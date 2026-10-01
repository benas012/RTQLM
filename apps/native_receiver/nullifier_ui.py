"""Embedded nullifier analysis tab."""

from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, ttk
from typing import Any

from apps.native_receiver.constants import COLORS
from apps.native_receiver.nullifier_acquisition import (
    AcquisitionWindowControls,
    acquisition_description,
    match_builtin_basis,
)
from apps.native_receiver.nullifier_renderer import PLOT_CHOICES, NullifierRendererMixin
from quantum_light_engine.post_processing import (
    DEFAULT_CLUSTER_P,
    DEFAULT_CLUSTER_X,
    config_from_dict,
    config_to_dict,
)

REFRESH_INTERVAL_MS = 200


class NullifierMonitorPanel(NullifierRendererMixin, ttk.Frame):
    def __init__(self, parent: Any, receiver: Any) -> None:
        super().__init__(parent, style="Surface.TFrame")
        self.parent = receiver
        self.palette = getattr(receiver, "ui_colors", COLORS)
        self.status = tk.StringVar(self, value="Waiting for two-channel samples")
        self.readout = tk.StringVar(self, value="Shot-noise calibration required")
        self.schedule = tk.StringVar(self)
        self.plot_choice = tk.StringVar(self, value="Auto")
        self.variance_estimator = tk.StringVar(self)
        self._refresh_after_id: str | None = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(self, style="Toolbar.TFrame", padding=(8, 5))
        toolbar.grid(row=0, column=0, sticky="ew")
        self.acquisition = AcquisitionWindowControls(toolbar, compact=True)
        self.acquisition.pack(side="left", fill="x", expand=True)

        actions = ttk.Frame(self, style="Toolbar.TFrame", padding=(8, 2))
        actions.grid(row=1, column=0, sticky="ew")
        ttk.Button(
            actions,
            text="Configure…",
            command=receiver.open_cluster_monitor,
            style="Compact.TButton",
        ).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Button(
            actions, text="Use 1D preset", command=self._preset, style="Compact.TButton"
        ).grid(row=0, column=1, sticky="w", padx=(0, 12))
        ttk.Button(
            actions, text="Apply timing / basis", command=self._apply, style="Compact.TButton"
        ).grid(row=0, column=2, sticky="w", padx=(0, 12))
        ttk.Label(actions, text="Plot", style="Toolbar.TLabel").grid(row=0, column=3, sticky="w")
        ttk.Combobox(
            actions,
            textvariable=self.plot_choice,
            values=PLOT_CHOICES,
            state="readonly",
            width=14,
        ).grid(row=0, column=4, sticky="w", padx=5)
        calibration = ttk.Frame(actions, style="Toolbar.TFrame")
        calibration.grid(row=1, column=0, columnspan=5, sticky="w", pady=(3, 0))
        for title, command in (
            ("Start shot noise", self._start_calibration),
            ("Finish", self._finish_calibration),
            ("Clear", self._clear_calibration),
        ):
            ttk.Button(calibration, text=title, command=command, style="Compact.TButton").pack(
                side="left", padx=2
            )
        ttk.Label(calibration, text="Variance estimate", style="Toolbar.TLabel").pack(
            side="left", padx=(12, 5)
        )
        ttk.Combobox(
            calibration,
            textvariable=self.variance_estimator,
            values=("Repeated windows", "Rolling modes"),
            state="readonly",
            width=17,
        ).pack(side="left")
        self.canvas = tk.Canvas(
            self,
            height=120,
            bg=self.palette["bg_deep"],
            highlightthickness=1,
            highlightbackground=self.palette["border_subtle"],
        )
        self.canvas.grid(row=2, column=0, sticky="nsew")
        footer = ttk.Frame(self, style="Surface.TFrame", padding=(8, 3))
        footer.grid(row=3, column=0, sticky="ew")
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(footer, textvariable=self.readout, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(footer, textvariable=self.schedule, style="Muted.TLabel").pack(anchor="w")
        self.load_config(receiver.cluster_processor.config)
        self.bind("<Destroy>", self._destroyed, add="+")
        self._draw_message("Waiting for nullifier data · enable both channels")
        self._refresh_after_id = self.parent.after(REFRESH_INTERVAL_MS, self._refresh)

    def load_config(self, config: Any) -> None:
        self.acquisition.load(config)
        self.schedule.set(acquisition_description(config))
        self.variance_estimator.set(
            "Repeated windows" if config.variance_estimator == "ensemble" else "Rolling modes"
        )

    def _selected_config(self) -> Any:
        data = config_to_dict(self.parent.cluster_processor.config)
        self.acquisition.apply_to(data)
        data["varianceEstimator"] = (
            "ensemble" if self.variance_estimator.get() == "Repeated windows" else "rolling"
        )
        return match_builtin_basis(config_from_dict(data))

    def _apply(self) -> None:
        try:
            self.parent.apply_cluster_config(self._selected_config())
        except Exception as exc:
            messagebox.showerror("Invalid nullifier timing", str(exc), parent=self)

    def _preset(self) -> None:
        try:
            config = self._selected_config()
            spec = DEFAULT_CLUSTER_X if config.measurement_basis == "x" else DEFAULT_CLUSTER_P
            self.parent.apply_cluster_config(replace(config, nullifiers=(spec,)))
        except Exception as exc:
            messagebox.showerror("Invalid nullifier configuration", str(exc), parent=self)

    def _start_calibration(self) -> None:
        try:
            self.parent.start_cluster_calibration()
            self.status.set("Acquiring shot noise; use the shot-noise measurement")
        except Exception as exc:
            messagebox.showerror("Calibration error", str(exc), parent=self)

    def _finish_calibration(self) -> None:
        try:
            self.parent.finish_cluster_calibration()
            self.status.set("Shot-noise calibration complete")
        except Exception as exc:
            messagebox.showerror("Calibration error", str(exc), parent=self)

    def _clear_calibration(self) -> None:
        self.parent.clear_cluster_calibration()
        self.status.set("Shot-noise calibration cleared")

    def _refresh(self) -> None:
        result, error = self.parent.cluster_snapshot()
        if error:
            self.status.set(error)
            self.readout.set("Check both channels and the temporal-mode sample rate")
            self._draw_message(error)
        elif result is None:
            self.status.set(self.parent.cluster_acquisition_state())
            self._draw_message("Waiting for admitted temporal modes")
        else:
            self.status.set(f"{self.parent.cluster_acquisition_state()} · {result.status}")
            self._draw_result(result)
        description = acquisition_description(self.parent.cluster_processor.config)
        if result is not None:
            description += f" · actual mode {result.mode_period_seconds * 1e9:g} ns"
        self.schedule.set(description)
        self._refresh_after_id = self.parent.after(REFRESH_INTERVAL_MS, self._refresh)

    def _destroyed(self, event: Any) -> None:
        if event.widget is self and self._refresh_after_id is not None:
            try:
                self.parent.after_cancel(self._refresh_after_id)
            except tk.TclError:
                pass
            self._refresh_after_id = None
