from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from apps.native_receiver import receiver_tools
from apps.native_receiver.cluster_monitor import ClusterMonitorDialog
from apps.native_receiver.native_receiver import ReceiverApp
from quantum_light_engine.post_processing import (
    DEFAULT_CLUSTER_P,
    NullifierSpec,
    NullifierTerm,
)


def check_nullifier_tabs() -> None:
    def start_ui(app: ReceiverApp) -> None:
        app.attributes("-alpha", 0)
        app._build_menu()
        app._build()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(ReceiverApp, "_start_receiver", start_ui)
        monkeypatch.setattr(receiver_tools, "save_cluster_config", lambda _config: None)
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
                assert panel.canvas.winfo_height() >= 120
                assert panel.acquisition.winfo_width() >= panel.acquisition.winfo_reqwidth()
                bottom = panel.winfo_rooty() - app.winfo_rooty() + panel.winfo_height()
                assert bottom <= app.winfo_height()
            panel.acquisition.variables["basis"].set("p")
            panel.variance_estimator.set("Rolling modes")
            panel._apply()
            assert app.cluster_processor.config.nullifiers == (DEFAULT_CLUSTER_P,)
            assert app.cluster_processor.config.variance_estimator == "rolling"
            custom = replace(
                app.cluster_processor.config,
                nullifiers=(NullifierSpec("custom", (NullifierTerm("a", 2, -2.0),)),),
            )
            app.apply_cluster_config(custom)
            panel.acquisition.variables["basis"].set("x")
            panel._apply()
            assert app.cluster_processor.config.nullifiers == custom.nullifiers
            dialog = ClusterMonitorDialog(app)
            dialog.attributes("-alpha", 0)
            app.update()
            assert dialog._config().nullifiers == custom.nullifiers
            assert dialog._config().history_modes == custom.history_modes
            assert dialog._config().variance_estimator == custom.variance_estimator
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
