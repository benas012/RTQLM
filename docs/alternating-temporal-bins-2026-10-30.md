# Time-based temporal bins

When `modeSamples` is null and no explicit template is present, the requested width T now
defines a clock: boundary k is `floor(k * T * Fs)`. Consecutive boundaries determine the
sample count. At 125 MS/s and T = 308 ns the pattern is 38, 39, 38, 39 samples. A full
625,024-sample capture contains 16,234 complete bins. The average period is 308 ns;
individual integration windows remain 304 or 312 ns.

The RP C processor and Python reference use the same rule. Ordinary packet splits preserve
the clock phase. Independent captures, collection boundaries, and detected gaps restart it.
The native continuous TCP path also retains the phase while sliding its overlapping windows.
Each of the two sampled templates is normalized so `sum(weights**2) / Fs = 1`, preventing
white-noise gain from alternating solely because one bin contains an extra sample. Existing
fixed sample counts and explicit templates remain fixed-size.

`modeSamples` in results is the smaller count; `modeSamplesMax` is the larger. The UI displays
the range and average period. Calculations use every accepted sample; display downsampling
does not determine the mode boundaries. A fresh reference is required after this update.

This corrects rounding drift. It does not locate the optical mode origin, remove acquisition
gaps, or perform fractional-sample interpolation. Different bin widths can respond differently
to a DC displacement, so check input offsets, detector balance, and the experimental mode shape.

## Verification

- Receiver suite: 496 passed; 24 native parity cases also passed separately on the RP.
- Native executable: 24 command checks and 8 streaming checks, including odd-length overlapping
  windows that switch the starting 38/39 phase.
- Native boundary test: 1,000,000 boundaries match `floor(k*77/2)` exactly; both noise gains are 1.
- Real buffered acquisition: 125 MS/s, 625,024 samples per channel, 16,234 complete bins;
  `modeSamples=38`, `modeSamplesMax=39`, average `modePeriodSeconds=308e-9`.
- Active RP release: `e7f2c4a19ef0`; the preceding release remains available for rollback.
