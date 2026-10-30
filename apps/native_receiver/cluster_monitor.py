"""Cluster-nullifier controls used by the native receiver."""

from __future__ import annotations

import json
import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, ttk
from typing import Any

from apps.native_receiver.calibration_ui import CalibrationProgress
from apps.native_receiver.helpers import set_if_changed
from apps.native_receiver.nullifier_acquisition import (
    AcquisitionWindowControls,
    AnalysisControls,
    match_builtin_basis,
)
from apps.native_receiver.nullifier_renderer import PLOT_CHOICES, NullifierRendererMixin
from quantum_light_engine.post_processing import (
    ClusterProcessingConfig,
    config_from_dict,
    config_to_dict,
    lab_processing_config,
    single_mode_processing_config,
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
        self.title("Mode squeezing and nullifiers")
        self.geometry("960x760")
        self.minsize(840, 650)
        self.configure(bg=self.palette["bg"])
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())
        self.bind("<Control-Return>", lambda _event: self._apply())
        self.status = tk.StringVar(value="Waiting for samples")
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
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(16, 14, 16, 8), style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Mode squeezing and nullifiers", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="Single-mode measures each channel separately. Cluster formulas use the "
            "selected homodyne x / p basis.",
            style="Muted.TLabel",
            wraplength=850,
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        self.settings_notebook = ttk.Notebook(self, style="Analysis.TNotebook")
        self.settings_notebook.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 12))
        setup_tab = ttk.Frame(self.settings_notebook, padding=14, style="Surface.TFrame")
        mode_tab = ttk.Frame(self.settings_notebook, padding=14, style="Surface.TFrame")
        formula_tab = ttk.Frame(self.settings_notebook, padding=10, style="Surface.TFrame")
        preview_tab = ttk.Frame(self.settings_notebook, padding=8, style="Surface.TFrame")
        for tab, title in ((setup_tab, "Acquisition"), (mode_tab, "Temporal mode"),
                           (formula_tab, "Templates & formulas"), (preview_tab, "Live preview")):
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(0, weight=1)
            self.settings_notebook.add(tab, text=title)
        setup_tab = self._scrollable_body(setup_tab)
        mode_tab = self._scrollable_body(mode_tab)
        formula_tab.rowconfigure(0, weight=1)
        preview_tab.rowconfigure(0, weight=1)
        controls = ttk.LabelFrame(mode_tab, text="Temporal mode and variance", padding=14)
        controls.grid(row=0, column=0, sticky="ew")
        for column in range(3):
            controls.columnconfigure(column, weight=1, uniform="mode_fields")

        self.frequency_mhz = tk.StringVar()
        self.variance_window_us = tk.StringVar()
        self.delay_samples = tk.StringVar()
        self.gate_threshold = tk.StringVar()
        self.minimum_samples = tk.StringVar()
        self.history_modes = tk.StringVar()
        self.mode_shape = tk.StringVar()
        self.mode_oversample = tk.StringVar()
        self.variance_estimator = tk.StringVar()
        fields = (
            ("Mode bandwidth (MHz)", self.frequency_mhz),
            ("Variance window (µs)", self.variance_window_us),
            ("B delay (samples)", self.delay_samples),
            ("Artifact reject |V| (optional)", self.gate_threshold),
            ("Minimum samples/mode", self.minimum_samples),
            ("Maximum modes/window", self.history_modes),
            ("Template oversample", self.mode_oversample),
        )
        for index, (label, variable) in enumerate(fields):
            row, column = divmod(index, 3)
            ttk.Label(controls, text=label).grid(row=row * 2, column=column, sticky="w",
                                                padx=(0, 12), pady=(0, 5))
            ttk.Entry(controls, textvariable=variable, width=14).grid(
                row=row * 2 + 1, column=column, sticky="ew", padx=(0, 12), pady=(0, 12)
            )
        ttk.Label(controls, text="Analytic shape").grid(row=6, column=0, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.mode_shape,
            values=(
                "identity", "gaussian_linear", "gaussian_linear_cosine",
                "lab_gaussian_linear_cosine",
            ),
            state="readonly",
            width=24,
        ).grid(row=7, column=0, columnspan=2, sticky="ew", padx=(0, 12), pady=(5, 12))
        ttk.Label(
            controls,
            text="Amplitude rejection may bias variance; use timing for lock exclusion.",
            wraplength=760, style="Muted.TLabel",
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(controls, text="Variance estimator").grid(row=8, column=0, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.variance_estimator,
            values=("ensemble", "rolling", "lab_rolling"),
            state="readonly",
            width=24,
        ).grid(row=9, column=0, columnspan=2, sticky="ew", padx=(0, 12), pady=(5, 0))
        ttk.Label(
            controls,
            text="Ensemble: repeat aligned windows. Rolling: variance across modes.",
            wraplength=760, style="Muted.TLabel",
        ).grid(row=11, column=0, columnspan=3, sticky="w", pady=(5, 0))

        acquisition_frame = ttk.Frame(setup_tab, style="Toolbar.TFrame", padding=14)
        acquisition_frame.grid(row=0, column=0, sticky="ew")
        self.acquisition = AcquisitionWindowControls(acquisition_frame)
        self.acquisition.pack(fill="x")
        self.analysis = AnalysisControls(acquisition_frame,
                                         sample_rate_provider=self.parent.mode_sample_rate_hz)
        self.mode_width_us = self.analysis.variables["mode_width_us"]
        self.mode_shape.trace_add("write", lambda *_args:
                                  self.analysis.set_mode_shape(self.mode_shape.get()))
        self.analysis.pack(fill="x", pady=(3, 0))
        ttk.Label(
            acquisition_frame,
            text="Periodic starts with locking. Triggered uses sampled CH1 / CH2, "
            "independently of the waveform display trigger.",
            wraplength=760,
        ).pack(anchor="w", pady=(8, 0))
        presets = ttk.Frame(acquisition_frame, style="Toolbar.TFrame")
        presets.pack(anchor="w", pady=(4, 0))
        ttk.Button(
            presets,
            text="Load lab postprocessing preset",
            command=self._load_preset,
            style="Compact.TButton",
        ).pack(side="left")
        ttk.Button(
            presets,
            text="Single-mode squeezing preset",
            command=self._load_single_preset,
            style="Compact.TButton",
        ).pack(side="left", padx=(8, 0))

        editors = ttk.PanedWindow(formula_tab, orient=tk.HORIZONTAL)
        editors.grid(row=0, column=0, sticky="nsew")
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
        nullifier_frame = ttk.LabelFrame(
            editors, text="Cluster formulas (JSON; single-mode uses the selected channels)"
        )
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
            preview_tab, text="Live nullifiers · ensemble per mode / rolling diagnostic", padding=6
        )
        chart_frame.grid(row=0, column=0, sticky="nsew")
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
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, wraplength=600).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(footer, textvariable=self.readout, wraplength=840).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(4, 8)
        )
        ttk.Combobox(
            footer, textvariable=self.plot_choice, values=PLOT_CHOICES, state="readonly", width=15
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))
        self._build_plot_controls(footer).grid(
            row=3, column=1, columnspan=3, sticky="e", padx=(8, 0), pady=(8, 0)
        )
        ttk.Button(
            footer, text="Apply configuration", style="Accent.TButton", command=self._apply
        ).grid(row=0, column=3, sticky="e", padx=(8, 0))
        ttk.Button(
            footer,
            text="Start shot-noise calibration",
            command=self._start_calibration,
        ).grid(row=2, column=0, sticky="w")
        ttk.Button(footer, text="Finish calibration", command=self._finish_calibration).grid(
            row=2, column=1, sticky="w", padx=(8, 0)
        )
        ttk.Button(
            footer,
            text="Clear calibration",
            style="Danger.TButton",
            command=self._clear_calibration,
        ).grid(row=2, column=2, sticky="w", padx=(8, 0))
        self.calibration_progress_view = CalibrationProgress(footer, self.parent, length=150)
        self.calibration_progress_view.grid(row=2, column=3, sticky="e", padx=(12, 0))

    def _scrollable_body(self, tab: Any) -> Any:
        canvas = tk.Canvas(tab, bg=self.palette["surface"], highlightthickness=0,
                           width=1, height=1)
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set, yscrollincrement=24)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        body = ttk.Frame(canvas, style="Surface.TFrame")
        body.columnconfigure(0, weight=1)
        item = canvas.create_window(0, 0, anchor="nw", window=body)
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))

        def wheel(event: Any) -> str | None:
            widget = event.widget
            while widget is not None and widget not in (body, canvas):
                widget = getattr(widget, "master", None)
            if widget is None or not canvas.winfo_ismapped():
                return None
            if body.winfo_height() > canvas.winfo_height():
                canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"
            return None

        self.bind("<MouseWheel>", wheel, add="+")
        return body

    def _load(self, config: ClusterProcessingConfig) -> None:
        self._clear_plot_view()
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
        self.mode_oversample.set(str(config.mode_oversample))
        self.variance_estimator.set(str(data["varianceEstimator"]))
        self.acquisition.load(config)
        self.analysis.load(config)
        self.mode_text.delete("1.0", tk.END)
        self.mode_text.insert("1.0", json.dumps(data["explicitModeTemplate"]))
        self.nullifier_text.delete("1.0", tk.END)
        self.nullifier_text.insert("1.0", json.dumps(data["nullifiers"], indent=2))

    def _config(self) -> ClusterProcessingConfig:
        gate = self.gate_threshold.get().strip()
        data = config_to_dict(self.parent.cluster_processor.config)
        data.update(
            {
                "measurementFrequencyHz": float(self.frequency_mhz.get()) * 1e6,
                "varianceWindowSeconds": float(self.variance_window_us.get()) * 1e-6,
                "channelBDelaySamples": int(self.delay_samples.get()),
                "gateAbsoluteThreshold": None if not gate else float(gate),
                "minSamplesPerMode": int(self.minimum_samples.get()),
                "historyModes": int(self.history_modes.get()),
                "explicitModeTemplate": json.loads(self.mode_text.get("1.0", tk.END)),
                "nullifiers": json.loads(self.nullifier_text.get("1.0", tk.END)),
                "modeShape": self.mode_shape.get(),
                "modeOversample": int(self.mode_oversample.get()),
                "varianceEstimator": self.variance_estimator.get(),
            }
        )
        self.acquisition.apply_to(data)
        self.analysis.apply_to(data)
        return match_builtin_basis(config_from_dict(data))

    def _load_preset(self) -> None:
        basis = self.acquisition.variables["basis"].get()
        config = lab_processing_config(basis)
        self._load(config)

    def _load_single_preset(self) -> None:
        try:
            current = self._config()
            single = single_mode_processing_config(
                mode_samples=current.mode_samples,
                variance_window_modes=current.variance_window_modes or 100,
            )
            self._load(replace(
                single,
                mode_width_seconds=current.mode_width_seconds,
                mode_samples=None,
                mode_shape=current.mode_shape,
                min_samples_per_mode=current.min_samples_per_mode,
                measurement_frequency_hz=current.measurement_frequency_hz,
                explicit_mode_template=current.explicit_mode_template,
                channel_b_delay_samples=current.channel_b_delay_samples,
                measurement_basis=current.measurement_basis,
                single_channel=current.single_channel,
                window_mode=current.window_mode,
                lock_seconds=current.lock_seconds,
                collect_seconds=current.collect_seconds,
                collection_guard_seconds=current.collection_guard_seconds,
                collection_end_guard_samples=current.collection_end_guard_samples,
                trigger_channel=current.trigger_channel,
                trigger_edge=current.trigger_edge,
                trigger_level_volts=current.trigger_level_volts,
                trigger_delay_seconds=current.trigger_delay_seconds,
                trigger_holdoff_seconds=current.trigger_holdoff_seconds,
                trigger_hysteresis_volts=current.trigger_hysteresis_volts,
            ))
        except Exception as exc:
            messagebox.showerror("Invalid squeezing configuration", str(exc), parent=self)

    def _apply(self) -> None:
        try:
            config = self._config()
            self.parent.apply_cluster_config(config)
            self.status.set("Configuration applied; shot-noise calibration was cleared")
        except Exception as exc:
            messagebox.showerror("Invalid cluster configuration", str(exc), parent=self)

    def _start_calibration(self) -> None:
        try:
            config = self._config()
            if config != self.parent.cluster_processor.config:
                self.parent.apply_cluster_config(config)
            self.parent.start_cluster_calibration()
            self.status.set("Acquiring shot noise; keep the shot-noise measurement active")
        except Exception as exc:
            messagebox.showerror("Calibration error", str(exc), parent=self)

    def _finish_calibration(self) -> None:
        try:
            coefficients = self.parent.finish_cluster_calibration()
            channels = ", ".join(
                f"{'CH1' if channel == 'a' else 'CH2'} x {value:.5g}"
                for channel, value in coefficients.items()
            )
            self.status.set(f"Calibration complete: {channels}")
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
            set_if_changed(self.status, error)
            self._set_readout("Check the selected channels and temporal-mode sample rate")
            self._draw_message(error)
        elif result is None:
            recording = self.parent.__dict__.get("_calibration_started") is not None
            self._draw_message("Recording shot noise · finishes automatically after 10 s"
                               if recording else "Waiting for processed modes")
        else:
            set_if_changed(self.status, result.status)
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
