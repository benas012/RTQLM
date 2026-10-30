from __future__ import annotations

import tkinter as tk
from typing import Any

from apps.native_receiver.constants import (
    COLORS,
    UI_LOGGER,
)
from apps.native_receiver.protocol import Device


class DeviceTabsMixin:
    def _set_engine_status(self, status: str) -> None:
        normalized = status.strip().lower()
        display = {
            "ok": "OK",
            "checking": "Checking",
            "starting": "Starting",
            "stopping": "Stopping",
            "stopped": "Stopped",
            "offline": "Offline",
            "exited": "Exited",
            "timeout": "Timed out",
            "error": "Error",
            "unknown": "Unknown",
            "": "Unknown",
        }.get(normalized, normalized.capitalize())
        if normalized == "ok":
            color = COLORS["ch1"]
        elif normalized in {"checking", "starting", "stopping"}:
            color = COLORS["warning"]
        elif normalized in {"unknown", "stopped", "offline", ""}:
            color = COLORS["text_disabled"]
        else:
            color = COLORS["danger"]
        self.engine_status.set(display)
        dot = getattr(self, "engine_status_dot", None)
        dot_item = getattr(self, "engine_status_dot_item", None)
        if dot is not None and dot_item is not None:
            dot.itemconfigure(
                dot_item,
                fill=color,
                outline=color,
            )
        self._ui_log("engine status updated", status=display)

    def _ui_log(self, message: str, **fields: Any) -> None:
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        UI_LOGGER.info("%s%s", message, f" {details}" if details else "")

    def _populate_redpitaya_menu(self) -> None:
        self.redpitaya_menu.delete(0, tk.END)
        self.redpitaya_menu.add_command(label="Refresh Red Pitayas", command=self.refresh_devices)
        self.redpitaya_menu.add_command(label="Add device…", command=self.add_device)
        self.redpitaya_menu.add_separator()

        if self.devices:
            for device in self.devices:
                marker = (
                    "*" if self.selected_device and self.selected_device.id == device.id else " "
                )
                state = device.state or "-"
                self.redpitaya_menu.add_command(
                    label=f"{marker} {device.name} ({state})",
                    command=lambda device_id=device.id: self._select_device_by_id(device_id),
                )
        else:
            self.redpitaya_menu.add_command(label="No Red Pitayas registered", state=tk.DISABLED)

        self.redpitaya_menu.add_separator()
        selection_state = tk.NORMAL if self.selected_device is not None else tk.DISABLED
        self.redpitaya_menu.add_command(
            label="Connect",
            command=self.connect_selected,
            state=selection_state,
        )
        self.redpitaya_menu.add_command(
            label="Output signals…", command=self.open_output_signals, state=selection_state
        )
        self.redpitaya_menu.add_command(label="Disconnect device", command=self.disconnect_selected)
        self.redpitaya_menu.add_command(label="Forget device…", command=self.forget_selected)
        self.redpitaya_menu.add_separator()
        self.redpitaya_menu.add_command(
            label="Toggle CH1 acquisition", command=lambda: self.toggle_channel(1)
        )
        self.redpitaya_menu.add_command(
            label="Toggle CH2 acquisition", command=lambda: self.toggle_channel(2)
        )

    def _device_by_id(self, device_id: str) -> Device | None:
        return next((device for device in self.devices if device.id == device_id), None)

    def _open_device_tab(self, device_id: str) -> None:
        if device_id not in self.open_device_ids:
            self.open_device_ids.append(device_id)
        self._draw_tabs()

    def _draw_tabs(self) -> None:
        if not hasattr(self, "tab_bar"):
            return
        self.tab_bar.delete("all")
        self.tab_hitboxes.clear()
        height = max(self.tab_bar.winfo_height(), 34)
        bottom = height - 1
        x = 0.0
        open_devices = [self._device_by_id(device_id) for device_id in self.open_device_ids]
        open_devices = [device for device in open_devices if device is not None]

        if not open_devices:
            self.tab_bar.grid_remove()
            return

        self.tab_bar.grid()
        self.open_device_ids = [device.id for device in open_devices]
        for device in open_devices:
            selected = self.selected_device is not None and device.id == self.selected_device.id
            width = min(max(160, 62 + len(device.name) * 7), 240)
            self.tab_bar.create_rectangle(
                x, 0, x + width, bottom,
                fill=COLORS["surface_elevated"] if selected else COLORS["surface"], outline="",
            )
            if selected:
                self.tab_bar.create_line(x + 12, bottom, x + width - 12, bottom,
                                         fill=COLORS["accent"], width=2)
            self.tab_bar.create_text(
                x + 12,
                height / 2,
                text=self._tab_label(device),
                fill=COLORS["text"] if selected else COLORS["muted"],
                anchor="w",
                font=("Segoe UI", 9, "bold"),
            )
            self.tab_hitboxes.append((x, x + width, device.id))
            x += width + 6

    def _tab_label(self, device: Device) -> str:
        channels = "CH1" if device.channel_1_enabled else ""
        if device.channel_2_enabled:
            channels = f"{channels}/CH2" if channels else "CH2"
        return f"{device.name}  {channels or '-'}"

    def _select_tab_at(self, event: tk.Event[tk.Canvas]) -> None:
        for start_x, end_x, device_id in self.tab_hitboxes:
            if start_x <= event.x <= end_x:
                self._select_device_by_id(device_id)
                break
