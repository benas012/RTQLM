"""Embedded nullifier analysis tab."""

from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, ttk
from typing import Any

import numpy as np

from apps.native_receiver.calibration_ui import CalibrationProgress
from apps.native_receiver.constants import COLORS
from apps.native_receiver.dialogs import ToolTip
from apps.native_receiver.helpers import format_readout, set_if_changed
from apps.native_receiver.nullifier_acquisition import (
    AcquisitionWindowControls,
    AnalysisControls,
    acquisition_description,
    match_builtin_basis,
)
from apps.native_receiver.nullifier_renderer import (
    AMPLITUDE_CHOICES,
    PLOT_CHOICES,
    NullifierRendererMixin,
    select_nullifier_traces,
)
from quantum_light_engine.post_processing import (
    config_from_dict,
    config_to_dict,
    lab_processing_config,
    single_mode_processing_config,
)

REFRESH_INTERVAL_MS = 200


class NullifierMonitorPanel(NullifierRendererMixin, ttk.Frame):
    def __init__(self, parent: Any, receiver: Any) -> None:
        super().__init__(parent, style="Surface.TFrame")
        self.parent = receiver
        self.palette = getattr(receiver, "ui_colors", COLORS)
        self.status = tk.StringVar(self, value="Waiting for samples")
        self.readout = tk.StringVar(self, value="Shot-noise calibration required")
        self.schedule = tk.StringVar(self)
        self.plot_choice = tk.StringVar(self, value="Auto")
        self.variance_estimator = tk.StringVar(self)
        self._refresh_after_id: str | None = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        self.settings_expanded = False
        self.reference_state = tk.StringVar(self, value="Reference needed")
        self.mode_summary = tk.StringVar(self, value="Waiting for mode timing")
        self.metric_titles = [tk.StringVar(self, value=f"CH{n} · latest estimate") for n in (1, 2)]
        self.metric_values = [tk.StringVar(self, value="—") for _ in range(2)]

        header = ttk.Frame(self, style="Surface.TFrame", padding=(12, 10, 12, 4))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=self.reference_state, style="Accent.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.settings_button = ttk.Button(
            header, text="Setup…", command=self._toggle_settings, style="Compact.TButton"
        )
        self.settings_button.grid(row=0, column=1, padx=(8, 6))
        ttk.Button(header, text="Full settings…", command=receiver.open_cluster_monitor,
                   style="Quiet.TButton").grid(row=0, column=2)

        metrics = ttk.Frame(self, style="Surface.TFrame", padding=(12, 4, 12, 10))
        metrics.grid(row=1, column=0, sticky="ew")
        for index in range(2):
            metrics.columnconfigure(index, weight=1, uniform="readings")
            card = ttk.Frame(metrics, style="Surface.TFrame")
            card.grid(row=0, column=index, sticky="ew", padx=(0, 16))
            ttk.Label(card, textvariable=self.metric_titles[index], style="Muted.TLabel").pack(
                anchor="w"
            )
            ttk.Label(card, textvariable=self.metric_values[index], style="Hero.TLabel",
                       width=12, anchor="w",
                       foreground=self.palette["ch1" if index == 0 else "ch2"]).pack(anchor="w")
        metrics.columnconfigure(2, weight=1)
        right = ttk.Frame(metrics, style="Surface.TFrame")
        right.grid(row=0, column=2, sticky="e")
        ttk.Label(right, textvariable=self.mode_summary, style="Reference.TLabel",
                   width=24, justify="right", anchor="e").pack(anchor="e", pady=(0, 4))
        self.calibration_progress_view = CalibrationProgress(right, receiver)
        self.calibration_progress_view.pack(anchor="e")

        self.setup_window = tk.Toplevel(self)
        self.setup_window.withdraw()
        self.setup_window.title("Mode setup")
        self.setup_window.transient(receiver)
        self.setup_window.resizable(False, False)
        self.setup_window.configure(bg=self.palette["surface_elevated"])
        self.setup_window.protocol("WM_DELETE_WINDOW", self._hide_settings)
        self.setup_window.bind("<Escape>", lambda _event: self._hide_settings())
        self.setup_panel = ttk.Frame(self.setup_window, style="Toolbar.TFrame", padding=20)
        self.setup_panel.pack(fill="both", expand=True)
        ttk.Label(self.setup_panel, text="Mode setup", style="Section.TLabel").pack(
            anchor="w", pady=(0, 14)
        )
        self.analysis = AnalysisControls(self.setup_panel, compact=True,
                                         sample_rate_provider=receiver.mode_sample_rate_hz)
        self.analysis.pack(fill="x")
        self.acquisition = AcquisitionWindowControls(self.setup_panel, compact=True)
        self.acquisition.pack(fill="x", pady=(4, 0))
        settings_actions = ttk.Frame(self.setup_panel, style="Toolbar.TFrame")
        settings_actions.pack(fill="x", pady=(4, 0))
        ttk.Button(settings_actions, text="Apply settings", command=self._apply,
                   style="Accent.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(settings_actions, text="Lab 1D preset", command=self._preset,
                   style="Compact.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(settings_actions, text="Single-mode preset", command=self._single_preset,
                   style="Compact.TButton").pack(side="left")

        actions = ttk.Frame(self, style="Toolbar.TFrame", padding=(10, 7))
        actions.grid(row=3, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        calibration = ttk.Frame(actions, style="Toolbar.TFrame")
        calibration.grid(row=0, column=0, sticky="w")
        ttk.Label(calibration, text="REFERENCE", style="GroupLabel.TLabel").pack(
            side="left", padx=(0, 8)
        )
        for title, command in (("Record", self._start_calibration),
                                ("Finish", self._finish_calibration),
                                ("Clear", self._clear_calibration)):
            button = ttk.Button(calibration, text=title, width=6, command=command,
                                style="Compact.TButton")
            button.pack(side="left", padx=(0, 5))
            ToolTip(button, {"Record": "Record the reference for 10 seconds, "
                            "then finish automatically. "
                            "Tagged captures use both E1 HIGH and LOW for this reference. "
                            "For optical measurements, "
                            "measure vacuum shot noise with the same detector settings.",
                            "Finish": "Finish early and use the measured reference variance.",
                            "Clear": "Discard the reference calibration."}[title])
        ttk.Combobox(
            header,
            textvariable=self.plot_choice,
            values=PLOT_CHOICES,
            state="readonly",
            width=14,
        ).grid(row=0, column=3, sticky="e", padx=(8, 0))
        estimator = ttk.Frame(self.setup_panel, style="Toolbar.TFrame")
        estimator.pack(fill="x", pady=(8, 0))
        ttk.Label(estimator, text="Variance estimator", style="Toolbar.TLabel").pack(
            side="left", padx=(0, 8)
        )
        ttk.Combobox(
            estimator,
            textvariable=self.variance_estimator,
            values=("Lab rolling / average", "Repeated windows", "Rolling modes"),
            state="readonly",
            width=17,
        ).pack(side="left")
        ttk.Button(self.setup_panel, text="Use 62.5 MS/s server stream",
                   command=self._server_stream).pack(anchor="w", pady=(10, 0))
        ttk.Label(self.setup_panel,
                  text="Server stream: E1.3 HIGH gates analysis; "
                       "uncertain edges and gaps are excluded.",
                  style="Toolbar.TLabel", wraplength=480).pack(anchor="w", pady=(4, 0))
        ttk.Button(self.setup_panel, text="Use 125 MS/s buffered captures",
                   command=self._buffered_capture).pack(anchor="w", pady=(10, 0))
        ttk.Label(self.setup_panel, text="Buffered: 5 ms acquisitions with gaps between captures.",
                  style="Toolbar.TLabel").pack(anchor="w", pady=(4, 0))
        self.chart_controls = self._build_plot_controls(actions)
        ttk.Label(
            self.setup_panel,
            text="Samples are grouped from each collection start. Align that start to your "
                 "experiment's clock.",
            style="Toolbar.TLabel", wraplength=480,
        ).pack(anchor="w", pady=(12, 0))
        self.chart_controls.grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 0))
        self.canvas = tk.Canvas(
            self,
            height=190,
            bg=self.palette["bg_deep"],
            highlightthickness=1,
            highlightbackground=self.palette["border_subtle"],
        )
        self.canvas.grid(row=5, column=0, sticky="nsew")
        footer = ttk.Frame(self, style="Surface.TFrame", padding=(12, 6))
        footer.grid(row=6, column=0, sticky="ew")
        self.footer_labels = [
            ttk.Label(footer, textvariable=variable, style="Numeric.Muted.TLabel",
                      width=1, anchor="w", wraplength=700)
            for variable in (self.readout, self.schedule)
        ]
        for label in self.footer_labels:
            label.pack(anchor="w", fill="x")
        self.bind("<Configure>", self._resize_labels, add="+")
        self.load_config(receiver.cluster_processor.config)
        self.bind("<Destroy>", self._destroyed, add="+")
        self._draw_message("Waiting for admitted temporal modes")
        self._refresh_after_id = self.parent.after(REFRESH_INTERVAL_MS, self._refresh)

    def _toggle_settings(self) -> None:
        if self.settings_expanded:
            self._hide_settings()
        else:
            self.settings_expanded = True
            self.setup_window.update_idletasks()
            x = max(0, min(self.winfo_rootx() + 20,
                           self.winfo_screenwidth() - self.setup_window.winfo_reqwidth()))
            y = max(0, min(self.winfo_rooty(),
                           self.winfo_screenheight() - self.setup_window.winfo_reqheight() - 50))
            self.setup_window.geometry(f"+{x}+{y}")
            self.setup_window.deiconify()
            self.setup_window.lift()

    def _hide_settings(self) -> None:
        self.settings_expanded = False
        self.setup_window.withdraw()

    def _resize_labels(self, event: Any) -> None:
        if event.widget is self:
            for label in self.footer_labels:
                label.configure(wraplength=max(event.width - 24, 100))
            wide = event.width >= 650
            if wide != getattr(self, "_wide_actions", None):
                self._wide_actions = wide
                self.chart_controls.grid_forget()
                self.chart_controls.grid(row=0 if wide else 1, column=1 if wide else 0,
                                         columnspan=1 if wide else 2, sticky="e" if wide else "w",
                                         pady=(0, 0) if wide else (7, 0))

    def _update_metrics(self, result: Any) -> None:
        state = ("Recording reference" if result.calibrating else
                 "Reference ready" if result.calibrated else "Reference needed")
        set_if_changed(self.reference_state, state)
        choice = self.plot_choice.get()
        traces, _unit, logarithmic = select_nullifier_traces(result, choice)
        quantity = "amplitude" if choice in AMPLITUDE_CHOICES else "variance"
        if logarithmic:
            quantity = "dB"
        pairs = list(traces.items())
        for index in range(2):
            if index < len(pairs):
                name, values = pairs[index]
                finite = np.asarray(values)[np.isfinite(values)]
                if not finite.size:
                    value = "—"
                elif logarithmic:
                    value = format_readout(finite[-1], width=9, decimals=2, signed=True) + " dB"
                else:
                    value = format_readout(finite[-1])
                set_if_changed(self.metric_titles[index], f"{name} · latest {quantity}")
                set_if_changed(self.metric_values[index], value)
            else:
                set_if_changed(self.metric_titles[index], "No second trace")
                set_if_changed(self.metric_values[index], "—")
        period = result.mode_period_seconds
        scale, unit = (1e9, "ns") if period < 1e-6 else (
            (1e6, "µs") if period < 1e-3 else (1e3, "ms")
        )
        maximum = getattr(result, "mode_samples_max", 0) or result.mode_samples
        alternating = maximum != result.mode_samples
        samples = (f"{result.mode_samples:4d}/{maximum:4d}" if alternating
                   else f"{result.mode_samples:4d}")
        set_if_changed(self.mode_summary,
                       f"{samples} samples / mode\n"
                       f"{format_readout(period * scale, width=9)} {unit} "
                       + ("average" if alternating else "/ mode"))

    def load_config(self, config: Any) -> None:
        self._clear_plot_view()
        self.acquisition.load(config)
        self.analysis.load(config)
        self.schedule.set(acquisition_description(config))
        self.variance_estimator.set(
            {"ensemble": "Repeated windows", "rolling": "Rolling modes",
             "lab_rolling": "Lab rolling / average"}[config.variance_estimator]
        )

    def _selected_config(self) -> Any:
        data = config_to_dict(self.parent.cluster_processor.config)
        self.acquisition.apply_to(data)
        data["varianceEstimator"] = {
            "Repeated windows": "ensemble", "Rolling modes": "rolling",
            "Lab rolling / average": "lab_rolling",
        }[self.variance_estimator.get()]
        self.analysis.apply_to(data)
        return match_builtin_basis(config_from_dict(data))

    def _apply(self) -> None:
        try:
            self.parent.apply_cluster_config(self._selected_config())
        except Exception as exc:
            messagebox.showerror("Invalid nullifier timing", str(exc), parent=self)

    def _buffered_capture(self) -> None:
        provider = self.analysis.sample_rate_provider
        try:
            self.analysis.sample_rate_provider = lambda: 125_000_000.0
            config = self._selected_config()
            self.parent.start_buffered_capture(config)
        except Exception as exc:
            messagebox.showerror("Buffered capture unavailable", str(exc), parent=self)
        finally:
            self.analysis.sample_rate_provider = provider
            self.analysis.refresh_sampling()

    def _server_stream(self) -> None:
        provider = self.analysis.sample_rate_provider
        try:
            self.analysis.sample_rate_provider = lambda: 62_500_000.0
            self.parent.start_server_stream(self._selected_config())
        except Exception as exc:
            messagebox.showerror("Server stream unavailable", str(exc), parent=self)
        finally:
            self.analysis.sample_rate_provider = provider
            self.analysis.refresh_sampling()

    def _preset(self) -> None:
        try:
            config = self._selected_config()
            lab = lab_processing_config(config.measurement_basis)
            self.parent.apply_cluster_config(replace(
                lab, window_mode=config.window_mode, lock_seconds=config.lock_seconds,
                collect_seconds=config.collect_seconds,
                trigger_channel=config.trigger_channel, trigger_edge=config.trigger_edge,
                trigger_level_volts=config.trigger_level_volts,
                trigger_delay_seconds=config.trigger_delay_seconds,
                trigger_holdoff_seconds=config.trigger_holdoff_seconds,
                trigger_hysteresis_volts=config.trigger_hysteresis_volts,
            ))
        except Exception as exc:
            messagebox.showerror("Invalid nullifier configuration", str(exc), parent=self)

    def _single_preset(self) -> None:
        try:
            current = self._selected_config()
            single = single_mode_processing_config(
                mode_samples=current.mode_samples,
                variance_window_modes=current.variance_window_modes or 100,
            )
            self.parent.apply_cluster_config(replace(
                single,
                mode_width_seconds=current.mode_width_seconds,
                mode_samples=None,
                mode_shape=current.mode_shape,
                mode_expression=current.mode_expression,
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

    def _start_calibration(self) -> None:
        try:
            config = self._selected_config()
            if config != self.parent.cluster_processor.config:
                self.parent.apply_cluster_config(config)
            self.parent.start_cluster_calibration()
            self.status.set("Acquiring shot noise; keep the shot-noise measurement active")
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
        recording = self.parent.__dict__.get("_calibration_started") is not None
        if error:
            for value in self.metric_values:
                set_if_changed(value, "—")
            set_if_changed(self.reference_state, "Check measurement settings")
            set_if_changed(self.status, error)
            self._set_readout("Check the selected channels and temporal-mode sample rate")
            self._draw_message(error)
        elif result is None:
            for value in self.metric_values:
                set_if_changed(value, "—")
            set_if_changed(self.reference_state, "Recording shot noise" if recording
                           else "Waiting for samples")
            set_if_changed(self.status, self.parent.cluster_acquisition_state())
            self._draw_message("Recording shot noise · finishes automatically after 10 s"
                               if recording else "Waiting for admitted temporal modes")
        else:
            self._update_metrics(result)
            set_if_changed(self.status, result.status)
            self._draw_result(result)
        description = acquisition_description(self.parent.cluster_processor.config)
        device = self.parent.__dict__.get("selected_device")
        if device is not None and getattr(device, "acquisition_mode", "streaming") == "buffered":
            capture_ms = device.capture_samples / 125_000_000 * device.decimation * 1e3
            e1 = (f" · E1 HIGH excluded: {result.rejected_modes:6d} modes"
                  if result and result.tagged_e1 and not error else "")
            description = (f"Buffered · {capture_ms:.5g} ms per capture{e1} "
                           "· gaps between captures · waveform preview")
        set_if_changed(self.schedule, description)
        buffered = getattr(device, "acquisition_mode", "streaming") == "buffered"
        self._refresh_after_id = self.parent.after(50 if buffered else REFRESH_INTERVAL_MS,
                                                   self._refresh)

    def _destroyed(self, event: Any) -> None:
        if event.widget is self and self._refresh_after_id is not None:
            try:
                self.parent.after_cancel(self._refresh_after_id)
            except tk.TclError:
                pass
            self._refresh_after_id = None
