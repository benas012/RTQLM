from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox

import numpy as np
import pytest

from apps.native_receiver import native_receiver as receiver
from apps.native_receiver import receiver_tools
from apps.native_receiver.cluster_monitor import ClusterMonitorDialog
from apps.native_receiver.native_receiver import ReceiverApp
from quantum_light_engine.post_processing import (
    LAB_CLUSTER_P,
    LAB_CLUSTER_X,
    ClusterProcessingResult,
    NullifierSpec,
    NullifierTerm,
    config_from_dict,
    config_to_dict,
    lab_processing_config,
    processing_nullifiers,
)


def check_nullifier_tabs() -> None:
    def start_ui(app: ReceiverApp) -> None:
        app.attributes("-alpha", 0)
        app._build_menu()
        app._build()

    with pytest.MonkeyPatch.context() as monkeypatch:
        errors: list[tuple[object, ...]] = []
        monkeypatch.setattr(ReceiverApp, "_start_receiver", start_ui)
        monkeypatch.setattr(receiver, "load_cluster_config", lab_processing_config)
        monkeypatch.setattr(receiver_tools, "save_cluster_config", lambda _config: None)
        monkeypatch.setattr(messagebox, "showerror", lambda *args, **_kwargs: errors.append(args))
        app = ReceiverApp()
        try:
            app.update()
            panel = app.nullifier_panel
            assert app.analysis_notebook.tab(0, "text") == "Spectrum analyzer"
            assert app.analysis_notebook.tab(1, "text") == "Mode analysis"
            assert app.analysis_notebook.select() == str(panel)
            for geometry in ("1180x760", "960x640", "1600x1000"):
                app.geometry(geometry)
                app.update()
                assert panel.canvas.winfo_height() >= 120, (
                    geometry, panel.winfo_height(), panel.winfo_reqheight(),
                    panel.canvas.winfo_height(), panel.analysis.winfo_reqwidth(),
                    panel.analysis.winfo_width(),
                )
                assert not panel.setup_window.winfo_ismapped()
                panel._toggle_settings()
                app.update()
                assert panel.acquisition.winfo_ismapped()
                assert panel.analysis.winfo_ismapped()
                panel._hide_settings()
                app.update()
                bottom = panel.winfo_rooty() - app.winfo_rooty() + panel.winfo_height()
                assert bottom <= app.winfo_height()
            # A live chart retains its Canvas items and axes as batches change.
            modes = np.linspace(-4, -1, 2000)
            result = ClusterProcessingResult(
                mode_period_seconds=1e-6, mode_index_start=0, quadratures={},
                nullifiers={"CH1": modes}, variances={"CH1": modes ** 2},
                squeezing_db={"CH1": modes}, shot_noise_coefficients={"a": 1},
                calibrated=True, calibrating=False, rejected_modes=0, status="live",
                analysis_mode="single", mode_samples=16, variance_window_modes=100,
            )
            panel._update_metrics(result)
            assert panel.metric_values[0].get() == "-1.00 dB"
            assert panel.metric_values[1].get() == "—"
            assert panel.mode_summary.get() == "16 samples / mode\n1 µs / mode"
            panel.plot_choice.set("Variance")
            panel._update_metrics(result)
            assert panel.metric_values[0].get() == "1"
            assert "variance" in panel.metric_titles[0].get()
            panel.plot_choice.set("Auto")
            panel._draw_result(result)
            grid = panel.canvas.find_withtag("grid:0")
            trace = panel.canvas.find_withtag("trace:CH1:0:0")
            assert len(grid) == len(trace) == 1
            original_bounds = panel._plot_bounds
            original_items = panel.canvas.find_all()
            panel._draw_result(result)
            assert panel.canvas.find_all() == original_items
            panel._draw_result(replace(result, squeezing_db={"CH1": modes * 1.5}))
            assert panel.canvas.find_withtag("grid:0") == grid
            assert panel.canvas.find_withtag("trace:CH1:0:0") == trace
            assert panel._plot_bounds == original_bounds
            trace_coordinates = panel.canvas.coords(trace)
            panel._draw_result(replace(result, squeezing_db={"CH1": np.full(2000, np.nan)}))
            assert panel.canvas.coords(trace) == trace_coordinates
            assert panel.canvas.itemcget(trace, "state") == "normal"
            assert "last valid" in panel.readout.get()
            notice = panel.canvas.find_withtag("notice")
            for _ in range(10):
                panel._draw_message("FPGA samples lost")
            assert panel.canvas.find_withtag("notice") == notice
            assert panel.canvas.find_withtag("grid:0") == grid
            panel._draw_result(result)
            panel._zoom_plot_y(0.5)
            assert panel._plot_bounds[1] - panel._plot_bounds[0] == pytest.approx(
                (original_bounds[1] - original_bounds[0]) / 2
            )
            panel._zoom_plot_time(0.5)
            assert panel._plot_view_modes == 1000
            expanded = replace(result, squeezing_db={"CH1": modes * 10})
            panel._draw_result(expanded)
            assert "Outside view range" in panel.readout.get()
            panel._fit_plot()
            assert panel._plot_view_modes == 2000
            assert panel._plot_bounds[0] <= -40
            assert panel.canvas.find_withtag("grid:0") == grid
            assert panel.canvas.itemcget(grid, "state") == "normal"
            np.testing.assert_array_equal(result.squeezing_db["CH1"], modes)
            # Clearing calibration also invalidates a held chart of the old reference.
            app.clear_cluster_calibration()
            assert panel._last_chart_result is None
            panel._draw_result(replace(result, squeezing_db={"CH1": np.full(100, -np.inf)}))
            zero_notice = panel.canvas.find_withtag("notice")
            assert "Zero variance" in panel.readout.get()
            panel._draw_result(replace(result, squeezing_db={"CH1": np.full(100, -np.inf)}))
            assert panel.canvas.find_withtag("notice") == zero_notice
            panel.acquisition.variables["basis"].set("p")
            panel.variance_estimator.set("Rolling modes")
            panel._apply()
            assert app.cluster_processor.config.nullifiers == (LAB_CLUSTER_P,)
            assert app.cluster_processor.config.variance_estimator == "rolling"
            custom = replace(
                app.cluster_processor.config,
                nullifiers=(NullifierSpec("custom", (NullifierTerm("a", 2, -2.0),)),),
            )
            app.apply_cluster_config(custom)
            panel.acquisition.variables["basis"].set("x")
            panel._apply()
            assert app.cluster_processor.config.nullifiers == custom.nullifiers
            panel.analysis.variables["calculation"].set("Single-mode")
            panel.analysis.variables["window_modes"].set("")
            panel.variance_estimator.set("Lab rolling / average")
            panel._apply()
            selected = app.cluster_processor.config
            assert selected.analysis_mode == "single"
            assert selected.variance_estimator == "rolling"
            assert selected.variance_window_modes == 100
            assert selected.nullifiers == custom.nullifiers
            assert panel.variance_estimator.get() == "Rolling modes"
            panel.analysis.variables["calculation"].set("Cluster")
            panel._apply()
            dialog = ClusterMonitorDialog(app)
            dialog.attributes("-alpha", 0)
            app.update()
            dialog._draw_result(result)
            dialog_grid = dialog.canvas.find_withtag("grid:0")
            dialog_bounds = dialog._plot_bounds
            dialog._zoom_plot_y(0.5)
            assert dialog._plot_bounds[1] - dialog._plot_bounds[0] == pytest.approx(
                (dialog_bounds[1] - dialog_bounds[0]) / 2
            )
            dialog._zoom_plot_time(0.5)
            assert dialog._plot_view_modes == 1000
            dialog._fit_plot()
            assert dialog._plot_view_modes == 2000
            assert dialog.canvas.find_withtag("grid:0") == dialog_grid
            assert dialog._config().nullifiers == custom.nullifiers
            assert dialog._config().history_modes == custom.history_modes
            assert dialog._config().variance_estimator == custom.variance_estimator
            dialog._load_preset()
            loaded = dialog._config()
            assert loaded.mode_shape == "lab_gaussian_linear_cosine"
            assert loaded.mode_oversample == 1000
            assert loaded.collection_guard_seconds == pytest.approx(.0008006)

            dialog._load_single_preset()
            loaded = dialog._config()
            assert loaded.analysis_mode == "single"
            assert loaded.mode_samples is None
            assert loaded.variance_window_modes == 100
            assert loaded.calibration_estimator == "pooled"
            assert loaded.mode_shape == "lab_gaussian_linear_cosine"
            assert loaded.channel_b_delay_samples == 2
            assert loaded.lock_seconds == pytest.approx(.005)
            assert loaded.collect_seconds == pytest.approx(.005)
            dialog._apply()
            assert panel.analysis.variables["calculation"].get() == "Single-mode"
            assert float(panel.analysis.variables["mode_width_us"].get()) == pytest.approx(.308)
            assert "mode_samples" not in panel.analysis.variables
            assert {
                term.source for spec in processing_nullifiers(loaded) for term in spec.terms
            } == {"a", "b"}

            # Time width, channel selection and full-trace calibration survive the editor.
            dialog.analysis.variables["mode_width_us"].set(".256")
            dialog.analysis.variables["window_modes"].set("100")
            dialog.analysis.variables["channel"].set("CH1")
            dialog.analysis.variables["calibration"].set("Whole accepted trace")
            dialog.mode_shape.set("identity")
            dialog._apply()
            single = app.cluster_processor.config
            assert single.mode_samples is None
            assert single.mode_width_seconds == pytest.approx(256e-9)
            assert single.variance_window_modes == 100
            assert single.single_channel == "a"
            assert single.calibration_estimator == "pooled"
            assert single.mode_shape == "identity"
            assert config_from_dict(config_to_dict(single)) == single
            assert len(processing_nullifiers(single)) == 1
            assert panel._selected_config() == single
            with pytest.MonkeyPatch.context() as calibration_patch:
                calibration_patch.setattr(app, "finish_cluster_calibration", lambda: {"a": 1.25})
                dialog._finish_calibration()
            assert "CH1" in dialog.status.get()
            assert "CH2" not in dialog.status.get()

            # Calibration starts with the edited width.
            panel.analysis.variables["mode_width_us"].set(".384")
            panel._start_calibration()
            assert app.cluster_processor.config.mode_samples is None
            assert app.cluster_processor.config.mode_width_seconds == pytest.approx(384e-9)
            assert app.cluster_processor.calibrating
            app.clear_cluster_calibration()

            panel.acquisition.variables["basis"].set("p")
            panel._apply()
            assert app.cluster_processor.config.analysis_mode == "single"
            assert len(processing_nullifiers(app.cluster_processor.config)) == 1
            panel._preset()
            restored = app.cluster_processor.config
            assert restored.analysis_mode == "cluster"
            assert restored.mode_samples is None
            assert restored.variance_window_modes is None
            assert restored.calibration_estimator == "auto"
            assert restored.nullifiers == (LAB_CLUSTER_P,)
            assert restored.variance_estimator == "lab_rolling"
            panel.acquisition.variables["basis"].set("x")
            panel._apply()
            assert app.cluster_processor.config.nullifiers == (LAB_CLUSTER_X,)
            assert not errors
            dialog._close()
            app.analysis_notebook.select(0)
            app.update()
            assert app.spectrum_canvas.winfo_width() > 500
        finally:
            # Exercise root-owned timer cancellation before child widgets are destroyed.
            for callback in app.tk.call("after", "info"):
                app.after_cancel(callback)
            app.destroy()


def test_nullifier_tabs_configuration_layout_and_timer_cleanup() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests.e2e.test_native_receiver_nullifiers import "
            "check_nullifier_tabs; check_nullifier_tabs()",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
