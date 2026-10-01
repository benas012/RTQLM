from __future__ import annotations

from tkinter import messagebox
from typing import Any

from apps.native_receiver.constants import (
    TEST_SINE_AMPLITUDE_VOLTS,
    TEST_SINE_FREQUENCY_HZ,
)
from apps.native_receiver.dialogs import ApiError
from apps.native_receiver.helpers import (
    format_frequency,
    format_voltage,
)


class InstrumentControlsMixin:
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
