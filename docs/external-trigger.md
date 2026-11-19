# External digital trigger: first implementation

## Plan

1. Keep digital input polling in the engine, independent of ADC sample transport.
2. Use one SSH connection and a small, temporary board-side Python reader. Each request reads
   one extension pin; nothing is installed on the board and no digital stream is started.
3. Start with a 10 ms requested polling period. Make it adjustable from 1 to 1,000 ms. Actual
   timing also includes network and operating-system delays. Both high and low portions of a
   pulse must exceed the actual interval to reliably observe successive edges.
4. Compare consecutive successful 0/1 readings. The initial reading establishes a baseline;
   it never fires a trigger. Support rising, falling, and either edge, with rising as default.
5. Expose configuration, current level, trigger count, and observation times through REST.
   Stop and release the reader on disable, disconnect, profile changes, and streaming ADC startup.
   Buffered acquisition uses the compatible v0.94 FPGA and supports concurrent input polling.
6. Test edge detection, failures, shutdown, API validation, and the connected board's reader.

## Firmware finding

The connected STEMlab 125-14 Pro v2.0 runs OS 3.00-799. Its normal `v0.94` image supports the
vendor `rp_DpinGetState` API. Its installed `stream_app` FPGA revision is
`024eef5a085e40ec89116362b7828881a123c2e7`. That image has no live GPIO input readback register;
its GPIO `0x20`/`0x24` registers are acquisition timestamps, not pin levels. The normal digital
pin API must not be used against that image. See the
[installed streaming GPIO register decoder](https://github.com/RedPitaya/RedPitaya-FPGA/blob/024eef5a085e40ec89116362b7828881a123c2e7/prj/stream_app/rtl/rtl/rp_gpio/rp_gpio.sv)
and [vendor pin API](https://github.com/RedPitaya/RedPitaya/blob/master/rp-api/api/src/rp.cpp).

This implementation checks the loaded image before initializing or reading the pin and reports
unsupported firmware explicitly. Concurrent GPIO polling and stock TCP ADC streaming require a
firmware readback extension. Buffered DMA acquisition uses v0.94 and can poll E1 concurrently.
This version detects edges for clients to consume; it does not gate
ADC frames or analysis windows, and observation times are not ADC sample timestamps.

## API and operation

- `GET /api/v1/devices/{deviceId}/trigger`: cached state; does not contact the board.
- `PUT /api/v1/devices/{deviceId}/trigger`: replace configuration, for example:

  ```json
  {"enabled": true, "pin": "DIO0_P", "edge": "rising", "pollIntervalMs": 10}
  ```

- Disable with `{"enabled": false}`. Reconfiguration resets the count and establishes a new baseline.
- Pin choices are `DIO0_P` through `DIO7_P` and `DIO0_N` through `DIO7_N`. The pin must already
  be configured as an input. The reader never drives an extension pin or changes its direction.
- Polling can be enabled for a physical device without connecting its ADC source. Connecting or
  starting stock TCP acquisition stops polling before its FPGA configuration can change.
  In buffered mode, polling can run while acquiring and remains enabled across Stop/Start.
  Simulator profiles
  return 409. Unsupported firmware or SSH/read failures return 503 and leave `level: null`.
- SSH uses `root`, the user's existing keys, and known-host verification. If password access is
  needed, set `QL_TRIGGER_SSH_PASSWORD` in the engine environment. There is no factory-password
  fallback. Passwords are not stored in profiles or exposed by the API.
- A read error stops polling and clears the level; re-enable explicitly after fixing the cause.
  `lastPollIntervalMs` reports the latest actual interval between successful observations.
  A persistent high input counts once. Missed pulses cannot be reconstructed.
- This is for slow control signals. For a 5 ms high / 5 ms low signal, a 10 ms poll is inadequate;
  use hardware edge capture if every edge or precise alignment is required.

## Native receiver

Use **GPIO (E1)…** beside Diagnostics, or **Signal > GPIO (E1) input…**.
Select a board, choose a pin and polling interval, then click **Read input**.
The window shows High/Low, the actual interval between reads, the number of observed transitions
(either edge), and the last successful read time. An error or disabled reader shows an unknown
level. **Stop reading** releases the board reader; closing the window leaves the engine monitor
running. Disconnect also releases it. The selected board is pinned when the window opens.

Default wiring for STEMlab 125-14 PRO Gen 2: **DIO0_P is E1 pin 3**, with a common ground on
**E1 pin 25 or 26**. Use 0 to 3.3 V logic; a 5 V signal needs level conversion. The reader checks
that the pin is already an input and does not drive it. See the
[vendor E1 pinout](https://redpitaya.readthedocs.io/en/latest/developerGuide/hardware/GEN2/125-14_Gen2_Pro/top.html#e1-connector-digital-i-o-can).

This monitor is intended for slow control levels. It does not capture a digital waveform,
measure reliable pulse widths/frequency, or synchronize 308 ns temporal modes.

## Verification on 2026-10-21

On the connected board's normal scope image, a two-second check made 143 observations of
`DIO0_P`. It remained high, with no spurious trigger from the initial high level. A requested
10 ms period produced a mean actual interval of 14.0 ms and a maximum of 22.6 ms in that check.
The reader stopped cleanly. No physical edges were injected; automated tests cover rising,
falling, and either-edge sequences, repeated levels, failures, and cleanup. This short timing
check does not establish a worst-case latency bound. The unit/integration suite passed 386 tests.
