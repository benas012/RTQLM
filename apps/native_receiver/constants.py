from __future__ import annotations

import logging
from pathlib import Path

MAX_POINTS = 800
# About 34 seconds at 488 kS/s; two int16 channels use at most 64 MiB.
# Plotting still reads only the selected display resolution from the ring.
HISTORY_MAX_SAMPLES = 16_777_216
TIME_ZOOM_MIN = 1 / 4096
DEFAULT_TIME_SPAN = "One batch"
TIME_SPANS_MS = {
    "One batch": None,
    "100 ms": 100,
    "500 ms": 500,
    "1 s": 1000,
    "5 s": 5000,
    "10 s": 10000,
    "30 s": 30000,
}
DEFAULT_SIGNAL_RESOLUTION = "Medium"
SIGNAL_RESOLUTION_POINTS = {
    "Low": 400,
    "Medium": MAX_POINTS,
    "High": 1600,
    "Ultra": 3200,
}
SIGNAL_PLOT_TOP_PAD = 14
# STEMlab 125-14 streams native signed 14-bit ADC counts inside int16
# containers. 8192 counts is therefore 1 V at the standard LV (1:1) input.
ADC_COUNTS_PER_VOLT = 8_192.0
TRIGGER_PRE_SAMPLES_FRACTION = 0.25
# Reject small threshold recrossings from ADC noise before accepting a new edge.
TRIGGER_HYSTERESIS_VOLTS = 0.01
TEST_SINE_FREQUENCY_HZ = 1_000
TEST_SINE_AMPLITUDE_VOLTS = 0.2
UI_FRAME_INTERVAL_MS = 16
WIDE_VIEW_FAST_REFRESH_SECONDS = 8.0
WIDE_VIEW_AUTO_INTERVAL_MS = 500
STATS_MIN_INTERVAL_MS = 200
SPECTRUM_MIN_INTERVAL_MS = 180
ENGINE_READY_TIMEOUT_SECONDS = 10.0
ENGINE_HEALTH_TIMEOUT_SECONDS = 1.0
ENGINE_CONNECT_TIMEOUT_SECONDS = 5.0
REDPITAYA_CONNECT_TIMEOUT_SECONDS = 360.0
REDPITAYA_START_TIMEOUT_SECONDS = 8.0
ENGINE_PING_INTERVAL_MS = 3000
ENGINE_PROCESS_TITLE = "RPs Server"
TOAST_VISIBLE_MS = 5000
TOAST_FADE_INTERVAL_MS = 50
TOAST_FADE_STEPS = 10
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DISCONNECTED_STATES = {"", "-", "disconnected"}
STREAMING_STATES = {"streaming", "reconnecting", "starting"}
READY_STATES = {"ready"}
UI_LOGGER = logging.getLogger("quantum_light_engine.native_ui")
CONNECTION_FAILURE_BUSY_LABELS = frozenset({"Starting Red Pitaya acquisition"})
CONNECTION_OVERLAY_BUSY_LABELS = frozenset(
    {"Opening sample stream", "Starting Red Pitaya acquisition"}
)
CONNECTION_OVERLAY_BG = "#10141b"
CONNECTION_OVERLAY_OPACITY = 0.70
CONNECTION_FAILED_NOTICE = "Red Pitaya is not connected to the RTQLM acquisition engine"
CONNECTION_FAILED_SPECTRUM_NOTICE = "Spectrum unavailable until Red Pitaya is connected"
DEFAULT_SIGNAL_SCRIPT = """\
# Available:
#   signals: dict[str, numpy.ndarray]
#   np: numpy
#
# Stable IDs include:
#   signals["selected.ch1"]
#   signals["selected.ch2"]
#   signals["<device_slug>.ch1"]
#   signals["<device_slug>.ch2"]
#
# Return one of:
#   array
#   (ch1, ch2)
#   {"ch1": array, "ch2": array}

def process(signals, np):
    # Example: show CH1 minus CH2 if both are available.
    ch1 = signals.get("selected.ch1")
    ch2 = signals.get("selected.ch2")
    if ch1 is not None and ch2 is not None:
        n = min(len(ch1), len(ch2))
        return {"ch1": ch1[:n] - ch2[:n], "ch2": ch2[:n]}
    return {"ch1": ch1}
"""
# Semantic visual tokens.  Tk widgets that cannot consume ttk styles still use
# this single palette so the instrument remains coherent and easy to tune.
COLORS = {
    "bg": "#10151e",
    "bg_deep": "#0c1119",
    "sidebar": "#131a25",
    "surface": "#19222f",
    "surface_elevated": "#202b3b",
    "surface_active": "#2b3b50",
    "hover": "#2a394c",
    "selected": "#243e54",
    "border_subtle": "#2b394c",
    "border_strong": "#496079",
    "text": "#edf3fa",
    "text_secondary": "#a8b8cc",
    "text_disabled": "#697c93",
    "accent": "#4dd4bf",
    "accent_hover": "#7ce3d3",
    "success": "#36c275",
    "warning": "#e7ae45",
    "danger": "#f06f83",
    "ch1": "#43c97b",
    "ch2": "#45c6e8",
    "plot_grid": "#1d2938",
    "plot_axis": "#425168",
    "plot_annotation": "#b6c0cf",
}
# Short aliases used by the Tk style definitions below.
COLORS.update(
    {
        "panel": COLORS["surface"],
        "panel_2": COLORS["surface_elevated"],
        "panel_3": COLORS["surface_active"],
        "muted": COLORS["text_secondary"],
        "grid": COLORS["border_subtle"],
        "warn": COLORS["warning"],
    }
)
FONTS = {
    "small": ("Segoe UI", 9),
    "body": ("Segoe UI", 10),
    "body_bold": ("Segoe UI", 10, "bold"),
    "section": ("Segoe UI", 11, "bold"),
    "title": ("Segoe UI", 20, "bold"),
    "mono": ("Consolas", 10),
    "mono_bold": ("Consolas", 11, "bold"),
    "metric": ("Consolas", 19, "bold"),
}
DIMENSIONS = {
    "sidebar_width": 258,
    "control_height": 30,
    "plot_border": 1,
    "min_width": 960,
    "min_height": 640,
}
