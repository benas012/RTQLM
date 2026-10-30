# Real Red Pitaya validation — 29 October 2026

Tested `rp-f0f9eb.local` with the user-confirmed physical connections OUT1 → IN1
and OUT2 → IN2. The other saved board was left alone. The receiver displayed
live ADC waveforms and board-computed variances and dB values.

These are **electrical loopback tests against generated electrical references**.
The negative dB readings demonstrate the processing and display path. They do
not demonstrate an optically squeezed state, entanglement, or a vacuum reference.
Clear the electrical calibration before using real homodyne measurements.

## Verified calculations

Halving the generated amplitude should change the variance by
`10 log10(0.5²) = −6.0206 dB`.

| Test | Measured dB after halving amplitude | Result |
| --- | --- | --- |
| Single-mode CH1, 1.953125 MS/s | −5.96 | Passed |
| Single-mode CH2, 1.953125 MS/s | −5.99 | Passed |
| Lab 1D X formula, 1.953125 MS/s | −6.06; reference −0.03 | Passed |
| Lab 1D P formula, 1.953125 MS/s | −6.01; reference −0.02 | Passed |
| Single-mode CH1, buffered 125 MS/s | −5.98 | Passed |
| Single-mode CH2, buffered 125 MS/s | −6.11 | Passed |
| Final visible CH1, 122.07 kS/s | −6.27 | Passed |
| Final visible CH2, 122.07 kS/s | −6.24 | Passed |

The live 1.953125 MS/s tests used a flat, normalized 16-sample temporal mode,
a 1,000-mode rolling population variance, pooled reference calibration,
5 ms locking, and 20 ms collection. This electrical test mode is 8.192 µs long;
it is not the nominal 308 ns optical mode.

The buffered 125 MS/s test acquired 625,024 samples per channel in each of
three captures. Each capture contained 16,448 complete 38-sample modes. The
lab temporal template was used, with 15,449 complete variance windows in the
final capture. Reference and signal came from standard SDK-generated loopback
sines; halving their amplitude changed the measured variances as expected.
CH1 ADC standard deviation changed from approximately 0.03482 V to 0.01751 V.
These captures have transfer and rearm dead time and establish burst operation.

The lab template truncates a nominal 308 ns mode to 38 samples at 125 MS/s,
giving an actual bin duration of 304 ns. Optical timing alignment remains a
separate requirement. At decimation 8, its four samples span 256 ns.

## Throughput limits measured on this board

`/sys/class/net/eth0/speed` reported **100 Mb/s**, not Gigabit. This is at most
12.5 MB/s before protocol overhead. Two 16-bit ADC channels require 62.5 MB/s
at 15.625 MS/s and 500 MB/s at 125 MS/s. Both higher-rate continuous tests
received actual hardware samples but reported substantial FPGA sample loss.

The ARM benchmark operated on synthetic samples already in RAM. It excluded
ADC acquisition, packet conversion, Ethernet, and the display. It measured
the median of three trials of 40 complete collections each, without history
truncation. The calibrated 1D calculation used one four-term row and rolling
variance.

| Input rate per channel | Input duration | Calibrated 1D calculation time |
| --- | --- | --- |
| 15.625 MS/s | 5 ms | 13.88 ms |
| 125 MS/s | 5 ms | 27.13 ms |

Both exceed the 10 ms interval of a 5 ms lock / 5 ms collect cycle before
transport overhead is included. Faster continuous operation requires solving
both transport and processing limits. Full-rate temporal projection on FPGA
and transport of derived values remains a distinct implementation task.

At 1.953125 MS/s there was a clean 10-second interval, but a longer run later
accumulated both FPGA loss and analysis-queue drops. That rate was therefore
not accepted as a sustained loss-free operating point. The final visible demo
uses the receiver's lower 2CH stable acquisition preset: decimation 1024,
122.0703125 kS/s nominal rate, 32,768-byte blocks, flat 16-sample modes,
1,000-mode variance windows, 5 ms locking, and 200 ms collection. Its temporal
bin is 131.072 µs and is solely an electrical demonstration setting.
A 30-second interval returned 148 new analysis results with no new FPGA losses
or analysis-queue drops. Its final settings and measured counter changes are
in `tmp/rp-hardware-demo-20261028/stable-visible-demo.json`.

## Fix, deployment, and remaining checks

Testing found that the P basis selector could retain the lab X row when using
an identity temporal template. `match_builtin_basis()` now switches known lab
rows regardless of template shape, while preserving custom formulas. Both
directions have regression coverage. The reopened UI was used to select P,
and the saved formula was checked for the P signs and `start=1`.

- Full local unit suite: **414 passed**, 18 native comparisons skipped in that run.
- The 18 C/Python numerical comparisons passed separately before the UI-only fix.
- Real ARM board: **21 native command tests and 6 TCP streaming tests passed**.
- Current C source built with the installed board SDK and strict compiler warnings.
- Active release: `/root/rtqlm-nullifiers/releases/e4e57b4a8c75/redpitaya_nullifiers`.
- Previous release remains available; the managed `current` link selects the new release.
- The isolated burst test restored the `stream_app` FPGA image on exit.

Absolute voltage scaling of the stock streaming generator/receiver needs a
separate check: programmed generator amplitude and displayed ADC amplitude
showed an approximately eightfold difference. The dB tests compare measured
variances at two levels and do not validate that absolute voltage scale. The
standard SDK burst test's voltage scale was consistent with its sine settings.

Verify reference variance is stable after waveform changes before calibrating.
A DAC upload acknowledgement alone does not establish a settled analog reference.
Loss counters remain visible; affected partial windows are discarded.

Raw reports, reference/result snapshots, and the visible receiver screenshot
are retained in `tmp/rp-hardware-demo-20261028/`. The measured CPU benchmark is
`dist/redpitaya-nullifiers/board-cpu-limit.json`. Pre-test profile and processing
settings are backed up in the raw-report directory. The live demo deliberately
keeps its electrical reference and generated outputs active for inspection.

Stop the generated signals using **Outputs… → Stop both outputs**.
