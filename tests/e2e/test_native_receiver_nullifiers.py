from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox

import pytest

from apps.native_receiver import receiver_tools
from apps.native_receiver.cluster_monitor import ClusterMonitorDialog
from apps.native_receiver.native_receiver import ReceiverApp
from quantum_light_engine.post_processing import (
    LAB_CLUSTER_P,
    LAB_CLUSTER_X,
    NullifierSpec,
    NullifierTerm,
    config_from_dict,
    config_to_dict,
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
        monkeypatch.setattr(receiver_tools, "save_cluster_config", lambda _config: None)
        monkeypatch.setattr(messagebox, "showerror", lambda *args, **_kwargs: errors.append(args))
        app = ReceiverApp()
        try:
            app.update()
            panel = app.nullifier_panel
            assert app.analysis_notebook.tab(0, "text") == "Spectrum analyzer"
            assert app.analysis_notebook.tab(1, "text") == "Nullifiers"
            assert app.analysis_notebook.select() == str(panel)
            for geometry in ("1180x760", "960x640", "1600x1000"):
                app.geometry(geometry)
                app.update()
                assert panel.canvas.winfo_height() >= 120, (
                    geometry, panel.winfo_height(), panel.winfo_reqheight(),
                    panel.canvas.winfo_height(), panel.analysis.winfo_reqwidth(),
                    panel.analysis.winfo_width(),
                )
                assert panel.acquisition.winfo_width() >= panel.acquisition.winfo_reqwidth()
                assert panel.analysis.winfo_width() >= panel.analysis.winfo_reqwidth()
                bottom = panel.winfo_rooty() - app.winfo_rooty() + panel.winfo_height()
                assert bottom <= app.winfo_height()
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
            assert panel.analysis.variables["mode_samples"].get() == ""
            assert {
                term.source for spec in processing_nullifiers(loaded) for term in spec.terms
            } == {"a", "b"}

            # Explicit bins, channel selection and full-trace calibration survive the editor.
            dialog.analysis.variables["mode_samples"].set("32")
            dialog.analysis.variables["window_modes"].set("100")
            dialog.analysis.variables["channel"].set("CH1")
            dialog.analysis.variables["calibration"].set("Whole accepted trace")
            dialog.mode_shape.set("identity")
            dialog._apply()
            single = app.cluster_processor.config
            assert single.mode_samples == 32
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

            # Calibration starts with the edited point count, rather than the old settings.
            panel.analysis.variables["mode_samples"].set("48")
            panel._start_calibration()
            assert app.cluster_processor.config.mode_samples == 48
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
