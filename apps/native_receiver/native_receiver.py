# This module intentionally re-exports the receiver's public API for compatibility.
# ruff: noqa: F401
from __future__ import annotations

import ctypes
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from typing import Any
from urllib.request import urlopen

try:
    import numpy as np
    import websockets
except ImportError as exc:  # pragma: no cover - user-facing startup guard
    raise SystemExit("Missing dependency. Run this with `uv run`.") from exc

from apps.native_receiver.acquisition_controls import AcquisitionControlsMixin
from apps.native_receiver.api_client import ApiClientMixin
from apps.native_receiver.connection_overlay import ConnectionOverlayMixin
from apps.native_receiver.constants import (
    ADC_COUNTS_PER_VOLT,
    COLORS,
    CONNECTION_FAILED_NOTICE,
    CONNECTION_FAILED_SPECTRUM_NOTICE,
    CONNECTION_FAILURE_BUSY_LABELS,
    CONNECTION_OVERLAY_BG,
    CONNECTION_OVERLAY_BUSY_LABELS,
    CONNECTION_OVERLAY_OPACITY,
    DEFAULT_SIGNAL_RESOLUTION,
    DEFAULT_SIGNAL_SCRIPT,
    DEFAULT_TIME_SPAN,
    DIMENSIONS,
    DISCONNECTED_STATES,
    ENGINE_CONNECT_TIMEOUT_SECONDS,
    ENGINE_HEALTH_TIMEOUT_SECONDS,
    ENGINE_PING_INTERVAL_MS,
    ENGINE_PROCESS_TITLE,
    ENGINE_READY_TIMEOUT_SECONDS,
    FONTS,
    HISTORY_MAX_SAMPLES,
    MAX_POINTS,
    READY_STATES,
    REDPITAYA_CONNECT_TIMEOUT_SECONDS,
    REDPITAYA_START_TIMEOUT_SECONDS,
    SIGNAL_PLOT_TOP_PAD,
    SIGNAL_RESOLUTION_POINTS,
    SPECTRUM_MIN_INTERVAL_MS,
    STATS_MIN_INTERVAL_MS,
    STREAMING_STATES,
    TEST_SINE_AMPLITUDE_VOLTS,
    TEST_SINE_FREQUENCY_HZ,
    TIME_SPANS_MS,
    TIME_ZOOM_MIN,
    TOAST_FADE_INTERVAL_MS,
    TOAST_FADE_STEPS,
    TOAST_VISIBLE_MS,
    TRIGGER_HYSTERESIS_VOLTS,
    TRIGGER_PRE_SAMPLES_FRACTION,
    UI_FRAME_INTERVAL_MS,
    UI_LOGGER,
    WIDE_VIEW_AUTO_INTERVAL_MS,
    WIDE_VIEW_FAST_REFRESH_SECONDS,
    WORKSPACE_ROOT,
)
from apps.native_receiver.device_controller import DeviceControllerMixin
from apps.native_receiver.device_repository import DeviceRepositoryMixin
from apps.native_receiver.device_tabs import DeviceTabsMixin
from apps.native_receiver.dialogs import (
    AddRedPitayaDialog,
    ApiError,
    EngineSettingsDialog,
    SignalScriptDialog,
    ToolTip,
    configure_ui_logging,
    position_dialog,
)
from apps.native_receiver.engine_controller import EngineControllerMixin
from apps.native_receiver.entrypoint import main
from apps.native_receiver.frame_processor import FrameProcessorMixin
from apps.native_receiver.helpers import (
    acquisition_batch_seconds,
    acquisition_channel_count,
    adc_counts_to_volts,
    compute_spectrum,
    device_slug,
    display_interval_ms,
    draw_channel,
    draw_spectrum_line,
    editable_profile_payload,
    find_trigger_crossing,
    format_duration,
    format_frequency,
    format_voltage,
    max_sample_abs,
    next_frame_delay_ms,
    normalize_script_channel,
    safe_builtins,
    sample_range,
    samples_to_numpy,
    set_if_changed,
    short_error,
    smooth_acquisition_block_size,
    start_display_timer,
    terminate_process_tree,
    time_monotonic_ns,
    update_trace_line,
)
from apps.native_receiver.instrument_controls import InstrumentControlsMixin
from apps.native_receiver.layout import LayoutMixin
from apps.native_receiver.notifications import NotificationsMixin
from apps.native_receiver.protocol import Device, SampleFrame, SampleHistory, parse_frame
from apps.native_receiver.receiver_tools import ReceiverToolsMixin
from apps.native_receiver.settings import (
    SIGNAL_SCRIPT_FEATURE_ENV,
    AppSettings,
    load_cluster_config,
    load_settings,
    signal_script_feature_enabled,
)
from apps.native_receiver.sidebar_ui import SidebarUiMixin
from apps.native_receiver.signal_view import SignalViewMixin
from apps.native_receiver.spectrum_renderer import SpectrumRendererMixin
from apps.native_receiver.spectrum_ui import SpectrumUiMixin
from apps.native_receiver.state import ReceiverStateMixin
from apps.native_receiver.stream_controller import StreamControllerMixin
from apps.native_receiver.theme import ThemeMixin
from apps.native_receiver.time_controls import TimeControlsMixin
from apps.native_receiver.waveform_renderer import WaveformRendererMixin
from apps.native_receiver.waveform_ui import WaveformUiMixin
from apps.native_receiver.workspace_ui import WorkspaceUiMixin
from quantum_light_engine.post_processing import (
    ClusterProcessingResult,
    RealtimeClusterProcessor,
)


class ReceiverApp(
    ReceiverStateMixin,
    ThemeMixin,
    LayoutMixin,
    SidebarUiMixin,
    WorkspaceUiMixin,
    WaveformUiMixin,
    SpectrumUiMixin,
    ConnectionOverlayMixin,
    DeviceTabsMixin,
    TimeControlsMixin,
    InstrumentControlsMixin,
    SignalViewMixin,
    AcquisitionControlsMixin,
    ReceiverToolsMixin,
    EngineControllerMixin,
    NotificationsMixin,
    DeviceRepositoryMixin,
    DeviceControllerMixin,
    StreamControllerMixin,
    FrameProcessorMixin,
    WaveformRendererMixin,
    SpectrumRendererMixin,
    ApiClientMixin,
    tk.Tk,
):
    def __init__(self) -> None:
        configure_ui_logging()
        super().__init__()
        self.title("RTQLM · Native receiver")
        self.geometry("1180x840")
        self.minsize(DIMENSIONS["min_width"], DIMENSIONS["min_height"])

        self.settings = load_settings()
        self._configure_style()
        self._initialize_ui_state()
        self._initialize_processing_state()
        self._initialize_runtime_state()
        self._start_receiver()

    def _build(self) -> None:
        self.columnconfigure(0, minsize=DIMENSIONS["sidebar_width"])
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_workspace()
