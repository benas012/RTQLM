# Command-configurable nullifier processing

For **C calculation on the Red Pitaya ARM processor**, use the
[native processor and its SSH command protocol](../native/redpitaya_nullifiers/README.md).
When `QL_NATIVE_NULLIFIER_ENABLED=true`, these HTTP endpoints control the installed C processor
for the selected `QL_NATIVE_NULLIFIER_HOSTS`. The receiver uses its results directly. Other
devices use the Python processor on the engine host.
The [verified rp-f0f9eb.local deployment](native-nullifier-board-validation.md) includes the exact
SSH command and examples for the installed native C executable.

Each device has an optional nullifier processor in the engine. Send commands to
`POST /api/v1/devices/{deviceId}/nullifiers/commands` to change the calculation without editing code
or connecting the desktop receiver. Install the native executable before enabling the board
backend; configuration commands do not upload a program or alter the FPGA.

Host processing starts disabled. `configure` enables it, using the lab preset for settings omitted from
the first command. Later configure commands update only the supplied settings. The configuration
is independent for each device and lasts for the engine session. It is separate from the desktop
receiver's locally saved configuration for host processing. With the board backend, the receiver
sends its saved settings when it opens the stream. Formula changes take effect without stopping
raw acquisition; streaming windows support `periodic` and `continuous` modes.

## Change the formula

For each configured row, the processor calculates:

```text
nullifier[k] = sum(term.weight * quadrature[term.source][k + term.offset])
```

`source` selects rail `a` (CH1) or `b` (CH2). `offset` is a nonnegative integer in temporal modes;
`start` is the first included anchor index in each collection. Only complete support within a
single uninterrupted collection contributes. The coefficients act on mode quadratures after
channel-delay alignment, temporal projection and optional shot-noise normalization.

For example, this command applies the lab X formula:

```json
{
  "command": "configure",
  "config": {
    "measurementBasis": "x",
    "nullifiers": [{
      "name": "cluster_x",
      "start": 0,
      "terms": [
        {"source": "a", "offset": 0, "weight": 1},
        {"source": "a", "offset": 1, "weight": 1},
        {"source": "b", "offset": 0, "weight": 1},
        {"source": "b", "offset": 1, "weight": -1}
      ]
    }]
  }
}
```

Changing names, weights, offsets and the number of rows changes the calculation. Send the entire
`nullifiers` array when replacing formulas. A basis-only command switches the built-in lab X/P
formula; custom formulas remain as supplied. The basis setting selects the measured expression;
it does not rotate the physical homodyne measurement.

The other [processing settings](nullifiers.md#custom-formulas-and-persisted-configuration) can be
sent in the same `config` object, including mode shape, `modeExpression`, explicit template, channel delay,
collection schedule and variance estimator. For example, this switches to continuous processing
with a flat four-sample mode and a 100-mode rolling variance:

```json
{
  "command": "configure",
  "config": {
    "windowMode": "continuous",
    "collectionGuardSeconds": 0,
    "collectionEndGuardSamples": 0,
    "modeShape": "identity",
    "modeSamples": 4,
    "explicitModeTemplate": [],
    "minSamplesPerMode": 2,
    "channelBDelaySamples": 0,
    "varianceEstimator": "rolling",
    "varianceWindowModes": 100
  }
}
```

Choose mode timing that matches the physical experiment. An explicit template overrides the
analytic shape. `modeSamples` sets a fixed ADC sample count; set it to `null` to return to a
width-based analytic template. Multiple rows operate on the same physically measured basis.

Configure commands validate the complete proposed settings before applying them between packets.
An accepted change increments `revision`, clears previous results and shot-noise calibration,
discards partial modes, and restarts collection timing. Invalid settings return 422 and preserve
the current configuration, calibration and results. Unknown settings and unknown row/term fields
are rejected, including misspelled coefficient names.

## Calibration and control commands

Send these bodies to the same commands endpoint:

| Body | Effect |
| --- | --- |
| `{"command":"start_calibration"}` | Accumulate vacuum modes for the configured measurement. |
| `{"command":"finish_calibration"}` | Finish when every rail used by the formulas has at least 100 valid modes. |
| `{"command":"finish_calibration","minimumModes":2000}` | Require the specified minimum per required rail. |
| `{"command":"clear_calibration"}` | Remove normalization and clear derived results. |
| `{"command":"reset"}` | Clear results, accumulated variance and partial modes; retain coefficients. |
| `{"command":"disable"}` | Stop processing and clear results and calibration. |

Start acquisition with the existing device `/start` command. Calibration commands require enabled
processing. Finishing without an active calibration, or without enough valid modes, returns 409.
A failed finish keeps calibration active so more modes can be collected. Rails used only as a
trigger input do not need a shot-noise coefficient. Use vacuum data to calibrate, then acquire
signal data with the same hardware and processing settings.

## Read status and results

`GET /api/v1/devices/{deviceId}/nullifiers` returns `enabled`, `revision`, the complete `config`,
`calibrated`, `calibrating`, `calibrationModeCounts`, `shotNoiseCoefficients`, `hasResult` and `error`.

`GET /api/v1/devices/{deviceId}/nullifiers/latest` returns `revision`, `streamId`, `sequence` and
the processor result: `quadratures`, `nullifiers`, `variances`, `squeezingDb`, mode timing,
collection metadata and variance counts/index starts. The sequence identifies the packet that
produced the result. Nullifier/variance history is bounded by `historyModes`; quadratures contain
the newly extracted modes. Before calibration, squeezing arrays are empty. Non-finite values,
including rejected modes and negative-infinite dB for zero variance, are encoded as JSON `null`
without removing their array positions.

Processing uses acquired packets before display changes. Missing required channels and invalid
sample rates report an analysis error while raw sample publication continues. Packet gaps, stream
changes and changed loss counters isolate collection history. Packets reporting new FPGA loss
are excluded because the stream does not locate the missing samples inside the packet. Stopping
acquisition clears derived results; disconnecting or changing the device profile also clears
calibration. Processing settings remain available until the engine session ends.
