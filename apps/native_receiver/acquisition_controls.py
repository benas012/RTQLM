from __future__ import annotations

from tkinter import messagebox
from typing import Any

from apps.native_receiver.api_client import ApiError
from apps.native_receiver.constants import (
    DISCONNECTED_STATES,
    TEST_SINE_AMPLITUDE_VOLTS,
    TEST_SINE_FREQUENCY_HZ,
)
from apps.native_receiver.helpers import (
    acquisition_batch_seconds,
    acquisition_channel_count,
    editable_profile_payload,
    format_duration,
    format_frequency,
    format_voltage,
    smooth_acquisition_block_size,
)
from apps.native_receiver.protocol import Device


class AcquisitionControlsMixin:
    def _sync_acquisition_controls(self, device: Device) -> None:
        self.acquisition_decimation.set(str(device.decimation))
        self.acquisition_block_size.set(str(device.block_size_bytes))
        self._update_acquisition_rate()

    def _update_acquisition_rate(self) -> None:
        try:
            decimation = max(int(self.acquisition_decimation.get()), 1)
            block_size = max(int(self.acquisition_block_size.get()), 1)
        except ValueError:
            self.acquisition_rate.set("Sample rate: -")
            self.acquisition_batch.set("Batch: -")
            return
        channel_count = acquisition_channel_count(self.__dict__.get("selected_device"))
        sample_rate = 125_000_000 / decimation
        batch_seconds = acquisition_batch_seconds(block_size, channel_count, sample_rate)
        self.acquisition_rate.set(f"Sample rate: {format_frequency(sample_rate)}")
        device = self.__dict__.get("selected_device")
        if device is not None and getattr(device, "acquisition_mode", "streaming") == "buffered":
            duration = format_duration(device.capture_samples / sample_rate)
            self.acquisition_batch.set(f"Buffered: {duration} · gaps")
        else:
            self.acquisition_batch.set(
                f"Batch: {format_duration(batch_seconds)} · {channel_count} ch")
        self._refresh_mode_sampling()

    def _is_stream_active(self, runtime_state: str | None = None) -> bool:
        socket_alive = self.socket_thread is not None and self.socket_thread.is_alive()
        backend_active = str(runtime_state or "").lower() not in DISCONNECTED_STATES
        return socket_alive or backend_active

    def apply_fast_acquisition(self) -> None:
        self._ui_log("smooth acquisition preset clicked")
        # Improve delivery cadence without increasing the sample rate/bandwidth.
        block_size = smooth_acquisition_block_size(int(self.acquisition_decimation.get()))
        self.acquisition_block_size.set(str(block_size))
        self._update_acquisition_rate()
        self.apply_acquisition_settings()

    def apply_stable_two_channel(self) -> None:
        self._ui_log("stable two-channel preset clicked")
        self.acquisition_decimation.set("1024")
        self.acquisition_block_size.set("32768")
        self._update_acquisition_rate()
        self.apply_acquisition_settings()

    def apply_acquisition_settings(self) -> None:
        if self.selected_device is None:
            messagebox.showerror("No device", "Select a Red Pitaya first.")
            return
        device = self.selected_device
        try:
            decimation = int(self.acquisition_decimation.get())
            block_size = int(self.acquisition_block_size.get())
        except ValueError:
            messagebox.showerror("Invalid speed", "Use numeric acquisition settings.")
            return

        if decimation < 1 or decimation > 65_536:
            messagebox.showerror("Invalid decimation", "Decimation must be from 1 to 65536.")
            return
        if block_size < 2_048 or block_size > 2_097_152:
            messagebox.showerror("Invalid block size", "Block size must be 2048 to 2097152 bytes.")
            return

        api_base = self._api_base()
        self._ui_log(
            "apply acquisition clicked",
            device_id=device.id,
            decimation=decimation,
            block_size=block_size,
        )

        def work() -> tuple[str, list[Device]]:
            profile = self._api_for_base(api_base, "GET", f"/api/v1/devices/{device.id}")
            runtime_state = str((profile.get("runtime") or {}).get("state") or device.state)
            body = editable_profile_payload(profile)
            body["decimation"] = decimation
            body["blockSizeBytes"] = block_size
            # This sidebar selects the raw continuous-stream acquisition settings.
            body["acquisitionMode"] = "streaming"
            body["bufferedProcessing"] = "arm"

            if self._is_stream_active(runtime_state):
                raise ApiError(
                    "Stop acquisition with Disconnect before changing acquisition speed."
                )
            self._require_backend_disconnected(device.id, runtime_state, api_base=api_base)

            updated = self._api_for_base(api_base, "PUT", f"/api/v1/devices/{device.id}", body)
            return str(updated["id"]), self._load_devices(api_base)

        def apply(result: tuple[str, list[Device]]) -> None:
            updated_id, devices = result
            self._clear_live_plot()
            self._apply_loaded_devices(devices)
            self._select_device_by_id(updated_id)
            self.status.set("acquisition speed updated")
            self._update_acquisition_rate()

        self._run_busy("Applying acquisition settings", work, apply)

    def open_output_signals(self) -> None:
        device = self.selected_device
        if device is None:
            messagebox.showerror("No Red Pitaya", "Select and connect a Red Pitaya first.")
            return
        from apps.native_receiver.output_signals import OutputSignalDialog

        OutputSignalDialog(self, device)

    def toggle_test_output(self) -> None:
        enable = not self.test_output_enabled
        device = self.selected_device
        if enable:
            if device is None:
                messagebox.showerror("No Red Pitaya", "Select a Red Pitaya first.")
                return
            target_device_id = device.id
        else:
            target_device_id = self.test_output_device_id or (
                device.id if device is not None else ""
            )
            if not target_device_id:
                self._set_test_output_ui(False)
                return
        action = "Starting" if enable else "Stopping"
        self.status.set(f"{action.lower()} OUT1 test sine")
        api_base = self._api_base()

        def work() -> dict[str, Any]:
            result = self._api_for_base(
                api_base,
                "POST",
                f"/api/v1/devices/{target_device_id}/outputs/1/test-sine",
                {"enabled": enable},
                timeout_seconds=30,
            )
            if not isinstance(result, dict):
                raise ApiError("The acquisition engine returned an invalid OUT1 response.")
            return result

        def apply(result: dict[str, Any]) -> None:
            enabled = bool(result.get("enabled"))
            self._set_test_output_ui(
                enabled,
                target_device_id if enabled else None,
            )
            if enabled:
                frequency_hz = float(result.get("frequencyHz", TEST_SINE_FREQUENCY_HZ))
                amplitude_volts = float(result.get("amplitudeVolts", TEST_SINE_AMPLITUDE_VOLTS))
                self.status.set(
                    f"OUT1 test: {format_frequency(frequency_hz)}, "
                    f"{format_voltage(amplitude_volts)} sine"
                )
            else:
                self.status.set("OUT1 test output stopped")
            self._ui_log(
                "OUT1 test output changed through engine",
                enabled=enabled,
                device_id=target_device_id,
            )

        self._run_busy(f"{action} OUT1 test sine", work, apply)

    def _set_test_output_ui(self, enabled: bool, device_id: str | None = None) -> None:
        self.test_output_enabled = enabled
        self.test_output_device_id = device_id if enabled else None
        self.test_output_label.set("Stop OUT1" if enabled else "OUT1 sine")
        if "test_output_button" in self.__dict__:
            self.test_output_button.configure(
                style="Active.TButton" if enabled else "Compact.TButton"
            )

    def nudge_channel_offset(self, channel: int, divisions: float) -> None:
        variable = (
            self.channel_1_offset_divisions if channel == 1 else self.channel_2_offset_divisions
        )
        next_value = min(max(variable.get() + divisions, -3.0), 3.0)
        variable.set(next_value)
        self._update_channel_offset_labels()
        self.status.set(f"CH{channel} vertical offset {next_value:+.1f} div")
        self._ui_log("channel vertical offset changed", channel=channel, divisions=next_value)
        self._redraw_latest_signal()

    def reset_channel_offsets(self) -> None:
        self.channel_1_offset_divisions.set(0.0)
        self.channel_2_offset_divisions.set(0.0)
        self._update_channel_offset_labels()
        self.status.set("channel offsets reset")
        self._ui_log("channel vertical offsets reset")
        self._redraw_latest_signal()

    def _update_channel_offset_labels(self) -> None:
        self.channel_1_offset_label.set(f"CH1 {self.channel_1_offset_divisions.get():+.1f} div")
        self.channel_2_offset_label.set(f"CH2 {self.channel_2_offset_divisions.get():+.1f} div")

    def _channel_y_offset(self, channel: int, plot_height: float) -> float:
        divisions = (
            self.channel_1_offset_divisions.get()
            if channel == 1
            else self.channel_2_offset_divisions.get()
        )
        return plot_height / 2 - divisions * plot_height / 8
