"""An engine-owned E1 input monitor; pin reads never run on the Tk thread."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

from apps.native_receiver.api_client import EngineClient
from apps.native_receiver.constants import COLORS, FONTS
from apps.native_receiver.dialogs import position_dialog
from quantum_light_engine.devices.trigger import TriggerConfig


def input_readout(state: dict[str, Any]) -> tuple[str, str]:
    if state.get("state") == "watching" and type(state.get("level")) is int:
        if state["level"] in (0, 1):
            return ("HIGH · 1", COLORS["ch1"]) if state["level"] else (
                "LOW  · 0", COLORS["ch2"])
    return "—", COLORS["text_secondary"]


class GpioInputDialog(tk.Toplevel):
    def __init__(self, app: Any, device: Any) -> None:
        super().__init__(app)
        self.title(f"GPIO (E1) — {device.name}")
        self.transient(app)
        self.resizable(False, False)
        self.client = EngineClient(app._api_base())
        self.path = f"/api/v1/devices/{device.id}/trigger"
        self.pin = tk.StringVar(value="DIO0_P")
        self.interval = tk.StringVar(value="10")
        self.detail = tk.StringVar(value="Reading status…")
        self.wiring = tk.StringVar()
        self.results: queue.Queue[dict[str, Any]] = queue.Queue()
        self.closed = threading.Event()
        self.pending = False
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=device.name, style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Label(body, text="E1 input").grid(row=1, column=0, sticky="w")
        self.pin_box = ttk.Combobox(body, textvariable=self.pin, state="readonly", width=12,
            values=tuple(f"DIO{i}_{side}" for i in range(8) for side in ("P", "N")))
        self.pin_box.grid(row=1, column=1, sticky="w", padx=8)
        self.pin_box.bind("<<ComboboxSelected>>", lambda _: self._wiring())
        ttk.Label(body, text="Poll interval (ms)").grid(row=2, column=0, sticky="w", pady=8)
        ttk.Entry(body, textvariable=self.interval, width=14).grid(
            row=2, column=1, sticky="w", padx=8)
        self.level = tk.Label(body, text="—", bg=COLORS["bg"], fg=COLORS["text_secondary"],
                              font=("Consolas", 30, "bold"), width=12, padx=20, pady=12)
        self.level.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 12))
        ttk.Label(body, textvariable=self.wiring, wraplength=460).grid(
            row=4, column=0, columnspan=3, sticky="w")
        self.start_button = ttk.Button(body, text="Read input", command=self._start)
        self.start_button.grid(row=5, column=0, sticky="ew", pady=12)
        self.stop_button = ttk.Button(body, text="Stop reading",
            command=lambda: self._request("PUT", {"enabled": False}))
        self.stop_button.grid(row=5, column=1, sticky="ew", padx=8, pady=12)
        ttk.Button(body, text="Close", command=self._close).grid(row=5, column=2, sticky="ew")
        ttk.Label(body, textvariable=self.detail, wraplength=460, justify="left",
                  font=FONTS["mono"], width=50).grid(row=6, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="Slow level monitoring. Short pulses may be missed.\n"
                  "The readings are not aligned to ADC samples or temporal modes.\n"
                  "Reading continues when closed; Stop reading or Disconnect ends it.",
                  wraplength=460, style="Muted.TLabel").grid(
                      row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Destroy>", lambda event: self.closed.set() if event.widget is self else None)
        self._wiring()
        position_dialog(self, app)
        self._request("GET")
        self.timer = self.after(100, self._tick)

    def _wiring(self) -> None:
        pin = self.pin.get()
        number = 3 + 2 * int(pin[3]) + int(pin.endswith("N"))
        self.wiring.set(f"{pin}: E1 pin {number}. Ground: E1 pin 25 or 26.\n"
                        "Use 0 to 3.3 V logic. A 5 V signal needs level conversion.")

    def _start(self) -> None:
        try:
            config = TriggerConfig(enabled=True, pin=self.pin.get(), edge="either",
                                   poll_interval_ms=float(self.interval.get()))
        except ValueError:
            self.detail.set("Enter a polling interval from 1 to 1000 ms.")
            return
        self._request("PUT", config.model_dump(mode="json", by_alias=True))

    def _request(self, method: str, body: dict[str, Any] | None = None) -> None:
        if self.closed.is_set():
            return
        if self.pending:
            if method == "PUT":
                self._queued = (method, body)
            return
        self.pending = True
        if method == "PUT":
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")

        def work() -> None:
            try:
                self.results.put(self.client.request(method, self.path, body, timeout_seconds=12))
            except Exception as exc:
                self.results.put({"state": "error", "lastError": str(exc)})

        threading.Thread(target=work, daemon=True).start()

    def _tick(self) -> None:
        if self.closed.is_set():
            return
        try:
            state = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.pending = False
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="normal" if state.get("state") == "watching"
                                       else "disabled")
            text, color = input_readout(state)
            self.level.configure(text=text, fg=color)
            config = state.get("config", {})
            pin = config.get("pin", self.pin.get())
            interval = state.get("lastPollIntervalMs")
            timing = f" · actual interval {interval:6.1f} ms" if interval is not None else ""
            self.detail.set(state.get("lastError") or (
                f"{pin} · {state.get('state', 'unknown')}{timing}\n"
                f"Observed transitions: {state.get('triggerCount', 0):8d} · "
                f"Last read: {state.get('lastReadAtUtc') or '—'}"))
        queued = self.__dict__.pop("_queued", None)
        self._request(*queued) if queued else self._request("GET")
        self.timer = self.after(250, self._tick)

    def _close(self) -> None:
        self.closed.set()
        self.after_cancel(self.timer)
        self.destroy()
