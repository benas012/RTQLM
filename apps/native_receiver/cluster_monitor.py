"""Cluster-nullifier controls used by the native receiver."""

from __future__ import annotations

import json
import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, ttk
from typing import Any

from apps.native_receiver.nullifier_acquisition import (
    AcquisitionWindowControls,
    match_builtin_basis,
)
from apps.native_receiver.nullifier_renderer import PLOT_CHOICES, NullifierRendererMixin
from quantum_light_engine.post_processing import (
    DEFAULT_CLUSTER_P,
    DEFAULT_CLUSTER_X,
    ClusterProcessingConfig,
    config_from_dict,
    config_to_dict,
)

REFRESH_INTERVAL_MS = 200


class ClusterMonitorDialog(NullifierRendererMixin, tk.Toplevel):
    """Configuration, calibration controls, and live nullifier plot."""

    def __init__(self, parent: Any) -> None:
        super().__init__(parent)
        self.parent = parent
        self.palette = getattr(
            parent,
            "ui_colors",
            {
                "bg": "#10151d",
                "bg_deep": "#0b0f15",
                "surface": "#151b24",
                "surface_elevated": "#1a222d",
                "text": "#e8edf5",
                "text_secondary": "#a5b0c0",
                "border_subtle": "#273241",
                "plot_grid": "#202a37",
                "ch1": "#43c97b",
                "ch2": "#45c6e8",
            },
        )
        self.title("Cluster-nullifier monitor")
        self.geometry("1080x850")
        self.minsize(960, 740)
        self.configure(bg=self.palette["bg"])
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())
        self.bind("<Control-Return>", lambda _event: self._apply())
        self.status = tk.StringVar(value="Waiting for two-channel samples")
        self.readout = tk.StringVar(value="Shot-noise calibration required")
        self.plot_choice = tk.StringVar(value="Auto")
        self._refresh_after_id: str | None = None
        self._build()
        self._load(parent.cluster_processor.config)
        self.update_idletasks()
        width, height = self.winfo_width(), self.winfo_height()
        x = min(
            max(parent.winfo_rootx() + (parent.winfo_width() - width) // 2, 0),
            max(self.winfo_screenwidth() - width, 0),
        )
        y = min(
            max(parent.winfo_rooty() + (parent.winfo_height() - height) // 2, 0),
            max(self.winfo_screenheight() - height, 0),
        )
        self.geometry(f"+{x}+{y}")
        self._refresh_after_id = self.parent.after(REFRESH_INTERVAL_MS, self._refresh)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        header = ttk.Frame(self, padding=(16, 14, 16, 8), style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Cluster-nullifier monitor", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="Both homodyne phases must match the selected x / p basis; "
            "formulas remain editable.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        controls = ttk.LabelFrame(self, text="Temporal mode and variance", padding=10)
        controls.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 8))
        for column in range(8):
            controls.columnconfigure(column, weight=1 if column % 2 else 0)

        self.mode_width_us = tk.StringVar()
        self.frequency_mhz = tk.StringVar()
        self.variance_window_us = tk.StringVar()
        self.delay_samples = tk.StringVar()
        self.gate_threshold = tk.StringVar()
        self.minimum_samples = tk.StringVar()
        self.history_modes = tk.StringVar()
        self.mode_shape = tk.StringVar()
        self.variance_estimator = tk.StringVar()
        fields = (
            ("Mode width (µs)", self.mode_width_us),
            ("Mode bandwidth (MHz)", self.frequency_mhz),
            ("Variance window (µs)", self.variance_window_us),
            ("B delay (samples)", self.delay_samples),
            ("Artifact reject |V| (optional)", self.gate_threshold),
            ("Minimum samples/mode", self.minimum_samples),
            ("Maximum modes/window", self.history_modes),
        )
        for index, (label, variable) in enumerate(fields):
            row, pair = divmod(index, 3)
            column = pair * 2
            ttk.Label(controls, text=label).grid(row=row, column=column, sticky="w", padx=(0, 5))
            ttk.Entry(controls, textvariable=variable, width=14).grid(
                row=row, column=column + 1, sticky="ew", padx=(0, 14), pady=3
            )
        ttk.Label(controls, text="Analytic shape").grid(row=3, column=0, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.mode_shape,
            values=("gaussian_linear", "gaussian_linear_cosine"),
            state="readonly",
            width=24,
        ).grid(row=3, column=1, columnspan=2, sticky="w", pady=3)
        ttk.Label(
            controls,
            text="Amplitude rejection may bias variance; use timing for lock exclusion.",
        ).grid(row=3, column=3, columnspan=4, sticky="w", padx=(8, 0))
        ttk.Label(controls, text="Variance estimator").grid(row=4, column=0, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.variance_estimator,
            values=("ensemble", "rolling"),
            state="readonly",
            width=24,
        ).grid(row=4, column=1, columnspan=2, sticky="w", pady=3)
        ttk.Label(
            controls,
            text="Ensemble: repeat aligned windows. Rolling: variance across modes.",
        ).grid(row=4, column=3, columnspan=4, sticky="w", padx=(8, 0))

        acquisition_frame = ttk.LabelFrame(self, text="Nullifier acquisition window", padding=8)
        acquisition_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 4))
        self.acquisition = AcquisitionWindowControls(acquisition_frame)
        self.acquisition.pack(fill="x")
        ttk.Label(
            acquisition_frame,
            text="Periodic starts with locking. Triggered uses sampled CH1 / CH2, "
            "independently of the waveform display trigger.",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Button(
            acquisition_frame,
            text="Load selected 1D basis preset",
            command=self._load_preset,
            style="Compact.TButton",
        ).pack(anchor="w", pady=(4, 0))

        editors = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        editors.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 8))
        mode_frame = ttk.LabelFrame(editors, text="Explicit mode template (JSON; [] = analytic)")
        self.mode_text = tk.Text(
            mode_frame,
            height=6,
            bg=self.palette["bg_deep"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            highlightbackground=self.palette["border_subtle"],
            highlightthickness=1,
        )
        self.mode_text.pack(fill="both", expand=True, padx=6, pady=6)
        editors.add(mode_frame, weight=1)
        nullifier_frame = ttk.LabelFrame(editors, text="Nullifiers (JSON)")
        self.nullifier_text = tk.Text(
            nullifier_frame,
            height=6,
            bg=self.palette["bg_deep"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            highlightbackground=self.palette["border_subtle"],
            highlightthickness=1,
        )
        self.nullifier_text.pack(fill="both", expand=True, padx=6, pady=6)
        editors.add(nullifier_frame, weight=2)

        chart_frame = ttk.LabelFrame(
            self, text="Live nullifiers · ensemble per mode / rolling diagnostic", padding=6
        )
        chart_frame.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 8))
        chart_frame.columnconfigure(0, weight=1)
        chart_frame.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            chart_frame,
            bg=self.palette["bg_deep"],
            highlightthickness=1,
            highlightbackground=self.palette["border_subtle"],
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        footer = ttk.Frame(self, padding=(16, 4, 16, 14), style="Panel.TFrame")
        footer.grid(row=5, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status).grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=self.readout).grid(row=1, column=0, sticky="w")
        ttk.Combobox(
            footer, textvariable=self.plot_choice, values=PLOT_CHOICES, state="readonly", width=15
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Button(
            footer, text="Apply configuration", style="Accent.TButton", command=self._apply
        ).grid(row=0, column=1, rowspan=2, padx=(8, 0))
        ttk.Button(
            footer,
            text="Start shot-noise calibration",
            command=self._start_calibration,
        ).grid(row=0, column=2, rowspan=2, padx=(8, 0))
        ttk.Button(footer, text="Finish calibration", command=self._finish_calibration).grid(
            row=0, column=3, rowspan=2, padx=(8, 0)
        )
        ttk.Button(
            footer,
            text="Clear calibration",
            style="Danger.TButton",
            command=self._clear_calibration,
        ).grid(row=0, column=4, rowspan=2, padx=(8, 0))

    def _load(self, config: ClusterProcessingConfig) -> None:
        data = config_to_dict(config)
        self.mode_width_us.set(f"{config.mode_width_seconds * 1e6:g}")
        self.frequency_mhz.set(f"{config.measurement_frequency_hz / 1e6:g}")
        self.variance_window_us.set(f"{config.variance_window_seconds * 1e6:g}")
        self.delay_samples.set(str(config.channel_b_delay_samples))
        self.gate_threshold.set(
            "" if config.gate_absolute_threshold is None else f"{config.gate_absolute_threshold:g}"
        )
        self.minimum_samples.set(str(config.min_samples_per_mode))
        self.history_modes.set(str(config.history_modes))
        self.mode_shape.set(str(data["modeShape"]))
        self.variance_estimator.set(str(data["varianceEstimator"]))
        self.acquisition.load(config)
        self.mode_text.delete("1.0", tk.END)
        self.mode_text.insert("1.0", json.dumps(data["explicitModeTemplate"]))
        self.nullifier_text.delete("1.0", tk.END)
        self.nullifier_text.insert("1.0", json.dumps(data["nullifiers"], indent=2))

    def _config(self) -> ClusterProcessingConfig:
        gate = self.gate_threshold.get().strip()
        data = config_to_dict(self.parent.cluster_processor.config)
        data.update(
            {
                "modeWidthSeconds": float(self.mode_width_us.get()) * 1e-6,
                "measurementFrequencyHz": float(self.frequency_mhz.get()) * 1e6,
                "varianceWindowSeconds": float(self.variance_window_us.get()) * 1e-6,
                "channelBDelaySamples": int(self.delay_samples.get()),
                "gateAbsoluteThreshold": None if not gate else float(gate),
                "minSamplesPerMode": int(self.minimum_samples.get()),
                "historyModes": int(self.history_modes.get()),
                "explicitModeTemplate": json.loads(self.mode_text.get("1.0", tk.END)),
                "nullifiers": json.loads(self.nullifier_text.get("1.0", tk.END)),
                "modeShape": self.mode_shape.get(),
                "varianceEstimator": self.variance_estimator.get(),
            }
        )
        self.acquisition.apply_to(data)
        return match_builtin_basis(config_from_dict(data))

    def _load_preset(self) -> None:
        basis = self.acquisition.variables["basis"].get()
        preset = DEFAULT_CLUSTER_X if basis == "x" else DEFAULT_CLUSTER_P
        config = replace(self.parent.cluster_processor.config, nullifiers=(preset,))
        self.nullifier_text.delete("1.0", tk.END)
        self.nullifier_text.insert(
            "1.0", json.dumps(config_to_dict(config)["nullifiers"], indent=2)
        )

    def _apply(self) -> None:
        try:
            config = self._config()
            self.parent.apply_cluster_config(config)
            self.status.set("Configuration applied; shot-noise calibration was cleared")
        except Exception as exc:
            messagebox.showerror("Invalid cluster configuration", str(exc), parent=self)

    def _start_calibration(self) -> None:
        try:
            self.parent.start_cluster_calibration()
            self.status.set("Acquiring shot noise; switch to the shot-noise measurement")
        except Exception as exc:
            messagebox.showerror("Calibration error", str(exc), parent=self)

    def _finish_calibration(self) -> None:
        try:
            coefficients = self.parent.finish_cluster_calibration()
            self.status.set(
                f"Calibration complete: A x {coefficients['a']:.5g}, B x {coefficients['b']:.5g}"
            )
        except Exception as exc:
            messagebox.showerror("Calibration error", str(exc), parent=self)

    def _clear_calibration(self) -> None:
        self.parent.clear_cluster_calibration()
        self.status.set("Shot-noise calibration cleared")

    def _refresh(self) -> None:
        if not self.winfo_exists():
            return
        result, error = self.parent.cluster_snapshot()
        if error:
            self.status.set(error)
            self.readout.set("Use two channels and a sample rate adequate for the configured mode")
            self._draw_message(error)
        elif result is None:
            self._draw_message("Waiting for processed modes")
        else:
            self.status.set(result.status)
            self._draw_result(result)
        self._refresh_after_id = self.parent.after(REFRESH_INTERVAL_MS, self._refresh)

    def _close(self) -> None:
        if self._refresh_after_id is not None:
            try:
                self.parent.after_cancel(self._refresh_after_id)
            except tk.TclError:
                pass
            self._refresh_after_id = None
        self.destroy()
