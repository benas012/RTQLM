from __future__ import annotations

from tkinter import messagebox
from typing import Any

import numpy as np

from apps.native_receiver.dialogs import (
    EngineSettingsDialog,
    SignalScriptDialog,
)
from apps.native_receiver.helpers import (
    device_slug,
    safe_builtins,
)
from apps.native_receiver.settings import (
    SIGNAL_SCRIPT_FEATURE_ENV,
    AppSettings,
    save_cluster_config,
    save_settings,
    signal_script_feature_enabled,
)
from quantum_light_engine.post_processing import (
    ClusterProcessingConfig,
    ClusterProcessingResult,
)


class ReceiverToolsMixin:
    def refresh_devices(self) -> None:
        self.status.set("loading devices")
        api_base = self._api_base()
        self._ui_log("refresh devices clicked", api_base=api_base)
        self._run_busy(
            "Refreshing Red Pitayas",
            lambda: self._load_devices(api_base),
            self._apply_loaded_devices,
        )

    def save_settings(self) -> None:
        settings = AppSettings(
            api_base=self._api_base(),
            launch_engine_on_start=self.launch_engine_on_start.get(),
        )
        save_settings(settings)
        self.status.set("settings saved")

    def open_engine_settings(self) -> None:
        EngineSettingsDialog(self)

    def open_signal_editor(self) -> None:
        if not self._signal_scripting_enabled():
            messagebox.showinfo(
                "Signal scripting disabled",
                (
                    f"Python signal scripting is disabled. Set {SIGNAL_SCRIPT_FEATURE_ENV}=1 "
                    "before launching the native receiver to enable it."
                ),
                parent=self,
            )
            return
        SignalScriptDialog(self)

    def _signal_scripting_enabled(self) -> bool:
        return bool(getattr(self, "signal_scripts_enabled", signal_script_feature_enabled()))

    def _signal_script_active(self) -> bool:
        return (
            self._signal_scripting_enabled() and getattr(self, "signal_processor", None) is not None
        )

    def available_signal_ids(self) -> list[str]:
        ids = ["selected.ch1", "selected.ch2"]
        for device in self.devices:
            slug = device_slug(device)
            if device.channel_1_enabled:
                ids.append(f"{slug}.ch1")
                ids.append(f"{device.id}.ch1")
            if device.channel_2_enabled:
                ids.append(f"{slug}.ch2")
                ids.append(f"{device.id}.ch2")
        return sorted(dict.fromkeys(ids))

    def set_signal_script(self, code: str) -> None:
        if not self._signal_scripting_enabled():
            raise ValueError(
                f"Python signal scripting is disabled. Set {SIGNAL_SCRIPT_FEATURE_ENV}=1 "
                "before launching the native receiver to enable it."
            )
        namespace: dict[str, Any] = {"np": np}
        exec(code, {"__builtins__": safe_builtins()}, namespace)
        processor = namespace.get("process")
        if not callable(processor):
            raise ValueError("Script must define a callable process(signals, np).")
        self.signal_script = code
        self.signal_processor = processor
        self.script_status.set("signal script enabled")
        self.status.set("signal script enabled")

    def clear_signal_script(self) -> None:
        self.signal_processor = None
        self.script_status.set("signal script disabled")
        self.status.set("signal script disabled")

    def open_cluster_monitor(self) -> None:
        if self.cluster_dialog is not None and self.cluster_dialog.winfo_exists():
            self.cluster_dialog.lift()
            self.cluster_dialog.focus_force()
            return
        from apps.native_receiver.cluster_monitor import ClusterMonitorDialog

        self.cluster_dialog = ClusterMonitorDialog(self)

    def apply_cluster_config(self, config: ClusterProcessingConfig) -> None:
        with self.cluster_lock:
            self.cluster_processor.configure(config)
            self.latest_cluster_result = None
            self.cluster_error = None
            self.cluster_last_sequence = None
        save_cluster_config(config)
        panel = getattr(self, "nullifier_panel", None)
        if panel is not None:
            panel.load_config(config)

    def start_cluster_calibration(self) -> None:
        with self.cluster_lock:
            self.cluster_processor.start_calibration()
            self.latest_cluster_result = None
            self.cluster_error = None

    def finish_cluster_calibration(self) -> dict[str, float]:
        with self.cluster_lock:
            coefficients = self.cluster_processor.finish_calibration()
            self.latest_cluster_result = None
            self.cluster_error = None
            return dict(coefficients)

    def clear_cluster_calibration(self) -> None:
        with self.cluster_lock:
            self.cluster_processor.clear_calibration()
            self.latest_cluster_result = None
            self.cluster_error = None

    def cluster_snapshot(self) -> tuple[ClusterProcessingResult | None, str | None]:
        with self.cluster_lock:
            return self.latest_cluster_result, self.cluster_error

    def cluster_acquisition_state(self) -> str:
        with self.cluster_lock:
            return self.cluster_processor.acquisition_state
