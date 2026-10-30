# Native streaming nullifiers on rp-f0f9eb.local

Validated on 26 October 2026 with OS 3.00-799, ARMv7, GCC/G++ 13.3 and the installed SDK.
The streaming path uses the stock `stream_app` FPGA/application. Calculation and JSON control
run in C on the board; only the diagnostic DMA adapter uses C++ for the vendor SDK.

## Normal receiver operation

The engine's local `.env` enables the native backend for `rp-f0f9eb.local`. The second saved
board keeps its existing backend. Restart the engine, connect in the receiver and press
**Start**. The engine opens the C command session over SSH, and the receiver sends its saved
nullifier settings. Formula, calibration, Start and Stop controls use that session.

The executable is stored on the SD card. No manual SSH session or installation after boot is
needed; no boot service has been installed. The C program forwards original continuous ADC
packets and calculates on a separate worker. Collection windows cross packet boundaries.
Formula changes reset statistics without stopping acquisition. Raw waveform/spectrum display
remains available, and the receiver displays derived C results without recalculating them.

Root SSH uses the computer's trusted Ed25519 key; other authorized keys were preserved.
The raw relay listens on 18902 with the stock raw service's network access model.

## Hardware verification and limits

The physical check exercised the existing engine HTTP/WebSocket interfaces, decoded C results
into the receiver result type, changed formulas during acquisition, ran calibration commands,
applied the saved receiver settings, stopped, restarted and disconnected.

- CH1 streamed at decimation 8: 15,625,000 samples/s. The lab template had four samples,
  an actual mode period of 256 ns.
- A 15-second run with 5 ms locking / 5 ms collection returned 981 completed calculations
  while the display was polled 53 times. Its final counters were 428 raw packets,
  **90 analysis-queue drops** and **41 packets with FPGA loss**.
- The additional saved-settings check used zero locking and 10 ms collection. Cumulative
  counters reached 720 packets, 120 analysis-queue drops and 157 packets with FPGA loss.
  The 25,000-mode history cap truncated the collection prefix; `prefixTruncated` was true,
  and the chart status identifies this limit.
- Formula changes retained the acquisition connection. Stop/Start produced fresh results;
  disconnect cleared queued results and ended the command session.
- Calibration commands produced finite coefficients and dB arrays. The input was **not a
  vacuum standard**; this verified software flow, not a scientific shot-noise calibration.

These checks establish continuous streaming and command integration, **not gap-free analysis
at this rate**. The processing queue is bounded. Overruns and FPGA loss discard partial
windows and accumulated derived statistics before calculation resumes. Both counters appear
in the chart status. A measurement must use settings that keep them stable. Template timing
must remain valid if the sampling rate changes.

Display requests select at most 500 points per derived array. `displayStride` preserves their
chart spacing; actual mode timing and computed statistics are unchanged. Full arrays remain
available through the native `latest` command without `maxPoints`.

Local validation passed 21 native command tests, six TCP streaming tests and nine SDK adapter
tests. TCP checks cover original-byte preservation, fragmentation, cross-packet windows,
continuous mode, live configuration, loss and stale-result prevention after disconnect.
Nine numerical configurations matched the Python reference. Relevant engine, device and
receiver regression tests passed.

## Installed release

`/root/rtqlm-nullifiers/current` selects:

```text
/root/rtqlm-nullifiers/releases/cf190a1188ef/redpitaya_nullifiers
```

Source archive SHA-256:

```text
cf190a1188eff161bafbe4635d522a69cefd2d58c15795fbcb79e94c8117d4c3
```

Earlier releases remain available, including the prior burst-only selection `3d43a31b91d0`.
The board's clock and boot settings were preserved.

## Manual commands and burst diagnostics

For manual JSON control, keep one SSH session open:

```powershell
ssh root@rp-f0f9eb.local /root/rtqlm-nullifiers/current/build/rp-nullifierd
```

The engine normally owns this session; do not start a second raw relay while it is connected.
See the [native processor guide](../native/redpitaya_nullifiers/README.md) for continuous
streaming, formula and calibration commands.

The older `capture` command remains available with the normal `v0.94` FPGA and streaming
stopped. It performs a diagnostic DMA burst with processing/rearm dead time. Earlier hardware
validation completed five two-channel 125 MS/s bursts in one session, including a formula
change. The adapter keeps SDK mappings open for the session and releases them once at exit,
avoiding an installed-SDK issue with repeated UIO release/reinitialization.
