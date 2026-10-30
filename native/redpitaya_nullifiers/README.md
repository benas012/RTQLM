# Red Pitaya ARM nullifier processor

This program performs temporal projection, shot-noise normalization, configurable nullifier
calculation and variance estimation **on the Red Pitaya's ARM CPU**. The calculation library
(`nullifier.c`) and JSON command server (`commands.c`) are C11. Only `acquire_rp.cpp` uses C++ to
bridge the vendor SDK, whose current headers include C++ types. No Python or NumPy is used on the
board. cJSON 1.7.19 is included under its MIT license in `vendor/`.

The normal receiver path uses the board's stock streaming application. The C processor reads its
continuous ADC TCP packets, forwards the original packets to the engine, and calculates nullifiers
on a separate worker. Collection windows can span packet boundaries. Changing formulas does not
stop or rearm acquisition. The older `capture` command remains available for diagnostic DMA bursts.

The engine starts the C program through SSH when the device connects. Enable it for installed boards
in the engine's `.env` and restart the engine:

```dotenv
QL_NATIVE_NULLIFIER_ENABLED=true
QL_NATIVE_NULLIFIER_HOSTS=["rp-f0f9eb.local"]
QL_NATIVE_NULLIFIER_EXECUTABLE=/root/rtqlm-nullifiers/current/build/rp-nullifierd
QL_NATIVE_NULLIFIER_PORT=18902
```

The existing Start, Stop, formula and calibration controls use this backend automatically. Raw
waveform and spectrum display remain available. SSH requires a trusted host key and root key
access; the engine does not fall back to a factory password. No boot service is required.

CPU capacity depends on sampling rate, mode size, formula count and estimator. A bounded analysis
queue prevents processing delays from blocking raw acquisition. `stream.droppedPackets` reports
analysis overruns and `stream.lossPackets` reports packets with FPGA loss. Either kind of gap clears
partial windows and derived statistics before calculation resumes. Inspect these counters before
using a run for measurement; continuous acquisition alone does not guarantee every sample was analyzed.

## Build on the board

Use the installed SDK and a C/C++ compiler compatible with that SDK:

```sh
cd native/redpitaya_nullifiers
make board RP_PREFIX=/opt/redpitaya
./build/rp-nullifierd
```

The Makefile compiles the calculations and command loop with `cc -std=c11`; it compiles the SDK
bridge with `c++ -std=c++20` and links the installed `rp`, `rp-hw-calib` and `rp-hw-profiles` libraries.
`RP_PREFIX`, `CC`, `CXX`, `BUILD`, `CFLAGS`, `CXXFLAGS` and `LDFLAGS` can be overridden for a
matching cross-compilation sysroot. An ARM executable linked against a newer desktop sysroot is
not assumed compatible with the board's installed OS; build/link against that OS's SDK and libc.

For diagnostic `capture` commands, stop other applications that own the ADC/DMA and select the normal `v0.94`
acquisition FPGA through the board's normal management tools. The program checks
`/tmp/loaded_fpga.inf` and refuses incompatible images for burst capture, including `stream_app`. It never changes
the FPGA image, stops another application, changes the device tree, or modifies boot settings.
Do not use burst capture concurrently with the raw streaming receiver. The burst adapter takes an advisory lock
against other instances of this processor; unrelated applications do not share that lock.

The DMA region must fit both captures. Addresses are aligned to 4096 bytes and capacity is checked
conservatively at four stored bytes per sample per channel. A default capture reserves about
5 MB for the two 625,024-sample windows. Both channel fill states are checked. SDK errors, short
reads, timeouts and termination stop the acquisition and release enabled channels/resources.
SDK register mappings remain open for the command session and are released once on process exit.
This avoids an installed-SDK issue where repeated release/reinitialization reuses closed UIO
descriptors; every capture still rearms acquisition and disables both DMA channels afterward.

## Send commands

One JSON object per line on standard input produces one JSON response on standard output.
Keep the same process/session open to retain configuration, calibration and ensemble statistics.
For remote control, use an SSH session running `rp-nullifierd`. Control commands use that SSH
session. `start_stream` opens a raw ADC relay on the configured port, with the same network
access model as the stock raw ADC service. For example, after copying and building this directory on the board:

```sh
ssh root@BOARD_IP /root/RTQLM/native/redpitaya_nullifiers/build/rp-nullifierd
```

Replace `BOARD_IP` and the executable path with the actual deployment. Paste commands into that
session or send newline-delimited JSON through your control program. The supplied
`example-commands.jsonl` can also be piped into one session. Closing the session ends the processor.

This configures the lab X formula; edit the `weight`, `source` and `offset` fields to change the
calculation without rebuilding:

```json
{"command":"configure","config":{"measurementBasis":"x","nullifiers":[{"name":"cluster_x","start":0,"terms":[{"source":"a","offset":0,"weight":1},{"source":"a","offset":1,"weight":1},{"source":"b","offset":0,"weight":1},{"source":"b","offset":1,"weight":-1}]}]}}
```

For manual continuous operation, configure the stock streaming service with the existing engine,
and have its ADC client use this relay:

```json
{"command":"start_stream","port":18902,"channelMask":3}
{"command":"latest","maxPoints":500}
{"command":"status"}
{"command":"stop_stream"}
```

The normal engine connection handles this sequence. `channelMask` is 1 for CH1, 2 for CH2, or 3
for both. The stock ADC service still controls sample rate and starts the FPGA; the C relay does
not create samples by itself. For a separate diagnostic burst, use:

```json
{"command":"capture"}
{"command":"latest"}
{"command":"status"}
```

`a` is IN1 and `b` is IN2. Positive `channelBDelaySamples` pairs A[n] with B[n+delay]; negative
delay shifts A forward. Every formula is a weighted sum of the selected source at `k+offset`.
`start` selects the first local anchor index. Each capture is a separate collection: partial
modes and formula support are never joined across captures.

Changes are validated completely before commit. Invalid commands return `{"ok":false,"error":...}`
and preserve the active configuration. Successful configuration increments `revision` and clears
calibration and results. Unknown/duplicate fields, invalid sources, fractional/negative offsets,
non-finite coefficients, cancelling formulas and excessive allocation sizes are rejected. A basis
change switches the built-in lab formula; custom formulas remain as supplied. A physical X/P
measurement still requires the corresponding optical setup.

Commands are synchronous. A capture completes or reaches `triggerTimeoutSeconds` before the next
command is read. SIGINT, SIGTERM and SSH session termination cancel acquisition at its next fill poll.
The result is tagged `backend: redpitaya-arm-c`; the separate test executable identifies itself
as `c-replay`.

## Calibration and variance

With vacuum/shot-noise connected, send:

```json
{"command":"start_calibration"}
{"command":"capture"}
{"command":"finish_calibration","minimumModes":100}
```

During streaming, send `start_calibration`, allow vacuum windows to accumulate, then send
`finish_calibration`; no `capture` commands are needed. For burst diagnostics, repeat `capture`
before finishing if necessary. A failed finish leaves calibration active so more
vacuum modes can be acquired. Only rails referenced by the formulas need a valid reference.
Each rail is normalized to variance 1/2; each formula's reference is half the sum of its combined
squared weights. After switching the experiment to the signal measurement, send another `capture`.

`calibrationEstimator: pooled` uses stable moments over all retained vacuum modes;
`segment_mean` averages each capture's population variance equally, matching the lab method.
The default `lab_rolling` estimator calculates centered rolling variance per capture and averages
the dB traces equally across completed captures. `rolling` reports only the latest capture;
`ensemble` accumulates stable population variance independently at each aligned mode index across
captures, requiring two valid observations per index. Repeated captures need consistent optical
timing and trigger alignment for ensemble results to be meaningful.

`reset` clears results and accumulated variance while preserving calibration. `clear_calibration`
removes calibration and results. `disable` also stops admitting captures; `configure` enables it
again. These are sent as `{"command":"reset"}` and analogous single-command objects.

## Native configuration

Settings omitted from the first configure command use the lab defaults below. Later commands
update only supplied fields; a supplied `nullifiers` array replaces all formula rows.

| Setting | Default / supported range |
| --- | --- |
| `decimation` | 1; supports 1, 2, 4, 8 and 16–65536. Rate = 125 MHz / decimation. |
| `captureSamples` | 625024 per channel; multiples of 64, at most 4194304, also bounded by board DMA memory. |
| `modeShape` | `lab_gaussian_linear_cosine`; also `gaussian_linear`, `identity`. |
| `modeWidthSeconds`, `measurementFrequencyHz` | `308e-9`, `6e6`. |
| `modeSamples` | `null`: derive sample count from width for every shape. An explicit legacy count overrides width. |
| `explicitModeTemplate` | `[]`; a nonzero finite template overrides the analytic shape. |
| `minSamplesPerMode`, `modeOversample` | 4, 1000; coefficient grids are capped at two million points. |
| `channelBDelaySamples` | 2; signed integer, magnitude at most 65536. |
| `collectionGuardSeconds`, `collectionEndGuardSamples` | `0.0008006`, 1000; trim inside each captured window. |
| `triggerSource` | `now`; `external_rising`, `external_falling`, `a_rising`, `a_falling`, `b_rising`, `b_falling`. |
| `triggerLevelVolts` | 0; applies to analog A/B edge triggers, range −1 to 1 V. |
| `triggerTimeoutSeconds` | 5; between 0.001 and 60 s for trigger and DMA fill together. |
| `varianceEstimator` | `lab_rolling`; also `rolling`, `ensemble`. |
| `varianceWindowSeconds`, `varianceWindowModes` | `1/3000`, `null`; explicit mode count overrides duration. |
| `calibrationEstimator` | `segment_mean`; also `pooled`. |
| `gateAbsoluteThreshold` | `null`: off; positive voltage threshold excludes entire modes, preserving indices. |
| `historyModes` | 25000; range 100–25000. Retains a prefix of each capture and reports truncation. |
| `measurementBasis` | `x`; also `p`. |
| `windowMode` | `periodic`; also `continuous` with the rolling estimator. Triggered streaming windows are not implemented. |
| `lockSeconds`, `collectSeconds` | 0.005 each; sample-clock phases for periodic windows. |
| `requireFullVarianceWindow` | false; the receiver enables this for single-mode analysis. |
| `nullifiers` | Lab X row; at most 16 named rows, each with at most 32 terms. |

Templates are bounded to 4096 samples. The lab Gaussian-linear-cosine coefficients preserve the
lab script's cosine convention and fine-grid normalization before interpolation. The plain
Gaussian-linear shape and explicit templates use normalization on the actual ADC grid. The
reported `modeSamples` and `modePeriodSeconds` describe the actual tiled timing.
The receiver edits T width and displays the derived count. The lab shape truncates
`width * sampleRateHz`; other shapes round to the nearest sample. Continuous reference
calibration uses complete-mode batches of at most about 0.2 seconds (or one mode for
longer modes), so a large display history does not delay the 10-second reference timer.

Result objects contain `nullifiers`, `variances`, `squeezingDb`, `varianceCounts`, `indexStarts`,
`quadratures`, capture counts, calibration state and configuration revision. `latest.maxPoints`
optionally samples display arrays and returns their common `displayStride`; mode timing and all
accumulated statistics retain their original resolution. Omit it for complete result arrays.
Arrays preserve positions; values
that JSON cannot represent, including rejected modes and negative-infinite dB, are `null`.
Before calibration, `squeezingDb` is empty. A failed capture clears derived results and excludes
the entire incomplete capture. Completed vacuum references remain available.

The existing `/api/v1/devices/{id}/nullifiers` HTTP endpoints forward to this command session for
enabled boards. The receiver sends its saved analysis settings and displays the returned C
results, without recalculating nullifiers in Python. Devices outside the configured host list
keep their existing processing path.

## Validation without hardware

```sh
make test
make BUILD=build-arm CC=arm-linux-gnueabihf-gcc
NF_RUNNER="qemu-arm -L /usr/arm-linux-gnueabihf" python3 tests/check_commands.py build-arm/rp-nullifier-replay
make BUILD=build-sdk RP_PREFIX=/path/to/sdk test-sdk
```

`test-sdk` links the real acquisition bridge to a test double compiled against the official SDK
headers and uses isolated image/lock paths in its build directory. It never accesses board hardware.
The repository's `test_native_nullifier_parity.py` compares C projection and rolling variance with
the Python scientific reference. Set `RTQLM_NATIVE_COMMAND` to a replay invocation when a local C
compiler is unavailable. Hardware timing, installed SDK compatibility, optical alignment and ARM
throughput require validation on the actual board.

SDK declarations were compile-checked against Red Pitaya commit
`0e46d64396a57752bf97315789d6cc6c9fbddad8`. See the official
[deep-memory acquisition example](https://redpitaya.readthedocs.io/en/latest/appsFeatures/examples/DMM/deepMemoryAcq.html)
and [memory/throughput explanation](https://redpitaya.readthedocs.io/en/latest/appsFeatures/remoteControl/deepMemoryMode.html).
