# 308 ns receiver capture — 29 October 2026

The original Apply error correctly rejected a 308 ns bin at 122.07 kS/s: it
contained zero sampled points. Removing that check would not create a valid mode.
The receiver now exposes the existing high-rate DMA acquisition through its normal
engine and UI.

In **Mode analysis → Setup**, enter **0.308 µs** and click **Use 125 MS/s buffered
captures**. This stops the selected acquisition, selects buffered capture with
decimation 1, transfers the requested analysis settings and reconnects the board.
The choice persists in the device profile as `acquisitionMode: buffered`.
The other saved board is unaffected.

Each 625,024-sample acquisition lasts 5.000192 ms at 125 MS/s. Projection, calibration,
nullifiers and variances are calculated from the full capture on the RP's ARM C
processor. The PC receives only the first 1,024 ADC samples per rail as a waveform
preview, and up to 500 points from each derived trace. Preview compaction does not
change the statistics or their timebase. A fresh waveform stream ID prevents the
viewer from joining separate acquisitions. The UI explicitly labels capture gaps;
these acquisitions do not provide a continuous optical mode train.

The requested width of 308 ns gives 38 whole samples and an actual 304 ns bin under
the existing integer-bin convention. This does not implement fractional-sample
optical-clock alignment. The existing temporal shape, basis, formulas and variance
settings are retained; selecting a width does not automatically choose a lab shape.

Buffered acquisition uses the standard `v0.94` FPGA image. The engine first checks
that no raw ADC client is attached and remembers the prior image. Disconnect ends
the C command session and releases its SDK/DMA mappings before restoring that
image. Reconnection and restoration to `stream_app` were tested on the real board.
Lost command sessions retain PID/start-time checks before cleanup or restoration.

Standard sine, square, triangle, sawtooth, DC and noise outputs use the SDK generator
in buffered mode. Generator initialization resets its state once per session and
triggers enabled channels. DAC streaming rate/buffer settings and deterministic
noise seeds do not control this generator. Arbitrary output buffers still require
streaming acquisition. Disconnect disables both generated outputs.

Validation: 469 Python tests passed; 24 optional native comparisons were skipped in
that run and passed separately on the board. The final native release passed 24
command tests and 7 TCP-stream regression tests and built with strict compiler
warnings. Ruff and mypy checks for the changed engine modules passed. Actual UI
clicks started the new acquisition and successfully applied 0.308 µs; the board
reported 125 MS/s, 38 samples/mode and 304 ns actual duration. Live calibration and
electrical loopback evidence is retained in `tmp/buffered-308ns-20261029/`.

The final UI reference completed automatically after 10.203 seconds and accepted
328,960 modes per rail. Halving the generated 6 MHz sine amplitude from 0.2 V to
0.1 V gave median readings of −6.032 dB (CH1) and −6.029 dB (CH2), compared with
the expected −6.021 dB. This verifies the electrical variance-ratio path, not
optical squeezing or vacuum calibration. Reconnection clears the reference;
record a fresh reference before interpreting dB. Use optical vacuum shot noise
for an optical measurement, rather than this electrical test reference.
