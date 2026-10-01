# Receiver nullifiers and sample-hold acquisition

The receiver's analysis area contains **Spectrum analyzer** and **Nullifiers** tabs. Nullifiers are
calculated from both raw acquired channels before waveform rendering, display downsampling, channel
visibility, offsets or optional display scripts. All received packets are processed even when the
display refreshes less often. Choose variance across repeated collection windows, as in the paper,
or a live rolling diagnostic of neighboring modes in the selected measurement basis. The graph
alone is not an entanglement certificate.

## Scientific reference and 1D preset

The reference is Mikkel Vilsboll Larsen, *Continuous Variable Cluster State Computation* (DTU, 2021),
chapter 3. Section 3.4.2.2, printed page 37 (PDF page 50), gives the unnumbered 1D expressions:

```text
n_x[k] = x_A[k] + x_B[k] - x_A[k+1] + x_B[k+1]
n_p[k] = p_A[k] + p_B[k] + p_A[k+1] - p_B[k+1]
```

CH1 supplies spatial rail A and CH2 supplies spatial rail B. Select the measured X or P basis to
match the physical local-oscillator phases at BOTH homodyne detectors. Selecting a basis in the
receiver selects its formula; it does not rotate the optics or turn measured X data into P data.
Take separate X and P acquisitions with matching shot-noise calibrations. Custom formulas describe
linear combinations of the two measured rails and temporal offsets, not additional simultaneously
measured quadratures.

The paper's temporal extraction is section 3.6.3, Eq. (3.25), printed pages 50-51 (PDF pages 63-64):

```text
q[k] = integral(f_k(t) q(t) dt)
f_k(t) = N (t-k*tau) exp(-kappa^2 (t-k*tau)^2 / 2), |t-k*tau| < tau/2
f_k(t) = 0 otherwise
tau = 247 ns, kappa = 2*pi*2.7 MHz
```

The paper preset uses this Gaussian-linear function. The earlier Gaussian-linear-cosine function
is available as an alternative shape, and an explicit discrete template can be supplied. Discrete
templates are normalized so that `sum(f**2)*dt = 1`, then each mode is projected using
`sum(samples*f)*dt`. This normalization is consistent with the explicit shot-noise calibration.

The actual mode length is an integer number of ADC samples. At 125 MS/s, 247 ns rounds to 31 samples,
or **248 ns**. Match the actual lab delay to the sample grid or validate a fractional-grid method
before interpreting a long sequence of nominal 247 ns modes; rounding can accumulate timing drift.
Changing the temporal template or sample rate requires a new calibration.

## Locking and collection windows

The receiver defaults to the requested **5 ms locking followed by 5 ms collecting**, repeating on
the ADC sample clock. Both durations can be changed. The paper itself uses 10 ms locking and 5 ms
collecting (section 3.3, printed page 32 / PDF page 45); the lab's requested timing takes precedence.

Choose the acquisition mode that matches the experiment:

| Mode | Behavior | Appropriate use |
| --- | --- | --- |
| Periodic | Skip the locking interval, process the collection interval, repeat. | A known cycle aligned to the beginning of the stream. |
| Continuous | Process all samples. | Offline-style checks, simulation, continuously measured light. |
| Triggered | A sampled CH1/CH2 edge starts collection after the configured delay. | A known transition in the acquired signal that reliably marks the experiment phase. |

Collection gating happens **before** temporal projection and shot-noise accumulation. Incomplete
temporal modes are discarded at the end of collection. Every term of a nullifier must lie in the
same collection window; the processor does not join the last mode before locking with the first
mode afterward. Rolling variances are also confined to each uninterrupted collection segment.

Window timing uses sample positions, not packet arrival times or desktop timers. Packet boundaries
can fall inside either phase. Collection and locking times are quantized to whole samples at the
configured sample rate. Sequence gaps, loss flags, changed FPGA loss counters, channel loss, stream
restarts and sample-rate changes discard partial state. Periodic timing then starts a fresh locking
interval; triggered mode waits for a fresh edge. A periodic restart cannot recover the experiment's
absolute phase after lost samples.

The nullifier acquisition trigger is independent of the waveform display trigger. Its source,
edge, threshold, hysteresis and delay are configurable. Use hysteresis to avoid triggering repeatedly
on noisy threshold crossings. Do not use quantum noise itself as a selection criterion for estimating
unbiased squeezing. Optional amplitude rejection also conditions the measured sample set and can
bias the result downward.

### Recommended lab synchronization

Use the same function-generator TTL that controls probe chopping and feedback to mark collection,
as in the paper. For reliable experiments, capture that event at the board and retain its sample
index with the data. A desktop timer or a free-running receiver cycle can drift relative to the lab.

The existing binary sample protocol carries two analog rails and loss metadata; it has no separate
TTL event channel. The current receiver therefore supports **sampled analog edge detection**, not an
external digital trigger. Both homodyne channels are needed for nullifiers, so routing TTL into one
of those inputs would sacrifice a rail unless the acquisition arrangement changes. Red Pitaya's
stock Streaming application is continuous and has no triggering capability; hardware TTL capture
requires an acquisition backend or FPGA/protocol extension. See the vendor's
[Streaming documentation](https://redpitaya.readthedocs.io/en/latest/appsFeatures/applications/streaming/top.html).

## Calibration and interpretation

With the optical system set to a vacuum/shot-noise reference, start calibration, accumulate enough
accepted complete modes, and finish calibration. The processor normalizes each rail to vacuum
quadrature variance `1/2` (the paper's `hbar=1` convention). For independent normalized vacuum modes,
the four-term 1D nullifier has reference variance `2`, and its squeezing is:

```text
squeezing_dB = 10*log10(rolling_variance / shot_noise_reference_variance)
```

Negative values mean variance below the chosen shot-noise reference. Custom rows use their own
reference calculated from the summed weights of independent `(source, offset)` variables. If
vacuum modes are correlated, measure the nullifier reference directly; the independent-source
reference no longer describes that measurement. Check intermode and interchannel correlations of
the shot-noise acquisition, as discussed in section 3.6.3 of the thesis.

Match calibration to the same basis, LO power, channel gain, temporal function, sample rate and
collection settings used for the signal. Configuration and sample-rate changes clear calibration.
The uncalibrated graph has arbitrary measurement units; it must not be read as squeezing in dB.

The paper's section 3.7 estimates the variance at each temporal index `k` from **10,000 repeated,
aligned traces**, separately in X and P. Select **Repeated windows** (`varianceEstimator: ensemble`)
for this estimator: one nullifier observation at a given relative mode index is accumulated from
each collection window using stable running moments. At least two accepted observations at that
index are required for variance; the chart reports the observation counts. Acquiring thousands of
repeats may be needed for a useful estimate. Packet fragmentation does not create extra observations.
Only complete nullifier support within the collection window contributes. Accumulation is bounded
by the configured history/mode limit and clears when configuration, calibration or stream continuity
changes.

Ensemble acquisition assumes that corresponding `k` indices represent the same temporal position
in each shot. Align the optical mode grid and collection start with the experiment; periodic timing
started at an arbitrary stream phase cannot establish this alignment. Validate edge-trigger timing
jitter and the trigger marker before comparing ensemble results to the paper.

Select **Rolling modes** (`varianceEstimator: rolling`) for variance across neighboring modes inside
one uninterrupted collection window, assuming a stationary source. The paper's -3 dB full-inseparability
criterion concerns its particular 2D state; do not automatically apply it to a 1D/custom formula or
an unsynchronized live diagnostic.

## Custom formulas and persisted configuration

Settings are stored in `data/cluster_processing.json`. Advanced configuration accepts temporal
template samples and nullifier rows with `name`, `start` and a list of `terms`. Each term specifies
`source` (`a` or `b`), a nonnegative integer `offset` in temporal modes, and a finite `weight`:

```json
{
  "name": "rail_difference",
  "start": 0,
  "terms": [
    {"source": "a", "offset": 0, "weight": 1.0},
    {"source": "b", "offset": 0, "weight": -1.0}
  ]
}
```

Applying new configuration clears prior results and calibration. Use validated coefficient rows
instead of executable formula strings. The same representation can eventually be translated to
on-board coefficients without evaluating arbitrary injected code.

| JSON setting | Units / values | New receiver default |
| --- | --- | --- |
| `windowMode` | `periodic`, `continuous`, `triggered` | `periodic` |
| `lockSeconds`, `collectSeconds` | seconds | `0.005`, `0.005` |
| `measurementBasis` | `x`, `p` | `x` |
| `varianceEstimator` | `ensemble` across aligned windows; `rolling` across neighboring modes | `ensemble` |
| `triggerChannel`, `triggerEdge` | `a`/`b`, `rising`/`falling` | `a`, `rising` |
| `triggerLevelVolts`, `triggerHysteresisVolts` | volts | `0`, `0` |
| `triggerDelaySeconds` | seconds after accepted edge | `0` |
| `triggerHoldoffSeconds` | minimum seconds between accepted edges | `0.010` |
| `modeShape` | `gaussian_linear`, `gaussian_linear_cosine` | `gaussian_linear` |
| `modeWidthSeconds` | seconds per temporal mode | `247e-9` |
| `measurementFrequencyHz` | `kappa/(2*pi)` for paper shape; carrier for cosine shape | `2.7e6` |
| `explicitModeTemplate` | discrete finite nonzero samples, overrides analytic shape | `[]` |
| `channelBDelaySamples` | positive pairs A[n] with B[n+delay] | `0` |
| `varianceWindowSeconds` | rolling window inside each collect segment | `1/3000` |
| `minSamplesPerMode`, `historyModes` | counts | `16`, `25000` |

Trigger holdoff is measured from the accepted edge; edges during the trigger delay or active
collection are ignored. The generic processor API defaults to continuous/rolling processing for
callers without receiver settings; the desktop receiver selects the lab schedule and ensemble
estimation. In continuous mode there is only one collection window, so use rolling estimation or
select periodic/triggered collection to obtain repeated-window variances.

The ensemble estimator retains a fixed prefix of within-window mode indices up to `historyModes`.
The receiver's 25,000-mode default covers a full 5 ms collection at 125 MS/s and 31 samples/mode.
Increase the limit for longer windows if needed. The rolling estimator retains recent modes instead.

When loading an old receiver configuration that exactly matches the previous built-in preset,
the receiver migrates it in memory to the corrected paper preset and lab schedule. Arbitrary custom
formulas are preserved, and their missing analytic shape is treated as the legacy cosine shape.
The file is saved only when configuration is applied.

## Sample rate and where to compute

Insufficient **ADC samples per temporal mode** is different from insufficient packets on the screen.
The receiver processes full packets, checks the configured minimum samples per mode, and rejects a
sample rate too low for the analytic template. More packets cannot reconstruct an under-sampled
optical mode. Use a slower mode only if the optical delay/measurement genuinely supports it; simply
lengthening the receiver setting changes the measurement being performed.

For a two-channel 16-bit raw stream, payload rate is `4*sample_rate_hz` bytes/s. At 125 MS/s this is
500 MB/s during collection. A 5 ms collection produces 2.5 MB; repeating every 10 ms still averages
250 MB/s, above even the theoretical 125 MB/s of Gigabit Ethernet. Current vendor documentation
lists approximately 62.5 MB/s practical streaming, or 15.625 MS/s per channel for two 16-bit channels.
That yields fewer than four samples in 247 ns. Older OS versions have lower limits; actual throughput
also depends on the board, firmware and acquisition library. See
[performance limits](https://redpitaya.readthedocs.io/en/latest/appsFeatures/applications/streaming/reference/performance_optimization.html)
and [version-specific limits](https://redpitaya.readthedocs.io/en/latest/appsFeatures/applications/streaming/reference/limitations.html).

Recommended progression:

1. Use this receiver implementation to validate formulas, timing and calibration with adequate
   recorded/simulated samples or a supported acquisition arrangement.
2. For full-rate lab validation, consider externally triggered deep-memory captures transferred
   between runs. This supports bursts, with transfer/rearm dead time; it cannot promise every 5/5
   cycle as raw data. Red Pitaya deep-memory storage can use four bytes per stored sample, so reserve
   about 5 MB for two channels over 5 ms. See
   [Deep Memory Mode](https://redpitaya.readthedocs.io/en/latest/appsFeatures/remoteControl/deepMemoryMode.html).
3. For sustained operation, project temporal modes at the ADC rate on the FPGA, preserve collection
   boundaries and mode indices, and send mode quadratures or nullifier statistics. Compute configurable
   linear rows and stable variance accumulation on FPGA/ARM as appropriate. The Quantum Light Engine
   should own versioned, validated configuration, calibration and result metadata; the receiver should
   display results. Keep occasional raw captures for validation against the receiver calculation.

Moving only the final addition/subtraction to the board does not solve raw-data bandwidth. Temporal
projection and gating must happen before transport. Python running on the board's ARM CPU should not
be assumed to sustain 125 MS/s; benchmark a native implementation or use FPGA processing. No on-board
deployment or hardware/protocol modification is performed by this receiver feature.
