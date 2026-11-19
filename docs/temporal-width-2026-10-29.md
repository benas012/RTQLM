# Temporal-width controls — 2026-10-29

Both Setup and Full settings now edit **T width (µs)**. **Samples / mode** is a
read-only preview derived from the selected ADC rate. The preview also reports
the actual sampled duration, which can differ from the requested width because
bins contain whole samples. Changing the selected rate keeps the requested width.

The lab mode preserves its integer-truncation convention; flat and plain
Gaussian-linear modes use nearest-sample rounding. Applying settings checks the
configured minimum, the 4096-sample limit and any explicit template length.
The API retains explicit `modeSamples` for older clients; the UI sends `null`
and lets the processor derive the count from `modeWidthSeconds`.

Existing fixed-count settings are displayed at their equivalent time width when
a device rate becomes available. The live configuration was migrated from
56 explicit samples to **458.752 µs**, preserving its actual bin duration at
125 MHz / 1024 = 122070.3125 samples/s. The old nominal 308 ns value had been
overridden by the explicit sample count.

On `rp-f0f9eb.local`, changing T width through the actual UI to **262.144 µs**
produced **32 samples** in the ARM C processor, then restoring **458.752 µs**
produced **56 samples**. Both commands contained `modeSamples: null`.
The board used the existing FPGA streaming application.

Continuous reference calibration now processes short complete-mode batches,
independently of the longer display history. The UI's automatic reference
finished after **10.015 s**, accepting **21,315 modes per channel** at the
restored width. The final stream reported zero processing drops, zero FPGA
loss packets and no processor error. The connected DAC-to-ADC noise signals
remain an electrical demonstration, not an optical squeezing measurement.

Validation: **463 Python tests passed**, 24 optional native tests skipped in
that run; all **24 Python/C parity cases** passed separately on the board;
**23 native command tests** and **7 TCP stream tests** passed on the board.
Ruff and the processing module's mypy check passed. Evidence is retained under
`tmp/mode-width-20261029/`; the previous board release is retained for rollback.
