from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import pytest

from quantum_light_engine.post_processing import config_to_dict, single_mode_processing_config
from tests.e2e.test_native_receiver_rendering import receiver


def assert_controls_inside(window: tk.Misc) -> None:
    pending = list(window.winfo_children())
    left, top = window.winfo_rootx(), window.winfo_rooty()
    right, bottom = left + window.winfo_width(), top + window.winfo_height()
    while pending:
        widget = pending.pop()
        if widget.winfo_toplevel() != window or not widget.winfo_ismapped():
            continue
        if isinstance(widget, (ttk.Button, ttk.Entry, ttk.Combobox, ttk.Checkbutton)):
            x, y = widget.winfo_rootx(), widget.winfo_rooty()
            assert left <= x < x + widget.winfo_width() <= right, (widget, x, right)
            assert top <= y < y + widget.winfo_height() <= bottom, (widget, y, bottom)
        pending.extend(widget.winfo_children())


def check_original_layout() -> None:
    with receiver(show_live_signal=False) as app:
        app.attributes("-alpha", 0)
        panel = app.nullifier_panel
        controls = panel.analysis
        rate = [125_000_000.0]
        original_provider = controls.sample_rate_provider
        original_config = app.cluster_processor.config
        controls.sample_rate_provider = lambda: rate[0]
        controls.load(single_mode_processing_config(4, 100))
        controls.variables['mode_width_us'].set('.320')
        assert 'mode_samples' not in controls.variables
        assert 'Samples / mode: 40' in controls.sampling_readout.get()
        controls.variables['mode_width_us'].set('.308')
        assert 'Samples / mode: 38/39' in controls.sampling_readout.get()
        assert 'average width: 0.308' in controls.sampling_readout.get()
        assert 'bins: 0.304/0.312' in controls.sampling_readout.get()
        controls.variables['mode_width_us'].set('.320')
        rate[0] = 15_625_000
        controls.refresh_sampling()
        assert controls.variables['mode_width_us'].get() == '.320'
        assert 'Samples / mode: 5' in controls.sampling_readout.get()
        data = config_to_dict(single_mode_processing_config(4, 100))
        controls.apply_to(data)
        assert data['modeSamples'] is None
        assert data['modeWidthSeconds'] == pytest.approx(320e-9)
        rate[0] = 125_000_000 / 1024
        controls.variables['mode_width_us'].set('.308')
        assert 'Samples / mode: 0' in controls.sampling_readout.get()
        with pytest.raises(ValueError, match='0 samples / mode'):
            controls.apply_to(data)
        controls.load(single_mode_processing_config(56, 100))
        assert float(controls.variables['mode_width_us'].get()) == pytest.approx(458.752)
        controls.sample_rate_provider = original_provider
        controls.load(original_config)
        for geometry in ("1280x900", "1180x840", "960x740"):
            app.geometry(geometry)
            app.update()
            assert not app.canvas.winfo_ismapped()
            assert not app.waveform_surface.winfo_ismapped()
            assert app.analysis_notebook.winfo_ismapped()
            assert app.analysis_notebook.select() == str(panel)
            assert panel.canvas.winfo_ismapped()
            assert not panel.setup_window.winfo_ismapped()
            assert panel.canvas.winfo_height() >= 120
            expanded_height = panel.canvas.winfo_height()
            app.show_live_signal.set(True)
            app._update_live_signal_visibility()
            app.update()
            assert app.canvas.winfo_ismapped()
            assert panel.canvas.winfo_height() < expanded_height
            assert_controls_inside(app)
            app.show_live_signal.set(False)
            app._update_live_signal_visibility()
            app.update()
            assert not app.canvas.winfo_ismapped()
            assert panel.canvas.winfo_height() == expanded_height
            assert_controls_inside(app)
            panel._toggle_settings()
            app.update()
            assert panel.acquisition.winfo_ismapped()
            assert panel.analysis.winfo_ismapped()
            assert panel.canvas.winfo_height() >= 120
            assert_controls_inside(panel.setup_window)
            panel._hide_settings()
            app.update()
            app._toggle_waveform_controls()
            app.update()
            assert_controls_inside(app.display_settings_window)
            app._hide_waveform_controls()
            app.update()
            assert not hasattr(app, "waveform_more_button")
            pending = [app]
            while pending:
                widget = pending.pop()
                if isinstance(widget, ttk.Combobox):
                    if str(widget.cget("textvariable")) == str(app.acquisition_decimation):
                        assert not widget.winfo_ismapped()
                if isinstance(widget, (ttk.Label, ttk.Button)):
                    if widget.cget("text") in {"Decimation (÷)", "Low delay", "2CH stable"}:
                        assert not widget.winfo_ismapped()
                if isinstance(widget, ttk.Button):
                    assert "More controls" not in widget.cget("text")
                    assert "Analysis settings" not in widget.cget("text")
                pending.extend(widget.winfo_children())


def test_original_nullifier_layout() -> None:
    result = subprocess.run(
        [sys.executable, "-c",
         "from tests.e2e.test_native_receiver_layout import check_original_layout; "
         "check_original_layout()"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
