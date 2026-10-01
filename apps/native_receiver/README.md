# Native Receiver

The native receiver is the first-class desktop client in `apps/native_receiver`. It connects to the
[Quantum Light Engine](../../src/quantum_light_engine/README.md); it does not acquire from Red Pitaya
hardware directly. The application renders live waveforms, spectra and nullifiers while preserving
bounded history,
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
pacing, FFT spectra and acquisition controls. The analysis area has **Spectrum analyzer** and
**Nullifiers** tabs. The Nullifiers tab shows a configurable live 1D graph, collection controls and
shot-noise calibration, with advanced temporal-template and formula settings.

New receiver configurations default to **5 ms locking / 5 ms collecting**, with variance at each
temporal index across repeated, aligned collection windows. Rolling variance across neighboring modes
is also available. Continuous, periodic and sampled-edge-triggered collection modes are available.
Set the measured X/P basis to match both
homodyne detectors and calibrate against shot noise before interpreting dB. The paper preset follows
Larsen chapter 3's 1D signs and Gaussian-linear mode. Only complete modes in collection windows are
used; nullifiers and variance windows do not cross locking intervals or reported data loss. Processing
errors are reported without stopping the sample stream.

See the [nullifier guide](../../docs/nullifiers.md) for equations, calibration, timing and trigger
limitations, sample-rate requirements, and the recommended FPGA/Red Pitaya processing architecture.
The current analog trigger is independent of the waveform display trigger; external TTL acquisition
requires board/backend support. Stored custom configurations are retained, so review their formulas,
mode shape and timing when upgrading from the earlier monitor. The exact previous built-in preset
migrates in memory to the corrected paper preset; custom formulas are retained.

Optional Python signal scripts are disabled by default. Set `QL_NATIVE_RECEIVER_SIGNAL_SCRIPT=1` before
launch to enable the editor. Scripts execute in the receiver process against live NumPy arrays. The
restricted built-in set is a convenience guard, not a secure sandbox: run only code you trust.

## Code organization

`native_receiver.py` is the application shell and compatibility import surface. Its `ReceiverApp`
assembles small, responsibility-specific mixins rather than owning the full implementation:

- `*_ui.py`, `layout.py`, and `theme.py` construct and arrange reusable Tk views.
- `engine_controller.py`, `device_controller.py`, and `stream_controller.py` own lifecycle actions.
- `frame_processor.py`, `signal_view.py`, and the renderer modules transform and display samples.
- `api_client.py`, `device_repository.py`, and `helpers.py` isolate external I/O and pure utilities.
- `state.py`, `constants.py`, and `dialogs.py` keep initialization, presentation data, and modals
  independent; `entrypoint.py` owns command-line startup.

Receiver source files are kept below 400 physical lines; a unit test protects this boundary.

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
