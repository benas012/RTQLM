from __future__ import annotations

import time
from tkinter import messagebox
from typing import Any

import numpy as np

from apps.native_receiver.calibration_ui import CALIBRATION_SECONDS
from apps.native_receiver.constants import REDPITAYA_CONNECT_TIMEOUT_SECONDS
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

    def open_gpio_input(self) -> None:
        device = self.selected_device
        if device is None:
            messagebox.showinfo("GPIO (E1)", "Select a Red Pitaya first.", parent=self)
            return
        dialogs = self.__dict__.setdefault("gpio_dialogs", {})
        dialog = dialogs.get(device.id)
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        from apps.native_receiver.gpio_input import GpioInputDialog

        dialogs[device.id] = GpioInputDialog(self, device)

    def mode_sample_rate_hz(self) -> float | None:
        if self.__dict__.get("selected_device") is None:
            return None
        try:
            return 125_000_000 / max(int(self.acquisition_decimation.get()), 1)
        except ValueError:
            return None

    def _refresh_mode_sampling(self) -> None:
        for name in ("nullifier_panel", "cluster_dialog"):
            view = self.__dict__.get(name)
            if view is not None and view.winfo_exists():
                view.analysis.refresh_sampling()

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
        if self.__dict__.get("_calibration_started") is not None:
            self.clear_cluster_calibration()
        if self.__dict__.get("cluster_board_backend", False):
            from quantum_light_engine.post_processing import config_to_dict

            self._board_nullifier_command("configure", config_to_dict(config))
        with self.cluster_lock:
            self.cluster_processor.configure(config)
            self.latest_cluster_result = None
            self.cluster_error = None
            self.cluster_last_sequence = None
        save_cluster_config(config)
        self._clear_cluster_chart_views()
        panel = getattr(self, "nullifier_panel", None)
        if panel is not None:
            panel.load_config(config)

    def start_server_stream(self, config: ClusterProcessingConfig) -> None:
        from dataclasses import replace

        config = replace(config, lock_seconds=0, window_mode="periodic")
        self.start_buffered_capture(config, server_stream=True)

    def start_buffered_capture(
        self, config: ClusterProcessingConfig, *, server_stream: bool = False,
    ) -> None:
        from apps.native_receiver.helpers import editable_profile_payload
        from quantum_light_engine.post_processing import config_to_dict

        device = self.selected_device
        if device is None:
            raise ValueError("Select a Red Pitaya first")
        base = self._api_base()
        self._cancel_calibration_timer()
        self.disconnect_socket()

        def work() -> list[Any]:
            path = f"/api/v1/devices/{device.id}"
            self._api_for_base(base, "POST", path + "/disconnect")
            body = editable_profile_payload(self._api_for_base(base, "GET", path))
            body.update(
                acquisitionMode="streaming" if server_stream else "buffered",
                serverStreamProcessing=server_stream, bufferedProcessing="arm",
                decimation=2 if server_stream else 1, captureSamples=625_024,
            )
            if server_stream:
                body.update(channel1Enabled=True, channel2Enabled=True,
                            measurementWindowMs=config.collect_seconds * 1000)
            self._api_for_base(base, "PUT", path, body)
            self._api_for_base(base, "POST", path + "/nullifiers/commands",
                               {"command": "configure", "config": config_to_dict(config)},
                               timeout_seconds=REDPITAYA_CONNECT_TIMEOUT_SECONDS)
            self._api_for_base(base, "POST", path + "/connect",
                               timeout_seconds=REDPITAYA_CONNECT_TIMEOUT_SECONDS)
            self._api_for_base(base, "POST", path + "/start", timeout_seconds=40)
            return self._load_devices(base)

        def apply(devices: list[Any]) -> None:
            with self.cluster_lock:
                self.cluster_processor.configure(config)
            save_cluster_config(config)
            self._clear_live_plot()
            self._apply_loaded_devices(devices)
            self._select_device_by_id(device.id)
            self.nullifier_panel.load_config(config)
            self.calibration_progress.set(0)
            self.calibration_text.set("Shot noise · 10 s")
            self._open_device_tab(device.id)
            self._start_socket(device.id)

        self._run_busy("Starting server stream" if server_stream else "Starting buffered captures",
                       work, apply)

    def start_cluster_calibration(self) -> None:
        if self.__dict__.get("_calibration_started") is not None:
            self.clear_cluster_calibration()
        else:
            self._cancel_calibration_timer()
        target = None
        if self.__dict__.get("cluster_board_backend", False):
            self._board_nullifier_command("start_calibration")
            target = (self._api_base(), self.selected_device.id)
        with self.cluster_lock:
            self.cluster_processor.start_calibration()
            self.latest_cluster_result = None
            self.cluster_error = None
        self._clear_cluster_chart_views()
        self._calibration_target = target
        self._calibration_started = time.monotonic()
        self.calibration_progress.set(0.0)
        self._update_calibration_progress()

    def finish_cluster_calibration(self) -> dict[str, float]:
        target = self.__dict__.get("_calibration_target")
        if target is not None or self.__dict__.get("cluster_board_backend", False):
            result = self._board_nullifier_command("finish_calibration", target=target)
            with self.cluster_lock:
                self.latest_cluster_result = None
                self.cluster_error = None
            self._clear_cluster_chart_views()
            coefficients = dict(result["shotNoiseCoefficients"])
        else:
            with self.cluster_lock:
                coefficients = self.cluster_processor.finish_calibration()
                self.latest_cluster_result = None
                self.cluster_error = None
            self._clear_cluster_chart_views()
        self._cancel_calibration_timer()
        self.calibration_progress.set(CALIBRATION_SECONDS)
        self.calibration_text.set("Reference ready")
        return dict(coefficients)

    def clear_cluster_calibration(self) -> None:
        target = self.__dict__.get("_calibration_target")
        self._cancel_calibration_timer()
        self.calibration_progress.set(0.0)
        self.calibration_text.set("Shot noise · 10 s")
        if target is not None or self.__dict__.get("cluster_board_backend", False):
            self._board_nullifier_command("clear_calibration", target=target)
        with self.cluster_lock:
            self.cluster_processor.clear_calibration()
            self.latest_cluster_result = None
            self.cluster_error = None
        self._clear_cluster_chart_views()

    def _cancel_calibration_timer(self) -> None:
        callback = self.__dict__.get("_calibration_after_id")
        if callback is not None:
            self.after_cancel(callback)
        self._calibration_after_id = None
        self._calibration_started = None
        self._calibration_target = None

    def _update_calibration_progress(self) -> None:
        self._calibration_after_id = None
        started = self.__dict__.get("_calibration_started")
        if started is None:
            return
        elapsed = max(0.0, time.monotonic() - started)
        self.calibration_progress.set(min(elapsed, CALIBRATION_SECONDS))
        remaining = max(0.0, CALIBRATION_SECONDS - elapsed)
        if remaining > 0:
            self.calibration_text.set(f"Recording · {remaining:4.1f} s left")
            self._calibration_after_id = self.after(100, self._update_calibration_progress)
            return
        self.calibration_text.set("Finishing reference…")
        try:
            self.finish_cluster_calibration()
        except Exception as exc:
            try:
                self.clear_cluster_calibration()
            except Exception:
                self._cancel_calibration_timer()
            self.calibration_text.set("Calibration failed")
            with self.cluster_lock:
                self.cluster_error = f"Calibration failed: {exc}"
            self._show_toast("Calibration failed", str(exc), level="error")

    def _clear_cluster_chart_views(self) -> None:
        for name in ("nullifier_panel", "cluster_dialog"):
            view = getattr(self, name, None)
            if view is not None and view.winfo_exists():
                view._clear_plot_view()

    def cluster_snapshot(self) -> tuple[ClusterProcessingResult | None, str | None]:
        with self.cluster_lock:
            return self.latest_cluster_result, self.cluster_error

    def cluster_acquisition_state(self) -> str:
        with self.cluster_lock:
            if self.__dict__.get("cluster_board_backend", False):
                return (self.latest_cluster_result.acquisition_state
                        if self.latest_cluster_result is not None else "waiting for board results")
            return self.cluster_processor.acquisition_state

    def _board_nullifier_command(
        self, command: str, config: dict[str, object] | None = None,
        *, target: tuple[str, str] | None = None,
    ) -> dict:
        from apps.native_receiver.api_client import EngineClient

        body: dict[str, object] = {"command": command}
        if config is not None:
            body["config"] = config
        api_base, device_id = target or (self._api_base(), self.selected_device.id)
        return EngineClient(api_base).request(
            "POST", f"/api/v1/devices/{device_id}/nullifiers/commands", body,
            timeout_seconds=REDPITAYA_CONNECT_TIMEOUT_SECONDS,
        )
