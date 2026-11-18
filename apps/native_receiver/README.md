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
**Mode analysis** tabs. Mode analysis shows the latest channel or nullifier readings, the actual
samples and duration per mode, reference calibration, and a live graph. Drag the divider between
the two plots to adjust their relative sizes.

**Setup…** opens calculation, temporal-width, variance-window and collection controls without covering
the plots with permanent toolbars. **Full settings…** separates acquisition, temporal-mode parameters,
templates/formulas and live preview into tabs; long forms scroll within their page. **Display
controls…** contains waveform zoom, offsets, cursors and the display trigger. These display controls
are independent of mode collection timing. **Diagnostics** shows transport counters and voltage
ranges. The Engine, Red Pitayas and Signal menus retain the device and advanced actions.

Set **CH2 delay (samples, 0-100)** in **Setup…** or **Full settings… → Acquisition**, then
apply the settings. The default remains **2 samples**. A value of 0 disables the offset;
positive values pair CH1 sample `n` with CH2 sample `n + delay`. The value is saved with the
analysis configuration and sent to the board's C processor for native analysis.

Edit **Mode function (Python expression)** in the same controls. The field starts with the
current analytic formula; for example, the lab preset is
`t * cos(f*t) * exp(-0.5 * (2*pi*f*t)**2)`. Here `t` is centered time in seconds,
`T` is the current bin width in seconds, and `f` is the mode bandwidth in Hz from
**Full settings… → Temporal mode**. Use `**` for powers. Functions include `exp`, `sin`,
`cos`, `tan`, `tanh`, `sqrt`, `log`, `log10`, `abs`, `where`, `minimum`, `maximum` and `clip`;
`np.exp(...)` and `np.pi` also work. For a Gaussian pulse, try `exp(-0.5*(t/(T/5))**2)`.
Blank restores the selected analytic shape. Expressions must produce finite, nonzero weights;
imports, assignments and arbitrary Python code are rejected. Clear any explicit JSON template
before using a custom expression.

The expression is saved with the analysis settings and used for both channels. The selected
analytic shape retains its sampling convention (lab fine-grid interpolation or ADC sub-sample
averaging); weights are normalized automatically. Fractional-width bins receive separate sampled
weights for each length. Applying a changed function clears shot-noise calibration, so record a
new reference. Native C processing receives precomputed weights and requires a worker rebuilt
from the updated sources; existing installed executables are not replaced automatically.

Enter **T width (µs)** to set the temporal bin. **Samples / mode** is a read-only calculation
from that width and the selected ADC rate; the preview also shows the actual sampled duration.
Changing the sampling rate keeps T width and updates the count. Applying settings rejects bins
outside the configured minimum–4096 sample range. Legacy fixed-count settings are shown as their
equivalent width at the selected rate. Time-based bins use consecutive boundaries on the requested
clock, alternating the lower and upper sample counts when needed. The UI reports their average
width and individual sampled durations. The count pattern survives ordinary packet splits.

For the v0.94 server stream, select **Use 62.5 MS/s server stream**. This uses
engine-side E1-gated analysis; see the [live results and limitations](../../docs/server-stream-v094-2026-11-02.md).
For the ARM comparison with a 308 ns mode, enter **0.308** and choose **Use 125 MS/s buffered captures** in
Setup. This reconnects the selected board at decimation 1 and processes independent
625,024-sample captures on its C processor. Only a 1,024-sample raw waveform preview
and compact calculated results reach the PC. The UI labels the gaps between captures;
its waveform history does not join separate captures. At this rate a requested 308 ns
width gives alternating 38/39 samples (304/312 ns), averaging 308 ns. Disconnect restores the previous
FPGA image; reconnect resumes the selected buffered mode. To return to raw continuous
streaming, disconnect, select a suitable sidebar decimation and apply its settings.

The optimized buffered reader removes the extra capture pause and reuses completed
board results for display. The validated 625,024-sample configuration reached about
7.3 new frames/s on the test board. It preserves full capture processing and checks
the optimized voltage conversion against the SDK. See the
[capture performance measurements](../../docs/capture-performance-2026-10-29.md).

When the engine has the native Red Pitaya backend enabled, the same controls send commands to
the board's C processor. Its continuous ADC relay preserves the raw waveform stream; the receiver
displays derived results without recalculating them in Python. Periodic and continuous collection
modes are supported on the board. The chart status reports analysis overruns and FPGA loss.
See the [native processor guide](../../native/redpitaya_nullifiers/README.md) for installation
and engine settings. Restart the engine after changing these settings.

The waveform and analysis area are displayed together. Reference controls remain above the analysis
chart; the larger readings show the latest finite plotted estimates, with units matching the selected
quantity. Reference calibration and calculations are unchanged by the layout.

**Stop streaming**, beside Connect, stops acquisition on the selected Red Pitaya
and keeps the last waveform and analysis visible. The acquisition status changes
to **Stopped**, and **Start streaming** restarts measurements. Buffered captures
keep the completed reference and output settings across Stop/Start. An active reference
recording is cancelled when stopped. Disconnect remains available separately.

**Record** collects a shot-noise reference for **10 seconds**, then finishes automatically.
The progress bar on the right shows the time remaining and changes to **Reference ready**
on success. The full settings editor shares the same countdown. **Finish** stops early;
**Clear**, changing calculation settings, or disconnecting cancels the countdown. An
unsuccessful reference reports an error instead of marking the calibration complete.
In tagged E1 buffered captures, reference recording uses both E1 HIGH and LOW samples;
normal measurements admit LOW and exclude HIGH after the reference finishes. The input must
be the shot-noise reference throughout recording.

New receiver configurations default to the supplied **lab postprocessing method**: 308 ns,
6e6 frequency parameter, Gaussian-linear-cosine weighting, lab X/P signs, per-segment vacuum
calibration and rolling variances averaged in dB across completed windows. The minimum is configurable
and defaults to 4 samples/mode; this permits testing, not guaranteed measurement accuracy.
The **5 ms locking / 5 ms collecting** cycle includes configurable 0.8006 ms start and 1000-sample
end settling guards. Continuous, periodic and sampled-edge-triggered collection modes are available.
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
migrates in memory to the lab preset; custom formulas are retained.

Optional Python signal scripts are disabled by default. Set `QL_NATIVE_RECEIVER_SIGNAL_SCRIPT=1` before
launch to enable the editor. Scripts execute in the receiver process against live NumPy arrays. The
restricted built-in set is a convenience guard, not a secure sandbox: run only code you trust.

The waveform display trigger detects the selected rising or falling edge in raw samples before
downsampling. **Noise guard** defaults to 0.01 V: a rising edge must start below `level - guard`
and reach `level + guard`; falling edges reverse those bounds. The displayed time remains the
crossing of the selected level. Lower the guard for small signals, or raise it if noise triggers
the display. A missing qualified edge holds the last capture. Sample-loss boundaries and stream
changes reset raw history so a discontinuity cannot create a false edge. The selected edge pair
is plotted at its actual sample positions; zoom in when a wide view cannot resolve fast cycles.
After a history reset, a valid edge can trigger immediately: unavailable portions of the selected
time window stay blank. Noise-guard arming and confirmation use adjacent available samples even
when those samples lie outside the allowed edge positions within the display window.

## Code organization

`native_receiver.py` owns application initialization and shutdown; `entrypoint.py` owns command-line
startup. Import utilities and models from their owning modules:

- `api_client.py` provides the UI-independent `EngineClient` for requests, health checks, device
  decoding, and sample-stream URLs.
- `engine_controller.py` handles engine launch and status; `device_controller.py` handles device
  actions and selection; `acquisition_controls.py` handles acquisition settings and outputs.
- `stream_controller.py` receives and processes frames; `signal_view.py` owns waveform selection,
  zoom, pause, and trigger controls; `waveform_renderer.py` draws waveforms.
- `layout.py` builds the menu, sidebar, and workspace. `waveform_ui.py` builds waveform controls,
  `spectrum_ui.py` builds and draws spectra, and `notifications.py` owns loading and error feedback.
- `theme.py`, `constants.py`, `protocol.py`, and `helpers.py` provide styles, presentation values,
  sample history and decoding, and shared utilities.

Related behavior stays together without a fixed file-size limit. Acquisition isolation, bounded queues,
loss tracking, and the engine's external interfaces remain part of the acquisition service.

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

## Output signals

Use **Outputs…**, **Signal > Red Pitaya output signals…**, or **Red Pitayas > Output signals…** for
the selected connected board. The editor controls OUT1 and OUT2 together with separate waveforms,
frequencies, peak amplitudes, offsets and phases. It supports normalized custom sample files and
the lab X/P cancellation presets. Closing the editor leaves outputs running; Stop both outputs
stops that board's pair. See [the output signal guide](../../docs/output-signals.md).

