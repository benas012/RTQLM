"""Per-board editor for both Red Pitaya DAC outputs."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from pydantic import ValidationError

from redpitaya_acquisition.redpitaya.output_signals import (
    OutputSignalsConfig,
    fit_low_frequency_output,
)


class OutputSignalDialog(tk.Toplevel):
    def __init__(self, app: Any, device: Any) -> None:
        super().__init__(app)
        self.app = app
        self.device_id = device.id
        self.api_base = app._api_base()
        self.title(f"Output signals — {device.name}")
        self.transient(app)
        self.resizable(False, False)
        self.variables: dict[str, tk.Variable] = {}
        self.custom_samples: dict[int, list[float]] = {1: [], 2: []}
        self.status = tk.StringVar(value="Reading engine output settings…")
        self.pending = False
        self.dac_rate = tk.StringVar(value="15625000")
        self.buffer_size = tk.StringVar(value="65536")
        self.fit_slow_rate = tk.BooleanVar(value=True)
        self._build()
        self._load(OutputSignalsConfig())
        self._request("GET", "", None, self._loaded)

    def _build(self) -> None:
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Settings apply to both outputs of this Red Pitaya.").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
        )
        for number in (1, 2):
            frame = ttk.LabelFrame(body, text=f"OUT{number}", padding=10)
            frame.grid(row=1, column=number - 1, sticky="nsew", padx=(0, 10))
            enabled = tk.BooleanVar(value=False)
            self.variables[f"{number}.enabled"] = enabled
            ttk.Checkbutton(frame, text="Enable output", variable=enabled).grid(
                row=0, column=0, columnspan=2, sticky="w"
            )
            fields = (
                ("waveform", "Waveform", "sine"),
                ("frequencyHz", "Frequency (Hz)", "1000"),
                ("amplitudeVolts", "Peak amplitude (V)", "0.1"),
                ("offsetVolts", "DC offset (V)", "0"),
                ("phaseDegrees", "Phase (degrees)", "0"),
                ("dutyCycle", "Square duty (0-1)", "0.5"),
                ("noiseSeed", "Noise seed", str(number)),
            )
            for row, (key, label, default) in enumerate(fields, start=1):
                variable = tk.StringVar(value=default)
                self.variables[f"{number}.{key}"] = variable
                ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
                if key == "waveform":
                    control = ttk.Combobox(
                        frame,
                        textvariable=variable,
                        state="readonly",
                        values=(
                            "sine",
                            "square",
                            "triangle",
                            "sawtooth",
                            "dc",
                            "noise",
                            "arbitrary",
                        ),
                        width=16,
                    )
                else:
                    control = ttk.Entry(frame, textvariable=variable, width=18)
                control.grid(row=row, column=1, sticky="ew", padx=(8, 0))
            ttk.Button(
                frame,
                text="Load custom samples…",
                command=lambda channel=number: self._import_samples(channel),
            ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        rate = ttk.Frame(body)
        rate.grid(row=2, column=0, columnspan=2, sticky="ew", pady=12)
        ttk.Label(rate, text="DAC rate (samples/s)").pack(side="left")
        ttk.Combobox(
            rate,
            textvariable=self.dac_rate,
            width=12,
            values=("976562", "1953125", "3906250", "7812500", "15625000", "31250000"),
        ).pack(side="left", padx=8)
        ttk.Label(rate, text="Buffer samples").pack(side="left")
        ttk.Entry(rate, textvariable=self.buffer_size, width=10).pack(side="left", padx=8)
        ttk.Checkbutton(
            rate, text="Fit rate for slow signals", variable=self.fit_slow_rate
        ).pack(side="left")
        ttk.Label(
            body,
            text=(
                "Amplitude is peak: 0.1 V means 0.2 V peak-to-peak for a sine.\n"
                "DC uses offset. Custom samples: one normalized period in [-1, 1].\n"
                "Noise repeats the buffer. Outputs share a start on this board;\n"
                "Different boards need hardware synchronization.\n"
                "Higher DAC rates need a throughput test."
            ),
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        presets = ttk.Frame(body)
        presets.grid(row=4, column=0, columnspan=2, sticky="w", pady=12)
        for label, phase in (("Lab X cancellation preset", -90), ("Lab P cancellation preset", 90)):
            ttk.Button(presets, text=label, command=lambda p=phase: self._preset(p)).pack(
                side="left", padx=(0, 8)
            )
        actions = ttk.Frame(body)
        actions.grid(row=5, column=0, columnspan=2, sticky="w")
        self.apply_button = ttk.Button(
            actions, text="Apply & start selected outputs", command=self._apply
        )
        self.apply_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(actions, text="Stop both outputs", command=self._stop)
        self.stop_button.pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Refresh", command=self._refresh).pack(side="left")
        ttk.Label(body, textvariable=self.status, wraplength=590, justify="left").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )
        ttk.Label(
            body, text="Closing this editor leaves outputs running; use Stop to stop them."
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _load(self, config: OutputSignalsConfig) -> None:
        payload = config.model_dump(mode="json", by_alias=True)
        self.dac_rate.set(str(config.sample_rate_hz))
        self.buffer_size.set(str(config.buffer_samples))
        for number in (1, 2):
            channel = payload[f"output{number}"]
            for key, value in channel.items():
                variable = self.variables.get(f"{number}.{key}")
                if variable is not None:
                    variable.set(value)
            self.custom_samples[number] = channel["samples"]

    def _config(self) -> OutputSignalsConfig:
        payload: dict[str, Any] = {
            "sampleRateHz": int(self.dac_rate.get()),
            "bufferSamples": int(self.buffer_size.get()),
        }
        for number in (1, 2):
            channel: dict[str, Any] = {"samples": self.custom_samples[number]}
            for name, variable in self.variables.items():
                prefix, key = name.split(".")
                if int(prefix) == number:
                    channel[key] = variable.get()
            payload[f"output{number}"] = channel
        config = (fit_low_frequency_output(payload) if self.fit_slow_rate.get()
                  else OutputSignalsConfig.model_validate(payload))
        self.dac_rate.set(str(config.sample_rate_hz))
        return config

    def _import_samples(self, number: int) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="One-period normalized samples",
            filetypes=(("Samples", "*.json *.csv *.txt"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            source = Path(path)
            if source.stat().st_size > 2_000_000:
                raise ValueError("sample file is too large; maximum 16,384 samples")
            text = source.read_text(encoding="utf-8-sig")
            values = (
                json.loads(text)
                if source.suffix.lower() == ".json"
                else [float(value) for value in text.replace(",", " ").split()]
            )
            if not isinstance(values, list):
                raise ValueError("JSON must contain an array of normalized samples")
            from redpitaya_acquisition.redpitaya.output_signals import OutputChannelConfig

            checked = OutputChannelConfig(waveform="arbitrary", samples=values)
            self.custom_samples[number] = checked.samples
            self.variables[f"{number}.waveform"].set("arbitrary")
            self.status.set(f"OUT{number}: loaded {len(checked.samples)} samples. Apply to send.")
        except (ValueError, OSError) as exc:
            messagebox.showerror("Invalid samples", str(exc), parent=self)

    def _preset(self, phase: int) -> None:
        self._load(
            OutputSignalsConfig.model_validate(
                {
                    "output1": {"enabled": True, "frequencyHz": 976562.5, "amplitudeVolts": 0.1},
                    "output2": {
                        "enabled": True,
                        "frequencyHz": 976562.5,
                        "amplitudeVolts": 0.1,
                        "phaseDegrees": phase,
                    },
                }
            )
        )
        self.status.set("Preset ready. Receiver: 15.625 MS/s, lab template, B delay 0, continuous.")

    def _apply(self) -> None:
        try:
            config = self._config()
        except ValidationError as exc:
            message = "\n".join(error["msg"].removeprefix("Value error, ")
                                for error in exc.errors(include_url=False, include_input=False))
            messagebox.showerror("Invalid output settings", message, parent=self)
            return
        except ValueError as exc:
            messagebox.showerror("Invalid output settings", str(exc), parent=self)
            return
        self._request("PUT", "", config.model_dump(mode="json", by_alias=True), self._loaded)

    def _stop(self) -> None:
        self._request("POST", "/stop", None, self._loaded)

    def _refresh(self) -> None:
        self._request("GET", "", None, self._loaded)

    def _loaded(self, result: dict[str, Any]) -> None:
        self._load(OutputSignalsConfig.model_validate(result["config"]))
        details = []
        for channel in result.get("channels", []):
            if channel["enabled"]:
                frequency = channel.get("actualFrequencyHz")
                details.append(
                    f"OUT{channel['channel']} {channel['waveform']}"
                    + (f" {frequency:g} Hz" if frequency is not None else "")
                )
        rate = result.get("actualSampleRateHz")
        suffix = f" DAC rate {rate:g} samples/s." if rate is not None else ""
        self.status.set(f"Engine state: {result['state']}. " + "; ".join(details) + suffix)
        if self.app.test_output_device_id == self.device_id and result["state"] != "legacy-test":
            self.app._set_test_output_ui(False)

    def _request(self, method: str, suffix: str, body: Any, callback: Any) -> None:
        if self.pending:
            return
        self.pending = True
        self.apply_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.status.set("Contacting engine…")

        def work() -> dict[str, Any]:
            try:
                return {
                    "result": self.app._api_for_base(
                        self.api_base,
                        method,
                        f"/api/v1/devices/{self.device_id}/outputs{suffix}",
                        body,
                        timeout_seconds=30,
                    )
                }
            except Exception as exc:
                return {"error": str(exc)}

        def apply(response: dict[str, Any]) -> None:
            if not self.winfo_exists():
                return
            self.pending = False
            self.apply_button.configure(state="normal")
            self.stop_button.configure(state="normal")
            if "error" in response:
                self.status.set(
                    f"Output request failed: {response['error']}. Refresh to check state."
                )
            else:
                callback(response["result"])

        self.app._run_busy("Updating output signals", work, apply)
