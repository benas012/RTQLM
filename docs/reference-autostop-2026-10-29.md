# Automatic reference calibration · 29 October 2026

**Record** now records a reference for ten seconds and finishes automatically.
The progress bar on the right shows the remaining time and fills completely when
the reference is ready. The main view and full settings editor share one countdown.
**Finish** remains available for stopping early; **Clear**, configuration changes
and acquisition disconnect cancel the countdown.

The timer uses elapsed monotonic time. A new recording replaces the earlier timer,
and the finish command remains tied to the board on which recording began. At the
deadline, insufficient data reports a failure and clears the incomplete reference.
Closing the app aborts an active recording.

## Verification

- Full suite: **459 passed, 18 skipped**; lint passed.
- Tests cover the ten-second deadline, early finish, clear, restart, no-data failure
  and changing the selected device while a reference is being recorded.
- The real Red Pitaya was started through the full settings UI. Read-only status
  polling observed automatic completion after **10.094 seconds**, without pressing
  Finish, with 74,725 reference modes on each channel. Polling adds timing uncertainty.
- The progress bars in both windows were visually checked while recording and after
  completion. The updated receiver remains open on the electrical loopback demo.

[Timing evidence](../tmp/reference-autostop-20261029/timing-verification.json) ·
[Progress preview](../tmp/reference-autostop-20261029/running-progress.png)
