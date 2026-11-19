from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from apps.native_receiver.api_client import ApiError, EngineClient
from apps.native_receiver.constants import (
    COLORS,
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
        can_restart = runtime_state in DISCONNECTED_STATES | {"faulted", "ready"}
        connected = device is not None and (
            runtime_state not in DISCONNECTED_STATES or socket_alive
        )
        if "acquisition_state" in self.__dict__:
            label = self._device_state_label(runtime_state)[0].lstrip("●○◐! ")
            if runtime_state == "ready" and self.__dict__.get("stream_stopped", False):
                label = "Stopped"
            if (device is not None
                    and getattr(device, "acquisition_mode", "streaming") == "buffered"
                    and runtime_state == "streaming"):
                label = "Buffered · gaps"
            set_if_changed(self.acquisition_state, label if device is not None else "Disconnected")
        if "connect_button" in self.__dict__:
            button_text = (
                "Retry connection" if runtime_state == "faulted" else
                "Start streaming" if runtime_state == "ready" else
                "Reconnect" if socket_alive and can_restart else
                "Attach stream" if runtime_state == "streaming" and not socket_alive else "Connect"
            )
            self.connect_button.configure(
                text=button_text,
                state=tk.NORMAL if enabled and (not socket_alive or can_restart) else tk.DISABLED,
            )
        if "stop_stream_button" in self.__dict__:
            self.stop_stream_button.configure(
                state=tk.NORMAL if enabled and runtime_state in STREAMING_STATES else tk.DISABLED,
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
        socket_alive = self.socket_thread is not None and self.socket_thread.is_alive()
        cached_state = str(self.selected_device.state or "").lower()
        if socket_alive and cached_state not in DISCONNECTED_STATES | {"faulted", "ready"}:
            return

        device_id = self.selected_device.id
        api_base = self._api_base()
        self._ui_log(
            "connect clicked",
            device_id=device_id,
            cached_state=self.selected_device.state,
        )
        self.connection_failure_active = False
        self.stream_stopped = False
        self.status.set("attaching sample socket")
        self._open_device_tab(device_id)
        if not socket_alive:
            self._start_socket(device_id)

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

    def stop_streaming(self) -> None:
        if self.selected_device is None:
            return
        device_id, api_base = self.selected_device.id, self._api_base()
        target = self.__dict__.get("_calibration_target")
        recording = self.__dict__.get("_calibration_started") is not None and (
            target is None or target[1] == device_id)
        if recording:
            self._cancel_calibration_timer()
            self.calibration_progress.set(0)
            self.calibration_text.set("Shot noise · 10 s")
            with self.cluster_lock:
                self.cluster_processor.clear_calibration()
        self.stream_stopped = True

        def work() -> list[Device]:
            try:
                if recording:
                    self._api_for_base(api_base, "POST",
                        f"/api/v1/devices/{device_id}/nullifiers/commands",
                        {"command": "clear_calibration"})
                self._api_for_base(api_base, "POST", f"/api/v1/devices/{device_id}/stop")
                return self._load_devices(api_base)
            except Exception:
                self.stream_stopped = False
                raise

        def apply(devices: list[Device]) -> None:
            self._apply_loaded_devices(devices)
            self._select_device_by_id(device_id)
            self.status.set("Streaming stopped")

        self._run_busy("Stopping streaming", work, apply)

    def disconnect_selected(self) -> None:
        if self.selected_device is None:
            messagebox.showerror("No device", "Select a Red Pitaya first.")
            return
        device_id = self.selected_device.id
        target = self.__dict__.get("_calibration_target")
        if "_calibration_started" in self.__dict__ and (target is None or target[1] == device_id):
            self._cancel_calibration_timer()
            self.calibration_progress.set(0.0)
            self.calibration_text.set("Shot noise · 10 s")
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

    def _load_devices(self, api_base: str) -> list[Device]:
        return EngineClient(api_base).devices()

    def _apply_loaded_devices(self, devices: list[Device]) -> None:
        self.devices = devices
        self._render_devices()
        socket_alive = self.socket_thread is not None and self.socket_thread.is_alive()
        self._set_engine_status("ok")
        self.status.set("connected" if socket_alive else "disconnected")
        self._ui_log("device list updated", count=len(devices), socket_alive=socket_alive)

    def _render_devices(self) -> None:
        selected_id = self.selected_device.id if self.selected_device is not None else None
        for item in self.device_list.get_children():
            self.device_list.delete(item)
        self.device_list.tag_configure("streaming", foreground=COLORS["success"])
        self.device_list.tag_configure("warning", foreground=COLORS["warning"])
        self.device_list.tag_configure("offline", foreground=COLORS["text_secondary"])
        self.device_list.tag_configure("empty", foreground=COLORS["text_disabled"])
        for device in self.devices:
            state_label, tag = self._device_state_label(device.state)
            channels = (
                "/".join(
                    label
                    for label, enabled in (
                        ("CH1", device.channel_1_enabled),
                        ("CH2", device.channel_2_enabled),
                    )
                    if enabled
                )
                or "None"
            )
            self.device_list.insert(
                "",
                tk.END,
                iid=device.id,
                text=f"{device.name}\n{device.host}",
                values=(state_label, channels),
                tags=(tag,),
            )
        if not self.devices:
            self.device_list.insert(
                "",
                tk.END,
                iid="__empty__",
                text="No devices registered\nUse Add device to begin",
                values=("", ""),
                tags=("empty",),
            )
            self.selected_device = None
            self.selected.set("-")
        elif selected_id and (selected := self._device_by_id(selected_id)) is not None:
            self.selected_device = selected
            self.device_list.selection_set(selected_id)
            self.device_list.see(selected_id)
        if hasattr(self, "redpitaya_menu"):
            self._populate_redpitaya_menu()
        self._update_connect_controls()
        self._draw_tabs()

    @staticmethod
    def _device_state_label(state: str) -> tuple[str, str]:
        normalized = str(state or "").lower()
        if normalized == "streaming":
            return "● Streaming", "streaming"
        if normalized == "ready":
            return "● Ready", "streaming"
        if normalized in {"starting", "connecting", "reconnecting"}:
            return f"◐ {normalized.capitalize()}", "warning"
        if normalized in DISCONNECTED_STATES:
            return "○ Offline", "offline"
        return f"! {normalized.capitalize() or 'Unknown'}", "warning"

    def _select_from_list(self, _event: tk.Event[ttk.Treeview]) -> None:
        selection = self.device_list.selection()
        if not selection:
            return
        device_id = selection[0]
        if device_id == "__empty__":
            self.device_list.selection_remove(device_id)
            return
        selected = self._device_by_id(device_id)
        if selected is None:
            return
        self.selected_device = selected
        self.selected.set(f"{self.selected_device.name} ({self.selected_device.host})")
        self._sync_acquisition_controls(self.selected_device)
        self._populate_redpitaya_menu()
        self._update_connect_controls()
        self._draw_tabs()

    def _select_device_by_id(self, device_id: str) -> None:
        for device in self.devices:
            if device.id == device_id:
                self.device_list.selection_set(device_id)
                self.device_list.see(device_id)
                self.selected_device = device
                self.selected.set(f"{device.name} ({device.host})")
                self._sync_acquisition_controls(device)
                self._populate_redpitaya_menu()
                self._update_connect_controls()
                self._draw_tabs()
                return

    def refresh_devices(self) -> None:
        self.status.set("loading devices")
        api_base = self._api_base()
        self._ui_log("refresh devices clicked", api_base=api_base)
        self._run_busy(
            "Refreshing Red Pitayas",
            lambda: self._load_devices(api_base),
            self._apply_loaded_devices,
        )
