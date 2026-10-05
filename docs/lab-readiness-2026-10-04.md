# Single-mode squeezing check — 4 October 2026

**The calculation and receiver controls are verified. CH1-only acquisition passed
a 90-second native-processing check with no lost samples or missing packets.**
The optical experiment still needs validation: the 308 ns mode has only four
samples at the saved rate, and the analysis clock has not been synchronized to
the laboratory lock controller. The saved setup now measures one channel at a
time, as requested.

The [single-mode guide](single-mode-squeezing.md) contains the complete operator
procedure and the function-by-function comparison with the supplied Python file.

## Verified calculation

The Single-mode preset performs the requested three steps independently for each
selected channel:

1. Build the mode weights and project each complete N_mode-sample bin using
   `q = sum(voltage * weights) * dt`.
2. During shot-noise calibration, calculate population variance over all accepted
   projected values. This is the **Whole accepted trace** choice.
3. Calculate population variance over full, centered windows of 100 projected
   modes and display `10*log10(V_signal/V_shot)`.

The default mode now retains the script's nominal 308 ns Gaussian-linear-cosine
shape and frequency parameter 6e6. N_mode remains automatic unless explicitly
entered. An identity mode is also available with an explicit sample count.

The script's active final calculation is a cluster calculation: it combines both
channels and neighbouring modes, averages shot-noise segment variances, and uses
a variance-window duration of 1/3000 seconds. Those choices differ from the
requested independent-channel, pooled-reference, 100-mode method. Separate
presets and calibration choices keep both procedures available.

## Corrections completed

- Added single-mode CH1, CH2 and Both analysis, adjustable N_mode, an explicit
  rolling-window count, and a pooled shot-noise reference.
- Withheld partial edge variance windows. Ordinary packet boundaries preserve
  complete-bin alignment; collection boundaries and detected gaps separate data.
- Preserved acquisition-clock position when starting, finishing or clearing
  calibration. A bin straddling a calibration change is excluded without moving
  later bin boundaries or modifying the supplied input arrays.
- Prevented discarded calibration fragments from satisfying the minimum count
  for the script's segment-variance estimator.
- Corrected the vendor's per-packet FPGA-loss values into cumulative counters.
  Repeated equal losses are now detectable. An affected packet is excluded
  because the vendor protocol does not locate missing samples inside it.
- Disabled compression for the receiver's binary sample WebSocket and enlarged
  the native ADC TCP receive buffer. Live delivery improved substantially.
- Allowed single-channel analysis when the unused input is absent. Triggered
  collection explicitly reports an absent configured trigger input.
- Rejected incompatible single-mode variance estimators and conflicting explicit
  mode-template/sample-count settings.
- Corrected the displayed acquisition-batch duration: the vendor block size is
  per channel, so enabling two channels does not halve the batch duration.

## Software validation

- **368 unit, integration and end-to-end checks passed**, including native UI
  layout, calibration transitions, interrupted streams, packet partitioning,
  single-channel inputs, full 100-mode windows, and API behavior.
- Lint checks passed for the changed calculation, receiver and test files.
- Strict type checks passed for the processing, ADC reader and callback modules.
- Extracted pure functions from the exact uploaded script, without running its
  file-loading or plotting code. At 15.625, 125 and 200 MS/s, mode coefficients,
  projection, reference normalization, X/P formulas, script rolling variance and
  dB conversion agreed. Maximum difference was **9.3e-14 dB**.
- Independent scaling tests verify **half variance → -3.0103 dB** and **half
  standard deviation → -6.0206 dB**.

There is one dependency deprecation warning from Starlette's test client; no
regression tests failed.

After the batch-duration correction, all 20 focused receiver-pacing checks and
lint checks for that correction passed.

## Real-board check through the coded app

Board: `rp-f0f9eb.local`, device
`3d0f4df8-c7ab-4a79-8dfe-8261a2f20af2`.

All board operations used the existing engine's device API and sample WebSocket.
The diagnostic passed received data through the native receiver's processing
method. It did not render the full GUI during the capture, so this is not a
complete live-UI performance certification.

The initial two-channel capture used the corrected lab-shape Single-mode preset, pooled calibration,
100-mode rolling variance, and the saved 5/5 ms analysis gate. It collected a
current-input baseline for approximately two seconds and measured the same inputs
for the remaining approximately eight seconds. This was **a software baseline
check, not a known optical shot-noise measurement**.

| Initial two-channel capture | Result |
| --- | --- |
| Configured ADC rate | 15.625 MS/s per channel |
| Lab N_mode / actual bin period | 4 / 256 ns |
| Capture duration | 10.017 seconds |
| Received packets | 1,053 |
| Delivered samples | 138,018,816 per channel |
| Observed delivery | 13.779 MS/s per channel |
| WebSocket sequence gaps | 3 |
| Packets with increased FPGA-loss counters | 908 |
| Accepted baseline modes | 10,109 per channel |
| Latest complete-window observations | 143 per channel |
| Median baseline comparison | CH1 +0.060 dB; CH2 +0.024 dB |

The rolling estimates fluctuate. These near-zero baseline results check software
normalization; they do not establish quantum squeezing or correct lock exclusion.
Most packets in this test had FPGA loss and were excluded from the calculation.
A final moment without an error message does not mean the whole acquisition was
continuous.

A separate capture with squeezing processing disabled still delivered only
13.896 MS/s and reported increased FPGA-loss counters in 897 of 1,061 packets.
Thus, the remaining loss occurs before the squeezing calculation. A temporary
16 MiB board-buffer probe improved delivery but did not eliminate loss. The
original **4 MiB ADC buffer, decimation 8 and 262,144-byte blocks** were restored
after those initial checks; the later saved setup below supersedes them.
Both DAC outputs remained disabled throughout these checks.

The detailed final capture is saved in
[live_app_report.json](../tmp/lab_comparison_20261004/live_app_report.json), and the
delivery-only comparison is in
[raw_delivery_report.json](../tmp/lab_comparison_20261004/raw_delivery_report.json).

## Follow-up: saved one-channel setup

The user confirmed that tomorrow's measurements can record one channel at a
time. CH1 alone was first checked for 30 seconds, then saved and checked for
**90.033 seconds** through the same device API, sample WebSocket and native
receiver processing method. This run loaded the actual saved processing
configuration. No optical shot-noise reference was acquired, and the full GUI
was not rendered during this run.

| Saved setup / 90-second result | Value |
| --- | --- |
| Enabled physical input / analysis selection | CH1 only / Single-mode CH1 |
| ADC rate / decimation | 15.625 MS/s / 8 |
| ADC buffer / block size per channel | 32 MiB / 1 MiB |
| Mode / N_mode / actual bin period | Lab 308 ns preset / 4 / 256 ns |
| Reference estimator / variance window | Whole accepted trace / 100 modes |
| Analysis gate | Periodic 5 ms lock / 5 ms collect, with lab guards |
| Received packets | 2,682 |
| Observed delivery | 15.618 MS/s |
| FPGA sample loss / WebSocket sequence gaps / service drops | 0 / 0 / 0 |
| Final processing error | None |

Both DAC outputs remained disabled during that capture. The initial 32 MiB
profile was subsequently adjusted as described below; the processing settings remain
saved. See [saved_ch1_delivery_report.json](../tmp/lab_comparison_20261004/saved_ch1_delivery_report.json)
for the capture and saved-profile evidence. This establishes continuity over the
tested interval; it does not guarantee indefinite operation or validate optical
squeezing and lock exclusion.

The former two-channel setup generated 62.5 MB/s, exactly the documented maximum
network-streaming rate. CH1 alone generates 31.25 MB/s at the same ADC rate,
providing more transport headroom. Larger buffers alone did not establish
reliable two-channel operation: a clean 30-second interval was followed by losses.
See the [official Red Pitaya streaming specifications](https://redpitaya.readthedocs.io/en/latest/appsFeatures/applications/streaming/top.html).

To measure CH2 afterward, disconnect acquisition, enable CH2, then disable CH1
using the Red Pitaya menu's acquisition toggles. Select CH2 in the single-mode
calculation settings, apply them, reconnect and acquire a new reference for CH2.
Keep one physical input enabled during each measurement. If Triggered collection
is used, its trigger input must also be recorded.

## Follow-up: output memory and current saved capture buffer

The 32 MiB ADC reservation above allowed continuous CH1 acquisition but prevented
output startup on the default board. A fresh attempt reproduced HTTP 503 with
`DAC board stopped before waveform playback: insufficient board memory`.
ADC and DAC streaming share the board's reserved memory, and the DAC needs at
least twelve blocks, including each 128-byte header. The 1 MiB block size therefore
requires a 12,584,448-byte DAC reservation in addition to the capture allocation.
See the [official memory-management description](https://redpitaya.readthedocs.io/en/latest/appsFeatures/applications/streaming/reference/technical_details.html).

**The current saved ADC reservation is 16 MiB**, with CH1 only, 1 MiB blocks and
15.625 MS/s unchanged. The analysis configuration is still Single-mode CH1,
lab mode, pooled shot-noise calibration and rolling windows of 100 mode values.

- Output startup succeeded after reducing the ADC reservation.
- A 30.040-second capture during output playback received 894 packets with no
  FPGA loss, sequence gaps or service drops.
- After stopping the output, a 60.023-second capture received 1,789 packets with
  no FPGA loss, sequence gaps or service drops.
- Separate settled captures detected the editor's approximately 953.674 Hz sine
  on CH1, including after reapplying its phase. The original OUT1 sine button also
  started and its approximately 1 kHz tone was detected. Both paths reported no
  sample loss or sequence gaps in those captures.
- The sine disappeared after stopping. Both outputs were left stopped.

The earliest packet collected immediately after starting the output preceded
settled sine playback; the separate settled checks avoid treating that packet
as proof of absent output. Input/output voltage scaling was not independently
calibrated against an external instrument in this check.

Detailed evidence: [shared-memory report](../tmp/lab_comparison_20261004/output_shared_memory_report.json)
and [settled playback report](../tmp/lab_comparison_20261004/output_playback_repeat_report.json).

## What the laboratory must establish before interpreting results

**Mode and rate:** 308 ns is a physical duration, not a portable oscilloscope
sample count. The script's convention is `N_mode = int(308 ns * ADC_rate)`.
The saved Red Pitaya rate produces four samples and a shorter 256 ns repeating
bin. At 125 MS/s it would produce 38 samples and 304 ns bins, but that higher
continuous two-channel stream has not been verified. Interpolating mode weights
does not add measured ADC samples. Confirm the physical mode and adequate
sampling with the laboratory before choosing an operating rate.

**Continuous data:** CH1-only passed the 90-second check above. Confirm no FPGA
loss or receiver sequence gaps over the actual laboratory measurement duration.
Rejecting affected data protects arithmetic continuity but does not recover
missing measurements or their timing.

**Lock synchronization:** Periodic analysis currently starts its own 5 ms lock /
5 ms collect schedule at the stream's sample origin. It does not know the optical
controller's phase. Calibration controls preserve an established phase; they do
not establish one. Detected losses reset processing and can invalidate periodic
alignment. Verify a sampled lock edge and use Triggered collection with the
correct delay/guards, or provide a controller timing path supported by acquisition.
A waveform-display trigger is independent of the analysis gate. Separate TTL
timing is not currently recorded by this two-analog-channel stream.

**Optical reference:** Acquire real shot noise with the same sampling rate, gain,
mode weights, local-oscillator conditions and timing as the signal. Save reference
and signal traces for an offline comparison. A receiver display or sine-wave
loopback cannot substitute for this validation.

Reopen the receiver to load the saved **Single-mode CH1**, **Rolling window
(modes) = 100**, and **Whole accepted trace** settings. Apply any subsequent
calculation changes before acquiring a fresh shot-noise calibration. Follow the
[full procedure](single-mode-squeezing.md) only after the acquisition and timing
conditions above have been checked.
