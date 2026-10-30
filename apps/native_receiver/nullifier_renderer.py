"""Shared nullifier chart rendering for the embedded view and configuration dialog."""

from __future__ import annotations

from collections.abc import Mapping
from tkinter import ttk
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
        unit = ("mean window dB relative to shot noise"
                if result.variance_estimator == "lab_rolling" else "dB relative to shot noise")
        return result.squeezing_db, unit, True
    if choice == "Amplitude":
        return result.nullifiers, ("mode quadrature" if result.analysis_mode == "single"
                                  else "nullifier amplitude"), False
    unit = (
        "ensemble variance at each mode"
        if result.variance_estimator == "ensemble"
        else ("mean window rolling variance" if result.variance_estimator == "lab_rolling"
              else "rolling variance across modes")
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


def clip_trace(
    positions: Any, values: Any, low: float, high: float,
) -> list[np.ndarray[Any, Any]]:
    """Clip line segments to the Y viewport without inventing flat boundary values."""
    lines = []
    current: list[tuple[float, float]] = []
    for index in range(len(values) - 1):
        x0, x1 = float(positions[index]), float(positions[index + 1])
        y0, y1 = float(values[index]), float(values[index + 1])
        delta = y1 - y0
        if delta == 0:
            first, last = (0.0, 1.0) if low <= y0 <= high else (1.0, 0.0)
        else:
            ends = sorted(((low - y0) / delta, (high - y0) / delta))
            first, last = max(0.0, ends[0]), min(1.0, ends[1])
        if first > last:
            if current:
                lines.append(np.asarray(current))
                current = []
            continue
        start = ((x0, y0) if first == 0 else (x0 + (x1 - x0) * first, y0 + delta * first))
        stop = ((x1, y1) if last == 1 else (x0 + (x1 - x0) * last, y0 + delta * last))
        if current and current[-1] != start:
            lines.append(np.asarray(current))
            current = []
        if not current:
            current.append(start)
        current.append(stop)
    if current:
        lines.append(np.asarray(current))
    return lines


class NullifierRendererMixin:
    palette: dict[str, str]
    canvas: Any
    readout: Any
    plot_choice: Any

    def _set_readout(self, text: str) -> None:
        if self.readout.get() != text:
            self.readout.set(text)

    def _build_plot_controls(self, parent: Any) -> Any:
        controls = ttk.Frame(parent, style="Toolbar.TFrame")
        for title, command in (
            ("Time +", lambda: self._zoom_plot_time(0.5)),
            ("Time -", lambda: self._zoom_plot_time(2.0)),
            ("Y +", lambda: self._zoom_plot_y(0.5)),
            ("Y -", lambda: self._zoom_plot_y(2.0)),
            ("Fit", self._fit_plot),
        ):
            ttk.Button(controls, text=title, width=5, command=command,
                       style="Compact.TButton").pack(
                side="left", padx=2
            )
        return controls

    def _clear_plot_view(self) -> None:
        self._last_chart_result = None
        self._drawn_result = None
        self._plot_bounds = None
        self._plot_key = None
        self._plot_view_modes = None
        self._render_signature = None
        self._held_notice = None
        self._fit_requested = False
        for item in getattr(self, "_chart_items", {}).values():
            self.canvas.itemconfigure(item, state="hidden")
        self._chart_specs = {}

    def _chart_item(self, kind: str, key: str, *coordinates: Any, **options: Any) -> Any:
        if not hasattr(self, "_chart_items"):
            self._chart_items: dict[str, Any] = {}
            self._chart_specs: dict[str, Any] = {}
        options = dict(options, state="normal")
        item = self._chart_items.get(key)
        old = self._chart_specs.get(key)
        if item is None:
            item = getattr(self.canvas, f"create_{kind}")(
                *coordinates, tags=("nullifier_chart", key), **options
            )
            self._chart_items[key] = item
        else:
            if old is None or old[0] != coordinates:
                self.canvas.coords(item, *coordinates)
            if old is None or old[1] != options:
                self.canvas.itemconfigure(item, **options)
        self._chart_specs[key] = (coordinates, options)
        if hasattr(self, "_active_chart_items"):
            self._active_chart_items.add(key)
        return item

    def _finish_chart_scene(self) -> None:
        for key, item in self._chart_items.items():
            if key not in self._active_chart_items:
                old = self._chart_specs.get(key)
                if old is not None and old[1].get("state") != "hidden":
                    self.canvas.itemconfigure(item, state="hidden")
                    self._chart_specs[key] = (old[0], dict(old[1], state="hidden"))
        del self._active_chart_items

    def _redraw_plot_view(self) -> None:
        self._render_signature = None
        result = getattr(self, "_last_chart_result", None)
        notice = getattr(self, "_held_notice", None)
        if result is not None:
            self._draw_result(result)
            if notice:
                self._draw_message(notice)

    def _zoom_plot_time(self, factor: float) -> None:
        count = getattr(self, "_plot_view_modes", None) or 2000
        self._plot_view_modes = max(100, min(100_000, round(count * factor)))
        self._redraw_plot_view()

    def _zoom_plot_y(self, factor: float) -> None:
        bounds = getattr(self, "_plot_bounds", None)
        if bounds is None:
            return
        low, high = bounds
        if low == 0:
            self._plot_bounds = (0.0, max(high * factor, 1e-30))
        else:
            center = (low + high) / 2
            result = getattr(self, "_last_chart_result", None)
            if result is not None:
                traces, _, logarithmic = select_nullifier_traces(result, self.plot_choice.get())
                if logarithmic:
                    finite = [np.asarray(v)[np.isfinite(v)] for v in traces.values()]
                    finite = [v for v in finite if v.size]
                    if finite:
                        center = float(np.median(np.concatenate(finite)))
            half = max((high - low) * factor / 2, abs(center) * 1e-12, 1e-30)
            self._plot_bounds = (center - half, center + half)
        self._redraw_plot_view()

    def _fit_plot(self) -> None:
        result = getattr(self, "_last_chart_result", None)
        if result is None:
            return
        traces, _, _ = select_nullifier_traces(result, self.plot_choice.get())
        starts = result.variance_index_starts if self.plot_choice.get() != "Amplitude" else {}
        count = max((len(v) * result.display_stride + int(starts.get(name, 0))
                     for name, v in traces.items()), default=2000)
        self._plot_view_modes = max(
            100, count
        )
        self._plot_bounds = None
        self._fit_requested = True
        self._redraw_plot_view()

    def _draw_message(self, message: str) -> None:
        holding = getattr(self, "_last_chart_result", None) is not None
        self._render_signature = None
        self._held_notice = message if holding else None
        if not holding:
            self._active_chart_items = set()
        self._chart_item("text", "notice",
            max(self.canvas.winfo_width(), 1) / 2,
            max(self.canvas.winfo_height(), 1) / 2,
            text=(f"{message}\nShowing last valid chart; no current estimate"
                  if holding else message),
            fill=self.palette["text_secondary"],
            width=max(self.canvas.winfo_width() - 80, 100),
            justify="center",
        )
        if holding:
            self._set_readout("Showing last valid chart · no current estimate")
        else:
            self._finish_chart_scene()

    def _draw_result(self, result: ClusterProcessingResult) -> None:
        choice = self.plot_choice.get()
        traces, unit, logarithmic = select_nullifier_traces(result, choice)
        key = (unit, result.analysis_mode, result.variance_estimator, result.calibrating)
        if getattr(self, "_plot_key", None) != key:
            self._clear_plot_view()
            self._plot_key = key
        signature = (id(result), choice, self.canvas.winfo_width(), self.canvas.winfo_height(),
                     getattr(self, "_plot_view_modes", None), getattr(self, "_plot_bounds", None))
        if getattr(self, "_render_signature", None) == signature:
            return
        self._drawn_result = result
        if choice == "Squeezing (dB)" and not result.calibrated:
            self._draw_message("Calibrate shot noise to display squeezing in dB")
            self._set_readout("Shot-noise calibration required")
            return
        ensemble_plot = result.variance_estimator in {"ensemble", "lab_rolling"} \
            and choice != "Amplitude"
        view_count = getattr(self, "_plot_view_modes", None) or (25_000 if ensemble_plot else 2000)
        self._plot_view_modes = view_count
        stride = result.display_stride
        view_points = max(1, (view_count + stride - 1) // stride)
        displayed = {
            name: np.asarray(values[:view_points] if ensemble_plot else values[-view_points:])
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
            zero_variance = logarithmic and any(np.any(np.isneginf(v)) for v in displayed.values())
            message = ("Zero variance gives -infinity dB; no finite squeezing estimate"
                       if zero_variance else "Waiting for complete, finite mode estimates")
            if result.calibrating:
                message = result.status
            self._draw_message(message)
            if getattr(self, "_last_chart_result", None) is None:
                self._set_readout(message)
            return
        self._last_chart_result = result
        self._held_notice = None
        self._active_chart_items = set()
        width = max(self.canvas.winfo_width(), 100)
        height = max(self.canvas.winfo_height(), 100)
        narrow = width < 1000
        left, top, right, bottom = 62, 26, 16, 44 if narrow else 28
        plot_width = max(width - left - right, 1)
        plot_height = max(height - top - bottom, 1)
        bounds = getattr(self, "_plot_bounds", None)
        if bounds is None:
            low = min(float(np.min(v)) for v in finite_sets)
            high = max(float(np.max(v)) for v in finite_sets)
            if logarithmic:
                if getattr(self, "_fit_requested", False):
                    low, high = min(0.0, low), max(0.0, high)
                    margin = max(high - low, 1.0) * 0.1
                    low, high = low - margin, high + margin
                else:
                    low, high = min(-10.0, low), max(3.0, high)
            elif choice == "Amplitude":
                bound = max(abs(low), abs(high), 1e-12)
                low, high = -bound, bound
            else:
                low, high = 0.0, max(high * 1.1, 1e-12)
            if high <= low:
                high = low + 1
            self._plot_bounds = (low, high)
            self._fit_requested = False
        else:
            low, high = bounds
        for index in range(5):
            y = top + index * plot_height / 4
            value = high - index * (high - low) / 4
            self._chart_item("line", f"grid:{index}", left, y, left + plot_width, y,
                             fill=self.palette["plot_grid"])
            self._chart_item("text", f"ytick:{index}",
                left - 7, y, text=f"{value:.2g}", fill=self.palette["text_secondary"], anchor="e"
            )
            x = left + index * plot_width / 4
            self._chart_item("line", f"xgrid:{index}", x, top, x, top + plot_height,
                             fill=self.palette["plot_grid"])
        if logarithmic and low <= 0 <= high:
            y = top + high / (high - low) * plot_height
            self._chart_item("line", "zero",
                left, y, left + plot_width, y, fill=self.palette["text_secondary"], dash=(4, 3)
            )
        latest: list[str] = []
        for index, (name, shown) in enumerate(displayed.items()):
            color = TRACE_COLORS[index % len(TRACE_COLORS)]
            for run_index, (positions, run) in enumerate(finite_runs(shown, max_points=2000)):
                offset = int(index_starts.get(name, 0))
                offset = offset if ensemble_plot else view_count - 1 - (shown.size - 1) * stride
                denominator = max(view_count - 1, 1)
                positions = positions * stride
                admitted = positions + offset <= view_count - 1
                positions, run = positions[admitted], run[admitted]
                for piece, line in enumerate(clip_trace(positions, run, low, high)):
                    coords = np.empty(line.size, dtype=np.float64)
                    coords[0::2] = left + (line[:, 0] + offset) / denominator * plot_width
                    coords[1::2] = top + (high - line[:, 1]) / (high - low) * plot_height
                    self._chart_item("line", f"trace:{name}:{run_index}:{piece}",
                                     *coords.tolist(), fill=color, width=2)
                if run.size == 1 and low <= run[0] <= high:
                    x = left + (positions[0] + offset) / denominator * plot_width
                    y = top + (high - run[0]) / (high - low) * plot_height
                    self._chart_item("oval", f"point:{name}:{run_index}",
                                     x - 1, y - 1, x + 1, y + 1, fill=color, outline=color)
            finite = shown[np.isfinite(shown)]
            if finite.size:
                latest.append(f"{name}={float(finite[-1]):.4g}")
            self._chart_item("text", f"legend:{name}",
                left + index * 165,
                12,
                text=name,
                fill=color,
                anchor="w",
                font=("Segoe UI", 9, "bold"),
            )
        duration_ms = view_count * result.mode_period_seconds * 1e3
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
        elif result.analysis_mode == "single":
            time_label += f" · {result.variance_window_modes} modes/window"
        self._chart_item("text", "time-label",
            left,
            height - (27 if narrow else 10),
            text=time_label,
            fill=self.palette["text_secondary"],
            anchor="w",
        )
        self._chart_item("text", "unit-label",
            left if narrow else left + plot_width,
            height - 10,
            text=unit,
            fill=self.palette["text_secondary"],
            anchor="w" if narrow else "e",
        )
        suffix = " dB" if logarithmic else ""
        outside = any(np.any((v < low) | (v > high)) for v in finite_sets)
        outside_x = ensemble_plot and domain_count > view_count
        note = " · Outside view range: use Fit or zoom out" if outside or outside_x else ""
        self._set_readout(" · ".join(latest) + suffix + note)
        self._finish_chart_scene()
        self._render_signature = (
            id(result), choice, width, height, view_count, self._plot_bounds
        )
