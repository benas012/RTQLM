# Server stream on the existing v0.94 image

The receiver now selects engine results and calibration commands for `server-stream`
and `buffered-server-c`, as well as the ARM backend. Raw display packets are not
reprocessed in the receiver, which would bypass the E1 gate. The default board
profile is saved at decimation 2 with server streaming enabled. Restart the engine
and receiver to load it, or select **Use 62.5 MS/s server stream** in receiver setup.
The existing **Use 125 MS/s buffered captures** option selects the ARM comparison.
Previous local settings are backed up in `tmp/before-server-stream-20261102`.

The v0.94 DMA producer keeps both ADC rings running and transmits owned, paired
312,500-sample int16 channel blocks (1,250,000 bytes, 5 ms). This is a custom host
producer using the existing FPGA, not the stock `stream_app` FPGA/application.
No FPGA image or optical/DAC settings were changed. The implementation reuses the
C acquisition adapter; continuous-stream calculations use the RTQLM Python/NumPy
engine. The separate buffered backend still reuses the C calculation engine.

Analysis retains the existing formulas, configurable rail delay, calibration and
variance implementations. The saved setup uses 308 ns lab temporal weighting,
19/20 sample mode widths at 62.5 MS/s, zero periodic lock time and 5 ms collections.
Collection duration remains editable. Transfer chunks are always 5 ms; collection
windows can span chunks only when samples and the accepted gate remain continuous.
Any missing interval resets partial modes and rolling windows. Thus delivered
blocks/s must not be presented as complete, gate-qualified measurement bins/s.

## Synchronization and loss

The producer timestamps the ADC start call in board time, then uses absolute sample
indices. Both rails use the same ring position. It tracks ring wraps locally; a
monitor stall that makes wrap accounting ambiguous aborts the stream. Transport
backpressure causes explicit skipped intervals before copying overwritten regions.
Copy overlap with overwrite is rejected. Header sample indices and cumulative
skipped-sample counters let the receiver mark gaps without relying on arrival time.
These counters describe software ring skips, not an independent FPGA loss counter.

E1 physical pin 3 is DIO0_P, a digital HIGH/LOW input, not a voltage measurement.
The mapping and official references are recorded in
[the buffered report](buffered-server-2026-11-01.md#e1-physical-pin-3).
A separate board process polls nominally every 1 ms during capture and transfer;
timestamped observations are sent over SSH. Missing observations, LOW and timing
uncertainty exclude samples. Poll gaps over 2 ms split accepted intervals.

The current 1 ms clock margin is conservative software policy, **not a verified
hardware edge-alignment bound**. A pulse entirely between polls can be missed.
A 5 ms HIGH pulse does not guarantee a full 5 ms accepted window, or exact 308 ns
phase alignment to that pulse. Physical pulse-edge alignment remains unverified.

## Real-board evidence

[Integrated API run](../../benchmarks/results/engine-stream-live.json) exercises raw WebSocket
decoding, both single-mode and cluster results through the receiver decoder,
diagnostic calibration commands, and stop/disconnect. Diagnostic calibration is
not an optical vacuum reference and is not installed as the user's calibration.

[Stage benchmark](../../benchmarks/results/ring-stages-live.json) records 100 blocks per case,
with/without local polling and with both server calculations. Reported read time
includes waiting for the producer and network, not only wire transfer. Board copy
and previous-send times are measured separately; send completion means accepted
by the socket, not acknowledged by the receiver. Per-case results vary with load.

The final integrated run measured 18.68 blocks/s and 23.35 MB/s overall. Its local
poll intervals averaged 1.10 ms, p95 1.60 ms, maximum 21.87 ms. It reported
1,812,562,104 skipped samples per rail. A stage run measured
approximately 27–29 ms board copy time and 20–28 ms send time. Server calculation
is not the only bottleneck. **62.5 MB/s continuous end-to-end throughput has not
been achieved.** The prior buffered benchmark's 67 MB/s payload-transfer figure
excludes its capture/processing/dead time and must not be applied to this stream.
The continuous two-channel source produces 250 MB/s at the requested sample rate;
this implementation does not promise transfer of every continuously produced sample.

## Remaining verification and limitations

- The prior identical-buffer ARM/x86 C parity and half/full-rate assessment apply
  to the buffered C path. They do not establish identical-sample parity for this
  newly integrated Python streaming path. That comparison remains required.
- Half-rate scientific equivalence to full rate is not established; see the
  buffered report. Recalibrate at the selected rate with the appropriate reference.
- ADC channel phase and the software ADC-to-GPIO epoch still need independent
  physical timing validation. Synthetic LOW/missing-observation exclusion is tested.
- Existing output settings are left untouched. Receiver output-control commands
  are not implemented by the new continuous DMA source; use the existing backend
  for output control. This is not full output-feature parity.
- A physically guaranteed 1 ms GPIO interval and exact edge-triggered 5 ms bins
  have not been achieved. Status exposes actual interval statistics.

Build board helpers with `make stream-board` in `native/redpitaya_nullifiers`.
The configured deployment uses `/root/rtqlm-buffered-server-20261101/ring-server`
and `gpio-monitor`, SSH host-key verification and authenticated TCP port 18904.
An unauthenticated listener expires after 10 seconds. Metadata and payload sizes
are bounded; truncated payloads, invalid timing and backward timelines fail closed.
