from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeAlias

import numpy as np
import numpy.typing as npt

FloatArray: TypeAlias = npt.NDArray[np.float64]


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
    name="cluster_x",
    terms=(
        NullifierTerm("a", 0, 1.0),
        NullifierTerm("a", 1, 1.0),
        NullifierTerm("b", 0, 1.0),
        NullifierTerm("b", 1, -1.0),
    ),
)

DEFAULT_CLUSTER_P = NullifierSpec(
    name="cluster_p",
    terms=(
        NullifierTerm("a", 0, 1.0),
        NullifierTerm("a", 1, -1.0),
        NullifierTerm("b", 0, 1.0),
        NullifierTerm("b", 1, 1.0),
    ),
    start=1,
)


@dataclass(frozen=True, slots=True)
class ClusterProcessingConfig:
    """Configuration for continuous two-channel cluster-state processing.

    ``channel_b_delay_samples`` is the measured delay of channel B relative to
    channel A. Positive values pair A[n] with B[n + delay].
    """

    mode_width_seconds: float = 0.308e-6
    measurement_frequency_hz: float = 6e6
    variance_window_seconds: float = 1 / 3_000
    min_samples_per_mode: int = 16
    mode_oversample: int = 32
    channel_b_delay_samples: int = 0
    gate_absolute_threshold: float | None = None
    explicit_mode_template: tuple[float, ...] = ()
    nullifiers: tuple[NullifierSpec, ...] = field(
        default_factory=lambda: (DEFAULT_CLUSTER_X, DEFAULT_CLUSTER_P)
    )
    history_modes: int = 5_000

    def __post_init__(self) -> None:
        if self.mode_width_seconds <= 0:
            raise ValueError("mode_width_seconds must be positive")
        if self.measurement_frequency_hz <= 0:
            raise ValueError("measurement_frequency_hz must be positive")
        if self.variance_window_seconds <= 0:
            raise ValueError("variance_window_seconds must be positive")
        if self.min_samples_per_mode < 2:
            raise ValueError("min_samples_per_mode must be at least 2")
        if self.mode_oversample < 1:
            raise ValueError("mode_oversample must be at least 1")
        if self.gate_absolute_threshold is not None and self.gate_absolute_threshold <= 0:
            raise ValueError("gate_absolute_threshold must be positive")
        if not self.nullifiers:
            raise ValueError("at least one nullifier is required")
        if len({spec.name for spec in self.nullifiers}) != len(self.nullifiers):
            raise ValueError("nullifier names must be unique")
        if self.history_modes < 100:
            raise ValueError("history_modes must be at least 100")


@dataclass(frozen=True, slots=True)
class ClusterProcessingResult:
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


def build_gauss_lin_cos_mode(
    mode_width_seconds: float,
    measurement_frequency_hz: float,
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


def validate_mode_sampling(
    mode_width_seconds: float,
    measurement_frequency_hz: float,
    sample_rate_hz: float,
    *,
    min_samples_per_mode: int = 16,
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
    mode_width_seconds: float,
    measurement_frequency_hz: float,
    dt_seconds: float,
    *,
    oversample: int = 32,
    min_samples_per_mode: int = 2,
) -> FloatArray:
    """Sample and normalize the analytic mode on the actual ADC sample grid."""
    if dt_seconds <= 0:
        raise ValueError("dt_seconds must be positive")
    if oversample < 1:
        raise ValueError("oversample must be at least 1")
    points = validate_mode_sampling(
        mode_width_seconds,
        measurement_frequency_hz,
        1 / dt_seconds,
        min_samples_per_mode=min_samples_per_mode,
    )

    offsets = (np.arange(oversample, dtype=np.float64) + 0.5) / oversample
    fine_time = (
        np.arange(points, dtype=np.float64)[:, np.newaxis] + offsets[np.newaxis, :]
    ) * dt_seconds
    fine_mode = build_gauss_lin_cos_mode(
        mode_width_seconds,
        measurement_frequency_hz,
        fine_time.reshape(-1),
    ).reshape(points, oversample)
    sampled = np.asarray(np.mean(fine_mode, axis=1, dtype=np.float64), dtype=np.float64)
    return normalize_mode(sampled, dt_seconds)


def tile_mode(mode_template: npt.NDArray[np.floating], total_points: int) -> FloatArray:
    mode = np.asarray(mode_template, dtype=np.float64)
    if mode.ndim != 1 or mode.size == 0:
        raise ValueError("mode_template must be a non-empty 1-D array")
    if total_points < 0:
        raise ValueError("total_points must be non-negative")
    if total_points == 0:
        return np.empty(0, dtype=np.float64)
    return np.asarray(np.resize(mode, total_points), dtype=np.float64)


def apply_long_mode_to_trace(
    signal: npt.NDArray[np.number],
    long_mode: npt.NDArray[np.floating],
    short_mode: npt.NDArray[np.floating],
    dt_seconds: float,
) -> tuple[FloatArray, FloatArray, int]:
    if dt_seconds <= 0:
        raise ValueError("dt_seconds must be positive")
    values = np.asarray(signal, dtype=np.float64)
    tiled = np.asarray(long_mode, dtype=np.float64)
    mode = np.asarray(short_mode, dtype=np.float64)
    if values.ndim != 1 or tiled.ndim != 1 or mode.ndim != 1:
        raise ValueError("signal and modes must be 1-D arrays")
    if mode.size == 0:
        raise ValueError("short_mode must not be empty")
    if tiled.size < values.size:
        raise ValueError("long_mode must cover the full signal")

    points_per_mode = mode.size
    mode_count = values.size // points_per_mode
    usable = mode_count * points_per_mode
    modulated = np.zeros(values.size, dtype=np.float64)
    if usable == 0:
        return modulated, np.empty(0, dtype=np.float64), points_per_mode

    modulated[:usable] = values[:usable] * tiled[:usable]
    quadratures = modulated[:usable].reshape(mode_count, points_per_mode).sum(axis=1)
    return modulated, np.asarray(quadratures * dt_seconds, dtype=np.float64), points_per_mode


def rolling_variance(signal: npt.NDArray[np.number], window_points: int) -> FloatArray:
    """Population variance in an exact-width centered window, ignoring non-finite values."""
    if window_points < 1:
        raise ValueError("window_points must be at least 1")
    values = np.asarray(signal, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("signal must be a 1-D array")
    if values.size == 0:
        return np.empty(0, dtype=np.float64)

    left = window_points // 2
    right = window_points - left
    indices = np.arange(values.size)
    starts = np.maximum(0, indices - left)
    stops = np.minimum(values.size, indices + right)
    finite = np.isfinite(values)
    clean = np.where(finite, values, 0.0)
    prefix_count = np.concatenate(([0], np.cumsum(finite, dtype=np.int64)))
    prefix = np.concatenate(([0.0], np.cumsum(clean, dtype=np.float64)))
    prefix_sq = np.concatenate(([0.0], np.cumsum(clean * clean, dtype=np.float64)))
    counts = prefix_count[stops] - prefix_count[starts]
    sums = prefix[stops] - prefix[starts]
    sums_sq = prefix_sq[stops] - prefix_sq[starts]
    output = np.full(values.size, np.nan, dtype=np.float64)
    valid = counts >= 2
    means = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    calculated = np.divide(sums_sq, counts, out=np.zeros_like(sums_sq), where=counts > 0)
    output[valid] = np.maximum(calculated[valid] - means[valid] ** 2, 0.0)
    return output


def shot_noise_cluster_coefficient(
    quadrature_sets: Sequence[npt.NDArray[np.number]],
) -> float:
    variances = [
        float(np.var(np.asarray(quadratures, dtype=np.float64)))
        for quadratures in quadrature_sets
        if np.asarray(quadratures).size > 1
    ]
    if not variances:
        raise ValueError("at least one shot-noise quadrature set with two values is required")
    mean_variance = float(np.mean(variances))
    if mean_variance <= 0 or not np.isfinite(mean_variance):
        raise ValueError("shot-noise variance must be positive and finite")
    return float(1 / np.sqrt(2 * mean_variance))


def compute_nullifier(
    quadratures: Mapping[str, npt.NDArray[np.number]],
    spec: NullifierSpec,
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
    return float(0.5 * sum(term.weight * term.weight for term in spec.terms))


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
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

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


class RealtimeClusterProcessor:
    """Stateful mode extraction, calibration, and nullifier processing for packet streams."""

    def __init__(self, config: ClusterProcessingConfig | None = None) -> None:
        self.config = config or ClusterProcessingConfig()
        self._sample_rate_hz: float | None = None
        self._mode = np.empty(0, dtype=np.float64)
        self._pending_a = np.empty(0, dtype=np.float64)
        self._pending_b = np.empty(0, dtype=np.float64)
        self._delay_initialized = False
        self._mode_index = 0
        self._quadrature_history: dict[str, FloatArray] = {
            "a": np.empty(0, dtype=np.float64),
            "b": np.empty(0, dtype=np.float64),
        }
        self._moments = {"a": _RunningMoments(), "b": _RunningMoments()}
        self._shot_noise_coefficients: dict[str, float] = {}
        self._calibrating = False
        self._rejected_modes = 0

    @property
    def calibrated(self) -> bool:
        return set(self._shot_noise_coefficients) == {"a", "b"}

    @property
    def calibrating(self) -> bool:
        return self._calibrating

    @property
    def calibration_mode_counts(self) -> Mapping[str, int]:
        return {name: moments.count for name, moments in self._moments.items()}

    @property
    def shot_noise_coefficients(self) -> Mapping[str, float]:
        return dict(self._shot_noise_coefficients)

    def configure(self, config: ClusterProcessingConfig) -> None:
        self.config = config
        self._sample_rate_hz = None
        self._mode = np.empty(0, dtype=np.float64)
        self.clear_calibration()
        self.reset_stream()

    def reset_stream(self) -> None:
        self._pending_a = np.empty(0, dtype=np.float64)
        self._pending_b = np.empty(0, dtype=np.float64)
        self._delay_initialized = False
        self._mode_index = 0
        self._quadrature_history = {
            "a": np.empty(0, dtype=np.float64),
            "b": np.empty(0, dtype=np.float64),
        }

    def start_calibration(self) -> None:
        for moments in self._moments.values():
            moments.clear()
        self._calibrating = True
        self._shot_noise_coefficients.clear()
        self.reset_stream()

    def finish_calibration(self, *, minimum_modes: int = 100) -> Mapping[str, float]:
        coefficients: dict[str, float] = {}
        for name, moments in self._moments.items():
            if moments.count < minimum_modes:
                raise ValueError(
                    f"shot-noise calibration for channel {name.upper()} has {moments.count} modes; "
                    f"at least {minimum_modes} are required"
                )
            if not np.isfinite(moments.variance) or moments.variance <= 0:
                raise ValueError(f"shot-noise variance for channel {name.upper()} is invalid")
            coefficients[name] = float(1 / np.sqrt(2 * moments.variance))
        self._shot_noise_coefficients = coefficients
        self._calibrating = False
        self.reset_stream()
        return dict(coefficients)

    def clear_calibration(self) -> None:
        self._calibrating = False
        self._shot_noise_coefficients.clear()
        for moments in self._moments.values():
            moments.clear()
        self.reset_stream()

    def _ensure_mode(self, sample_rate_hz: float) -> None:
        if self._sample_rate_hz == sample_rate_hz and self._mode.size:
            return
        if self.config.explicit_mode_template:
            if sample_rate_hz <= 0:
                raise ValueError("sample_rate_hz must be positive")
            raw = np.asarray(self.config.explicit_mode_template, dtype=np.float64)
            if raw.size < self.config.min_samples_per_mode:
                raise ValueError(
                    f"explicit temporal mode has {raw.size} samples; at least "
                    f"{self.config.min_samples_per_mode} are required"
                )
            mode = normalize_mode(raw, 1 / sample_rate_hz)
        else:
            mode = build_mode_template(
                self.config.mode_width_seconds,
                self.config.measurement_frequency_hz,
                1 / sample_rate_hz,
                oversample=self.config.mode_oversample,
                min_samples_per_mode=self.config.min_samples_per_mode,
            )
        if self._sample_rate_hz is not None and self._sample_rate_hz != sample_rate_hz:
            self.reset_stream()
        self._sample_rate_hz = sample_rate_hz
        self._mode = mode

    def feed(
        self,
        channel_a: npt.NDArray[np.number],
        channel_b: npt.NDArray[np.number],
        sample_rate_hz: float,
        *,
        discontinuity: bool = False,
    ) -> ClusterProcessingResult | None:
        if discontinuity:
            self.reset_stream()
        self._ensure_mode(sample_rate_hz)
        extracted = self._extract_quadratures(channel_a, channel_b, sample_rate_hz)
        if extracted is None:
            return None
        raw_quadratures, result_start, dt_seconds = extracted
        period = self._mode.size * dt_seconds
        if self._calibrating:
            return self._calibration_result(raw_quadratures, result_start, period)
        return self._live_result(raw_quadratures, period)

    def _extract_quadratures(
        self,
        channel_a: npt.NDArray[np.number],
        channel_b: npt.NDArray[np.number],
        sample_rate_hz: float,
    ) -> tuple[dict[str, FloatArray], int, float] | None:
        """Buffer complete temporal modes and return their two quadratures."""
        a = np.asarray(channel_a, dtype=np.float64).reshape(-1)
        b = np.asarray(channel_b, dtype=np.float64).reshape(-1)
        if not self._delay_initialized:
            delay = self.config.channel_b_delay_samples
            if delay > 0:
                b = b[min(delay, b.size) :]
            elif delay < 0:
                a = a[min(-delay, a.size) :]
            self._delay_initialized = True
        if a.size:
            self._pending_a = (
                a if self._pending_a.size == 0 else np.concatenate((self._pending_a, a))
            )
        if b.size:
            self._pending_b = (
                b if self._pending_b.size == 0 else np.concatenate((self._pending_b, b))
            )

        available = min(self._pending_a.size, self._pending_b.size)
        mode_count = available // self._mode.size
        if mode_count == 0:
            return None
        usable = mode_count * self._mode.size
        mode_a = self._pending_a[:usable].reshape(mode_count, self._mode.size)
        mode_b = self._pending_b[:usable].reshape(mode_count, self._mode.size)
        dt_seconds = 1 / sample_rate_hz
        quadrature_a = np.asarray(mode_a @ self._mode * dt_seconds, dtype=np.float64)
        quadrature_b = np.asarray(mode_b @ self._mode * dt_seconds, dtype=np.float64)

        threshold = self.config.gate_absolute_threshold
        if threshold is None:
            valid: npt.NDArray[np.bool_] | None = None
            rejected = 0
        else:
            valid = np.all(np.isfinite(mode_a), axis=1) & np.all(np.isfinite(mode_b), axis=1)
            valid &= np.max(np.abs(mode_a), axis=1) < threshold
            valid &= np.max(np.abs(mode_b), axis=1) < threshold
            rejected = int(np.count_nonzero(~valid))
        self._rejected_modes += rejected
        if valid is not None:
            quadrature_a[~valid] = np.nan
            quadrature_b[~valid] = np.nan
        self._pending_a = self._pending_a[usable:]
        self._pending_b = self._pending_b[usable:]
        result_start = self._mode_index
        self._mode_index += mode_count
        return {"a": quadrature_a, "b": quadrature_b}, result_start, dt_seconds

    def _calibration_result(
        self, raw_quadratures: dict[str, FloatArray], result_start: int, period: float
    ) -> ClusterProcessingResult:
        for name, values in raw_quadratures.items():
            self._moments[name].update(values)
        return ClusterProcessingResult(
            mode_period_seconds=period,
            mode_index_start=result_start,
            quadratures=raw_quadratures,
            nullifiers={},
            variances={},
            squeezing_db={},
            shot_noise_coefficients={},
            calibrated=False,
            calibrating=True,
            rejected_modes=self._rejected_modes,
            status=(
                "calibrating shot noise: "
                f"A={self._moments['a'].count}, B={self._moments['b'].count} modes"
            ),
        )

    def _live_result(
        self, raw_quadratures: dict[str, FloatArray], period: float
    ) -> ClusterProcessingResult:
        normalized = {
            name: values * self._shot_noise_coefficients.get(name, 1.0)
            for name, values in raw_quadratures.items()
        }
        for name, values in normalized.items():
            combined = np.concatenate((self._quadrature_history[name], values))
            self._quadrature_history[name] = combined[-self.config.history_modes :]

        nullifiers: dict[str, FloatArray] = {}
        variances: dict[str, FloatArray] = {}
        squeezing: dict[str, FloatArray] = {}
        variance_points = max(round(self.config.variance_window_seconds / period), 2)
        for spec in self.config.nullifiers:
            nullifier = compute_nullifier(self._quadrature_history, spec)
            variance = rolling_variance(nullifier, variance_points)
            nullifiers[spec.name] = nullifier
            variances[spec.name] = variance
            if self.calibrated:
                squeezing[spec.name] = cluster_squeezing_db(
                    variance, nullifier_shot_noise_variance(spec)
                )

        return ClusterProcessingResult(
            mode_period_seconds=period,
            mode_index_start=max(
                0, self._mode_index - max(map(len, nullifiers.values()), default=0)
            ),
            quadratures=normalized,
            nullifiers=nullifiers,
            variances=variances,
            squeezing_db=squeezing,
            shot_noise_coefficients=dict(self._shot_noise_coefficients),
            calibrated=self.calibrated,
            calibrating=False,
            rejected_modes=self._rejected_modes,
            status=(
                "live calibrated nullifiers"
                if self.calibrated
                else "uncalibrated nullifiers; acquire shot noise and calibrate"
            ),
        )


def config_to_dict(config: ClusterProcessingConfig) -> dict[str, object]:
    return {
        "modeWidthSeconds": config.mode_width_seconds,
        "measurementFrequencyHz": config.measurement_frequency_hz,
        "varianceWindowSeconds": config.variance_window_seconds,
        "minSamplesPerMode": config.min_samples_per_mode,
        "modeOversample": config.mode_oversample,
        "channelBDelaySamples": config.channel_b_delay_samples,
        "gateAbsoluteThreshold": config.gate_absolute_threshold,
        "explicitModeTemplate": list(config.explicit_mode_template),
        "historyModes": config.history_modes,
        "nullifiers": [
            {
                "name": spec.name,
                "start": spec.start,
                "terms": [
                    {"source": term.source, "offset": term.offset, "weight": term.weight}
                    for term in spec.terms
                ],
            }
            for spec in config.nullifiers
        ],
    }


def config_from_dict(data: Mapping[str, object]) -> ClusterProcessingConfig:
    raw_nullifiers = data.get("nullifiers")
    specs: list[NullifierSpec] = []
    if isinstance(raw_nullifiers, list):
        for raw_spec in raw_nullifiers:
            if not isinstance(raw_spec, dict):
                raise ValueError("each nullifier must be an object")
            raw_terms = raw_spec.get("terms")
            if not isinstance(raw_terms, list):
                raise ValueError("each nullifier requires a terms list")
            terms = tuple(
                NullifierTerm(
                    source=str(term["source"]),
                    offset=int(term.get("offset", 0)),
                    weight=float(term.get("weight", 1.0)),
                )
                for term in raw_terms
                if isinstance(term, dict)
            )
            specs.append(
                NullifierSpec(
                    name=str(raw_spec["name"]),
                    terms=terms,
                    start=int(raw_spec.get("start", 0)),
                )
            )
    explicit = data.get("explicitModeTemplate", [])
    if not isinstance(explicit, list):
        raise ValueError("explicitModeTemplate must be a list")
    return ClusterProcessingConfig(
        mode_width_seconds=_as_float(data.get("modeWidthSeconds", 0.308e-6)),
        measurement_frequency_hz=_as_float(data.get("measurementFrequencyHz", 6e6)),
        variance_window_seconds=_as_float(data.get("varianceWindowSeconds", 1 / 3_000)),
        min_samples_per_mode=_as_int(data.get("minSamplesPerMode", 16)),
        mode_oversample=_as_int(data.get("modeOversample", 32)),
        channel_b_delay_samples=_as_int(data.get("channelBDelaySamples", 0)),
        gate_absolute_threshold=(
            None
            if data.get("gateAbsoluteThreshold") in (None, "")
            else _as_float(data["gateAbsoluteThreshold"])
        ),
        explicit_mode_template=tuple(_as_float(value) for value in explicit),
        nullifiers=tuple(specs) if specs else (DEFAULT_CLUSTER_X, DEFAULT_CLUSTER_P),
        history_modes=_as_int(data.get("historyModes", 5_000)),
    )


def _as_float(value: object) -> float:
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"expected a number, got {type(value).__name__}")
    return float(value)


def _as_int(value: object) -> int:
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"expected an integer, got {type(value).__name__}")
    return int(value)
