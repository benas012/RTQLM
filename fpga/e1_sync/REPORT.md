# Real-board result — 8 November 2026

The custom **Release_2026.1** FPGA image is installed in the burner SD's v0.94
location and has been tested on the connected **STEMlab 125-14 Gen 2 Pro,
Zynq 7010**. A subsequent real reboot loaded the same image, verified by its
signature and the SHA-256 hashes of both installed FPGA files.

The capture returns **CH1, CH2 and E1 physical pin 3** on one FPGA sample index
at **125 MS/s**. A 50 ms record contains **6,250,000 values per stream**.
E1 is captured in the FPGA; it is not polled by ARM software.

## Hardware verification

- OUT1 → CH1 and OUT2 → CH2, both running the saved 1 V, 1 kHz sine settings.
  Both measured spectral peaks were 1,000 Hz in all five benchmark captures
  and the capture after reboot.
- E1 pin 3 remained an input and was HIGH for all 6,250,000 samples in each
  real capture. Its wiring was not changed.
- Internal diagnostic captures passed for 64, 65, 127, 128, 129, 1,023, 1,024,
  1,025, 65,537, 6,250,000 and 8,388,544 samples. The receiver checked every
  counter increment, channel complement, digital tag and format marker.
- Both channels reported matching trigger positions. Non-multiple-of-64
  requests were rounded up for DMA and trimmed to the exact requested length.
- After reboot, another 6,250,000-sample diagnostic and real loopback capture
  passed. The control register was restored to `0x500` (ordinary acquisition),
  E1 remained HIGH/input, and both saved output settings were restored.
- The boot partition is read-only again. Factory FPGA backups remain on this
  PC in `backup/` and on the board in `/root/rtqlm-e1-sync/stock-image`.

## Measured speed

Medians of five real 50 ms captures; 667 MHz ARM, full-duplex 1 Gb/s Ethernet.
This benchmarks the **raw three-stream capture test**, including validation.
It does not measure nullifier/variance computation in the separate processing
service.

| Stage | Median |
| --- | ---: |
| Acquire the 50 ms record | 50.38 ms |
| Read/copy the two packed DMA rails on ARM | 178.62 ms |
| Check markers and shared E1 tags on ARM | 99.22 ms |
| Capture + ARM read/check | **328.28 ms** |
| Capture through receipt of all raw data over TCP | **645.07 ms** |
| Host unpacking and validation | 111.11 ms |
| Complete one-shot API call, including SSH process startup | 4,010.28 ms |

The TCP payload is **25,000,000 bytes**: two 16-bit rails with E1 packed in
their spare bits. The decoded NPZ holds two `int16` ADC arrays and one `uint8`
logic array. SSH launches the test process; the sample payload travels over
TCP. The one-shot launcher adds roughly 3.25 seconds of startup/cleanup beyond
capture, transfer and decoding. The component medians are measured separately.

Raw results are in [the external benchmark results](../../../benchmarks/results/hardware-results.json);
the reboot checks are in
`reboot-results.json`. The first real NPZ save took 50.05 ms and is outside
the capture timing.

## Files

- `captures/real-50ms.npz`: benchmark record with the three full-rate arrays.
- `captures/real-50ms-after-reboot.npz`: verified record captured after reboot.
- `captures/real-50ms.png`: first 5 ms preview, displayed at 1 µs intervals.
- `image/red_pitaya.bin` and `image/red_pitaya.bit`: installed image copies.
- `upstream.patch`, `rtl/`, `board/` and `capture.py`: reproducible source.
- `README.md`: build, capture, register and stock-image restore instructions.

```python
import numpy as np

with np.load("fpga/e1_sync/captures/real-50ms-after-reboot.npz") as record:
    ch1 = record["CH1"]       # int16, signed 14-bit ADC counts
    ch2 = record["CH2"]
    e1 = record["E1_PIN3"]   # uint8, 0 or 1
```

## Timing and limits

Vivado 2025.1 reports zero setup and hold violations against the declared
constraints: WNS **+0.014 ns**, WHS **+0.008 ns**. The two E1 synchronizer
registers retain `ASYNC_REG`; their internal path is timed. Existing upstream
warnings for unused ports, I/O register placement and daisy/housekeeping logic
remain in the saved reports. This is an experimental capture image.

The GPIO synchronizer/history and atomic sample packing are verified, but the
**physical E1-to-ADC edge offset is unmeasured** because E1 stayed HIGH as
requested. The default history tap is 5, a nominal pipeline match; exact edge
alignment requires a shared switching signal and a physical measurement.
Metadata therefore records `physicalTimingOffsetVerified: false`.

Tagged capture bypasses acquisition calibration/filtering/decimation and returns
raw ADC counts at decimation 1. Ordinary acquisition remains the reset default.
DDR waveform playback to DAC outputs is disabled in this image; ordinary BRAM
waveform generation is retained and was tested. The generator arithmetic changes
matched the original over 100,000 simulated cycles, in addition to the 20,000
E1-packing and 50,000 arithmetic test cases.
