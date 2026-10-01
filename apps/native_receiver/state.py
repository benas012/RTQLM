from __future__ import annotations

import queue
import subprocess
import threading
import tkinter as tk
from collections.abc import Callable
from typing import Any

import numpy as np

from apps.native_receiver.constants import (
    COLORS,
    DEFAULT_SIGNAL_RESOLUTION,
    DEFAULT_SIGNAL_SCRIPT,
    DEFAULT_TIME_SPAN,
    FONTS,
    UI_FRAME_INTERVAL_MS,
)
from apps.native_receiver.protocol import Device, SampleFrame, SampleHistory
from apps.native_receiver.settings import load_cluster_config, signal_script_feature_enabled
from quantum_light_engine.post_processing import (
    ClusterProcessingResult,
    RealtimeClusterProcessor,
)


class ReceiverStateMixin:
    def _initialize_ui_state(self) -> None:
        self.ui_colors = COLORS
        self.ui_fonts = FONTS
        self.api_base = tk.StringVar(value=self.settings.api_base)
        self.launch_engine_on_start = tk.BooleanVar(value=self.settings.launch_engine_on_start)
        self.status = tk.StringVar(value="disconnected")
        self.engine_status = tk.StringVar(value="UNKNOWN")
        self.selected = tk.StringVar(value="-")
        self.frames = tk.StringVar(value="0")
        self.sequence = tk.StringVar(value="-")
        self.sample_rate = tk.StringVar(value="-")
        self.samples = tk.StringVar(value="-")
        self.mask = tk.StringVar(value="-")
        self.ch1_range = tk.StringVar(value="-")
        self.ch2_range = tk.StringVar(value="-")
        self.lost1 = tk.StringVar(value="0")
        self.lost2 = tk.StringVar(value="0")
        self.lost_pair = tk.StringVar(value="0 / 0")
        self.flags = tk.StringVar(value="0")
        self.script_status = tk.StringVar(value="signal script disabled")
        self.activity = tk.StringVar(value="")
        self.loading_message = tk.StringVar(value="Ready")
        self.spectrum_channel = tk.StringVar(value="CH1")
        self.spectrum_fft_size = tk.StringVar(value="4096")
        self.spectrum_scale = tk.StringVar(value="dBFS")
        self.spectrum_peak = tk.StringVar(value="Peak: -")
        self.timebase = tk.StringVar(value="- ms/div")
        self.time_zoom_label = tk.StringVar(value="1.0x")
        self.time_window_label = tk.StringVar(value="Full capture")
        self.time_span_choice = tk.StringVar(value=DEFAULT_TIME_SPAN)
        self.display_mode = tk.StringVar(value="Auto")
        self.signal_resolution = tk.StringVar(value=DEFAULT_SIGNAL_RESOLUTION)
        self.pause_label = tk.StringVar(value="Pause")
        self.display_detail = tk.StringVar(value="Live")
        self.diagnostics_expanded = tk.BooleanVar(value=False)
        self.waveform_advanced_expanded = tk.BooleanVar(value=False)
        self.device_empty_message = tk.StringVar(value="No Red Pitayas registered")
        self.connection_summary = tk.StringVar(value="No device selected")
        self.cursor_status = tk.StringVar(value="Cursor off")
        self.trigger_enabled = tk.BooleanVar(value=False)
        self.trigger_channel = tk.StringVar(value="CH1")
        self.trigger_edge = tk.StringVar(value="Rising")
        self.trigger_level = tk.StringVar(value="0.0")
        self.trigger_status = tk.StringVar(value="Trigger off")
        self.test_output_label = tk.StringVar(value="OUT1 sine")
        self.y_axis_range_label = tk.StringVar(value="Y range ±1.00 V")
        self.channel_1_visible = tk.BooleanVar(value=True)
        self.channel_2_visible = tk.BooleanVar(value=True)
        self.channel_1_offset_divisions = tk.DoubleVar(value=0.0)
        self.channel_2_offset_divisions = tk.DoubleVar(value=0.0)
        self.channel_1_offset_label = tk.StringVar(value="CH1 0.0 div")
        self.channel_2_offset_label = tk.StringVar(value="CH2 0.0 div")
        self.acquisition_decimation = tk.StringVar(value="64")
        self.acquisition_block_size = tk.StringVar(value="16384")
        self.acquisition_rate = tk.StringVar(value="Sample rate: 1.95 MHz")
        self.acquisition_batch = tk.StringVar(value="Batch: -")
        self.connection_overlay_title = tk.StringVar(value="")
        self.connection_overlay_detail = tk.StringVar(value="")

    def _initialize_processing_state(self) -> None:
        self.display_paused = False
        self.paused_frame: SampleFrame | None = None
        self.paused_history: SampleHistory | None = None
        self.pending_time_span_choice = True
        self.signal_padding_samples = 0
        self.signal_window_samples = 0
        self.last_display_monotonic_ns = 0
        self.trigger_capture_valid = False
        self.trigger_window_precomputed = False
        self.precomputed_trigger_index: float | None = None
        self.test_output_enabled = False
        self.test_output_device_id: str | None = None
        self.signal_scripts_enabled = signal_script_feature_enabled()
        self.signal_script = DEFAULT_SIGNAL_SCRIPT
        self.signal_processor: Callable[[dict[str, np.ndarray[Any, Any]], Any], Any] | None = None
        self.cluster_processor = RealtimeClusterProcessor(load_cluster_config())
        self.cluster_lock = threading.Lock()
        self.latest_cluster_result: ClusterProcessingResult | None = None
        self.cluster_error: str | None = None
        self.cluster_last_sequence: int | None = None
        self.cluster_dialog: tk.Toplevel | None = None

    def _initialize_runtime_state(self) -> None:
        self.devices: list[Device] = []
        self.selected_device: Device | None = None
        self.open_device_ids: list[str] = []
        self.tab_hitboxes: list[tuple[float, float, str]] = []
        self.socket_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.frame_count = 0
        self.socket_frame_count = 0
        self.rendered_frame_count = 0
        self.latest_frame: SampleFrame | None = None
        self.sample_history = SampleHistory()
        self.frame_lock = threading.Lock()
        self.last_render_monotonic_ns = 0
        self.last_stats_monotonic_ns = 0
        self.grid_items: list[int] = []
        self.trace_items: list[int] = []
        self.spectrum_grid_items: list[int] = []
        self.spectrum_trace_items: list[int] = []
        self.signal_notice_items: list[int] = []
        self.spectrum_notice_items: list[int] = []
        self.axis_y_abs = 1.0
        self.y_axis_fit_pending = True
        self.y_axis_dirty = False
        self.axis_sample_rate_hz = 0.0
        self.axis_samples_per_channel = 0
        self.axis_trigger_offset_samples: float | None = None
        self.time_zoom = 1.0
        self.time_pan = 0.0
        self.cursor_enabled = False
        self.spectrum_axis_sample_rate_hz = 0.0
        self.spectrum_axis_scale = self.spectrum_scale.get()
        self.last_spectrum_monotonic_ns = 0
        self.last_frame_log_monotonic_ns = 0
        self.loading_active = False
        self.loading_label = ""
        self.spinner_running = False
        self.spinner_index = 0
        self.spinner_token = 0
        self.engine_status_dot: tk.Canvas | None = None
        self.engine_status_dot_item: int | None = None
        self.toast_frames: list[tk.Toplevel] = []
        self.connection_failure_active = False
        self.connection_overlay: tk.Toplevel | None = None
        self.connection_overlay_canvas: tk.Canvas | None = None
        self.connection_overlay_bg_rect: int | None = None
        self.connection_overlay_spinner_track: int | None = None
        self.connection_overlay_spinner_arc: int | None = None
        self.connection_overlay_title_item: int | None = None
        self.connection_overlay_detail_item: int | None = None
        self.engine_ping_in_flight = False
        self.signal_resize_after_id: str | None = None
        self.spectrum_resize_after_id: str | None = None
        self.control_layout_after_id: str | None = None
        self._last_control_layout = ""
        self.closing = False
        self.engine_process: subprocess.Popen[str] | None = None

    def _start_receiver(self) -> None:
        self._build_menu()
        self._build()
        self._bind_shortcuts()
        self._set_engine_status("UNKNOWN")
        self._ui_log("app opened", api_base=self.api_base.get())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(UI_FRAME_INTERVAL_MS, self._drain_events)
        self.after(1000, self._ping_engine_status_async)
        if self.launch_engine_on_start.get():
            self.after(100, self.ensure_engine_running)
            self.after(2500, self.refresh_devices)
        else:
            self.refresh_devices()
