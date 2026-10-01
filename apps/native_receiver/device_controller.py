from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any

from apps.native_receiver.constants import (
    CONNECTION_FAILED_NOTICE,
    CONNECTION_FAILED_SPECTRUM_NOTICE,
    CONNECTION_OVERLAY_BUSY_LABELS,
    DISCONNECTED_STATES,
    ENGINE_CONNECT_TIMEOUT_SECONDS,
    READY_STATES,
    REDPITAYA_CONNECT_TIMEOUT_SECONDS,
    REDPITAYA_START_TIMEOUT_SECONDS,
    STREAMING_STATES,
)
from apps.native_receiver.dialogs import (
    AddRedPitayaDialog,
    ApiError,
)
from apps.native_receiver.helpers import (
    editable_profile_payload,
    set_if_changed,
    short_error,
)
from apps.native_receiver.protocol import Device


class DeviceControllerMixin:
    def _update_connect_controls(self) -> None:
        device = self.selected_device
        enabled = device is not None and not self.__dict__.get("loading_active", False)
        socket_alive = self.socket_thread is not None and self.socket_thread.is_alive()
        runtime_state = str(device.state or "").lower() if device is not None else ""
        connected = device is not None and (
            runtime_state not in DISCONNECTED_STATES or socket_alive
        )
        if "connect_button" in self.__dict__:
            button_text = (
                "Attach stream" if runtime_state == "streaming" and not socket_alive else "Connect"
            )
            self.connect_button.configure(
                text=button_text, state=tk.NORMAL if enabled and not socket_alive else tk.DISABLED
            )
        if "disconnect_button" in self.__dict__:
            if connected:
                self.disconnect_button.grid()
                self.disconnect_button.configure(
                    state=tk.NORMAL if not self.loading_active else tk.DISABLED
                )
            else:
                self.disconnect_button.grid_remove()
        for name in (
            "channel_1_button",
            "channel_2_button",
            "forget_button",
            "apply_acquisition_button",
        ):
            button = self.__dict__.get(name)
            if button is not None:
                button.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        if "connection_summary" in self.__dict__:
            if device is None:
                summary = "No device selected"
            else:
                state_label, _tag = self._device_state_label(device.state)
                summary = f"{device.name} · {state_label.lstrip('●○◐! ')}"
            set_if_changed(self.connection_summary, summary)

    def add_device(self) -> None:
        dialog = AddRedPitayaDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        body = {
            "name": dialog.result["name"],
            "source": "redpitaya",
            "host": dialog.result["host"],
            "enabled": True,
            "isDefault": False,
            "channel1Enabled": dialog.result["channel1Enabled"],
            "channel2Enabled": dialog.result["channel2Enabled"],
            "decimation": 64,
            "blockSizeBytes": 16384,
            "adcBufferSizeBytes": 4194304,
            "streamingModulePath": None,
            "simulatorSamplesPerPacket": 4096,
            "simulatorSeed": 1,
        }

        api_base = self._api_base()
        self._ui_log("add Red Pitaya submitted", host=dialog.result["host"])

        def work() -> tuple[str, list[Device]]:
            created = self._api_for_base(api_base, "POST", "/api/v1/devices", body)
            return str(created["id"]), self._load_devices(api_base)

        def apply(result: tuple[str, list[Device]]) -> None:
            created_id, devices = result
            self._apply_loaded_devices(devices)
            self._select_device_by_id(created_id)
            self.status.set("Red Pitaya added")

        self._run_busy("Adding Red Pitaya", work, apply)

    def forget_selected(self) -> None:
        if self.selected_device is None:
            messagebox.showerror("No device", "Select a Red Pitaya first.")
            return
        device = self.selected_device
        confirmed = messagebox.askyesno(
            "Forget Red Pitaya",
            f"Forget '{device.name}' at {device.host}?\n\n"
            "This removes the saved profile. It does not modify the Red Pitaya.",
            parent=self,
        )
        if not confirmed:
            return
        device_id = device.id
        api_base = self._api_base()
        self._ui_log("forget Red Pitaya confirmed", device_id=device_id, host=device.host)
        if self.socket_thread and self.socket_thread.is_alive():
            self.disconnect_socket()

        def work() -> list[Device]:
            if str(device.state or "").lower() not in DISCONNECTED_STATES:
                self._api_for_base(api_base, "POST", f"/api/v1/devices/{device_id}/disconnect")
            self._api_for_base(api_base, "DELETE", f"/api/v1/devices/{device_id}")
            return self._load_devices(api_base)

        def apply(devices: list[Device]) -> None:
            self._apply_loaded_devices(devices)
            self.selected_device = None
            self.selected.set("-")
            self._clear_live_plot()
            self.status.set("device forgotten")

        self._run_busy("Forgetting Red Pitaya", work, apply)

    def toggle_channel(self, channel: int) -> None:
        if self.selected_device is None:
            messagebox.showerror("No device", "Select a Red Pitaya first.")
            return
        device = self.selected_device
        api_base = self._api_base()
        label = f"Updating CH{channel} acquisition"
        self._ui_log("acquisition channel toggle clicked", device_id=device.id, channel=channel)

        def work() -> tuple[str, bool, list[Device]]:
            profile = self._api_for_base(api_base, "GET", f"/api/v1/devices/{device.id}")
            runtime_state = str((profile.get("runtime") or {}).get("state") or device.state)
            body = editable_profile_payload(profile)
            key = "channel1Enabled" if channel == 1 else "channel2Enabled"
            body[key] = not bool(body[key])
            if not body["channel1Enabled"] and not body["channel2Enabled"]:
                raise ApiError("At least one channel must stay enabled.")

            if self._is_stream_active(runtime_state):
                raise ApiError(
                    f"Stop acquisition with Disconnect before changing CH{channel} acquisition.",
                )
            self._require_backend_disconnected(device.id, runtime_state, api_base=api_base)

            updated = self._api_for_base(api_base, "PUT", f"/api/v1/devices/{device.id}", body)
            return str(updated["id"]), bool(body[key]), self._load_devices(api_base)

        def apply(result: tuple[str, bool, list[Device]]) -> None:
            updated_id, enabled, devices = result
            self._clear_live_plot()
            self._apply_loaded_devices(devices)
            self._select_device_by_id(updated_id)
            self.status.set(f"CH{channel} {'enabled' if enabled else 'disabled'}")

        self._run_busy(label, work, apply)

    def connect_selected(self) -> None:
        if self.selected_device is None:
            messagebox.showerror("No device", "Select a Red Pitaya first.")
            return
        if self.socket_thread and self.socket_thread.is_alive():
            return

        device_id = self.selected_device.id
        api_base = self._api_base()
        self._ui_log(
            "connect clicked",
            device_id=device_id,
            cached_state=self.selected_device.state,
        )
        self.connection_failure_active = False
        self.status.set("attaching sample socket")
        self._open_device_tab(device_id)
        self._start_socket(device_id)

        cached_state = str(self.selected_device.state or "").lower()
        if cached_state in STREAMING_STATES:
            return

        def work() -> tuple[str, list[Device]]:
            status = self._api_for_base(
                api_base,
                "GET",
                f"/api/v1/devices/{device_id}/status",
                timeout_seconds=ENGINE_CONNECT_TIMEOUT_SECONDS,
            )
            runtime_state = str(status.get("state") or "").lower()
            if runtime_state in STREAMING_STATES:
                return device_id, self._load_devices(api_base)

            if runtime_state not in READY_STATES:
                self.events.put(("status", "connecting device"))
                try:
                    self._api_for_base(
                        api_base,
                        "POST",
                        f"/api/v1/devices/{device_id}/connect",
                        timeout_seconds=REDPITAYA_CONNECT_TIMEOUT_SECONDS,
                    )
                except Exception as exc:
                    raise ApiError(
                        "Connect failed. The receiver reached the acquisition engine, but it "
                        "could not open the Red Pitaya streaming client: "
                        f"{short_error(exc, 180)}"
                    ) from exc

            self.events.put(("status", "starting device"))
            try:
                self._api_for_base(
                    api_base,
                    "POST",
                    f"/api/v1/devices/{device_id}/start",
                    timeout_seconds=REDPITAYA_START_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                raise ApiError(
                    "Start acquisition failed. The engine connected to the selected profile but "
                    f"could not start streaming: {short_error(exc, 180)}"
                ) from exc
            return device_id, self._load_devices(api_base)

        def apply(result: tuple[str, list[Device]]) -> None:
            connected_id, devices = result
            self._open_device_tab(connected_id)
            self._apply_loaded_devices(devices)

        self._run_busy("Starting Red Pitaya acquisition", work, apply)

    def disconnect_socket(self) -> None:
        self.stop_event.set()
        self.status.set("disconnecting socket")
        self._ui_log("socket disconnect requested")

    def disconnect_selected(self) -> None:
        if self.selected_device is None:
            messagebox.showerror("No device", "Select a Red Pitaya first.")
            return
        device_id = self.selected_device.id
        api_base = self._api_base()
        self._ui_log("disconnect clicked", device_id=device_id)
        self.disconnect_socket()

        def work() -> list[Device]:
            self._api_for_base(api_base, "POST", f"/api/v1/devices/{device_id}/disconnect")
            return self._load_devices(api_base)

        def apply(devices: list[Device]) -> None:
            self._clear_live_plot()
            self._apply_loaded_devices(devices)
            self._select_device_by_id(device_id)
            self.status.set("disconnected")

        self._run_busy("Disconnecting Red Pitaya", work, apply)

    def _require_backend_disconnected(
        self, device_id: str, runtime_state: str | None, *, api_base: str | None = None
    ) -> None:
        def request(method: str, path: str) -> Any:
            if api_base is None:
                return self._api(method, path)
            return self._api_for_base(api_base, method, path)

        if str(runtime_state or "").lower() not in DISCONNECTED_STATES:
            try:
                request("POST", f"/api/v1/devices/{device_id}/disconnect")
            except Exception as exc:
                raise ApiError(
                    "Cannot change channels while the backend stream is still connected. "
                    "Use Red Pitayas > Disconnect device, wait for state 'Offline', "
                    f"then toggle again. Disconnect failed: {short_error(exc)}"
                ) from exc
        status = request("GET", f"/api/v1/devices/{device_id}/status")
        state = str(status.get("state") or "").lower()
        if state not in DISCONNECTED_STATES:
            raise ApiError(
                f"Cannot change channels while backend state is '{state}'. "
                "Disconnect the device first."
            )

    def _clear_live_plot(self) -> None:
        self._reset_display_pause()
        self.trigger_capture_valid = False
        self.trigger_window_precomputed = False
        self.precomputed_trigger_index = None
        if "test_output_label" in self.__dict__:
            self._set_test_output_ui(False)
        with self.frame_lock:
            self.latest_frame = None
            self.sample_history.clear()
            self.socket_frame_count = 0
        with self.cluster_lock:
            self.cluster_processor.reset_stream()
            self.latest_cluster_result = None
            self.cluster_error = None
            self.cluster_last_sequence = None
        self.rendered_frame_count = 0
        self.last_stats_monotonic_ns = 0
        self.frames.set("0")
        self.sequence.set("-")
        self.mask.set("-")
        self.ch1_range.set("-")
        self.ch2_range.set("-")
        self.lost1.set("0")
        self.lost2.set("0")
        self.lost_pair.set("0 / 0")
        for item in self.trace_items:
            self.canvas.delete(item)
        self.trace_items.clear()
        self.canvas.delete("signal_trace")
        self.canvas.delete("signal_overlay")
        for item in self.spectrum_trace_items:
            self.spectrum_canvas.delete(item)
        self.spectrum_trace_items.clear()
        self.spectrum_canvas.delete("spectrum_trace")
        self.spectrum_peak.set("Peak: -")
        self._update_time_controls()
        notice = (
            self.loading_label
            if self.loading_active and self.loading_label not in CONNECTION_OVERLAY_BUSY_LABELS
            else CONNECTION_FAILED_NOTICE
            if self.connection_failure_active
            else "Waiting for live samples"
        )
        self._set_signal_notice(notice)
        self._set_spectrum_notice(
            self.loading_label
            if self.loading_active and self.loading_label not in CONNECTION_OVERLAY_BUSY_LABELS
            else CONNECTION_FAILED_SPECTRUM_NOTICE
            if self.connection_failure_active
            else "Waiting for spectrum data"
        )
