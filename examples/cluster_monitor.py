from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

import numpy as np

from quantum_light_engine.post_processing import (
    ClusterProcessingConfig,
    ClusterProcessingResult,
    config_from_dict,
    config_to_dict,
)

COLORS = ("#22c55e", "#38bdf8", "#f59e0b", "#f472b6", "#a78bfa")
REFRESH_INTERVAL_MS = 200


class ClusterMonitorDialog(tk.Toplevel):
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
        self.geometry("980x760")
        self.minsize(760, 600)
        self.configure(bg=self.palette["bg"])
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())
        self.bind("<Control-Return>", lambda _event: self._apply())
        self.status = tk.StringVar(value="Waiting for two-channel samples")
        self.readout = tk.StringVar(value="Shot-noise calibration required")
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
        self._refresh_after_id = self.after(REFRESH_INTERVAL_MS, self._refresh)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        header = ttk.Frame(self, padding=(16, 14, 16, 8), style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Cluster-nullifier monitor", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="Configure temporal modes, calibrate shot noise, and inspect rolling variance.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        controls = ttk.LabelFrame(self, text="Temporal mode and rolling variance", padding=10)
        controls.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        for column in range(8):
            controls.columnconfigure(column, weight=1 if column % 2 else 0)

        self.mode_width_us = tk.StringVar()
        self.frequency_mhz = tk.StringVar()
        self.variance_window_us = tk.StringVar()
        self.delay_samples = tk.StringVar()
        self.gate_threshold = tk.StringVar()
        self.minimum_samples = tk.StringVar()
        fields = (
            ("Mode width (µs)", self.mode_width_us),
            ("Frequency (MHz)", self.frequency_mhz),
            ("Variance window (µs)", self.variance_window_us),
            ("B delay (samples)", self.delay_samples),
            ("Gate |V| threshold", self.gate_threshold),
            ("Minimum samples/mode", self.minimum_samples),
        )
        for index, (label, variable) in enumerate(fields):
            row, pair = divmod(index, 3)
            column = pair * 2
            ttk.Label(controls, text=label).grid(row=row, column=column, sticky="w", padx=(0, 5))
            ttk.Entry(controls, textvariable=variable, width=14).grid(
                row=row, column=column + 1, sticky="ew", padx=(0, 14), pady=3
            )

        editors = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        editors.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
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

        chart_frame = ttk.LabelFrame(self, text="Live rolling nullifier variance", padding=6)
        chart_frame.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 8))
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
        footer.grid(row=4, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status).grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=self.readout).grid(row=1, column=0, sticky="w")
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
        self.mode_text.delete("1.0", tk.END)
        self.mode_text.insert("1.0", json.dumps(data["explicitModeTemplate"]))
        self.nullifier_text.delete("1.0", tk.END)
        self.nullifier_text.insert("1.0", json.dumps(data["nullifiers"], indent=2))

    def _config(self) -> ClusterProcessingConfig:
        gate = self.gate_threshold.get().strip()
        data: dict[str, object] = {
            "modeWidthSeconds": float(self.mode_width_us.get()) * 1e-6,
            "measurementFrequencyHz": float(self.frequency_mhz.get()) * 1e6,
            "varianceWindowSeconds": float(self.variance_window_us.get()) * 1e-6,
            "channelBDelaySamples": int(self.delay_samples.get()),
            "gateAbsoluteThreshold": None if not gate else float(gate),
            "minSamplesPerMode": int(self.minimum_samples.get()),
            "explicitModeTemplate": json.loads(self.mode_text.get("1.0", tk.END)),
            "nullifiers": json.loads(self.nullifier_text.get("1.0", tk.END)),
        }
        return config_from_dict(data)

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
        self._refresh_after_id = self.after(REFRESH_INTERVAL_MS, self._refresh)

    def _draw_message(self, message: str) -> None:
        self.canvas.delete("all")
        self.canvas.create_text(
            max(self.canvas.winfo_width(), 1) / 2,
            max(self.canvas.winfo_height(), 1) / 2,
            text=message,
            fill=self.palette["text_secondary"],
            width=max(self.canvas.winfo_width() - 80, 100),
            justify="center",
        )

    def _draw_result(self, result: ClusterProcessingResult) -> None:
        traces = result.squeezing_db if result.calibrated else result.nullifiers
        if not traces:
            self._draw_message(result.status)
            self.readout.set(result.status)
            return
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 100)
        height = max(self.canvas.winfo_height(), 100)
        left, top, right, bottom = 58, 18, 16, 34
        plot_width = max(width - left - right, 1)
        plot_height = max(height - top - bottom, 1)
        finite_sets = [values[np.isfinite(values)][-2000:] for values in traces.values()]
        finite_sets = [values for values in finite_sets if values.size]
        if not finite_sets:
            self._draw_message("Collecting enough modes for the rolling variance")
            return
        if result.calibrated:
            low = min(-10.0, min(float(np.min(values)) for values in finite_sets))
            high = max(3.0, max(float(np.max(values)) for values in finite_sets))
            unit = "dB relative to shot noise"
        else:
            bound = max(max(float(np.max(np.abs(values))) for values in finite_sets), 1e-12)
            low, high, unit = -bound, bound, "uncalibrated nullifier amplitude"
        if high <= low:
            high = low + 1
        for index in range(6):
            y = top + index * plot_height / 5
            value = high - index * (high - low) / 5
            self.canvas.create_line(left, y, left + plot_width, y, fill=self.palette["plot_grid"])
            self.canvas.create_text(
                left - 7,
                y,
                text=f"{value:.2g}",
                fill=self.palette["text_secondary"],
                anchor="e",
            )
        latest: list[str] = []
        for trace_index, (name, values) in enumerate(traces.items()):
            values = np.asarray(values[-2000:], dtype=np.float64)
            finite = np.isfinite(values)
            if not np.any(finite):
                continue
            indices = np.arange(values.size)[finite]
            plotted = values[finite]
            x_denominator = max(values.size - 1, 1)
            coords: list[float] = []
            for sample_index, value in zip(indices, plotted, strict=True):
                coords.extend(
                    (
                        left + float(sample_index) / x_denominator * plot_width,
                        top + (high - float(value)) / (high - low) * plot_height,
                    )
                )
            color = COLORS[trace_index % len(COLORS)]
            if len(coords) >= 4:
                self.canvas.create_line(*coords, fill=color, width=2)
            self.canvas.create_text(
                left + 10 + trace_index * 160,
                top + 10,
                text=name,
                fill=color,
                anchor="w",
                font=("Segoe UI", 9, "bold"),
            )
            latest.append(f"{name}={float(plotted[-1]):.3f}")
        duration_ms = (
            max(len(values) for values in traces.values()) * result.mode_period_seconds * 1e3
        )
        self.canvas.create_text(
            left,
            height - 12,
            text=f"last {duration_ms:.3g} ms",
            fill=self.palette["text_secondary"],
            anchor="w",
        )
        self.canvas.create_text(
            left + plot_width,
            height - 12,
            text=unit,
            fill=self.palette["text_secondary"],
            anchor="e",
        )
        self.readout.set(" · ".join(latest) if latest else result.status)

    def _close(self) -> None:
        if self._refresh_after_id is not None:
            try:
                self.after_cancel(self._refresh_after_id)
            except tk.TclError:
                pass
            self._refresh_after_id = None
        self.destroy()
