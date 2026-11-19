# Red Pitaya ARM nullifier processor

The engine checks the configured C executable when connecting to each Red Pitaya.
If it is absent, automatic installation (enabled by default) uploads the engine's
bundled sources and builds the worker using that board's compiler and Red Pitaya SDK.
The first connection can take up to five minutes. Later connections reuse the existing
executable; automatic installation does not upgrade or replace existing executables.
Builds are stored under `/root/rtqlm-nullifiers/engine-builds/<source-hash>/<installation-id>`
in a fresh directory for each attempt, so retries cannot reuse interrupted object files, and linked
to the configured executable only after a successful build. Installation is serialized
across sessions, and a failed build prevents acquisition with an explanatory error.
The board needs `make`, `gcc`, `g++`, `tar`, `flock`, and `/opt/redpitaya` SDK headers
and libraries. The installer reports missing prerequisites without installing OS packages.
Set `QL_NATIVE_NULLIFIER_AUTO_INSTALL=false` to require manual installation.

Moving the entire SD card normally carries its Linux partition and installed worker.
This check also handles a fresh or reimaged card. It does not install or replace an FPGA
image; tagged E1 acquisition still requires its matching custom FPGA image.

Benchmark sources, helpers, reports, and measurements are maintained in the sibling
`../../../benchmarks` folder outside this checkout. Its Makefile builds against
this processor. The benchmark targets below are available when that folder is present.

This program performs temporal projection, shot-noise normalization, configurable nullifier
calculation and variance estimation **on the Red Pitaya's ARM CPU**. The calculation library
(`nullifier.c`) and JSON command server (`commands.c`) are C11. Only `acquire_rp.cpp` uses C++ to
bridge the vendor SDK, whose current headers include C++ types. No Python or NumPy is used on the
board. cJSON 1.7.19 is included under its MIT license in `vendor/`.

Custom temporal mode functions are entered as `modeExpression` in the receiver or engine API.
The engine evaluates the restricted mathematical expression once at configuration time and
sends `sampledModeTemplates` to this worker: one array for integer-width bins, or two arrays
ordered shortest/longest for fractional-width bins. These arrays already include the selected
sampling and normalization convention. They preserve the requested bin cadence and cannot be
combined with `explicitModeTemplate`. Send `sampledModeTemplates: []` to restore analytic
weights. Existing workers must be rebuilt to accept the new setting; automatic installation
preserves installed executables.

For the current two-channel **50 ms at 125 MS/s** acquisition and TCP configuration,
see [the 50 ms implementation and measured benchmark](../../../benchmarks/reports/capture-50ms-2026-11-07.md).
Build both `make board buffered-board`: buffered ARM sessions use the binary-capable
worker's authenticated TCP connection for commands/results, while SSH only launches it.
The TCP launcher records its worker PID and process start time, releases that same
worker on close or failed startup, and retries a transient startup failure once.
Authentication failures and rejected configurations are reported without retry.
An unconnected listener exits after 15 seconds so an interrupted launch cannot hold
the port indefinitely. Listener failures include the port and operating-system cause.
On an occupied port, the launcher allows that timeout before its retry, without
terminating a worker belonging to an unknown session.
The 15-second receive timeout applies only to authentication. An authenticated
connection can remain idle across Connect or Stop without discarding calibration;
TCP keepalive detects a lost network connection, and closing the client releases
the worker. Sending a result to an unresponsive client still has a 15-second timeout.
The buffered TCP application requests variance-only replies (`resultFields: "variances"`):
variances, associated dB/counts and metadata, without ADC previews or amplitude arrays.
The worker also supports full raw-buffer transfer for host processing. Existing continuous
streaming command sessions retain their previous transport.

The receiver supports buffered DMA acquisition and the board's stock streaming application.
Buffered `capture` integrates signed ADC counts directly from stopped DMA memory into temporal
modes, then calculates nullifiers and variance. It converts only the requested waveform preview
to volts. Captures have gaps. In stock streaming mode, the C processor reads continuous ADC TCP
packets, forwards the original packets to the engine, and calculates nullifiers on a separate
worker. Collection windows can span packet boundaries in that mode.

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

For tagged E1 readouts, the full ADC record includes both digital states. Each LOW interval
starts its own measurement clock and contributes its complete temporal bins. Shot-noise
calibration uses both HIGH and LOW.

The DMA region must fit both captures. Addresses are aligned to 4096 bytes and capacity is checked
using the SDK's two-byte ADC words per sample per channel. A 50 ms capture uses about
25 MB for two 6,250,048-sample windows and fits the tested board's existing 32 MiB reservation.
Both channel fill states are checked. SDK errors, short
reads, timeouts and termination stop the acquisition and release enabled channels/resources.
SDK register mappings remain open for the command session and are released once on process exit.
This avoids an installed-SDK issue where repeated release/reinitialization reuses closed UIO
descriptors; every capture still rearms acquisition and disables both DMA channels afterward.

### E1 physical pin 3 acquisition gate

For non-tagged legacy readouts only, buffered hardware captures require HIGH on
**DIO0_P (E1 physical pin 3)** by default. The tagged readouts described below capture
the full record and admit LOW intervals for measurement.
The adapter configures
that pin as input, waits for HIGH before starting the ADC, checks while waiting for trigger/fill,
and checks once more after stopping. An observed LOW during an active capture discards the whole
capture before projection, calibration or statistics. A GPIO error also rejects the capture.
Waiting for HIGH uses `triggerTimeoutSeconds`; trigger/fill then has its own timeout of that
length. Cancellation interrupts either wait and disables both DMA mappings.

```json
{"command":"configure","config":{"acquisitionGatePin":"DIO0_P","acquisitionGatePollUs":500}}
```

`acquisitionGatePin` accepts `DIO0_P`, legacy `DIO3_P`/`DIO3_N`, or explicit `null` to disable this gate.
`acquisitionGatePollUs` accepts integer 50..1,000,000 microseconds. The default 500 us is a
0.5 ms target checked locally on the board, using absolute monotonic deadlines and skipping
missed deadlines. It is **not** a hard
real-time guarantee: SDK calls and Linux scheduling can delay a check, and pulses between checks
can be missed. A stricter/sample-accurate interlock needs FPGA support. This gate applies to
buffered acquisition on `v0.94`; stock `stream_app` TCP processing and replay do not read GPIO.
The SDK operations are documented in the official
[digital I/O API](https://redpitaya.readthedocs.io/en/latest/appsFeatures/remoteControl/command_list/commands-digital.html).

`captureTimingMs` adds `gateWait`, `gateCheck`, `gateMaxInterval`, `sampleDuration` and
`startInterval`, plus the `gateChecks` counter. These gate fields also appear on failed hardware
captures. `gateMeanInterval`, `gateIntervalCount`, and `gateIntervalHistogram` summarize all
intervals between check starts. Histogram buckets are 0.1 ms wide: `[0,0.1)`, ..., `[3.0,3.1)`,
with a final `[3.1,infinity)` overflow bucket. Percentiles inferred from buckets are bounds,
not exact measured quantiles. `gateMaxInterval` is the largest observed interval between check starts, including the
initial HIGH wait. `sampleDuration` is the configured sample count divided by sampling rate;
`startInterval` is the monotonic interval between ADC starts in this command session, including
processing and command delays (zero for the first start). Failed started captures also advance
that start marker. `gateCheck` overlaps the wait/setup stages, so do not add it to their totals.
`setup` excludes `gateWait`; `sampleWait` includes active gate checks and the completion check.

### Optional RAM readout comparison

For ordinary FPGA samples, `rawReadout` accepts `direct_dma` (default) and `ram_copy`. The latter copies both stopped,
wrapped ADC views into reusable owned RAM, then calls the C raw projector. Ungated weighted
RAM chunks of at least eight samples use four independent double accumulators; shorter chunks,
voltage rejection, and direct DMA retain their existing arithmetic paths. It retains
double precision, the temporal coefficients/boundaries, rejection lookup, rail delay and
statistics. It uses up to an extra 16 MiB at the largest capture, retained until SDK shutdown.
The copy removes the circular split, which can change the final rounding of the bin crossing
that split; it does not replace weighted integration with flat bins. Reconfiguration clears
the reference as usual.

```json
{"command":"configure","config":{"rawReadout":"ram_copy"}}
```

`captureTimingMs.rawCopy` measures copying/allocation, `rawCopyBytes` counts bytes copied, and
`rawProjection` measures the subsequent projection. Both stages are included in `read`.

On the [custom E1 FPGA](../../fpga/e1_sync/README.md), ARM burst captures also accept
`e1_direct`, `e1_bulk`, `e1_tile16`, `e1_tile64`, `e1_tile256` and `e1_tile64x2`.
Tile sizes are KiB per rail. `e1_tile64x2` projects the two rails concurrently with a
persistent worker. `rawProjection` includes tagged validation and any tile copies.
Each stored sample, including guards and padding, needs a valid marker and matching E1
tags on both rails. The entire requested record is captured, regardless of E1 level.
Measurement starts a new bin schedule at each contiguous E1 LOW interval. It integrates
complete bins whose aligned samples fit within that interval. An ordinary boundary between
raw ADC regions preserves bin phase. HIGH intervals appear as gaps in the result, separating
cluster formulas and rolling-variance windows. Shot-noise calibration uses all complete
temporal modes within the guards and treats the whole capture as one calibration segment.
Marker and paired-tag integrity checks apply in both cases. Averaging preserves gaps in the
current record. Display downsampling retains gaps shorter than a display stride.
`capturedModes`, `includedModes` and `excludedModes` report the result slots, completed LOW
bins, and gaps respectively. An all-HIGH capture is valid and has no finite measurement result.
Tagged capture requires immediate triggering,
decimation 1, `DIO0_P` and no voltage rejection threshold. With `previewPoints`, the
worker returns a calibrated voltage preview sampled across the measurement. Its
`preview.sampleStride` specifies the ADC samples between displayed points. Raw
buffer export uses the ordinary acquisition path. Use a fresh reference calibration
after switching to tagged acquisition.
The current same-capture comparison measured 81.82 ms direct versus 62.05 ms copy plus
projection/statistics for the tested two-row workload. See the measured review below for scope,
precision comparisons and live acquisition rates. Direct DMA remains an explicit fallback and
the default; these weighted-mode measurements do not establish a win for every configuration.
With exclusive ADC ownership and a valid HIGH input, compare:

```sh
make benchmark-board
./build/benchmark-board --dma --shape gaussian_linear --estimator lab_rolling --iterations 30 --raw-readout direct_dma
./build/benchmark-board --dma --shape gaussian_linear --estimator lab_rolling --iterations 30 --raw-readout ram_copy
```

These benchmarks keep the gate enabled. `--gate-pin DIO3_N` selects the other rail, and explicit
`--gate-pin none` disables it for a diagnostic. The readout option affects hardware DMA trials;
synthetic RAM trials have no DMA to copy. `postAcquisitionMs` excludes setup, HIGH wait and
trigger/fill wait. Inspect copy/projection together and p95, not projection alone.
See [the acquisition/optimization review](../../../benchmarks/reports/acquisition-gate-and-c-optimization-2026-10-31.md).
The subsequent [measured pipeline and transmission review](../../../benchmarks/reports/pipeline-review-2026-10-31.md)
includes 1,000 physical captures, the paired stopped-DMA comparison, and lossless-codec results.
The [half-rate experiment](../../../benchmarks/reports/half-rate-review-2026-11-01.md) compares fresh captures
at 125 and 62.5 MS/s with equal capture duration. The isolated review runner accepts
`--decimation` and `--capture-samples` for reproducing that comparison.
`NF_PROFILE_IO=1` writes serialization time, blocking stdout time, process CPU seconds,
RSS high-water mark and response bytes to stderr (one JSON line per response). It leaves
the normal stdout protocol intact. The stdout timer is not a remote-delivery acknowledgement.
Replies reuse a growable serialization buffer (up to 64 MiB), preserving the JSON format while
avoiding repeated allocation/shrink cycles. The final 500-capture RAM-copy test peaked at about
8 MiB RSS; this finite run does not establish indefinite memory stability.

Buffered mode projection consumes the SDK's raw DMA regions, including circular wrap, without
full-size raw copies or float voltage arrays. Equal-weight identity modes use integer sums;
other templates use weighted double sums. Constant ADC/display offsets are represented in count
units before integration, with no per-bin centering. Each channel receives its own shot-noise
normalization. Changing input units clears the old reference; record a new reference after this
change. `inputUnits` reports `adcCounts` for buffered results and `volts` for the legacy float path.

Only the small preview is converted to calibrated volts. Voltage rejection thresholds still use
the exact SDK voltage lookup. The first preview read for a calibration key is checked bit-for-bit
against the SDK voltage reader, retaining the SDK fallback for that preview if needed. The legacy
`nf_acquire` voltage-array interface remains for SDK diagnostics. DMA stays stopped and mapped
until projection and preview conversion finish. Raw mode integration requires signed 14-bit ADCs.

For ungated identity modes, the ARMv7 build uses NEON integer instructions to decode and sum
eight ADC samples at a time. Other mode shapes and voltage gates retain the general kernel.
Bin boundaries are cached when configuration changes using the original `nf_mode_boundary`
calculation, preserving the 38/39-sample sequence without repeated floating boundary work.
The cache adds 100,004 bytes per processor. ARM32 builds select `-mfpu=neon` for `nullifier.o`;
use `RAW_SIMD_FLAGS=` to build the scalar fallback. AArch64 and non-ARM targets use their
compiler's normal feature selection. `make BUILD=build-scalar RAW_SIMD_FLAGS= test-raw`
checks the fallback separately on the board.

Each buffered reply exposes read checkpoints in `captureTimingMs`: `readPointers` retrieves the
trigger positions; `readParameters` reads SDK calibration/range settings; `readTable` checks or
builds the voltage lookup table; `readRegions` obtains direct DMA spans; `readConvert` reads the
preview words, looks up calibrated voltages and writes float results; `readVerify` measures the first
SDK comparison for a calibration key; `readFallback` measures the SDK voltage fallback if used.
These stages are summed across both rails. `rawProjection` measures raw mode integration, including
DMA reads, signed decoding and weighted sums. `readChannel1` and `readChannel2` cover each rail's
access/preview preparation; the joint `rawProjection` time is separate. `read` includes both.
Counters reset for each capture, and clocks are sampled at stage boundaries, not per ADC sample.

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
| `captureSamples` | 625024 per channel; multiples of 64, at most 8388608, also bounded by board DMA memory. |
| `measurementSamples` | 0 uses the complete capture; set 6250000 with captureSamples 6250048 for exactly 50 ms at full rate. |
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
| `historyModes` | 25000 standalone default; range 100–262144. Buffered application settings expand it to retain every complete measurement mode. |
| `measurementBasis` | `x`; also `p`. |
| `windowMode` | `periodic`; also `continuous` with the rolling estimator. Triggered streaming windows are not implemented. |
| `lockSeconds`, `collectSeconds` | 0.005 each; sample-clock phases for periodic windows. |
| `requireFullVarianceWindow` | false; the receiver enables this for single-mode analysis. |
| `nullifiers` | Lab X row; at most 16 named rows, each with at most 32 terms. |

Templates are bounded to 4096 samples. The lab Gaussian-linear-cosine coefficients preserve the
lab script's cosine convention and fine-grid normalization before interpolation. The plain
Gaussian-linear shape and explicit templates use normalization on the actual ADC grid. The
reported `modeSamples`/`modeSamplesMax` give the lower/upper bin lengths, and `modePeriodSeconds`
gives their average period. With time-based widths, boundaries follow `floor(k*T*Fs)`;
308 ns at 125 MS/s alternates 38/39 samples. Both templates have equal discrete white-noise gain.
Fixed counts and explicit templates remain fixed-size. The collection origin still needs
alignment with the experiment. Continuous reference
calibration uses complete-mode batches of at most about 0.2 seconds (or one mode for
longer modes), so a large display history does not delay the 10-second reference timer.

Result objects contain `nullifiers`, `variances`, `squeezingDb`, `varianceCounts`, `indexStarts`,
`quadratures`, capture counts, calibration state and configuration revision. `latest.maxPoints`
optionally samples display arrays and returns their common `displayStride`; mode timing and all
accumulated statistics retain their original resolution. Omit it for complete result arrays.
Arrays preserve positions; values
that JSON cannot represent, including rejected modes and negative-infinite dB, are `null`.
Before calibration, `squeezingDb` is empty. An acquisition rejection excludes the entire
incomplete capture and leaves previous accepted results and accumulated calibration/statistics
intact. The failed reply has `ok:false`; an earlier `latest` result is not a new accepted capture.
A processing failure may have partially changed state and clears derived results. Completed
vacuum references remain available. Acquisition-only SDK shutdown preserves generator outputs.

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

## Processing benchmarks

`make test` also checks weighted raw projection against an independent scalar oracle, long
38/39 boundary sequences, buffer ownership, rolling/lab/ensemble statistics, and Larsen's
eight-term 2D formulas. `make test-sdk` checks the adapter against its SDK test double.

```sh
make benchmark
make benchmark-board benchmark-dma
# Stop other ADC clients before these explicit hardware diagnostics:
build/benchmark-board --dma --shape gaussian_linear --estimator rolling
build/benchmark-dma
```

The ordinary-RAM benchmark uses synthetic ADC words. The board benchmark uses the connected
inputs and keeps any calibration local to that process; it does not establish optical squeezing.
The DMA diagnostic copies stopped data into RAM only to compare memory access and projection
on identical words. Production projection retains direct circular DMA views, with only a 64-byte
NEON decode tile for weighted DMA access; the existing raw projector retains its RAM scalar path.
Hardware diagnostics
require the `v0.94` image and exclusive instrument ownership.

`nf_process_last_timing()` reports normalization, formula evaluation, rolling variance, and
statistics/dB stages, plus the number of actual `log10` calls. Capture replies expose these as
`normalization`, `formulas`, `rollingVariance`, `statistics`, and `log10Calls` within
`captureTimingMs`, alongside the existing acquisition checkpoints. `postDma` includes read,
projection, acquisition cleanup and processing; it excludes acquisition setup/wait, JSON and
transport. `rawProjection` is contained in `read`, so they must not be added together.

See the [measured before/after report](../../../benchmarks/reports/weighted-processing-optimization-2026-10-30.md)
for scope, numerical accuracy and the remaining ARM/FPGA latency limits.

SDK declarations were compile-checked against Red Pitaya commit
`0e46d64396a57752bf97315789d6cc6c9fbddad8`. See the official
[deep-memory acquisition example](https://redpitaya.readthedocs.io/en/latest/appsFeatures/examples/DMM/deepMemoryAcq.html)
and [memory/throughput explanation](https://redpitaya.readthedocs.io/en/latest/appsFeatures/remoteControl/deepMemoryMode.html).
