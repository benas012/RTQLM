# Receiver connection recovery · 29 October 2026

The reported failure was reproduced in the live UI. The engine reported
`cannot listen on native streaming port 18902`. An earlier `rp-nullifierd`
process still owned that port after its command connection was abandoned.
The receiver also disabled Connect whenever the display WebSocket was open,
including when the board acquisition was faulted.

## Fix

- New command sessions identify their process ID and Linux process start time.
- After a command transport failure, reconnect releases only that earlier process,
  checking its PID, start time and executable before signalling it. This prevents
  an abandoned listener from blocking the replacement session.
- Stream preparation retries a lost transport once. Rejected commands remain errors.
- Command connections send keepalives. Shutdown preserves the original transport
  error instead of dereferencing a client that the failure handler already cleared.
- The receiver offers **Retry connection** for a faulted board even while its display
  WebSocket is still open. Retrying reuses that display connection.
- New command sessions discard the previous reference calibration.

The verified legacy process holding port 18902 was stopped. The local engine and
receiver were reopened with the fix; no board reboot or FPGA change was needed.
The saved mode and timing settings were retained.

## Verification

- Full suite: **453 passed, 18 skipped**.
- Targeted connection tests: **31 passed**; lint and targeted type checks passed.
- Real-board recovery test on spare port 18912 reproduced a stranded process that
  retained its listener. A replacement session acquired the same port, with its
  reference cleared. The main measurement stream continued and advanced 16 frames
  during that check.
- The receiver was reconnected through its visible UI and fresh frame counters
  confirmed acquisition resumed. The final check advanced 22 frames in three
  seconds, with zero reported FPGA sample loss on both channels and zero analysis
  packet drops. A new electrical reference was recorded and both channel dB
  readings were finite.

See [real-board recovery evidence](../tmp/ui-redesign-20261029/recovery-verification.json).
The [final live counters](../tmp/ui-redesign-20261029/recovered-live-status.json) and
[repaired UI preview](../tmp/ui-redesign-20261029/recovered-live.png) were saved.
The displayed signal is an electrical loopback demonstration, not optical quantum
squeezing. Stop the demonstration outputs with **Outputs… → Stop both outputs**.
