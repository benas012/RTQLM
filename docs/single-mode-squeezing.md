# Single-mode squeezing: lab procedure and script comparison

Use **Single-mode** for the three-step measurement procedure described below. It
produces one squeezing curve per selected homodyne channel. Use the **lab 1D
cluster** preset for the uploaded script's active two-channel, neighbouring-mode
nullifier calculation. These are different measurements, even though they share
mode extraction and shot-noise calibration.

This document describes the calculation and operator settings. It does not
establish that a live optical acquisition has correct synchronization, adequate
bandwidth, or no lost samples. Numerical comparison and physical validation are
separate checks.

## 1. Define the mode and its number of samples

The **Single-mode preset** initially keeps the uploaded script's lab mode:
nominal width **308 ns**, frequency parameter **6 MHz**, and the
`lab_gaussian_linear_cosine` shape. **N_mode (samples)** is initially blank, so
the count is calculated from that width and the actual ADC rate, with the same
integer truncation as the script:

```text
N_mode = int(308 ns * Fs)
```

An explicit **N_mode (samples)** instead sets the number of consecutive ADC
samples in one mode. With sample rate `Fs`, the actual mode duration is:

```text
mode duration = N_mode / Fs
```

For an optional initial constant, or identity, mode choose `identity` and enter
an explicit N_mode. Every complete bin becomes one number: a weighted sum of its
samples. Discrete identity weights are normalized and bins are projected as
follows:

```text
sum(f[j]^2) * dt = 1
q[k] = sum(v[k*N_mode + j] * f[j]) * dt
dt = 1 / Fs
```

Identity weights are constant across the bin. Do not subtract the mean of each
bin before this projection: that would make an identity-mode result zero.
The lab shape is normalized on its fine grid before interpolation, following the
script. It is not normalized again after interpolation. An explicit template can
replace the analytic weights. Use exactly the same weights, sample rate and
channel settings for shot noise and the signal measurement; any constant scaling
of the weights cancels from their variance ratio.

The nominal width is not necessarily the actual bin duration:

| ADC rate | Automatic lab N_mode | Actual bin duration |
| --- | --- | --- |
| 15.625 MS/s | 4 | 256 ns |
| 31.25 MS/s | 9 | 288 ns |
| 125 MS/s | 38 | 304 ns |
| 200 MS/s, for a matching offline file | 61 | 305 ns |

At 15.625 MS/s, four samples provide weak resolution of the lab mode and the
256 ns bins do not follow a 308 ns optical clock. The receiver reproduces the
script's integer-bin convention; it does not establish accurate optical timing.
Increasing the rate also increases transport and processing requirements. Use a
higher sustained acquisition rate only after checking the actual connection for
lost samples; this document does not recommend unverified continuous 125 MS/s.
The optional flat-mode example of 16 samples lasts 1.024 microseconds at
15.625 MS/s and is not a lab mode choice.

Only complete modes are used. Ordinary network packet boundaries do not restart
the bins. Incomplete modes are discarded at a collection boundary or a detected
stream discontinuity.

## 2. Calibrate against shot noise

Choose **Shot-noise variance → Whole accepted trace** to follow the requested
full-trace calibration. The receiver projects all accepted complete shot-noise
bins, then calculates their population variance:

```text
V_shot = variance(all accepted q_shot[k])
```

This uses `ddof=0`, as does `numpy.var()` in the lab script. Data excluded as
locking or settling are not part of the reference. Each selected channel gets
its own calibration. Selecting CH1 alone does not require a valid CH2 reference;
selecting Both requires references for both channels.

The script's active cluster calculation instead calculates a variance for each
measurement segment and takes their equal-weight mean:

```text
V_shot_script = mean(variance(segment_1), variance(segment_2), ...)
```

Choose **Average collection variances** to reproduce that calibration choice.
It is independent of the mode shape. The two choices agree for one segment, but
can differ when segments have different lengths, variances, or mean levels.
Pooled calibration includes changes in the mean between segments; averaging
segment variances removes those changes from the reference.

Internally, the receiver multiplies each channel's projected values by:

```text
c = 1 / sqrt(2 * V_shot)
```

This sets the reference variance to 1/2. It is a normalization convention; the
final single-mode result is still the measured signal-to-shot-noise variance ratio.

## 3. Calculate rolling variance and squeezing

Set **Rolling window (modes)** to **100** for the proposed procedure. This means
100 projected mode values, not 100 raw ADC samples. The interior of a window
covers `100 * N_mode / Fs` seconds.

The single-mode calculation uses centered, full windows. For an even window of
100, the slice at index `k` is `q[k-50:k+50]`, with its upper bound excluded.
Values near the beginning or current end are withheld until a complete window
is available. The latest displayed completed value therefore needs approximately
50 later modes. The calculation never joins separate collection periods or
continues a variance window across detected lost data.

```text
V_signal[k] = variance(100 neighbouring projected signal modes)
squeezing_dB[k] = 10 * log10(V_signal[k] / V_shot)
```

After the internal normalization, the same calculation is:

```text
10 * log10(variance(c * q_signal) / (1/2))
  = 10 * log10(V_signal / V_shot)
```

Both channels can be shown independently. Single-mode analysis does not add
CH1 to CH2 or combine adjacent modes into a cluster nullifier. Internally,
`processing_nullifiers()` selects one-term expressions with reference variance
1/2. Cluster expressions use their own coefficient-dependent reference; the
uploaded script's four unit-weight terms use reference variance 2.

Negative dB indicates variance below the shot-noise reference. As checks on the
calculation, variance reduced by half gives -3.0103 dB; standard deviation reduced
by half gives -6.0206 dB. Finite rolling estimates fluctuate, so individual
negative points in an ordinary shot-noise trace are not evidence of squeezing.

## Equivalent methods in the uploaded Python file

The source is [20260522 - post_processing_1dcluster.py](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py>).
Line references below identify its active code and distinguish useful helper
functions from the script's final cluster pipeline.

| Operation | Uploaded script | Receiver |
| --- | --- | --- |
| Remove locking and settling | [`filter_out_lock_and_noise()`, line 97](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:97>) builds a mask; [`split_data_measure_segments()`, line 200](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:200>) retains measurement segments. Calls at lines 703–707 apply it to both signal and shot-noise channels. | Collection gating occurs before mode projection and calibration. Periodic or sampled-edge gating replaces the script's retrospective mask detection. |
| Define mode shape | [`count_mode_gauss_lin_cos()`, line 306](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:306>) defines the lab shape. Active [`build_long_mode_from_short()`, line 570](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:570>) normalizes on a fine grid and interpolates, called at line 740. | `build_lab_mode_template()` reproduces those lab weights. `single_mode_processing_config()` retains the lab shape and automatic integer sample count. Its optional explicit sample-count argument supplies an identity mode; the UI also allows identity plus an explicit `mode_samples` count. |
| Bin and project | [`apply_long_mode_to_trace()`, line 609](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:609>) calculates `sum(samples * weights) * dt`, called at lines 761–784. [`apply_mode_to_trace()`, line 365](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:365>) is an equivalent helper for an unshifted repeating template. | `RealtimeClusterProcessor._extract_quadratures()` retains partial network packets until a complete mode exists and applies the same projection. |
| Full-trace shot-noise variance | The requested equivalent is `np.var(np.concatenate(projected_shot_noise_segments))`. It is **not** the active script calibration. [`compute_global_sn_variance()`, line 469](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:469>) computes a mean of its concatenated inputs, not the variance of mode values. | `calibration_estimator="pooled"` uses streaming running moments over all accepted projected reference values. |
| Script calibration | [`compute_global_sn_coefficient_cluster()`, line 485](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:485>) averages segment variances and returns `1/sqrt(2*V)`. Calls at lines 800 and 803 calibrate the two rails separately. | `calibration_estimator="segment_mean"` reproduces the equal-weight segment choice. `"auto"` retains that choice for the lab cluster preset. |
| Cluster nullifiers | [X expression at line 821](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:821>): `A[k]+A[k+1]+B[k]-B[k+1]`, starting at 0. P at line 859: `A[k]-A[k+1]+B[k]+B[k+1]`, starting at 1. Active selection at line 869 is X. | `LAB_CLUSTER_X`, `LAB_CLUSTER_P` and `compute_nullifier()` reproduce the signs and indices. `analysis_mode="single"` uses each selected channel alone instead. |
| Rolling variance | [`rolling_variance()`, line 419](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:419>) uses centered population variance, including shorter edge windows. Final calls at line 941 use a width derived from `1/3000` seconds at lines 930–937, **not a fixed 100 modes**. Odd requested widths are reduced by one by its slicing convention. | `variance_window_modes=100` fixes the proposed count. Single-mode full windows withhold incomplete edges. `variance_estimator="lab_rolling"` retains the script's bounds and time-derived width for compatibility. |
| Single-mode dB | [`compute_squeezing_trace()`, line 450](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:450>) and `compute_squeezing_trace_global_sn()` at line 503 compute `10*log10(V_signal/V_shot)`. | `cluster_squeezing_db()` with the selected one-term reference 1/2 gives the same ratio after normalization. |
| Active cluster dB and averaging | [`compute_squeezing_trace_cluster()`, line 524](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:524>) divides normalized nullifier variance by 2. [`average_squeezing_traces()`, line 458](<C:/Users/benas/Downloads/20260522 - post_processing_1dcluster.py:458>) averages dB traces at corresponding segment indices, called at line 947. | `lab_rolling` averages completed segment dB traces equally. Ordinary single-mode rolling shows each collection's local variance and does not perform that extra across-segment dB average. |

The active script shifts channel B by two samples relative to A before splitting
segments (lines 663–682). The lab cluster and single-mode presets carry that
two-sample delay.
Measure the physical delay at the actual ADC rate; two samples represent different
times at different rates. Single-mode processing still projects each channel
independently. Retaining the script's delay aligns the start of B's projection
bins for comparison; it does not combine the two channels.

## Locking, synchronization and interruptions

The script's `filter_out_lock_and_noise()` defaults are **5 ms locking every
10 ms**, leaving a nominal 5 ms measurement interval. Its call at line 651 passes
only the filename, so these defaults apply. The separate 10/20 ms variables at
lines 638–639 are not passed into this function.

The script identifies an initial position using `0.99 * trace.max()`, then marks
repeating intervals. It excludes whole locking intervals, not just samples above
that threshold. The extra start exclusion is 0.8006 ms; a 1000-sample margin is
also removed before the next lock. The receiver's lab and single-mode presets
provide the corresponding guards. These reduce usable measurement time within
the 5/5 ms cycle; they do not change the cycle length.

In **Periodic** mode the board sends data continuously. The receiver excludes
locking intervals from analysis using ADC sample positions. Starting or finishing
calibration preserves that established sample-clock schedule and the current
mode boundary. It does not restart an already aligned 5/5 ms clock.

This preservation does not establish initial synchronization with the optical
controller. Starting a stream at an arbitrary point, changing acquisition
settings, or detecting lost data can invalidate the phase. A free-running periodic
schedule cannot recover the controller's phase by itself. **Triggered** mode can
observe a configured edge on CH1 or CH2; a waveform-display trigger alone does not
control analysis collection. A separate controller TTL input with recorded timing
still requires support in the acquisition path. Establish and verify the timing
before using periodic results as experimental measurements.

## Operator procedure for the planned measurement

1. Connect the homodyne channels and verify the actual ADC sample rate. Confirm
   that the chosen acquisition timing selects measurement intervals and excludes
   the lock and its settling period. Check that no samples are being lost.
2. In **Nullifiers**, select **Single-mode preset**. Keep the lab shape and
   nominal 308 ns width for comparison with the uploaded script. Leave
   **N_mode (samples)** blank to calculate the script's integer count from the
   actual ADC rate, and check the reported count and duration against the lab's
   optical timing. Set **Rolling window (modes) → 100** and **Single-mode
   channel → CH1, CH2 or Both**. Choose **Whole accepted trace** for the requested
   reference estimator. If the lab instead requests an initial constant mode,
   choose `identity` and enter the agreed explicit N_mode. Apply the settings
   before calibration.
3. Set the optics to the shot-noise condition using the lab's established
   procedure, with the local oscillator and detector settings matching the
   subsequent measurement. Allow any switching and settling to finish.
4. Start shot-noise calibration, collect enough valid measurement intervals,
   then finish calibration. At least 100 accepted modes per selected channel are
   required by the default minimum; collect more for a stable reference. The
   5/5 ms schedule continues through these controls.
5. Set the optics to the signal condition. Keep the ADC rate, gain, mode function,
   channel selection and timing settings the same. Read the completed-window dB
   curves after the signal has settled.
6. As a reference check, measure shot noise again: the curve should fluctuate
   around 0 dB. Save raw shot-noise and signal measurements with their settings
   so the live result can be compared with the lab calculation. A deterministic
   sine-wave loopback verifies signal reception but does not validate optical
   shot-noise calibration or quantum squeezing.

Changing the mode, ADC rate, detector gain, local-oscillator conditions, or
calculation settings requires a matching calibration. Consult
[the cluster-processing guide](nullifiers.md) when the objective is the uploaded
script's cluster nullifiers rather than independent single-mode squeezing.
