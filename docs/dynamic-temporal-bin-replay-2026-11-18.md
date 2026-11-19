# Dynamic temporal-bin replay benchmark

The baseline used commit `3e9435d` and was recorded before implementation. Both runs replayed the same 50 ms record from `rp-f0f9eb.local`, including both ADC rails and the sample-aligned E1 trace. Each rail has 6,250,000 samples at 125 MS/s. The first input segment has 20 samples and the second has 6,249,980. Python ran on `nelabasis` with NumPy 2.3.5 and one BLAS thread. Native C ran on the original ARMv7 Red Pitaya with GCC 13.3 and NEON from a temporary replay executable.

Each width used three warm-ups and 11 measured runs. The replay used identity weights, zero alignment delay and guards, uncalibrated ADC counts, and rolling variance over 10 modes. Settings application includes coefficient preparation and reset of the preceding result. Throughput is total input samples per channel, including HIGH intervals, divided by processing time. The timer excludes file I/O, packing the recording, board transfer, and live acquisition time.

The baseline Python adapter fed contiguous E1 LOW spans and reset the processor at each gap. The updated Python processor takes packet-aligned E1 directly and retains separate LOW histories. The baseline board masked against one capture-wide bin grid. The updated board restarts the grid at each LOW edge.

## Settings reapplied between replays

Values are medians with the full run-to-run range in brackets. Changes are relative to the baseline median. † marks overlapping before and after ranges.

| Processor | Width (µs) | Apply before (ms) | Apply after (ms) | Change | Throughput before (MS/s) | Throughput after (MS/s) | Change |
|---|---:|---:|---:|---:|---:|---:|---:|
| Python | 0.304 | 0.101 [0.099, 0.113] | 0.287 [0.219, 0.338] | +183.9% | 89.805 [80.360, 96.345] | 78.717 [58.839, 89.370] | -12.3% † |
| Python | 0.308 | 0.142 [0.102, 0.198] | 0.255 [0.183, 0.467] | +80.5% † | 99.948 [85.044, 113.559] | 85.114 [71.389, 96.889] | -14.8% † |
| Python | 0.310 | 0.146 [0.106, 0.321] | 0.244 [0.185, 0.370] | +67.6% † | 101.187 [86.638, 105.782] | 81.275 [59.620, 87.657] | -19.7% † |
| Red Pitaya | 0.304 | 46.874 [46.604, 65.232] | 6.652 [5.963, 7.098] | -85.8% | 17.969 [17.086, 20.668] | 18.281 [16.212, 21.514] | +1.7% † |
| Red Pitaya | 0.308 | 47.175 [46.419, 66.944] | 6.508 [6.065, 16.393] | -86.2% | 18.522 [17.651, 21.075] | 18.061 [17.305, 21.004] | -2.5% † |
| Red Pitaya | 0.310 | 46.891 [46.341, 86.725] | 6.532 [6.093, 16.575] | -86.1% | 18.252 [16.920, 20.971] | 19.281 [16.758, 20.068] | +5.6% † |

## Settings applied while changing width

These separate measurements alternate from a different width and leave the previous result empty in both versions. Python starts each measured apply with an empty resolver cache. The same 11-run protocol was used on the saved baseline and the updated implementation.

| Processor | New width (µs) | Apply before (ms) | Apply after (ms) | Change |
|---|---:|---:|---:|---:|
| Python | 0.304 | 0.037 [0.036, 0.039] | 0.036 [0.034, 0.056] | -3.8% † |
| Python | 0.308 | 0.047 [0.046, 0.059] | 0.045 [0.043, 0.057] | -4.3% † |
| Python | 0.310 | 0.048 [0.047, 0.051] | 0.046 [0.045, 0.056] | -3.4% † |
| Red Pitaya | 0.304 | 37.550 [37.150, 77.561] | 2.917 [2.881, 3.134] | -92.2% |
| Red Pitaya | 0.308 | 37.525 [37.131, 67.389] | 2.696 [2.660, 3.073] | -92.8% |
| Red Pitaya | 0.310 | 37.961 [37.167, 77.497] | 2.446 [2.404, 2.962] | -93.6% |

## Boundary and quadrature verification

| Width (µs) | Repeating bin sizes | Baseline board complete | Baseline board boundaries matching LOW oracle | Updated Python and board complete | Max absolute CH1 difference | Max absolute CH2 difference |
|---|---|---:|---:|---:|---:|---:|
| 0.304 | 38 | 82,230 | 25,024 | 82,233 | 8.88e-16 | 1.78e-15 |
| 0.308 | 38, 39 | 81,163 | 8,466 | 81,164 | 1.78e-15 | 3.55e-15 |
| 0.310 | 38, 39, 39, 39 | 80,640 | 8,411 | 80,643 | 2.66e-15 | 3.55e-15 |

For the updated processors, every start sample, bin length, and completed-bin count agreed with the independent integer boundary calculation for each LOW interval. Both quadrature rails agreed at the existing tolerance, `rtol=2e-11` and `atol=1e-16`, in the benchmark's ADC-count projection units.

The 308 ns regression checks passed in Python and on the board. The 20 + 57 LOW split produced one 38-sample bin and one 39-sample bin. With one HIGH sample between the groups, the first 20 were discarded and the final 57 produced one 38-sample bin.

[Raw timing runs, recording hashes, and parity results](dynamic-temporal-bin-replay-2026-11-18.json)
