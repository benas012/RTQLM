# CH1, CH2 and E1 pin 3 acquisition

Separate FPGA/capture project for the connected **STEMlab 125-14 Gen 2 Pro,
Zynq 7010** (`z10_125_pro_v2`). Base: official `Release_2026.1`, commit
`c3fc4f6ebebf9b24f008ecb606eb7a3a23f62422`, built with Vivado 2025.1.

**Installed and tested on the real board, including a reboot.** See
[REPORT.md](REPORT.md) for the measured timings, sample files and verification.

For the **original STEMlab 125-14** (`z10_125`), see
[the separate build and SD-card installer](original_125_14/README.md). That target
has no physical-board validation; its package records that limitation explicitly.

The FPGA samples E1 physical pin 3 (`DIO0_P`, `exp_p_in[0]`) in the ADC clock
domain. It carries the digital value inside both ADC sample words through the
existing delay, FIFO and DMA path. ARM polling is not used. The Python API
returns `(CH1, CH2, E1_PIN3, metadata)` with equal-length arrays and one shared
sample index. Default: **6,250,000 samples per array, 50 ms at 125 MS/s**.

## Data format and timing limits

Each 16-bit DMA word contains signed ADC bits `[13:0]`, E1 in bit 14, and a
constant format marker in bit 15. The TCP payload contains the two packed rails
(25,000,000 bytes for 50 ms); the host unpacks the third array. `CH1` and `CH2`
are `int16` values from -8192 to 8191; `E1_PIN3` is `uint8`, 0 or 1.

Tagged acquisition uses **raw ADC counts**, bypassing the factory acquisition
calibration, filter and decimation stages together. Use 125 MS/s, decimation 1.
The normal acquisition path remains available with control bit 0 cleared, but
ordinary stock applications must not interpret tagged DMA words as plain ADC
samples. Analogue trigger filtering is unchanged; this capture program uses an
immediate trigger only.

DAC output streaming from DDR is disabled in this experimental image: the
upstream implementation failed timing at 250 MHz on the Zynq 7010. The ordinary
BRAM waveform generators used by the current OUT1/OUT2 loopback remain active.
The failing logic is removed, rather than excluded from timing analysis.
The extended waveform-pointer arithmetic also uses carry-save and carry-select
addition to shorten its combinational path without reducing frequency precision
or changing the mathematical result. Its simulation covers 50,000 random and
overflow cases.
The complete modified waveform generator also matches the original RTL over
100,000 simulated cycles exercising fractional frequency, bursts, delays and
wrapping. See `simulation-results.json`. To reproduce that comparison, run
`sim/prepare_asg_equivalence.py`, compile its generated SV file, and run the
`tb_asg_equivalence` simulation.

E1 passes through a two-register synchronizer and a configurable history delay.
The default history tap is 5 (nominally seven 8 ns clock edges to match the
ADC pipeline plus its input register). This is a starting alignment setting,
not a measured calibration. An asynchronous edge near the sampling boundary
can be assigned to an adjacent sample. Exact physical E1-to-ADC offset requires
a shared switching signal on E1 and an analogue input and suitable voltage
levels. **The user requested the current wiring and E1 held HIGH**, so that
physical edge-offset measurement is deliberately not claimed. Metadata records
`physicalTimingOffsetVerified: false`.

## Capture

From the RTQLM workspace on this PC, after installation:

```powershell
.\.venv\Scripts\python.exe fpga\e1_sync\capture.py --output fpga\e1_sync\captures\latest.npz
```

The NPZ contains `CH1`, `CH2`, `E1_PIN3`, and JSON `metadata`. SSH starts the ARM
process; **all sample data travels over TCP** on port 18913. Each connection
uses a fresh token. This standalone raw capture is separate from the existing
variance-only TCP processing service.

Use `--diagnostic` to substitute a shared FPGA counter, its complement, and
counter bit 5 for the inputs. The receiver verifies every sample increment,
both rails, and the digital tag, including counter and DMA-buffer wrapping.
Diagnostic acquisition does not drive any external pin. Use `--delay 0..127`
to select the E1 history tap. Never connect another output to the externally
driven E1 pin during these tests.

## Rebuild

```powershell
git clone --depth 1 --branch Release_2026.1 https://github.com/RedPitaya/RedPitaya-FPGA.git fpga/e1_sync/upstream
.\.venv\Scripts\python.exe fpga\e1_sync\prepare_project.py
Set-Location fpga\e1_sync\upstream
& 'C:\Programs\Xilinx\2025.1\Vivado\bin\vivado.bat' -nojournal -mode batch -log ../build.log -source red_pitaya_vivado_Z10.tcl -tclargs v0.94 RTQLM_E1_SYNC
```

The exact changes are also exported in `upstream.patch`; the new module is
kept in `rtl/rp_e1_tag.sv` outside the disposable checkout. Build outputs live
in `upstream/prj/v0.94/out`. Simulation: run `xvlog -sv
../upstream/prj/v0.94/rtl/rp_e1_tag.sv tb_e1_tag.sv`, `xelab tb_e1_tag -s
e1_tag_sim`, and `xsim e1_tag_sim -runall` from `sim`. Decoder checks:
`python -m unittest discover -s fpga/e1_sync -p test_decode.py -v`.

After the build, run `python fpga/e1_sync/check_build.py` from the workspace to
verify routed timing and copy the images and reports to `image/`. Deployment
requires this timing result and the matching image hashes. For subsequent RTL
edits, run Vivado with `-source rebuild.tcl` from this folder to reuse the vendor
IP builds, use `Performance_ExplorePostRoutePhysOpt`, and include the E1
synchronizer timing/CDC reports. The deployment check uses the final post-route
optimization timing report when that strategy generates one.

## Install and restore

The connected replacement card's original FPGA files and hashes are preserved
in `backup/`. `deploy.py` also preserves an on-board copy in
`/root/rtqlm-e1-sync/stock-image`. It verifies the board model, backup and upload
hashes before replacing the v0.94 `.bit` and `.bin` files on the SD. The factory
device tree is retained because the AXI interfaces and memory map are unchanged.

```powershell
.\.venv\Scripts\python.exe fpga\e1_sync\build_board.py
.\.venv\Scripts\python.exe fpga\e1_sync\deploy.py
# Restore the original SD files and load them:
.\.venv\Scripts\python.exe fpga\e1_sync\deploy.py --restore-stock
```

Installation targets this task's explicitly authorized burner card. The image
is stored in the stock v0.94 location so subsequent v0.94 reloads use the custom
image. Loading a different FPGA application replaces it until v0.94 is reloaded.
The task-local host key in `board_access.py` pins the replacement SD; it does
not change the PC's global SSH host keys.

## Registers

Housekeeping base `0x40000000`: offset `0x110` reads signature `0x45513101`;
offset `0x114` is control. Bit 0 enables tagged raw DMA, bit 1 selects the
internal diagnostic pattern, and bits `[14:8]` select E1 history tap 0..127.
Reset value is `0x00000500` (ordinary acquisition). The ARM program restores the previous control
value and disables its DMA channels when finished. It leaves E1 as an input.

## Upstream references

- [Official Release_2026.1 FPGA source](https://github.com/RedPitaya/RedPitaya-FPGA/tree/Release_2026.1)
- [125-14 Gen 2 Pro hardware](https://redpitaya.readthedocs.io/en/latest/developerGuide/hardware/GEN2/125-14_Gen2_Pro/top.html)
- [LTC2145-14 ADC data sheet](https://www.analog.com/media/en/technical-documentation/data-sheets/21454314fa.pdf)
