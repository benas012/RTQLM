# E1 capture for the original STEMlab 125-14

Target: original 14-bit, two-input STEMlab 125-14 with Zynq 7010, model directory
`z10_125`. This is a separate build/package of the common Z10 v0.94 sources with
the existing RTQLM E1 modification. It is not a Z7020 or 125-10 qualification.

Hardware validation has **not** been performed on an original board. The build
manifest records synthesis/routed timing and simulation results separately from
that missing hardware test. No board is contacted by the build or packaging tools.

## Offline validation, 2026-11-12

| Check | Result |
| --- | --- |
| Synthesis and final routed timing | Passed; setup slack 0.014 ns, hold slack 0.008 ns, zero failing endpoints |
| E1 synchronizer | Both registers retain `ASYNC_REG`; stage-to-stage slack 7.238 ns |
| E1 physical pin assignment | E1 pin 3 / DIO0_P / G17 / LVCMOS33 |
| E1 packing simulation | 20,000 random/diagnostic ADC pairs; all ADC bits preserved |
| Arithmetic simulation | 50,000 random/overflow cases passed |
| Full waveform-generator equivalence | 100,000 cycles match the pinned original RTL |
| SD installer | 9 unit tests passed, including backup and rollback; extracted-package installation passed on a mock SD layout |
| Physical original-board test | Not performed |

This fresh build produced the same `fpga.bin` payload as the existing Gen 2
build (SHA-256 `52d4a29b6d73f402d2cacd2609e483524c84c5f0e7e34cf976a9f814d751f2d6`).
Both use the common Z10 design and the same FPGA part. This package provides the
original-board deployment identity and installer, including preservation of the
original model's device tree; identical logic does not replace physical validation.

## Behavior

E1 physical pin 3 (`DIO0_P`, FPGA pad G17) is synchronized on the ADC clock and
recorded in bit 14 of both ADC words. Bits 0–13 retain all signed ADC counts;
bit 15 is the format marker. Tagged acquisition uses raw counts at 125 MS/s and
bypasses stock calibration/filter/decimation. ARM processing handles offset and
shot-noise normalization, masks E1 HIGH during measurement, and uses both E1 states
during shot-noise calibration. The on-card C worker does not need new formulas.

The standard BRAM waveform generators remain available. DDR-to-DAC waveform
playback is disabled, matching the existing RTQLM capture image. The GPIO history
tap defaults to 5; its physical analogue-to-E1 timing offset still requires a
shared analogue/digital transition test on the actual board.

## Build from the RTQLM repository

Requires the pinned upstream checkout under `fpga/e1_sync/upstream`, Python, Git,
and Vivado 2025.1. The isolated source/build tree is `tmp/o125` under the RTQLM
repository, keeping generated IP paths within Windows tool limits.
Rebuilding requires the full RTQLM repository; the extracted package's SD-card
installer works independently with Python and the included `image/` directory.

```powershell
.\.venv\Scripts\python.exe fpga\e1_sync\original_125_14\build.py
.\.venv\Scripts\python.exe fpga\e1_sync\original_125_14\simulate.py
.\.venv\Scripts\python.exe fpga\e1_sync\original_125_14\package.py
```

The build uses `xc7z010clg400-1`, `Z10`, and `RTQLM_E1_SYNC`; it does not modify
the existing Gen 2 project's images. Packaging rejects failed timing, mismatched
target/pin/synchronizer checks, and missing or stale image/simulation evidence.

## Prepare an SD card without the older board

Shut down and power off the RP before moving its SD card to a card reader.
Extract the package and identify the mounted **boot partition** containing
`fpga/z10_125/v0.94/`. Run a preview first, replacing `E:/` with that partition:

```powershell
python install_sd.py --boot-root E:/
python install_sd.py --boot-root E:/ --install
```

The installer backs up `fpga.bin`, `fpga.bit`, `git_info.txt`, and `fpga.dtbo` to
`rtqlm-backups/z10_125/<installation-id>/` on that card. It verifies hashes before
replacement and rolls back replaced files if a later replacement fails. It changes
only the three image/metadata files in `fpga/z10_125/v0.94/`; it preserves that
model's existing `fpga.dtbo` and leaves the Gen 2 model directory unchanged.
Safely eject the card after installation. This stages files only; it does not
flash or load a running FPGA.

The C executable and its sources already reside on the transferred card. If the
worker is missing, the laptop engine can compile/install it on connection when
the Red Pitaya SDK/compiler are available. A different board hostname must also
be reflected in the saved connection and `QL_NATIVE_NULLIFIER_HOSTS` setting.

## Hardware check still required

On the original board, verify the detected model, boot, E1 signature `0x45513101`,
ADC loopback, DMA capacity for the requested capture, and known HIGH/LOW intervals.
Check analogue/E1 edge alignment and take a new shot-noise reference. Preserve
the backup until these checks pass. To restore, copy the three original
image/metadata files from that card's backup to the same `z10_125/v0.94` directory
with the RP powered off; retain its factory device-tree file.

## References

- Pinned source: https://github.com/RedPitaya/RedPitaya-FPGA/tree/c3fc4f6ebebf9b24f008ecb606eb7a3a23f62422
- Official model/build mapping: https://github.com/RedPitaya/RedPitaya/blob/master/Makefile.x86
- Original-board source and pin changes: the package's `source/upstream.patch`.
