"""Control the board's C processor; calculations stay on the Red Pitaya."""

from __future__ import annotations

import json
import math
import shlex
import time
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

import paramiko

from quantum_light_engine.post_processing import (
    ClusterProcessingConfig,
    config_from_dict,
    config_to_dict,
    expression_mode_templates,
    lab_processing_config,
    match_builtin_basis,
    processing_nullifiers,
    single_mode_processing_config,
)

from .native_install import ensure_native_worker
from .nullifiers import DeviceNullifierProcessor, NullifierCommand, _validate_config_keys


class BoardNullifierProcessor(DeviceNullifierProcessor):
    backend = "redpitaya-arm-c"

    def __init__(self, host: str, executable: str, decimation: int, channel_mask: int, port: int):
        super().__init__()
        self.host = urlsplit(host if "://" in host else "http://" + host).hostname
        self.executable, self.decimation, self.channel_mask, self.port = (
            executable,
            decimation,
            channel_mask,
            port,
        )
        self.config = (
            lab_processing_config()
            if channel_mask == 3
            else replace(
                single_mode_processing_config(),
                single_channel="a" if channel_mask == 1 else "b",
            )
        )
        self._client: Any = None
        self._stdin: Any = None
        self._stdout: Any = None
        self._cached: dict[str, Any] = {}
        self._analysis_error: str | None = None
        self._remote_process: tuple[int, int] | None = None
        self._stale_process: tuple[int, int] | None = None
        self.buffered = False
        self.capture_samples = 625_024
        self.variance_only = False
        self.raw_readout = "ram_copy"
        self.measurement_window_ms = 5.0
        self._previous_fpga: str | None = None
        self._capture_result: tuple[dict[str, Any], ClusterProcessingConfig] | None = None
        self._capture_metrics: dict[str, Any] = {}
        self.auto_install = True
        self._installation: str | None = None

    def _ensure_installed(self, client: Any, target: str | None = None) -> None:
        self._installation = ensure_native_worker(
            client, self.executable,
            target or ("rp-buffered-board" if self.buffered else "rp-nullifierd"),
            auto_install=self.auto_install,
        )

    def _call(self, command: dict[str, object]) -> dict[str, Any]:
        if self._stdin is None or self._stdout is None:
            raise RuntimeError("board C command session is not connected")
        try:
            self._stdin.write(json.dumps(command) + "\n")
            self._stdin.flush()
            line = self._stdout.readline(64 * 1024 * 1024 + 1)
            if not line or len(line) > 64 * 1024 * 1024:
                raise RuntimeError("board C command session closed or returned an oversized reply")
            reply: dict[str, Any] = json.loads(line)
            if not isinstance(reply, dict):
                raise ValueError("board returned a non-object reply")
        except Exception:
            if self._remote_process is not None:
                self._stale_process = self._remote_process
                self._remote_process = None
            if self._client is not None:
                self._client.close()
            self._client = self._stdin = self._stdout = None
            self._cached = {}
            self._capture_result = None
            self._analysis_error = "board command connection lost; a new session needs calibration"
            raise
        if not reply.get("ok"):
            raise ValueError(str(reply.get("error", "board C command rejected")))
        self._cached = reply
        return reply

    def _release_stale_process(self, client: Any) -> None:
        """Release only our previous command process, including PID-reuse checks."""
        if self._stale_process is None:
            return
        pid, started = self._stale_process
        executable = shlex.quote(self.executable)
        command = f"""
expected=$(readlink -f -- {executable}) || exit 1
same_process() {{
    test "$(awk '{{print $22}}' /proc/{pid}/stat 2>/dev/null)" = "{started}" &&
    test "$(readlink -f /proc/{pid}/exe 2>/dev/null)" = "$expected"
}}
if same_process; then
    kill -TERM {pid} || exit 1
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        same_process || exit 0
        sleep 0.2
    done
    if same_process; then kill -KILL {pid} || exit 1; fi
fi
"""
        _, stdout, stderr = client.exec_command(command, timeout=8)
        stdout.read()
        stderr.read()
        if stdout.channel.recv_exit_status() != 0:
            raise RuntimeError("could not release the previous board command process")
        self._stale_process = None

    def _settings(self, config: ClusterProcessingConfig) -> dict[str, object]:
        if config.window_mode == "triggered":
            raise ValueError("board streaming supports periodic or continuous collection windows")
        keys = {
            "modeWidthSeconds",
            "measurementFrequencyHz",
            "modeShape",
            "modeSamples",
            "varianceWindowModes",
            "varianceWindowSeconds",
            "varianceEstimator",
            "minSamplesPerMode",
            "modeOversample",
            "channelBDelaySamples",
            "gateAbsoluteThreshold",
            "explicitModeTemplate",
            "historyModes",
            "windowMode",
            "lockSeconds",
            "collectSeconds",
            "collectionGuardSeconds",
            "collectionEndGuardSamples",
            "measurementBasis",
        }
        result = {key: value for key, value in config_to_dict(config).items() if key in keys}
        # Evaluate formulas on the host. C receives only validated coefficients,
        # including separate weights for alternating fractional-width bins.
        if self.config.mode_expression:
            result["sampledModeTemplates"] = []
        if config.mode_expression:
            _, templates = expression_mode_templates(config, 125_000_000 / self.decimation)
            result["sampledModeTemplates"] = [templates[n].tolist() for n in sorted(templates)]
        result["decimation"] = self.decimation
        if self.buffered:
            result["captureSamples"] = self.capture_samples
            measurement = min(
                self.capture_samples, round(self.measurement_window_ms * 125000 / self.decimation)
            )
            result["measurementSamples"] = measurement
            result["rawReadout"] = self.raw_readout
            step = (len(config.explicit_mode_template) or config.mode_samples
                    or config.mode_width_seconds * 125000000 / self.decimation)
            # Keep every complete mode of the measurement; maxPoints only limits the display.
            history = max(config.history_modes, math.ceil(measurement / step) + 1)
            if history > 262144:
                raise ValueError("Buffered measurement exceeds the 262144 temporal-mode limit")
            result["historyModes"] = history
            result["collectSeconds"] = self.measurement_window_ms / 1000
        result["requireFullVarianceWindow"] = config.analysis_mode == "single"
        segment = config.calibration_estimator == "segment_mean" or (
            config.calibration_estimator == "auto"
            and config.analysis_mode == "cluster"
            and config.mode_shape == "lab_gaussian_linear_cosine"
        )
        result["calibrationEstimator"] = "segment_mean" if segment else "pooled"
        result["nullifiers"] = [
            {
                "name": row.name,
                "start": row.start,
                "terms": [
                    {"source": term.source, "offset": term.offset, "weight": term.weight}
                    for term in row.terms
                ],
            }
            for row in processing_nullifiers(config)
        ]
        return result

    def _connect(self) -> None:
        if self._client is not None:
            return
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        try:
            client.connect(self.host, username="root", timeout=5, auth_timeout=5, banner_timeout=5)
            transport = client.get_transport()
            if transport is not None:
                transport.set_keepalive(5)
            self._ensure_installed(client)
            self._release_stale_process(client)
            # exec preserves this PID/start time. Remembering both lets a reconnect
            # clean up its own orphan without touching another board process.
            launch = (
                "printf 'RTQLM_SESSION %s %s\\n' \"$$\" "
                "\"$(awk '{print $22}' /proc/$$/stat)\"; exec "
                + shlex.quote(self.executable)
            )
            stdin, stdout, _stderr = client.exec_command(launch, timeout=10)
            stdout.channel.settimeout(15)
            identity = stdout.readline(128).split()
            if (len(identity) != 3 or identity[0] != "RTQLM_SESSION"
                    or not identity[1].isdecimal() or not identity[2].isdecimal()
                    or int(identity[1]) <= 1 or int(identity[2]) <= 0):
                raise RuntimeError("board command process did not identify itself")
            self._remote_process = (int(identity[1]), int(identity[2]))
            self._client, self._stdin, self._stdout = client, stdin, stdout
            self._call({"command": "status"})
            try:
                self._call({"command": "configure", "config": self._settings(self.config)})
                self._analysis_error = None
            except ValueError as exc:
                self._analysis_error = str(exc)
        except Exception:
            if self._remote_process is not None:
                self._stale_process = self._remote_process
                self._remote_process = None
            client.close()
            self._client = self._stdin = self._stdout = None
            raise

    def prepare_stream(self) -> None:
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    self._call({"command": "start_stream", "port": self.port,
                                "channelMask": self.channel_mask})
                    return
                except Exception:
                    # A rejected command keeps its connection and must be shown
                    # to the user. Retry a lost transport once with a fresh process.
                    if attempt or self._client is not None:
                        raise

    def stop_stream(self) -> None:
        with self._lock:
            if self._client is not None:
                self._call({"command": "stop_stream"})

    @staticmethod
    def _remote_command(client: Any, command: str) -> str:
        _, stdout, stderr = client.exec_command(command, timeout=15)
        result = stdout.read().decode()
        error = stderr.read().decode()
        if stdout.channel.recv_exit_status():
            raise RuntimeError(error.strip() or "Board acquisition setup failed")
        return str(result).strip()

    def prepare_capture(self) -> None:
        with self._lock:
            self._connect()
            if self._previous_fpga is not None:
                return
            previous = self._remote_command(self._client, "cat /tmp/loaded_fpga.inf")
            if previous not in {"stream_app", "v0.94"}:
                raise RuntimeError(f"Buffered acquisition cannot replace FPGA image {previous!r}")
            self._remote_command(self._client,
                "test -z \"$(ss -Htn state established '( sport = :18900 or sport = :18902 )')\"")
            self._previous_fpga = previous
            if previous != "v0.94":
                self._remote_command(self._client, "/opt/redpitaya/sbin/overlay.sh v0.94")

    def capture_preview(self) -> dict[str, Any]:
        with self._lock:
            started = time.perf_counter()
            command: dict[str, object] = {"command": "capture", "maxPoints": 500}
            if self.variance_only:
                command["resultFields"] = "variances"
            else:
                command["previewPoints"] = 1024
            reply = self._call(command)
            self._capture_result = (reply, self.config)
            self._capture_metrics = dict(reply.get("captureTimingMs", {}))
            self._capture_metrics["commandTotal"] = (time.perf_counter() - started) * 1000
            return reply

    def set_output(self, command: dict[str, object]) -> None:
        with self._lock:
            self._call(command)

    def close(self) -> None:
        with self._lock:
            try:
                if self._client is not None:
                    client = self._client
                    try:
                        self._call({"command": "stop_stream"})
                        self._stdout.channel.shutdown_write()
                        if self._previous_fpga is not None:
                            self._stdout.read()  # SDK/DMA must finish before image restoration.
                    except Exception:
                        self._stale_process = self._remote_process or self._stale_process
                        raise
                    finally:
                        client.close()
                        self._client = self._stdin = self._stdout = None
                        self._remote_process = None
                        self._cached = {}
                        self._capture_result = None
            finally:
                if self._previous_fpga == "v0.94":
                    # Acquisition already used this image; reloading it would
                    # reset the user's generator state without restoring anything.
                    self._previous_fpga = None
                elif self._previous_fpga is not None:
                    previous = self._previous_fpga
                    restore = paramiko.SSHClient()
                    restore.load_system_host_keys()
                    try:
                        restore.connect(self.host, username="root", timeout=5)
                        self._release_stale_process(restore)
                        self._remote_command(restore,
                            "/opt/redpitaya/sbin/overlay.sh " + shlex.quote(previous))
                        self._previous_fpga = None
                    finally:
                        restore.close()

    def execute(self, command: NullifierCommand) -> dict[str, object]:
        with self._lock:
            self._capture_result = None
            self._connect()
            if command.command == "configure":
                update = command.config or {}
                current = config_to_dict(self.config)
                _validate_config_keys(update, set(current))
                config = config_from_dict({**current, **update})
                if "measurementBasis" in update and "nullifiers" not in update:
                    config = match_builtin_basis(config, self.config)
                self._call({"command": "configure", "config": self._settings(config)})
                self.config = config
                self._analysis_error = None
            else:
                body: dict[str, object] = {"command": command.command}
                if command.command == "finish_calibration":
                    body["minimumModes"] = command.minimum_modes
                self._call(body)
            self._capture_result = None
            return self.status()

    async def process_frame(self, frame: Any) -> None:
        # Raw frames are forwarded for display; the board already calculated them.
        return

    def reset_stream(self, *, clear_calibration: bool = False) -> None:
        with self._lock:
            if self._client is not None:
                self._call({"command": "clear_calibration" if clear_calibration else "reset"})
            self._capture_result = None

    def status(self) -> dict[str, object]:
        with self._lock:
            if self._client is not None:
                self._call({"command": "status"})
            return {
                **self._cached,
                "backend": self.backend,
                "processorInstallation": self._installation,
                "config": config_to_dict(self.config),
                "captureTimingMs": self._capture_metrics,
                "error": self._analysis_error
                or (self._cached.get("stream") or {}).get("error")
                or None,
            }

    def latest(self) -> dict[str, object] | None:
        if self.buffered:
            snapshot = self._capture_result
            if (self._client is None or snapshot is None
                    or not ({"variances", "quadratures"} & snapshot[0].keys())):
                return None
            return self._latest_payload(*snapshot)
        with self._lock:
            if self._client is None:
                return None
            try:
                reply = self._call({"command": "latest", "maxPoints": 500})
            except ValueError as exc:
                if "no processed capture" in str(exc):
                    return None
                raise
            return self._latest_payload(reply, self.config)

    def _latest_payload(self, reply: dict[str, Any],
                        config: ClusterProcessingConfig) -> dict[str, object]:
        period = float(reply["modePeriodSeconds"])
        points = config.variance_window_modes or max(
            int(config.variance_window_seconds / period),
            10 if config.variance_estimator == "lab_rolling" else 2,
        )
        tagged = self.buffered and reply.get("captureTimingMs", {}).get("taggedE1")
        tagged_status = (
            "Shot-noise calibration uses both E1 HIGH and LOW. "
            if tagged and reply.get("calibrating") else
            "E1 LOW admitted; HIGH intervals excluded from calculation. " if tagged else ""
        )
        return {
            **{key: value for key, value in reply.items() if key != "preview"},
            "modeIndexStart": 0, "measurementBasis": config.measurement_basis,
            "varianceEstimator": config.variance_estimator, "analysisMode": config.analysis_mode,
            "varianceWindowModes": points, "rejectedModes": int(reply.get("excludedModes", 0)),
            "status": tagged_status + (
                "Variances calculated in C on Red Pitaya; only variance results over TCP."
                if self.buffered and self.variance_only else
                "Buffered results calculated in C on Red Pitaya. Gaps between captures."
                if tagged else
                "Buffered capture calculated in C on Red Pitaya; gaps between captures; "
                "waveform shows the first 1024 samples." if self.buffered else (
                    "live nullifiers calculated in C on Red Pitaya; "
                    f"{reply['stream']['droppedPackets']} processing drops; "
                    f"{reply['stream']['lossPackets']} FPGA loss packets"
                    + ("; collection exceeds retained history"
                       if reply.get("prefixTruncated") else "")
                )
            ),
            "acquisitionState": "buffered" if self.buffered else config.window_mode,
            "collectionWindows": reply["captures"], "acceptedSamples": 0, "skippedSamples": 0,
            "varianceIndexStarts": reply.get("indexStarts", {}),
        }
