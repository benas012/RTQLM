from __future__ import annotations

from tkinter import messagebox

from apps.native_receiver.constants import (
    DISCONNECTED_STATES,
)
from apps.native_receiver.dialogs import ApiError
from apps.native_receiver.helpers import (
    acquisition_batch_seconds,
    acquisition_channel_count,
    editable_profile_payload,
    format_duration,
    format_frequency,
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
        self.acquisition_batch.set(f"Batch: {format_duration(batch_seconds)} · {channel_count} ch")

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
