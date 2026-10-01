from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from urllib.request import Request

from apps.native_receiver.constants import (
    COLORS,
    DISCONNECTED_STATES,
    ENGINE_HEALTH_TIMEOUT_SECONDS,
)
from apps.native_receiver.protocol import Device


class DeviceRepositoryMixin:
    def _engine_is_running_for_base(self, api_base: str) -> bool:
        from apps.native_receiver import native_receiver as receiver_api

        try:
            request = Request(f"{api_base.rstrip('/')}/health/live", method="GET")
            with receiver_api.urlopen(request, timeout=ENGINE_HEALTH_TIMEOUT_SECONDS) as response:
                running: bool = response.status == 200
                response.read()
                return running
        except Exception:
            return False

    def _wait_engine_ready(self, api_base: str, *, attempts: int, delay_seconds: float) -> bool:
        for _attempt in range(attempts):
            if self._engine_is_running_for_base(api_base):
                return True
            time.sleep(delay_seconds)
        return False

    def _load_devices(self, api_base: str) -> list[Device]:
        payload = self._api_for_base(api_base, "GET", "/api/v1/devices")
        return [
            Device(
                id=item["id"],
                name=item["name"],
                host=item.get("host") or "",
                state=(item.get("runtime") or {}).get("state", "-"),
                channel_1_enabled=bool(item.get("channel1Enabled")),
                channel_2_enabled=bool(item.get("channel2Enabled")),
                decimation=int(item.get("decimation") or 256),
                block_size_bytes=int(item.get("blockSizeBytes") or 131_072),
            )
            for item in payload
            if item.get("source") == "redpitaya"
        ]

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
