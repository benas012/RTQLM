"""Load the paired 50 ms LeCroy recordings and prepare a 125 MS/s ADC replay."""

import hashlib
import struct

import numpy as np

COUNT = 6_250_048
MEASUREMENT = 6_250_000


def read_trace(path):
    content = path.read_bytes()
    start = content.find(b"WAVEDESC")
    if start < 0:
        raise ValueError(f"No LeCroy descriptor: {path}")
    descriptor = memoryview(content)[start:]
    endian = "<" if struct.unpack_from("<h", descriptor, 34)[0] == 1 else ">"
    kind = struct.unpack_from(endian + "h", descriptor, 32)[0]
    lengths = struct.unpack_from(endian + "10i", descriptor, 36)
    count = struct.unpack_from(endian + "i", descriptor, 116)[0]
    gain, offset = struct.unpack_from(endian + "ff", descriptor, 156)
    interval = struct.unpack_from(endian + "f", descriptor, 176)[0]
    if (
        kind not in (0, 1)
        or abs(interval / 5e-9 - 1) > 1e-6
        or count < 10_000_000
        or any(x < 0 for x in lengths)
    ):
        raise ValueError("Expected a 50 ms, 200 MS/s LeCroy trace")
    begin = start + sum(lengths[:6])
    words = np.frombuffer(
        content, dtype="i1" if kind == 0 else endian + "i2", count=10_000_000, offset=begin
    )
    volts = words.astype(np.float64) * gain - offset
    return volts, dict(
        file=str(path),
        sha256=hashlib.sha256(content).hexdigest(),
        originalSamples=count,
        originalSampleIntervalSeconds=interval,
        verticalGainVolts=gain,
        verticalOffsetVolts=offset,
    )


def prepare_recording(folder, suffix):
    rails, sources = [], []
    for channel in (1, 2):
        volts, metadata = read_trace(folder / f"C{channel}{suffix}.trc")
        # Bandlimited 200 -> 125 MS/s Fourier resampling, with periodic edge assumption.
        spectrum = np.fft.rfft(volts)
        reduced = spectrum[: MEASUREMENT // 2 + 1].copy()
        reduced[-1] *= 2  # Fold the negative-frequency term into the new Nyquist bin.
        resampled = np.fft.irfft(reduced, n=MEASUREMENT) * (MEASUREMENT / len(volts))
        scaled = np.rint(resampled * 8192)
        clipped = int(np.count_nonzero((scaled < -8192) | (scaled > 8191)))
        if clipped:
            raise ValueError(f"Replay would clip {clipped} samples: {metadata['file']}")
        encoded = np.bitwise_and(scaled.astype(np.int16), 16383).astype("<i2")
        rails.append(np.pad(encoded, (0, COUNT - MEASUREMENT)).tobytes())
        sources.append({**metadata, "clippedSamples": clipped})
    return b"".join(rails), sources
