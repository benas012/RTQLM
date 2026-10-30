# Buffered capture refresh — 29 October 2026

The receiver previously produced about 1.6 new frames/s using 625,024-sample
captures at 125 MS/s. The optimized application produced 73 new frames in
10.0038 seconds: **7.30 frames/s**, approximately 4.5 times faster. Capture size,
sampling rate, temporal width and variance window were retained. This is still
buffered acquisition with gaps, not a continuous mode train.

| Stage | Previous path | Optimized path |
| --- | ---: | ---: |
| Physical acquisition | 5.5 ms including polling | 5.5 ms including polling |
| Read and voltage conversion, both rails | about 298 ms | about 53 ms |
| C projection and variance calculation | about 23 ms | about 23 ms |
| Complete board command round trip | about 383 ms | about 137 ms |
| Extra pause between captures | 150 ms | removed |

The SDK's direct DMA regions are copied in bulk into reusable memory. A cached
lookup table performs the same integer calibration and float voltage conversion
as the SDK. It is keyed by ADC format, calibration values, input range and voltage
offset. Unsupported formats use the original SDK voltage reader. On the first
capture for each calibration key, every converted sample is compared bit-for-bit
with `rp_AcqAxiGetDataV`; a mismatch disables the optimization for that key.

The isolated board test additionally compared **1,048,576 values** with the SDK,
covering every 16-bit DMA word on both rails, both input gain settings, two voltage
offsets, and circular-buffer wrapping. All comparisons were bit-exact. Synthetic
words were confined to stopped DMA buffers in the isolated test process and were
not published as measurements.

The engine now shares the completed C result with the UI instead of requesting
the same result over SSH again. Each cached result retains its own analysis
configuration, is invalidated by processing commands, and can be read while the
next capture is in progress. Buffered mode polling and plotting can refresh every
50 ms; they do not create additional measurement frames.

Validation: 471 Python tests passed; the 24 optional native comparisons passed
separately after the replay executable was built. The final board release passed
24 native command tests, 7 stream regression tests, and the exhaustive SDK readout
comparison. Ruff and mypy checks passed. The live interval retained 125 MS/s,
625,024 samples/capture, 38 samples/mode and 304 ns actual bin duration; no service
frame drops, packet gaps or processing errors were reported. Evidence is under
`tmp/capture-fps-20261029/`.

The implementation follows the supported direct-memory API described in
[Red Pitaya's DMM documentation](https://redpitaya.readthedocs.io/en/latest/appsFeatures/remoteControl/deepMemoryMode.html)
and checks its calibration behavior against the installed SDK. The upstream
[acquisition implementation](https://github.com/RedPitaya/RedPitaya/blob/master/rp-api/api/src/acq_handler.cpp)
and [conversion implementation](https://github.com/RedPitaya/RedPitaya/blob/master/rp-api/api/src/convert.hpp)
were consulted; the installed library remains the comparison oracle.
