# Native Receiver

The native receiver is the first-class desktop client in `apps/native_receiver`. It connects to the
[Quantum Light Engine](../../src/quantum_light_engine/README.md); it does not acquire from Red Pitaya
hardware directly. The application renders live waveforms and spectra while preserving bounded history,
health polling, loss counters, connection responsiveness, and display pacing.

## Start

Install the repository dependencies, then launch from the repository root:

```powershell
uv sync
uv run quantum-light-receiver
```

Validate imports and persisted configuration without opening a window:

```powershell
uv run quantum-light-receiver --check
```

Python's Tkinter must be available (it is included with standard Windows Python). NumPy and WebSockets
are required and installed by the project. A running engine is optional at startup.

## Connection and automatic engine launch

The default engine URL is `http://127.0.0.1:8080`. The receiver uses the engine's versioned device REST
API and connects to `ws://127.0.0.1:8080/ws/v1/devices/{deviceId}/samples` for binary frames. Change the
base URL in Engine Settings when the service runs elsewhere.

`Launch engine on app start` is enabled by default. The receiver first checks engine health and starts
`quantum-light-engine run --host 127.0.0.1 --port 8080` only when needed. A process it launched is
stopped when the receiver closes; an already-running external engine is left alone.

## Settings and processing

Receiver settings persist in `data/native_receiver_settings.json`; cluster configuration persists in
`data/cluster_processing.json`. Device profiles remain engine-owned under the engine data directory.

The receiver supports selectable history windows, channel visibility and offsets, adaptive render
pacing, FFT spectra, acquisition controls, and a live 1D cluster-nullifier dialog. Cluster processing
uses both channels, configurable temporal modes and delays, rolling variance, and explicit shot-noise
calibration. Processing errors are reported without stopping the sample stream.

Optional Python signal scripts are disabled by default. Set `QL_NATIVE_RECEIVER_SIGNAL_SCRIPT=1` before
launch to enable the editor. Scripts execute in the receiver process against live NumPy arrays. The
restricted built-in set is a convenience guard, not a secure sandbox: run only code you trust.

## Troubleshooting and validation

- Run `uv run quantum-light-receiver --check` to verify dependencies and settings parsing.
- Run `uv run quantum-light-engine validate-config` to validate stored engine profiles.
- Confirm `GET http://127.0.0.1:8080/health/ready` succeeds and that the selected profile is connected.
- For Red Pitaya failures, check the host, vendor module path, board streaming application, and engine
  log at `logs/engine.log`.
- For cluster-nullifier output, enable both channels and choose a sample rate adequate for the temporal
  mode.

Automated receiver tests cover connection flow, health reporting, history, pacing, rendering, frame
decoding, and post-processing:

```powershell
uv run pytest -q tests/unit/test_native_receiver_*.py tests/e2e
```
