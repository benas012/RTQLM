from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from functools import partial
from itertools import pairwise
from typing import Any, TypeAlias

import numpy as np
import numpy.typing as npt

from quantum_light_engine.acquisition_windows import AcquisitionWindowGate
from quantum_light_engine.ensemble_variance import EnsembleNullifierVariance

FloatArray: TypeAlias = npt.NDArray[np.float64]


def _empty_quadratures() -> dict[str, FloatArray]:
    return {name: np.empty(0, dtype=np.float64) for name in ("a", "b")}


@dataclass(frozen=True, slots=True)
class NullifierTerm:
    source: str
    offset: int = 0
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class NullifierSpec:
    name: str
    terms: tuple[NullifierTerm, ...]
    start: int = 0


DEFAULT_CLUSTER_X = NullifierSpec(
    "cluster_x",
    (NullifierTerm("a", 0, 1.0), NullifierTerm("a", 1, -1.0),
     NullifierTerm("b", 0, 1.0), NullifierTerm("b", 1, 1.0)),
)
DEFAULT_CLUSTER_P = NullifierSpec(
    "cluster_p",
    (NullifierTerm("a", 0, 1.0), NullifierTerm("a", 1, 1.0),
     NullifierTerm("b", 0, 1.0), NullifierTerm("b", 1, -1.0)),
)

LAB_CLUSTER_X = NullifierSpec(
    "cluster_x",
    (NullifierTerm("a", 0, 1), NullifierTerm("a", 1, 1),
     NullifierTerm("b", 0, 1), NullifierTerm("b", 1, -1)),
)
LAB_CLUSTER_P = NullifierSpec(
    "cluster_p",
    (NullifierTerm("a", 0, 1), NullifierTerm("a", 1, -1),
     NullifierTerm("b", 0, 1), NullifierTerm("b", 1, 1)),
    start=1,
)

SINGLE_MODE_A = NullifierSpec("CH1", (NullifierTerm("a"),))
SINGLE_MODE_B = NullifierSpec("CH2", (NullifierTerm("b"),))


@dataclass(frozen=True, slots=True)
class ClusterProcessingConfig:
    """Same-basis processing; positive B delay pairs A[n] with B[n + delay]."""

    mode_width_seconds: float = 247e-9
    measurement_frequency_hz: float = 2.7e6
    mode_shape: str = "gaussian_linear"
    analysis_mode: str = "cluster"
    single_channel: str = "both"
    mode_samples: int | None = None
    variance_window_modes: int | None = None
    calibration_estimator: str = "auto"
    measurement_basis: str = "x"
    variance_window_seconds: float = 1 / 3_000
    variance_estimator: str = "rolling"
    min_samples_per_mode: int = 16
    mode_oversample: int = 32
    channel_b_delay_samples: int = 0
    gate_absolute_threshold: float | None = None
    explicit_mode_template: tuple[float, ...] = ()
    nullifiers: tuple[NullifierSpec, ...] = (DEFAULT_CLUSTER_X,)
    history_modes: int = 5_000
    window_mode: str = "continuous"
    lock_seconds: float = 0.005
    collect_seconds: float = 0.005
    collection_guard_seconds: float = 0.0
    collection_end_guard_samples: int = 0
    trigger_channel: str = "a"
    trigger_edge: str = "rising"
    trigger_level_volts: float = 0.0
    trigger_delay_seconds: float = 0.0
    trigger_holdoff_seconds: float = 0.010
    trigger_hysteresis_volts: float = 0.0

    def __post_init__(self) -> None:
        if self.measurement_basis == "p" and self.nullifiers == (DEFAULT_CLUSTER_X,):
            object.__setattr__(self, "nullifiers", (DEFAULT_CLUSTER_P,))
        for name in ("mode_width_seconds", "measurement_frequency_hz",
                     "variance_window_seconds", "collect_seconds"):
            value = getattr(self, name)
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        for name in ("lock_seconds", "trigger_delay_seconds", "trigger_holdoff_seconds",
                     "trigger_hysteresis_volts", "collection_guard_seconds"):
            value = getattr(self, name)
            if not np.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be non-negative and finite")
        if not np.isfinite(self.trigger_level_volts):
            raise ValueError("trigger_level_volts must be finite")
        for name in ("min_samples_per_mode", "mode_oversample", "channel_b_delay_samples",
                     "history_modes", "collection_end_guard_samples"):
            if isinstance(getattr(self, name), bool) or not isinstance(
                getattr(self, name), (int, np.integer)
            ):
                raise ValueError(f"{name} must be an integer")
        if self.min_samples_per_mode < 2:
            raise ValueError("min_samples_per_mode must be at least 2")
        if self.mode_oversample < 1:
            raise ValueError("mode_oversample must be at least 1")
        if self.collection_end_guard_samples < 0:
            raise ValueError("collection_end_guard_samples must be non-negative")
        if self.collection_guard_seconds >= self.collect_seconds:
            raise ValueError("collection guard must be shorter than the collect interval")
        if self.gate_absolute_threshold is not None and (
            not np.isfinite(self.gate_absolute_threshold) or self.gate_absolute_threshold <= 0
        ):
            raise ValueError("gate_absolute_threshold must be positive and finite")
        if self.mode_shape not in {
            "identity", "gaussian_linear", "gaussian_linear_cosine", "lab_gaussian_linear_cosine"
        }:
            raise ValueError("unknown mode_shape")
        if self.analysis_mode not in {"cluster", "single"}:
            raise ValueError("analysis_mode must be cluster or single")
        if self.single_channel not in {"a", "b", "both"}:
            raise ValueError("single_channel must be a, b, or both")
        if self.calibration_estimator not in {"auto", "pooled", "segment_mean"}:
            raise ValueError("unknown calibration_estimator")
        for name in ("mode_samples", "variance_window_modes"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool)
                    or not isinstance(value, (int, np.integer)) or value < 2):
                raise ValueError(f"{name} must be an integer of at least 2")
        if self.measurement_basis not in {"x", "p"}:
            raise ValueError("measurement_basis must be x or p")
        if self.window_mode not in {"continuous", "periodic", "triggered"}:
            raise ValueError("window_mode must be continuous, periodic, or triggered")
        if self.variance_estimator not in {"rolling", "ensemble", "lab_rolling"}:
            raise ValueError("variance_estimator must be rolling, ensemble, or lab_rolling")
        if self.analysis_mode == "single" and self.variance_estimator != "rolling":
            raise ValueError("single-mode squeezing requires rolling variance")
        if self.trigger_channel not in {"a", "b"}:
            raise ValueError("trigger_channel must be a or b")
        if self.trigger_edge not in {"rising", "falling"}:
            raise ValueError("trigger_edge must be rising or falling")
        if self.explicit_mode_template:
            template = np.asarray(self.explicit_mode_template, dtype=np.float64)
            if not np.all(np.isfinite(template)) or not np.any(template):
                raise ValueError("explicit_mode_template must have finite, nonzero norm")
            if self.mode_samples is not None and len(template) != self.mode_samples:
                raise ValueError("explicit template length must match N_mode")
        if not self.nullifiers:
            raise ValueError("at least one nullifier is required")
        if len({spec.name for spec in self.nullifiers}) != len(self.nullifiers):
            raise ValueError("nullifier names must be unique")
        for spec in self.nullifiers:
            if not isinstance(spec.name, str) or not spec.name.strip() or not spec.terms:
                raise ValueError("each nullifier requires a nonempty name and terms")
            if isinstance(spec.start, bool) or not isinstance(spec.start, (int, np.integer)) \
                    or spec.start < 0:
                raise ValueError("nullifier start must be a non-negative integer")
            for term in spec.terms:
                if term.source not in {"a", "b"}:
                    raise ValueError(f"unknown quadrature source: {term.source}; use a or b")
                if isinstance(term.offset, bool) or not isinstance(term.offset, (int, np.integer)) \
                        or term.offset < 0:
                    raise ValueError("nullifier offset must be a non-negative integer")
                if not np.isfinite(term.weight):
                    raise ValueError("nullifier weight must be finite")
            if nullifier_shot_noise_variance(spec) <= 0:
                raise ValueError("nullifier combined weights must not all be zero")
        if self.history_modes < 100:
            raise ValueError("history_modes must be at least 100")
        if (self.variance_window_modes is not None
                and self.variance_window_modes > self.history_modes):
            raise ValueError("rolling window cannot exceed retained mode history")


@dataclass(frozen=True, slots=True)
class ClusterProcessingResult:
    """Processed display data.

    Amplitudes start at accepted-mode index ``mode_index_start``; ensemble
    variances use local collection indices from ``variance_index_starts``.
    """

    mode_period_seconds: float
    mode_index_start: int
    quadratures: Mapping[str, FloatArray]
    nullifiers: Mapping[str, FloatArray]
    variances: Mapping[str, FloatArray]
    squeezing_db: Mapping[str, FloatArray]
    shot_noise_coefficients: Mapping[str, float]
    calibrated: bool
    calibrating: bool
    rejected_modes: int
    status: str
    measurement_basis: str = "x"
    acquisition_state: str = "continuous"
    collection_windows: int = 0
    accepted_samples: int = 0
    skipped_samples: int = 0
    variance_estimator: str = "rolling"
    variance_counts: Mapping[str, npt.NDArray[np.int64]] = field(default_factory=dict)
    variance_index_starts: Mapping[str, int] = field(default_factory=dict)
    analysis_mode: str = "cluster"
    mode_samples: int = 0
    variance_window_modes: int = 0
    display_stride: int = 1


def build_gauss_lin_cos_mode(
    mode_width_seconds: float, measurement_frequency_hz: float,
    time_axis_seconds: npt.NDArray[np.floating],
) -> FloatArray:
    """Build the Gaussian-linear-cosine mode for a frequency in hertz."""
    if mode_width_seconds <= 0:
        raise ValueError("mode_width_seconds must be positive")
    if measurement_frequency_hz <= 0:
        raise ValueError("measurement_frequency_hz must be positive")
    time_axis = np.asarray(time_axis_seconds, dtype=np.float64)
    if time_axis.ndim != 1 or time_axis.size == 0:
        raise ValueError("time_axis_seconds must be a non-empty 1-D array")

    center_time = 0.5 * (float(time_axis[0]) + float(time_axis[-1]))
    time_diff = time_axis - center_time
    angular_frequency = 2 * np.pi * measurement_frequency_hz
    in_window = np.abs(time_diff) <= mode_width_seconds
    envelope = np.exp(-0.5 * (angular_frequency**2) * (time_diff**2))
    mode = time_diff * np.cos(angular_frequency * time_diff) * envelope
    return np.asarray(np.where(in_window, mode, 0.0), dtype=np.float64)


def normalize_mode(mode_template: npt.NDArray[np.floating], dt_seconds: float) -> FloatArray:
    if dt_seconds <= 0:
        raise ValueError("dt_seconds must be positive")
    mode = np.asarray(mode_template, dtype=np.float64)
    if mode.ndim != 1 or mode.size == 0:
        raise ValueError("mode_template must be a non-empty 1-D array")
    norm = float(np.sqrt(np.sum(mode * mode, dtype=np.float64) * dt_seconds))
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError("mode_template has zero or invalid norm")
    return np.asarray(mode / norm, dtype=np.float64)


def lab_processing_config(measurement_basis: str = "x") -> ClusterProcessingConfig:
    """The supplied lab script's active settings, with the requested 5/5 ms gate."""
    return ClusterProcessingConfig(
        mode_width_seconds=308e-9, measurement_frequency_hz=6e6,
        mode_shape="lab_gaussian_linear_cosine", mode_oversample=1000,
        measurement_basis=measurement_basis, min_samples_per_mode=4,
        channel_b_delay_samples=2, variance_estimator="lab_rolling",
        collection_guard_seconds=6e-7 + 40 * 20e-6,
        collection_end_guard_samples=1000,
        nullifiers=(LAB_CLUSTER_P if measurement_basis == "p" else LAB_CLUSTER_X,),
        window_mode="periodic", history_modes=25_000,
    )


def single_mode_processing_config(
    mode_samples: int | None = None, variance_window_modes: int = 100,
) -> ClusterProcessingConfig:
    """Lab-shaped rail squeezing, or a flat mode with an explicit sample count."""
    return replace(
        lab_processing_config(),
        analysis_mode="single", mode_samples=mode_samples,
        mode_shape="lab_gaussian_linear_cosine" if mode_samples is None else "identity",
        min_samples_per_mode=4 if mode_samples is None else 2,
        channel_b_delay_samples=2 if mode_samples is None else 0,
        variance_window_modes=variance_window_modes,
        calibration_estimator="pooled", variance_estimator="rolling",
    )


def processing_nullifiers(config: ClusterProcessingConfig) -> tuple[NullifierSpec, ...]:
    if config.analysis_mode == "single":
        return tuple(spec for channel, spec in (("a", SINGLE_MODE_A), ("b", SINGLE_MODE_B))
                     if config.single_channel in {channel, "both"})
    return config.nullifiers


def _segment_calibration(config: ClusterProcessingConfig) -> bool:
    return config.calibration_estimator == "segment_mean" or (
        config.calibration_estimator == "auto" and config.analysis_mode == "cluster"
        and config.mode_shape == "lab_gaussian_linear_cosine"
    )


def _variance_points(config: ClusterProcessingConfig, period: float) -> int:
    if config.variance_window_modes is not None:
        return config.variance_window_modes
    if config.variance_estimator == "lab_rolling":
        return max(int(config.variance_window_seconds / period), 10)
    return max(round(config.variance_window_seconds / period), 2)


def mode_sample_count(width_seconds: float, sample_rate_hz: float, shape: str) -> int:
    """Quantize a requested width using the selected template's sample-grid rule."""
    if any(not np.isfinite(value) or value <= 0 for value in (width_seconds, sample_rate_hz)):
        raise ValueError("mode width and sample rate must be positive and finite")
    points = width_seconds / (1 / sample_rate_hz)
    return (int(np.nextafter(points, np.inf)) if shape == "lab_gaussian_linear_cosine"
            else round(points))


def build_lab_mode_template(
    mode_width_seconds: float, measurement_frequency_hz: float, dt_seconds: float,
    *, oversample: int = 1000, min_samples_per_mode: int = 4,
) -> FloatArray:
    """Lab mode: cosine frequency has no 2*pi; center is a fine-grid sample.

    Normalize before interpolation and report the integer-truncated tiled period.
    """
    if any(not np.isfinite(value) or value <= 0 for value in (
        mode_width_seconds, measurement_frequency_hz, dt_seconds
    )):
        raise ValueError("mode parameters must be positive and finite")
    if oversample < 1:
        raise ValueError("oversample must be at least 1")
    points = mode_sample_count(mode_width_seconds, 1 / dt_seconds, "lab_gaussian_linear_cosine")
    if points < min_samples_per_mode:
        raise ValueError(
            f"temporal mode has only {points} samples; at least {min_samples_per_mode} "
            "are required"
        )
    if points * oversample > 2_000_000:
        raise ValueError("lab template fine grid exceeds 2 million points")
    fine_dt = dt_seconds / oversample
    time = np.arange(0, mode_width_seconds, fine_dt, dtype=np.float64)
    delta = time - time[len(time) // 2]
    raw = delta * np.cos(delta * measurement_frequency_hz) * np.exp(
        -0.5 * (2 * np.pi * measurement_frequency_hz * delta) ** 2
    )
    raw[np.abs(delta) > mode_width_seconds] = 0
    normalized = normalize_mode(raw, fine_dt)
    target = np.linspace(0, mode_width_seconds, points, endpoint=False)
    return np.asarray(np.interp(target, time, normalized), dtype=np.float64)


def validate_mode_sampling(
    mode_width_seconds: float, measurement_frequency_hz: float, sample_rate_hz: float,
    *, min_samples_per_mode: int = 16,
) -> int:
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    if sample_rate_hz < 2 * measurement_frequency_hz:
        raise ValueError(
            f"sample rate {sample_rate_hz:g} Hz is below the "
            f"{2 * measurement_frequency_hz:g} Hz Nyquist requirement"
        )
    points = round(mode_width_seconds * sample_rate_hz)
    if points < min_samples_per_mode:
        raise ValueError(
            f"temporal mode has only {points} samples; at least {min_samples_per_mode} are required"
        )
    return points


def build_mode_template(
    mode_width_seconds: float, measurement_frequency_hz: float, dt_seconds: float,
    *, oversample: int = 32, min_samples_per_mode: int = 2,
) -> FloatArray:
    """Sample and normalize the analytic mode on the actual ADC sample grid."""
    if dt_seconds <= 0:
        raise ValueError("dt_seconds must be positive")
    if oversample < 1:
        raise ValueError("oversample must be at least 1")
    points = validate_mode_sampling(
        mode_width_seconds, measurement_frequency_hz, 1 / dt_seconds,
        min_samples_per_mode=min_samples_per_mode,
    )

    offsets = (np.arange(oversample, dtype=np.float64) + 0.5) / oversample
    fine_time = (np.arange(points, dtype=np.float64)[:, None] + offsets[None, :]) * dt_seconds
    fine_mode = build_gauss_lin_cos_mode(
        mode_width_seconds, measurement_frequency_hz, fine_time.reshape(-1),
    ).reshape(points, oversample)
    sampled = np.asarray(np.mean(fine_mode, axis=1, dtype=np.float64), dtype=np.float64)
    return normalize_mode(sampled, dt_seconds)


def build_gauss_lin_mode_template(
    mode_width_seconds: float, bandwidth_hz: float, dt_seconds: float,
    *, oversample: int = 32, min_samples_per_mode: int = 16,
) -> FloatArray:
    """Sample Larsen chapter 3, Eq. 3.25: N t exp(-kappa² t² / 2).

    Support is one centered bin; kappa = 2 pi bandwidth. Minimum samples set
    resolution, since this Gaussian has no hard Nyquist cutoff.
    """
    if not np.isfinite(dt_seconds) or dt_seconds <= 0:
        raise ValueError("dt_seconds must be positive and finite")
    if not np.isfinite(mode_width_seconds) or mode_width_seconds <= 0:
        raise ValueError("mode_width_seconds must be positive and finite")
    if not np.isfinite(bandwidth_hz) or bandwidth_hz <= 0:
        raise ValueError("bandwidth_hz must be positive and finite")
    points = round(mode_width_seconds / dt_seconds)
    if points < min_samples_per_mode:
        raise ValueError(
            f"temporal mode has only {points} samples; at least {min_samples_per_mode} "
            "are required"
        )
    offsets = (np.arange(oversample, dtype=np.float64) + 0.5) / oversample
    fine_time = (np.arange(points)[:, None] + offsets[None, :]) * dt_seconds
    fine_time -= points * dt_seconds / 2
    kappa = 2 * np.pi * bandwidth_hz
    raw = fine_time * np.exp(-0.5 * (kappa * fine_time) ** 2)
    raw[np.abs(fine_time) >= mode_width_seconds / 2] = 0.0
    sampled = np.asarray(np.mean(raw, axis=1, dtype=np.float64), dtype=np.float64)
    return normalize_mode(sampled, dt_seconds)


def rolling_variance(
    signal: npt.NDArray[np.number], window_points: int, *, lab_window: bool = False,
    require_full_window: bool = False,
) -> FloatArray:
    """Population variance in an exact-width centered window, ignoring non-finite values."""
    if window_points < 1:
        raise ValueError("window_points must be at least 1")
    values = np.asarray(signal, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("signal must be a 1-D array")
    if values.size == 0:
        return np.empty(0, dtype=np.float64)

    left = window_points // 2
    right = left if lab_window else window_points - left
    indices = np.arange(values.size)
    starts = np.maximum(0, indices - left)
    stops = np.minimum(values.size, indices + right)
    finite = np.isfinite(values)
    all_finite = bool(np.all(finite))
    # Center the signal to avoid cancellation for large DC offsets.
    origin = float(values[finite.argmax()]) if np.any(finite) else 0.0
    if all_finite:
        clean = values - origin
        counts = stops - starts
    else:
        clean = np.where(finite, values - origin, 0.0)
        prefix_count = np.concatenate(([0], np.cumsum(finite, dtype=np.int64)))
        counts = prefix_count[stops] - prefix_count[starts]
    prefix = np.concatenate(([0.0], np.cumsum(clean, dtype=np.float64)))
    prefix_sq = np.concatenate(([0.0], np.cumsum(clean * clean, dtype=np.float64)))
    sums = prefix[stops] - prefix[starts]
    sums_sq = prefix_sq[stops] - prefix_sq[starts]
    output = np.full(values.size, np.nan, dtype=np.float64)
    valid = counts == window_points if require_full_window else counts >= 2
    means = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    calculated = np.divide(sums_sq, counts, out=np.zeros_like(sums_sq), where=counts > 0)
    output[valid] = np.maximum(calculated[valid] - means[valid] ** 2, 0.0)
    return output


def compute_nullifier(
    quadratures: Mapping[str, npt.NDArray[np.number]], spec: NullifierSpec,
) -> FloatArray:
    if not spec.terms:
        raise ValueError("nullifier spec must contain at least one term")
    if spec.start < 0:
        raise ValueError("nullifier start must be non-negative")

    arrays = {name: np.asarray(value, dtype=np.float64) for name, value in quadratures.items()}
    for term in spec.terms:
        if term.offset < 0:
            raise ValueError("nullifier term offsets must be non-negative")
        if term.source not in arrays:
            raise KeyError(f"missing quadrature source: {term.source}")
        if arrays[term.source].ndim != 1:
            raise ValueError("quadrature sources must be 1-D arrays")

    stop = min(arrays[term.source].size - term.offset for term in spec.terms)
    count = stop - spec.start
    if count <= 0:
        return np.empty(0, dtype=np.float64)

    result = np.zeros(count, dtype=np.float64)
    for term in spec.terms:
        source = arrays[term.source]
        result += term.weight * source[spec.start + term.offset : stop + term.offset]
    return result


def nullifier_shot_noise_variance(spec: NullifierSpec) -> float:
    """Reference variance after independent sources are normalized to vacuum variance 1/2."""
    if not spec.terms:
        raise ValueError("nullifier spec must contain at least one term")
    combined: dict[tuple[str, int], float] = {}
    for term in spec.terms:
        key = (term.source, term.offset)
        combined[key] = combined.get(key, 0.0) + term.weight
    return float(0.5 * sum(weight * weight for weight in combined.values()))


def cluster_squeezing_db(
    nullifier_variance: npt.NDArray[np.number],
    shot_noise_variance: float = 2.0,
) -> FloatArray:
    if shot_noise_variance <= 0 or not np.isfinite(shot_noise_variance):
        raise ValueError("shot_noise_variance must be positive and finite")
    variance = np.asarray(nullifier_variance, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        result = 10 * np.log10(variance / shot_noise_variance)
    return np.asarray(result, dtype=np.float64)


class _RunningMoments:
    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

    def update(self, values: FloatArray) -> None:
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            return
        count = int(finite.size)
        mean = float(np.mean(finite))
        m2 = float(np.sum((finite - mean) ** 2, dtype=np.float64))
        if self.count == 0:
            self.count, self.mean, self.m2 = count, mean, m2
            return
        total = self.count + count
        delta = mean - self.mean
        self.m2 += m2 + delta * delta * self.count * count / total
        self.mean += delta * count / total
        self.count = total

    @property
    def variance(self) -> float:
        return self.m2 / self.count if self.count else float("nan")


class _LabWindowStatistics:
    """Equal-weight per-index averages of completed segments, including mean dB."""

    def __init__(self, config: ClusterProcessingConfig) -> None:
        self.config = config
        self.current = _empty_quadratures()
        self.sums: dict[str, FloatArray] = {}
        self.db_sums: dict[str, FloatArray] = {}
        self.counts: dict[str, npt.NDArray[np.int64]] = {}
        self.db_counts: dict[str, npt.NDArray[np.int64]] = {}
        self.completed = 0
        self.prefix_limited = False

    def append(self, sources: dict[str, FloatArray]) -> None:
        for name, values in sources.items():
            combined = np.concatenate((self.current[name], values))
            if combined.size > self.config.history_modes:
                self.prefix_limited = True
            self.current[name] = combined[:self.config.history_modes]

    def close(self, period: float, calibrated: bool) -> None:
        if self.current["a"].size == 0:
            return
        points = _variance_points(self.config, period)
        for spec in processing_nullifiers(self.config):
            trace = compute_nullifier(self.current, spec)
            variance = rolling_variance(trace, points, lab_window=True)
            self._add(spec.name, variance, self.sums, self.counts)
            if calibrated:
                db = cluster_squeezing_db(variance, nullifier_shot_noise_variance(spec))
                self._add(spec.name, db, self.db_sums, self.db_counts)
        self.current = _empty_quadratures()
        self.completed += 1

    @staticmethod
    def _add(
        name: str, values: FloatArray, sums: dict[str, FloatArray],
        counts: dict[str, npt.NDArray[np.int64]],
    ) -> None:
        if name not in sums:
            sums[name] = np.zeros(values.size, dtype=np.float64)
            counts[name] = np.zeros(values.size, dtype=np.int64)
        elif sums[name].size < values.size:
            extra = values.size - sums[name].size
            sums[name] = np.pad(sums[name], (0, extra))
            counts[name] = np.pad(counts[name], (0, extra))
        # Zero variance means -inf dB and must remain -inf in the average.
        valid = ~np.isnan(values)
        indices = np.flatnonzero(valid)
        sums[name][indices] += values[valid]
        counts[name][indices] += 1

    def snapshot(self) -> tuple[dict[str, FloatArray], dict[str, FloatArray]]:
        def means(
            sums: dict[str, FloatArray], counts: dict[str, npt.NDArray[np.int64]]
        ) -> dict[str, FloatArray]:
            return {name: np.divide(values, counts[name],
                                   out=np.full(values.size, np.nan), where=counts[name] > 0)
                    for name, values in sums.items()}
        return means(self.sums, self.counts), means(self.db_sums, self.db_counts)


class RealtimeClusterProcessor:
    """Stateful mode extraction, calibration, and nullifier processing for packet streams."""

    _pending: dict[str, FloatArray]

    def __init__(self, config: ClusterProcessingConfig | None = None) -> None:
        self.config = config or ClusterProcessingConfig()
        self._sample_rate_hz: float | None = None
        self._mode = np.empty(0, dtype=np.float64)
        self._moments = {"a": _RunningMoments(), "b": _RunningMoments()}
        self._shot_noise_coefficients: dict[str, float] = {}
        self._calibrating = False
        self._rejected_modes = 0
        self._calibration_window = {name: _RunningMoments() for name in ("a", "b")}
        self._calibration_segment_variances: dict[str, list[float]] = {"a": [], "b": []}
        self._calibration_segment_counts: dict[str, int] = {"a": 0, "b": 0}
        self.reset_stream()

    @property
    def calibrated(self) -> bool:
        required = {term.source for spec in processing_nullifiers(self.config)
                    for term in spec.terms}
        return required <= self._shot_noise_coefficients.keys()

    @property
    def calibrating(self) -> bool:
        return self._calibrating

    @property
    def calibration_mode_counts(self) -> Mapping[str, int]:
        if _segment_calibration(self.config):
            return {name: (self._calibration_segment_counts[name]
                           + self._calibration_window[name].count)
                    for name in self._moments}
        return {name: moments.count for name, moments in self._moments.items()}

    @property
    def shot_noise_coefficients(self) -> Mapping[str, float]:
        return dict(self._shot_noise_coefficients)

    @property
    def acquisition_state(self) -> str:
        return self._window_gate.state if self._window_gate is not None else "not started"

    @property
    def accepted_samples(self) -> int:
        return self._window_gate.accepted_samples if self._window_gate is not None else 0

    @property
    def skipped_samples(self) -> int:
        return self._window_gate.skipped_samples if self._window_gate is not None else 0

    def configure(self, config: ClusterProcessingConfig) -> None:
        self.config = config
        self._sample_rate_hz = None
        self._mode = np.empty(0, dtype=np.float64)
        self.clear_calibration()
        self.reset_stream()

    def reset_stream(self, *, preserve_acquisition: bool = False) -> None:
        if preserve_acquisition:
            # Keep the bin phase, but exclude any mode spanning the change
            # between reference and signal. Copies avoid changing caller arrays.
            self._pending = {name: np.full(values.shape, np.nan, dtype=np.float64)
                             for name, values in self._pending.items()}
        else:
            self._pending = _empty_quadratures()
            self._delay_remaining = abs(self.config.channel_b_delay_samples)
            self._current_window_id: int | None = None
            self._window_gate: AcquisitionWindowGate | None = None
            self._mode_index = 0
        self._quadrature_history = _empty_quadratures()
        self._history_window_ids = np.empty(0, dtype=np.int64)
        self._ensemble = self._new_ensemble()
        self._lab_statistics = _LabWindowStatistics(self.config)
        for moments in self._calibration_window.values():
            moments.clear()

    def _new_ensemble(self) -> EnsembleNullifierVariance:
        return EnsembleNullifierVariance(
            {spec.name: partial(compute_nullifier, spec=spec)
             for spec in processing_nullifiers(self.config)},
            max_modes=self.config.history_modes,
        )

    def _close_window(self) -> None:
        """Discard incomplete modes and alignment tails at every collect boundary."""
        if _segment_calibration(self.config) and self._calibrating:
            self._commit_calibration_window()
        if self.config.variance_estimator == "lab_rolling" and self._sample_rate_hz:
            self._lab_statistics.close(self._mode.size / self._sample_rate_hz, self.calibrated)
        self._pending = _empty_quadratures()
        self._delay_remaining = abs(self.config.channel_b_delay_samples)
        self._current_window_id = None

    def start_calibration(self) -> None:
        self.clear_calibration()
        self._calibrating = True

    def finish_calibration(
        self, *, minimum_modes: int = 100, required_sources: set[str] | None = None,
    ) -> Mapping[str, float]:
        if _segment_calibration(self.config):
            self._commit_calibration_window()
        coefficients: dict[str, float] = {}
        required = required_sources if required_sources is not None else (
            {term.source for spec in processing_nullifiers(self.config) for term in spec.terms}
            if self.config.analysis_mode == "single" else {"a", "b"}
        )
        for name, moments in self._moments.items():
            if name not in required:
                continue
            count = (self._calibration_segment_counts[name] if _segment_calibration(self.config)
                     else moments.count)
            if count < minimum_modes:
                raise ValueError(
                    f"shot-noise calibration for channel {name.upper()} has {count} modes; "
                    f"at least {minimum_modes} are required"
                )
            segments = self._calibration_segment_variances[name]
            variance = (float(np.mean(segments)) if segments
                        and _segment_calibration(self.config)
                        else moments.variance)
            if not np.isfinite(variance) or variance <= 0:
                raise ValueError(f"shot-noise variance for channel {name.upper()} is invalid")
            coefficients[name] = float(1 / np.sqrt(2 * variance))
        self._shot_noise_coefficients = coefficients
        self._calibrating = False
        self.reset_stream(preserve_acquisition=True)
        return dict(coefficients)

    def clear_calibration(self) -> None:
        self._calibrating = False
        self._shot_noise_coefficients.clear()
        self._calibration_segment_variances = {"a": [], "b": []}
        self._calibration_segment_counts = {"a": 0, "b": 0}
        for moments in self._moments.values():
            moments.clear()
        self.reset_stream(preserve_acquisition=True)

    def _commit_calibration_window(self) -> None:
        for name, moments in self._calibration_window.items():
            if moments.count >= 2:
                self._calibration_segment_variances[name].append(moments.variance)
                self._calibration_segment_counts[name] += moments.count
            moments.clear()

    def _ensure_mode(self, sample_rate_hz: float) -> None:
        if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive and finite")
        if self._sample_rate_hz == sample_rate_hz and self._mode.size:
            return
        width = (float(np.nextafter(self.config.mode_samples / sample_rate_hz, np.inf))
                 if self.config.mode_samples is not None else self.config.mode_width_seconds)
        if self.config.explicit_mode_template:
            raw = np.asarray(self.config.explicit_mode_template, dtype=np.float64)
            if raw.size < self.config.min_samples_per_mode:
                raise ValueError(
                    f"explicit temporal mode has {raw.size} samples; at least "
                    f"{self.config.min_samples_per_mode} are required"
                )
            mode = normalize_mode(raw, 1 / sample_rate_hz)
        elif self.config.mode_shape == "identity":
            points = self.config.mode_samples or mode_sample_count(
                width, sample_rate_hz, self.config.mode_shape
            )
            if points < self.config.min_samples_per_mode:
                raise ValueError(f"temporal mode has only {points} samples; at least "
                                 f"{self.config.min_samples_per_mode} are required")
            mode = normalize_mode(np.ones(points, dtype=np.float64),
                                  1 / sample_rate_hz)
        else:
            builder = {
                "lab_gaussian_linear_cosine": build_lab_mode_template,
                "gaussian_linear": build_gauss_lin_mode_template,
                "gaussian_linear_cosine": build_mode_template,
            }[self.config.mode_shape]
            mode = builder(
                width, self.config.measurement_frequency_hz, 1 / sample_rate_hz,
                oversample=self.config.mode_oversample,
                min_samples_per_mode=self.config.min_samples_per_mode,
            )
        if self._sample_rate_hz is not None and self._sample_rate_hz != sample_rate_hz:
            # Changing ADC rate invalidates the integral and vacuum normalization.
            self.clear_calibration()
            self.reset_stream()
        self._sample_rate_hz = sample_rate_hz
        self._mode = mode

    def _ensure_window_gate(self, sample_rate_hz: float) -> AcquisitionWindowGate:
        if self._window_gate is None:
            self._window_gate = AcquisitionWindowGate(
                mode=self.config.window_mode,
                sample_rate_hz=sample_rate_hz,
                lock_seconds=self.config.lock_seconds,
                collect_seconds=self.config.collect_seconds,
                guard_seconds=self.config.collection_guard_seconds,
                end_guard_samples=self.config.collection_end_guard_samples,
                trigger_channel=self.config.trigger_channel,
                trigger_edge=self.config.trigger_edge,
                trigger_level_volts=self.config.trigger_level_volts,
                trigger_delay_seconds=self.config.trigger_delay_seconds,
                trigger_holdoff_seconds=self.config.trigger_holdoff_seconds,
                trigger_hysteresis_volts=self.config.trigger_hysteresis_volts,
            )
        return self._window_gate

    def feed(
        self, channel_a: npt.NDArray[np.number], channel_b: npt.NDArray[np.number],
        sample_rate_hz: float, *, discontinuity: bool = False,
    ) -> ClusterProcessingResult | None:
        if discontinuity:
            self.reset_stream()
        self._ensure_mode(sample_rate_hz)
        a = np.asarray(channel_a, dtype=np.float64)
        b = np.asarray(channel_b, dtype=np.float64)
        gate = self._ensure_window_gate(sample_rate_hz)
        spans = gate.select(a, b)
        extracted_a: list[FloatArray] = []
        extracted_b: list[FloatArray] = []
        result_start = self._mode_index
        for span in spans:
            if self._current_window_id != span.window_id:
                self._close_window()
                self._current_window_id = span.window_id
            raw = self._extract_quadratures(a[span.start:span.stop],
                                            b[span.start:span.stop], sample_rate_hz)
            if raw is not None:
                extracted_a.append(raw["a"])
                extracted_b.append(raw["b"])
                if self._calibrating:
                    for name, values in raw.items():
                        self._moments[name].update(values)
                        self._calibration_window[name].update(values)
                else:
                    self._append_history(raw, span.window_id)
            if span.closes_window:
                self._close_window()
        if not extracted_a:
            return None
        raw_quadratures = {"a": np.concatenate(extracted_a), "b": np.concatenate(extracted_b)}
        period = self._mode.size / sample_rate_hz
        if self._calibrating:
            status = ("calibrating shot noise: "
                      f"A={self._moments['a'].count}, B={self._moments['b'].count} modes")
            return self._result(raw_quadratures, result_start, period, status)
        return self._live_result(raw_quadratures, period)

    def _append_history(self, raw_quadratures: dict[str, FloatArray], window_id: int) -> None:
        normalized: dict[str, FloatArray] = {}
        for name, raw in raw_quadratures.items():
            values = raw * self._shot_noise_coefficients.get(name, 1.0)
            normalized[name] = values
            combined = np.concatenate((self._quadrature_history[name], values))
            self._quadrature_history[name] = combined[-self.config.history_modes:]
        ids = np.full(raw_quadratures["a"].size, window_id, dtype=np.int64)
        self._history_window_ids = np.concatenate((self._history_window_ids, ids))[
            -self.config.history_modes:
        ]
        if self.config.variance_estimator == "ensemble":
            self._ensemble.append(normalized, window_id)
        elif self.config.variance_estimator == "lab_rolling":
            self._lab_statistics.append(normalized)

    def _extract_quadratures(
        self, channel_a: npt.NDArray[np.number], channel_b: npt.NDArray[np.number],
        sample_rate_hz: float,
    ) -> dict[str, FloatArray] | None:
        """Buffer complete temporal modes and return their two quadratures."""
        channels = {name: np.asarray(values, dtype=np.float64).reshape(-1)
                    for name, values in (("a", channel_a), ("b", channel_b))}
        if self._delay_remaining:
            name = "b" if self.config.channel_b_delay_samples > 0 else "a"
            skipped = min(self._delay_remaining, channels[name].size)
            channels[name] = channels[name][skipped:]
            self._delay_remaining -= skipped
        for name, values in channels.items():
            if values.size:
                pending = self._pending[name]
                self._pending[name] = np.concatenate((pending, values)) if pending.size else values
        mode_count = min(values.size for values in self._pending.values()) // self._mode.size
        if mode_count == 0:
            return None
        usable = mode_count * self._mode.size
        modes = {name: values[:usable].reshape(mode_count, self._mode.size)
                 for name, values in self._pending.items()}
        quadratures = {name: values @ self._mode * (1 / sample_rate_hz)
                       for name, values in modes.items()}
        threshold = self.config.gate_absolute_threshold
        if threshold is not None:
            valid = np.ones(mode_count, dtype=np.bool_)
            for samples in modes.values():
                valid &= np.all(np.isfinite(samples), axis=1)
                valid &= np.max(np.abs(samples), axis=1) < threshold
            self._rejected_modes += int(np.count_nonzero(~valid))
            for values in quadratures.values():
                values[~valid] = np.nan
        self._pending = {name: values[usable:] for name, values in self._pending.items()}
        self._mode_index += mode_count
        return quadratures

    def _result(
        self, quadratures: dict[str, FloatArray], start: int, period: float,
        status: str, **values: Any,
    ) -> ClusterProcessingResult:
        values = {"nullifiers": {}, "variances": {}, "squeezing_db": {}, **values}
        return ClusterProcessingResult(
            mode_period_seconds=period, mode_index_start=start, quadratures=quadratures,
            shot_noise_coefficients=dict(self._shot_noise_coefficients),
            calibrated=self.calibrated, calibrating=self.calibrating,
            rejected_modes=self._rejected_modes, status=status,
            variance_estimator=self.config.variance_estimator,
            analysis_mode=self.config.analysis_mode,
            mode_samples=self._mode.size,
            variance_window_modes=_variance_points(self.config, period),
            measurement_basis=self.config.measurement_basis,
            acquisition_state=self.acquisition_state,
            collection_windows=self._window_gate.collection_windows if self._window_gate else 0,
            accepted_samples=self.accepted_samples, skipped_samples=self.skipped_samples,
            **values,
        )

    def _live_result(
        self, raw_quadratures: dict[str, FloatArray], period: float
    ) -> ClusterProcessingResult:
        normalized = {
            name: values * self._shot_noise_coefficients.get(name, 1.0)
            for name, values in raw_quadratures.items()
        }
        nullifiers: dict[str, FloatArray] = {}
        variances: dict[str, FloatArray] = {}
        squeezing: dict[str, FloatArray] = {}
        variance_counts: dict[str, npt.NDArray[np.int64]] = {}
        lab_rolling = self.config.variance_estimator == "lab_rolling"
        # Ensemble moments and completed lab windows already provide variance.
        # Only calculate a rolling trace when it is used in the returned result.
        use_rolling = self.config.variance_estimator != "ensemble" and not (
            lab_rolling and self._lab_statistics.completed
        )
        variance_points = _variance_points(self.config, period)
        boundaries = np.concatenate((
            [0], np.flatnonzero(np.diff(self._history_window_ids)) + 1,
            [self._history_window_ids.size],
        ))
        first_nullifier_indices: list[int] = []
        for spec in processing_nullifiers(self.config):
            chunks: list[FloatArray] = []
            variance_chunks: list[FloatArray] = []
            for start, stop in pairwise(boundaries):
                sources = {name: values[start:stop]
                           for name, values in self._quadrature_history.items()}
                chunk = compute_nullifier(sources, spec)
                if chunk.size:
                    first_nullifier_indices.append(
                        self._mode_index - self._history_window_ids.size + int(start) + spec.start
                    )
                    if chunks:
                        chunks.append(np.array([np.nan]))
                        if use_rolling:
                            variance_chunks.append(np.array([np.nan]))
                    chunks.append(chunk)
                    if use_rolling:
                        variance_chunks.append(
                            rolling_variance(
                                chunk, variance_points, lab_window=lab_rolling,
                                require_full_window=self.config.analysis_mode == "single",
                            )
                        )
            nullifier = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float64)
            variance = (np.concatenate(variance_chunks) if variance_chunks
                        else np.empty(0, dtype=np.float64))
            nullifiers[spec.name] = nullifier
            variances[spec.name] = variance
        if self.config.variance_estimator == "ensemble":
            variances, variance_counts = self._ensemble.snapshot()
        elif lab_rolling and self._lab_statistics.completed:
            variances, squeezing = self._lab_statistics.snapshot()
            variance_counts = dict(self._lab_statistics.counts)
        if self.calibrated:
            for spec in processing_nullifiers(self.config):
                if not (lab_rolling and self._lab_statistics.completed):
                    squeezing[spec.name] = cluster_squeezing_db(
                        variances[spec.name], nullifier_shot_noise_variance(spec)
                    )

        status = ("live calibrated nullifiers" if self.calibrated
                  else "uncalibrated nullifiers; acquire shot noise and calibrate")
        if self.config.analysis_mode == "single":
            status = ("live single-mode squeezing" if self.calibrated
                      else "single-mode variance; acquire shot noise and calibrate")
            status += f"; N_mode={self._mode.size}; full {variance_points}-mode variance windows"
        if self.config.variance_estimator == "ensemble":
            status += "; ensemble across aligned collection windows"
            if self._ensemble.prefix_limited:
                status += (f"; first {self.config.history_modes} temporal modes "
                           "per collection retained")
            if not any(np.any(counts >= 2) for counts in variance_counts.values()):
                status += "; waiting for at least 2 observations per temporal index"
        elif lab_rolling:
            status += (f"; lab rolling / mean dB across {self._lab_statistics.completed} "
                       "completed windows" if self._lab_statistics.completed else
                       "; lab rolling preview; collecting first window")
            if self._lab_statistics.prefix_limited:
                status += f"; first {self.config.history_modes} modes/window retained"

        return self._result(
            normalized, min(first_nullifier_indices, default=self._mode_index), period, status,
            nullifiers=nullifiers,
            variances=variances,
            squeezing_db=squeezing,
            variance_counts=variance_counts,
            variance_index_starts=(
                {spec.name: spec.start for spec in processing_nullifiers(self.config)}
                if self.config.variance_estimator in {"ensemble", "lab_rolling"} else {}
            ),
        )


def _config_key(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.title() for part in rest)


def config_to_dict(config: ClusterProcessingConfig) -> dict[str, object]:
    result = {_config_key(item.name): getattr(config, item.name) for item in fields(config)}
    result["explicitModeTemplate"] = list(config.explicit_mode_template)
    result["nullifiers"] = [
        {"name": spec.name, "start": spec.start, "terms": [asdict(term) for term in spec.terms]}
        for spec in config.nullifiers
    ]
    return result


def config_from_dict(data: Mapping[str, object]) -> ClusterProcessingConfig:
    raw_nullifiers = data.get("nullifiers")
    specs: list[NullifierSpec] = []
    if raw_nullifiers is not None:
        if not isinstance(raw_nullifiers, list) or not raw_nullifiers:
            raise ValueError("nullifiers must be a nonempty list")
        for raw_spec in raw_nullifiers:
            if not isinstance(raw_spec, dict):
                raise ValueError("each nullifier must be an object")
            if not isinstance(raw_spec.get("name"), str) or not raw_spec["name"].strip():
                raise ValueError("each nullifier requires a nonempty string name")
            raw_terms = raw_spec.get("terms")
            if not isinstance(raw_terms, list):
                raise ValueError("each nullifier requires a terms list")
            if not raw_terms or any(not isinstance(term, dict) for term in raw_terms):
                raise ValueError("each nullifier requires nonempty object terms")
            if any(not isinstance(term.get("source"), str) for term in raw_terms):
                raise ValueError("each nullifier term requires a string source")
            terms = tuple(
                NullifierTerm(
                    term["source"], _as_int(term.get("offset", 0)),
                    _as_float(term.get("weight", 1.0)),
                )
                for term in raw_terms
            )
            specs.append(NullifierSpec(raw_spec["name"], terms, _as_int(raw_spec.get("start", 0))))
    explicit = data.get("explicitModeTemplate", [])
    if not isinstance(explicit, list):
        raise ValueError("explicitModeTemplate must be a list")
    defaults = ClusterProcessingConfig()
    values: dict[str, Any] = {}
    converters: dict[type, Callable[[object], object]] = {float: _as_float, int: _as_int, str: str}
    for item in fields(defaults):
        key = _config_key(item.name)
        if key not in data or item.name in {"nullifiers", "explicit_mode_template"}:
            continue
        value, default = data[key], getattr(defaults, item.name)
        if default is None:
            convert = _as_float if item.name == "gate_absolute_threshold" else _as_int
            values[item.name] = None if value in (None, "") else convert(value)
        else:
            values[item.name] = converters[type(default)](value)
    return replace(
        defaults, **values,
        explicit_mode_template=tuple(_as_float(value) for value in explicit),
        nullifiers=tuple(specs) or defaults.nullifiers,
    )


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"expected a number, got {type(value).__name__}")
    result = float(value)
    if not np.isfinite(result):
        raise ValueError("numeric values must be finite")
    return result


def _as_int(value: object) -> int:
    number = _as_float(value)
    if not number.is_integer():
        raise ValueError(f"expected an integer, got {value!r}")
    return int(number)
