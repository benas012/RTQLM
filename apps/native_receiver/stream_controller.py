from __future__ import annotations

import asyncio
import queue
import threading
from tkinter import messagebox
from typing import Any

import numpy as np
import websockets

from apps.native_receiver.constants import (
    CONNECTION_FAILURE_BUSY_LABELS,
    CONNECTION_OVERLAY_BUSY_LABELS,
    TIME_SPANS_MS,
    UI_FRAME_INTERVAL_MS,
    UI_LOGGER,
)
from apps.native_receiver.helpers import (
    adc_counts_to_volts,
    next_frame_delay_ms,
    short_error,
)
from apps.native_receiver.protocol import SampleFrame, parse_frame


class StreamControllerMixin:
    def _start_socket(self, device_id: str) -> None:
        self._reset_display_pause()
        selected_span = self.time_span_choice.get()
        self.pending_time_span_choice = (
            selected_span in TIME_SPANS_MS and TIME_SPANS_MS[selected_span] is not None
        )
        self.stop_event.clear()
        self.frame_count = 0
        self.rendered_frame_count = 0
        with self.frame_lock:
            self.socket_frame_count = 0
            self.latest_frame = None
            self.sample_history.clear()
        with self.cluster_lock:
            self.cluster_processor.clear_calibration()
            self.latest_cluster_result = None
            self.cluster_error = None
            self.cluster_last_sequence = None
            self.cluster_last_frame = None
        self.frames.set("0")
        url = self._websocket_url(device_id)
        self._ui_log("opening sample socket", device_id=device_id, url=url)
        self.socket_thread = threading.Thread(
            target=lambda: asyncio.run(self._socket_loop(url)), daemon=True
        )
        self.socket_thread.start()

    async def _socket_loop(self, url: str) -> None:
        from apps.native_receiver import native_receiver as receiver_api

        release_timer = receiver_api.start_display_timer()
        try:
            UI_LOGGER.info("sample socket connecting url=%s", url)
            self.events.put(("status", "connecting socket"))
            # Raw ADC frames are high-rate binary data. Deflate can stall the
            # engine's event loop and backpressure the board into dropping samples.
            async with websockets.connect(url, max_size=None, compression=None) as websocket:
                UI_LOGGER.info("sample socket connected url=%s", url)
                self.events.put(("status", "connected"))
                while not self.stop_event.is_set():
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                    except TimeoutError:
                        continue
                    if isinstance(message, str):
                        continue
                    frame = parse_frame(message)
                    self._process_cluster_frame(frame)
                    with self.frame_lock:
                        self.socket_frame_count += 1
                        self.sample_history.append(frame)
                        self.latest_frame = frame
        except Exception as exc:
            if not self.stop_event.is_set():
                UI_LOGGER.warning("sample socket failed error=%s", short_error(exc, 180))
                UI_LOGGER.debug("sample socket traceback", exc_info=True)
                self.events.put(
                    (
                        "connection_error",
                        {
                            "title": "Connection failed",
                            "message": self._connection_failure_message(exc),
                        },
                    )
                )
        finally:
            release_timer()
            UI_LOGGER.info("sample socket closed url=%s", url)
            self.events.put(("socket_closed", None))

    def _process_cluster_frame(self, frame: SampleFrame) -> None:
        with self.cluster_lock:
            config = self.cluster_processor.config
            selected = config.single_channel if config.analysis_mode == "single" else "both"
            requires_a = selected in {"a", "both"}
            requires_b = selected in {"b", "both"}
            missing_input = ((requires_a and frame.channel_1 is None)
                             or (requires_b and frame.channel_2 is None))
            trigger_input = frame.channel_1 if config.trigger_channel == "a" else frame.channel_2
            missing_trigger = config.window_mode == "triggered" and trigger_input is None
            if missing_input or missing_trigger:
                self.cluster_processor.reset_stream()
                if not missing_input:
                    channel = "CH1" if config.trigger_channel == "a" else "CH2"
                    self.cluster_error = (
                        f"Triggered acquisition requires sampled trigger input {channel}"
                    )
                elif selected == "both":
                    calculation = (
                        "Single-mode squeezing" if config.analysis_mode == "single"
                        else "Cluster processing"
                    )
                    self.cluster_error = f"{calculation} requires both CH1 and CH2"
                else:
                    channel = "CH1" if selected == "a" else "CH2"
                    self.cluster_error = f"Single-mode squeezing requires {channel}"
                self.latest_cluster_result = None
                self.cluster_last_sequence = None
                self.cluster_last_frame = None
                return
            channel_a = adc_counts_to_volts(frame.channel_1)
            channel_b = adc_counts_to_volts(frame.channel_2)
            if channel_a is None:
                channel_a = np.zeros(frame.samples_per_channel, dtype=np.float64)
            if channel_b is None:
                channel_b = np.zeros(frame.samples_per_channel, dtype=np.float64)
            previous = self.__dict__.get("cluster_last_frame")
            fpga_loss = (
                bool(frame.fpga_lost_ch1 or frame.fpga_lost_ch2)
                if previous is None or frame.stream_id != previous.stream_id else
                (frame.fpga_lost_ch1 != previous.fpga_lost_ch1
                 or frame.fpga_lost_ch2 != previous.fpga_lost_ch2)
            )
            discontinuity = bool(frame.flags & 0b11) or (
                self.cluster_last_sequence is not None and (
                    frame.sequence != self.cluster_last_sequence + 1
                    or (previous is not None and (
                        frame.stream_id != previous.stream_id
                        or frame.sample_rate_hz != previous.sample_rate_hz
                        or frame.channel_mask != previous.channel_mask
                        or frame.fpga_lost_ch1 != previous.fpga_lost_ch1
                        or frame.fpga_lost_ch2 != previous.fpga_lost_ch2
                    ))
                )
            )
            self.cluster_last_sequence = frame.sequence
            self.cluster_last_frame = frame
            if fpga_loss:
                # The vendor does not locate gaps within a delivered buffer.
                # Discard the whole affected packet and isolate both its boundaries.
                self.cluster_processor.reset_stream()
                self.latest_cluster_result = None
                self.cluster_error = "FPGA samples lost: packet excluded; awaiting continuous data"
                return
            if discontinuity:
                self.latest_cluster_result = None
            try:
                result = self.cluster_processor.feed(
                    channel_a,
                    channel_b,
                    frame.sample_rate_hz,
                    discontinuity=discontinuity,
                )
                if result is not None:
                    self.latest_cluster_result = result
                self.cluster_error = None
            except ValueError as exc:
                self.latest_cluster_result = None
                self.cluster_error = f"Cluster processing unavailable: {short_error(exc, 180)}"

    @staticmethod
    def _connection_failure_message(exc: BaseException) -> str:
        detail = short_error(exc, 170)
        if not detail:
            detail = exc.__class__.__name__
        return f"Could not open the Red Pitaya sample stream. {detail}"

    def _show_connection_failure(self, title: str, message: str) -> None:
        self.status.set("RP not connected to engine")
        self._stop_spinner(restore_notices=False)
        self._set_connection_failed_notice()
        self._show_toast(title, message, level="error")

    def _drain_events(self) -> None:
        from apps.native_receiver import native_receiver as receiver_api

        try:
            while True:
                event, payload = self.events.get_nowait()
                try:
                    self._handle_ui_event(event, payload)
                except Exception as exc:
                    UI_LOGGER.warning(
                        "ui event handler failed event=%s error=%s",
                        event,
                        short_error(exc, 180),
                    )
                    UI_LOGGER.debug("ui event handler traceback", exc_info=True)
                    self.status.set("ui event error")
        except queue.Empty:
            pass

        now = receiver_api.time_monotonic_ns()
        self.last_render_monotonic_ns = now
        with self.frame_lock:
            frame = self.latest_frame
            received_count = self.socket_frame_count
        interval = self._display_interval_ms()
        display_due = interval <= UI_FRAME_INTERVAL_MS or (
            now - self.__dict__.get("last_display_monotonic_ns", 0) >= interval * 1_000_000
        )
        if (
            frame is not None
            and received_count != self.rendered_frame_count
            and display_due
            and not self.__dict__.get("display_paused", False)
        ):
            self.last_display_monotonic_ns = now
            self.rendered_frame_count = received_count
            self._handle_frame(frame, received_count)

        # Schedule from the start of this refresh, so processing time doesn't
        # lengthen every frame. Still yield to Tk and never redraw stale samples.
        self.after(
            next_frame_delay_ms(self.last_render_monotonic_ns, receiver_api.time_monotonic_ns()),
            self._drain_events,
        )

    def _handle_ui_event(self, event: str, payload: Any) -> None:
        if event == "status":
            status = str(payload)
            self.status.set(status)
            self._ui_log("ui status event", status=status)
            if status == "connecting socket":
                if not self.loading_active:
                    self._start_spinner("Opening sample stream")
            elif status == "connected":
                self.connection_failure_active = False
                if self.loading_active and self.loading_label in CONNECTION_OVERLAY_BUSY_LABELS:
                    self._show_connection_overlay(
                        "Connected to RTQLM acquisition engine",
                        "Waiting for Red Pitaya samples",
                    )
                if self.loading_label == "Opening sample stream":
                    self._stop_spinner()
                self._set_signal_notice("Waiting for first samples")
                self._set_spectrum_notice("Waiting for first spectrum")
        elif event == "error":
            self.status.set("error")
            self._stop_spinner()
            messagebox.showerror("Receiver error", str(payload))
        elif event == "connection_error":
            title = str(payload.get("title", "Connection failed"))
            message = str(payload.get("message", "Could not connect to Red Pitaya."))
            self._show_connection_failure(title, message)
        elif event == "engine_ping":
            running, elapsed_ms = payload
            self._handle_engine_ping(bool(running), int(elapsed_ms))
        elif event == "socket_closed":
            if not self.connection_failure_active:
                self.status.set("disconnected")
            self._ui_log("socket closed event")
            self.stop_event.clear()
            self.socket_thread = None
            self._stop_spinner(restore_notices=not self.connection_failure_active)
        elif event == "busy_success":
            label, callback, result, elapsed_ms = payload
            self._ui_log("background job finished", label=label, elapsed_ms=elapsed_ms)
            try:
                if callback is not None:
                    callback(result)
            except Exception as exc:
                self._show_error(exc)
            finally:
                if self.loading_label == label:
                    self._stop_spinner()
        elif event == "busy_error":
            label, exc, elapsed_ms = payload
            self._ui_log(
                "background job failed",
                label=label,
                elapsed_ms=elapsed_ms,
                error=short_error(exc, 180),
            )
            if label in CONNECTION_FAILURE_BUSY_LABELS:
                self._show_connection_failure(
                    "Connection failed",
                    f"Could not connect to Red Pitaya. {short_error(exc, 180)}",
                )
            else:
                if self.loading_label == label:
                    self._stop_spinner()
                self._show_error(exc)
