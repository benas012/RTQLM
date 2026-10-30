# Receiver UI update · 29 October 2026

The receiver now gives the waveform and mode analysis more space, with larger text,
clearer channel readings, and fewer permanent controls around the plots. The
measurement calculations and board processing are unchanged.

## Interface

- A coordinated dark palette with teal actions and consistent channel colours.
- Larger latest-estimate readings with the plotted quantity and units.
- Actual samples per mode and actual mode duration beside the readings.
- A draggable divider with minimum space reserved for the analysis chart.
- **Setup…** opens the common calculation and collection settings in a compact window.
- **Display controls…** opens waveform zoom, offsets, cursor and display-trigger controls.
- **Full settings…** separates acquisition, temporal mode, templates/formulas and live
  preview into tabs. Long forms scroll within the page.
- **Diagnostics** retains counters, voltage ranges and operation feedback.
- Acquisition state stays separate from messages produced by display adjustments;
  sampling rate is displayed in samples per second.

## Verification

The full suite passed with **448 passed, 18 skipped**. After the final status and
layout refinements, **50 focused checks passed**. Lint and whitespace checks passed.
Layout checks cover 1280 × 900, 1180 × 840 and 960 × 740, including checks that
buttons and fields remain inside the main window and compact setup windows.

Hands-on checks used the Windows computer-use interface and the real
`rp-f0f9eb.local` board with OUT1 → IN1 and OUT2 → IN2:

- Selected the board and connected/attached to its stream.
- Inspected mode setup, the full acquisition and temporal-mode pages, and display controls.
- Recorded and finished a reference using the settings editor.
- Changed time zoom and restored the full waveform using Fit.
- Paused and resumed the waveform display.
- Confirmed both channel readings and mode timing remained visible.

The saved live snapshot shows 122.07 kS/s, 16 samples per mode, zero reported
FPGA sample loss on both channels and zero analysis packet drops.

This is an **electrical loopback reference test**, not optical quantum squeezing.
The lower-amplitude noise outputs remain enabled at a programmed 0.0125 V peak
setting. Stop them with **Outputs… → Stop both outputs**. Use a fresh vacuum
shot-noise calibration before interpreting an optical measurement.

## Preview and evidence

- [Original interface](../tmp/ui-redesign-20261029/before.png)
- [Updated live interface](../tmp/ui-redesign-20261029/after.png)
- [Full settings editor](../tmp/ui-redesign-20261029/full-settings.png)
- [Display controls](../tmp/ui-redesign-20261029/display-controls.png)
- [Live counters and measurement snapshot](../tmp/ui-redesign-20261029/live-verification.json)
