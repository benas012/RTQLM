# Red Pitaya output signals

Select and connect a physical Red Pitaya in the receiver, then open **Outputs…** beside the display
controls, **Signal > Red Pitaya output signals…**, or **Red Pitayas > Output signals…**.
Each editor stays attached to the board named in its title, even when you switch receiver tabs.
Open another board's editor to control that board separately.

## Controls

OUT1 and OUT2 each have enable, waveform, frequency in Hz, **peak amplitude** in volts, DC offset,
and phase in degrees. Square waves also have a duty fraction; repeating noise has a random seed.
Available waveforms are sine, square, triangle, sawtooth, DC, uniform noise and arbitrary samples.
For DC, set the offset: its amplitude field is unused. An unchecked channel receives zero samples
while the other channel plays.

**Apply & start selected outputs** replaces the pair of signals together. Both channels use equal
length buffers and a single DAC start, preserving their requested relative phase on one board.
**Stop both outputs** stops that board's pair. Closing an editor leaves its outputs running.
Disconnecting the acquisition source performs output cleanup; restarting/stopping acquisition
can recreate that source, so reapply output settings afterward. Outputs do not automatically restart
after a disconnect or stream reconnection. Saved device profiles do not enable outputs on launch.

Amplitude is peak, not peak-to-peak: a 0.1 V sine has 0.2 V peak-to-peak. Settings are bounded by
`amplitude + abs(offset) <= 1 V`; verify the actual voltage at the input with your board's calibration
and load arrangement. These are nominal DAC scaling values, not a voltage measurement. Phase is a
waveform phase offset, not a desktop scheduling delay.

The engine uses the existing **3.x DAC streaming client and stock stream_app image**, through the
same board configuration connection as ADC acquisition. No new FPGA image or SCPI service is needed.
The source and isolated native worker both support the feature. Simulator profiles cannot drive
physical outputs. A missing DAC client or unsupported firmware returns an explicit error.

### Actual frequency and DAC rate

The two outputs share a DAC rate and buffer length. The receiver defaults to 15.625 MS/s and 65,536
samples. Buffers must be multiples of 64 samples, between 64 and 262,144 samples.

For the pinned STEMlab 125-14 streaming design, the DAC clock step is quantized:

```text
step = floor(requested_DAC_rate * 65536 / 125000000)
actual_DAC_rate = step * 125000000 / 65536
cycles = round(requested_frequency * buffer_samples / actual_DAC_rate)
actual_frequency = cycles * actual_DAC_rate / buffer_samples
```

This uses an integer number of periods per repeated buffer to avoid a discontinuity at its boundary.
The editor reports the **actual generated frequency**, which can differ from the requested value.
**Fit rate for slow signals** is enabled by default in the editor. If a periodic output
would round to zero complete cycles, the editor lowers the shared DAC rate to fit
one period of the slowest enabled waveform, using the nearest hardware clock step.
The adjusted rate appears in the DAC-rate field and the accepted actual frequency
appears in the status. Valid manually chosen timing is preserved. Disable this option
to keep the output rate fixed; low-frequency errors then explain which setting to change.
The receiver's ADC sampling rate and analysis settings are independent and are not changed.
Both enabled outputs must still fit below the adjusted DAC Nyquist limit.

For a **100 Hz test** with 65,536 buffer samples, the fitted requested DAC rate is
**6,553,650 samples/s**, producing approximately **100.000761 Hz**. The default
15.625 MS/s output rate stores only about 4.2 ms in the same buffer, so a 10 ms period
would round to zero cycles and was rejected. A real-board square-wave check through
the coded app measured approximately 100.000640 Hz over 26 periods, with no sample
loss or packet gaps; ADC acquisition remained at 15.625 MS/s on the same stream.
See [the 100 Hz capture](../tmp/lab_comparison_20261004/output_100hz_report.json).

For example, the default buffer/rate turns a 1,000 Hz request into approximately 953.674 Hz;
976,562.5 Hz is exact. Increase buffer length or lower DAC rate for finer frequency resolution.
Settings with no complete cycle or a fundamental at/above DAC Nyquist are rejected. Square/sawtooth
edges and custom shapes can still lose harmonics; sufficient waveform bandwidth needs validation.
The reported actual rate/frequency are calculated from the firmware rules, not measured readback.

The equivalent two-output 16-bit sample payload is `4 * actual_DAC_rate` bytes/s. This is **not a
claim of continuous network traffic**: the official client can upload a small buffer once and have
the board replay it. Larger buffers can require continued transport, depending on the active memory
block size. Benchmark the chosen buffer/rate alongside ADC streaming and check input packet loss.
125 MS/s is a configuration ceiling, not a promised network streaming rate. Multiple boards require
hardware clock/trigger synchronization for a defined phase relationship; separate engine requests
do not establish that relationship.

### Board stop behavior

In the pinned 3.00-57 source, `DACStreamClient.stopStreaming()` joins the local sender; a small buffer
with infinite board replay can keep running. The update sends a board DAC stop as well. If the wrapper
exposes `requestDACServerStop`, the engine uses it. The unmodified 3.00 Python client lacks that method,
so the engine sends the stock web app's `SS_DAC_START=false` control through `/wss` before replacing
or stopping outputs. This does not send the ADC stop parameter or reload the bitstream.

Therefore the unmodified client requires the **stock Data stream control web application** to be
running, as in the receiver's normal automatic board startup. A headless standalone streaming server
without that web application needs a wrapper exposing `requestDACServerStop`; otherwise the engine
reports an error. The web fallback allows parameter-update ticks before closing, but does not read
back the analog output voltage. Check physical stopping during your first hardware test.
Engine status is its last accepted operation, not independent hardware monitoring.
The original OUT1 test button uses the same board stop and upload checks. A successful start now
waits for waveform upload; asynchronous connection and board startup failures return an error.
The DAC memory reservation follows the acquisition block size: the stock firmware requires at
least twelve blocks including their 128-byte headers. Both the editor and original test button
increase the reservation for larger blocks instead of using a fixed reservation that fails above
131,072-byte blocks. The board still needs sufficient free memory for ADC and DAC together.
The capture buffer can occupy the shared region even when the output waveform itself is small.
On the default board, reserving 32 MiB for ADC acquisition prevented DAC startup. The saved
CH1 setup now reserves 16 MiB for ADC acquisition, with 1 MiB blocks and an unchanged
15.625 MS/s sample rate. The DAC reserves 12,584,448 bytes for those blocks. The editor and
original OUT1 button both started and produced a detectable sine on CH1 with this allocation;
the playback capture and subsequent 60-second capture reported no new sample loss or packet
gaps. See [the shared-memory check](../tmp/lab_comparison_20261004/output_shared_memory_report.json)
and [the settled playback and repeat-start check](../tmp/lab_comparison_20261004/output_playback_repeat_report.json).
This check confirmed the sine frequency and disappearance after stopping; it did not independently
calibrate the input voltage conversion or output amplitude against an external instrument.
Stopping playback can leave a held DC value at the DAC: it does not guarantee a zero-volt output.

### Receiver responsiveness

For supported 3.x clients, the isolated acquisition worker reads the stock ADC binary packets
directly into NumPy arrays, retaining the vendor client for board configuration and DAC playback.
The vendor sample getter builds a Python list for each channel; at 15.625 MS/s on both channels
that conversion can fall behind and display old samples long after the output settings change.
The direct reader avoids that conversion, validates packet headers and channel order, and preserves
packet-loss metadata. This improves responsiveness but does not guarantee lossless acquisition.

## Custom waveform import

Choose **Load custom samples…** for the relevant output. Import a JSON array or a one-column CSV/text
file of 2–16,384 finite values in `[-1, 1]`. No header is expected in CSV/text files. Values describe
one normalized period; periodic linear interpolation maps them into the shared DAC buffer:

```text
output_voltage = amplitude_volts * interpolated_shape + offset_volts
```

Files are read as data, not executable Python. Loading samples changes the editor; Apply sends them.
Noise is a reproducible uniform random buffer which repeats. It is useful for electrical tests,
but is not an optical vacuum reference or an indefinitely fresh random source.

## Nullifier loopback test

Connect OUT1 to IN1 and OUT2 to IN2 with matched cables. Use the receiver's lab preset with basis X,
ADC rate 15.625 MS/s, B delay 0, continuous collection and Rolling modes. Keep the uncalibrated graph
for this electrical test.

In the output editor choose **Lab X cancellation preset**, then Apply:

| Setting | OUT1 | OUT2 |
| --- | --- | --- |
| Waveform | Sine | Sine |
| Frequency | 976,562.5 Hz | 976,562.5 Hz |
| Peak amplitude | 0.1 V | 0.1 V |
| DC offset | 0 V | 0 V |
| Phase | 0 degrees | -90 degrees |

The current lab extraction uses four ADC points per mode, or 256 ns at this ADC rate. The sine
advances by a quarter cycle per mode. The lab X row
`A[k] + A[k+1] + B[k] - B[k+1]` cancels ideally with the -90 degree B phase. The variance should
fall toward the electronics/mismatch floor. Change B to +90 degrees: variance should rise.
**Lab P cancellation preset** reverses the cancelling B phase to +90 degrees for the lab P row.
The presets only populate generator settings; they do not change receiver processing settings.
Never use the cancelling sine itself as a shot-noise calibration. This verifies electrical
processing and does not demonstrate quantum squeezing.

After continuous testing, select the receiver's 5 ms lock / 5 ms collect schedule to test its data
selection. Generator start is not a timing marker in the current raw sample protocol, so this does
not synchronize collection phase to a generated waveform or external controller.

## Engine API

For a connected physical board, use:

- `GET /api/v1/devices/{deviceId}/outputs` — current accepted configuration and engine state.
- `PUT /api/v1/devices/{deviceId}/outputs` — replace the full two-output configuration.
- `POST /api/v1/devices/{deviceId}/outputs/stop` — stop that board's outputs.

Example PUT body:

```json
{
  "sampleRateHz": 15625000,
  "bufferSamples": 65536,
  "output1": {
    "enabled": true,
    "waveform": "sine",
    "frequencyHz": 976562.5,
    "amplitudeVolts": 0.1,
    "offsetVolts": 0,
    "phaseDegrees": 0
  },
  "output2": {
    "enabled": true,
    "waveform": "sine",
    "frequencyHz": 976562.5,
    "amplitudeVolts": 0.1,
    "offsetVolts": 0,
    "phaseDegrees": -90
  }
}
```

Omitted channel settings take disabled defaults: PUT is replacement, not partial editing.
Arbitrary channels add `"waveform": "arbitrary", "samples": [-1, 0, 1, 0]`.
Noise channels can set distinct `noiseSeed` values to avoid using the same repeating sequence.
Validation errors return 422; disconnected/simulator conflicts return 409; backend failures return
503. On failure, Refresh shows an unknown/unavailable state instead of claiming a successful start.
The original OUT1 test-sine button and API remain available; switching between that test and the
custom editor replaces the previous output path on the same board.

## Verification

Automated checks exercise waveform limits, quantized rates, shared phases, cancellation through
the real receiver processor, custom samples, API validation, board selection, output replacement,
stop commands, native-process control and the receiver editor. Binary packet tests also cover
fragmented reads, channel selection, invalid headers, truncated payloads and clean shutdown.

A physical two-channel loopback on 2026-10-02, using STEMlab 125-14 Pro v2.0 with OS 3.00-799,
detected the 976,562.5 Hz carrier on both channels and checked X/P arithmetic against an independent
calculation, including a completed 5 ms lock / 5 ms collect case. Both sine waves stopped after the
stop command. Full lab validation remains incomplete: some captures reported FPGA sample loss,
one phase setting did not produce the expected P cancellation, and nominal versus measured voltage
scaling needs calibration. These checks do not establish quantum squeezing or sustained throughput.

Official source references, pinned to Release_2026.1 / OS 3.00-57:

- [DAC client and repeat buffers](https://github.com/RedPitaya/RedPitaya/blob/Release_2026.1/apps-tools/streaming_manager/src/streaming_api_lib/src/dac_streaming.cpp)
- [DAC clock step and shared channel registers](https://github.com/RedPitaya/RedPitaya/blob/Release_2026.1/apps-tools/streaming_manager/src/common_lib/uio_lib/generator.cpp)
- [Web application's DAC stop handler](https://github.com/RedPitaya/RedPitaya/blob/Release_2026.1/apps-tools/streaming_manager/src/web_server/src/main.cpp)
