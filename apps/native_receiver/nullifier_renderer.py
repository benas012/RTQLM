"""Shared nullifier chart rendering for the embedded view and configuration dialog."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from quantum_light_engine.post_processing import ClusterProcessingResult

TRACE_COLORS = ("#22c55e", "#38bdf8", "#f59e0b", "#f472b6", "#a78bfa")
PLOT_CHOICES = ("Auto", "Variance", "Squeezing (dB)", "Amplitude")


def select_nullifier_traces(
    result: ClusterProcessingResult, choice: str = "Auto"
) -> tuple[Mapping[str, Any], str, bool]:
    """Choose a scientifically labelled quantity, without treating amplitude as variance."""
    if choice == "Squeezing (dB)" or (choice == "Auto" and result.calibrated):
        return result.squeezing_db, "dB relative to shot noise", True
    if choice == "Amplitude":
        return result.nullifiers, "nullifier amplitude", False
    unit = (
        "ensemble variance at each mode"
        if result.variance_estimator == "ensemble"
        else "rolling variance across modes"
    )
    return result.variances, unit, False


def finite_runs(
    values: Any, max_points: int | None = None
) -> list[tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]]:
    """Keep excluded modes as chart gaps instead of drawing across a lock interval."""
    array = np.asarray(values, dtype=np.float64)
    indices = np.flatnonzero(np.isfinite(array))
    if not indices.size:
        return []
    boundaries = np.flatnonzero(np.diff(indices) != 1) + 1
    stride = max(1, int(np.ceil(array.size / max_points))) if max_points else 1
    output = []
    for run in np.split(indices, boundaries):
        selected = run[::stride]
        if selected[-1] != run[-1]:
            selected = np.append(selected, run[-1])
        output.append((selected, array[selected]))
    return output


class NullifierRendererMixin:
    palette: dict[str, str]
    canvas: Any
    readout: Any
    plot_choice: Any

    def _draw_message(self, message: str) -> None:
        self.canvas.delete("all")
        self.canvas.create_text(
            max(self.canvas.winfo_width(), 1) / 2,
            max(self.canvas.winfo_height(), 1) / 2,
            text=message,
            fill=self.palette["text_secondary"],
            width=max(self.canvas.winfo_width() - 80, 100),
            justify="center",
        )

    def _draw_result(self, result: ClusterProcessingResult) -> None:
        choice = self.plot_choice.get()
        traces, unit, logarithmic = select_nullifier_traces(result, choice)
        if choice == "Squeezing (dB)" and not result.calibrated:
            self._draw_message("Calibrate shot noise to display squeezing in dB")
            self.readout.set("Shot-noise calibration required")
            return
        ensemble_plot = result.variance_estimator == "ensemble" and choice != "Amplitude"
        displayed = {
            name: np.asarray(values if ensemble_plot else values[-2000:])
            for name, values in traces.items()
        }
        index_starts = result.variance_index_starts if ensemble_plot else {}
        domain_count = max(
            (int(index_starts.get(name, 0)) + len(values) for name, values in displayed.items()),
            default=0,
        )
        finite_sets = list(displayed.values())
        finite_sets = [v[np.isfinite(v)] for v in finite_sets if np.any(np.isfinite(v))]
        if not finite_sets:
            self._draw_message(result.status)
            self.readout.set(result.status)
            return
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 100)
        height = max(self.canvas.winfo_height(), 100)
        left, top, right, bottom = 62, 26, 16, 28
        plot_width = max(width - left - right, 1)
        plot_height = max(height - top - bottom, 1)
        low = min(float(np.min(v)) for v in finite_sets)
        high = max(float(np.max(v)) for v in finite_sets)
        if logarithmic:
            low, high = min(-10.0, low), max(3.0, high)
        elif choice == "Amplitude":
            bound = max(abs(low), abs(high), 1e-12)
            low, high = -bound, bound
        else:
            low, high = 0.0, max(high * 1.1, 1e-12)
        if high <= low:
            high = low + 1
        for index in range(5):
            y = top + index * plot_height / 4
            value = high - index * (high - low) / 4
            self.canvas.create_line(left, y, left + plot_width, y, fill=self.palette["plot_grid"])
            self.canvas.create_text(
                left - 7, y, text=f"{value:.2g}", fill=self.palette["text_secondary"], anchor="e"
            )
        if logarithmic:
            y = top + high / (high - low) * plot_height
            self.canvas.create_line(
                left, y, left + plot_width, y, fill=self.palette["text_secondary"], dash=(4, 3)
            )
        latest: list[str] = []
        for index, (name, shown) in enumerate(displayed.items()):
            color = TRACE_COLORS[index % len(TRACE_COLORS)]
            for positions, run in finite_runs(shown, max_points=2000):
                coords = np.empty(run.size * 2, dtype=np.float64)
                offset = int(index_starts.get(name, 0))
                denominator = max(domain_count - 1 if ensemble_plot else shown.size - 1, 1)
                coords[0::2] = left + (positions + offset) / denominator * plot_width
                coords[1::2] = top + (high - run) / (high - low) * plot_height
                if coords.size >= 4:
                    self.canvas.create_line(*coords.tolist(), fill=color, width=2)
                elif coords.size == 2:
                    x, y = coords
                    self.canvas.create_oval(x - 1, y - 1, x + 1, y + 1, fill=color, outline=color)
            finite = shown[np.isfinite(shown)]
            if finite.size:
                latest.append(f"{name}={float(finite[-1]):.4g}")
            self.canvas.create_text(
                left + index * 165,
                12,
                text=name,
                fill=color,
                anchor="w",
                font=("Segoe UI", 9, "bold"),
            )
        duration_ms = domain_count * result.mode_period_seconds * 1e3
        time_label = (
            f"within collection · 0-{duration_ms:.3g} ms"
            if ensemble_plot
            else f"accepted mode time · {duration_ms:.3g} ms"
        )
        if ensemble_plot:
            counts = [v[v > 0] for v in result.variance_counts.values() if np.any(v > 0)]
            if counts:
                minimum = min(int(np.min(v)) for v in counts)
                maximum = max(int(np.max(v)) for v in counts)
                time_label += f" · N={minimum}-{maximum} windows"
        self.canvas.create_text(
            left,
            height - 10,
            text=time_label,
            fill=self.palette["text_secondary"],
            anchor="w",
        )
        self.canvas.create_text(
            left + plot_width,
            height - 10,
            text=unit,
            fill=self.palette["text_secondary"],
            anchor="e",
        )
        suffix = " dB" if logarithmic else ""
        self.readout.set(" · ".join(latest) + suffix)
