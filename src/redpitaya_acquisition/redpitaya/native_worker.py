from __future__ import annotations

import asyncio
import importlib.util
import multiprocessing
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from redpitaya_acquisition.acquisition.manager import NonRecoverableAcquisitionError
from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.domain.frame import RawSampleFrame

from .callback import create_synchronous_callback
from .compatibility import StreamingCompatibility
from .library_loader import (
    _configured_path_candidates,
    _installed_client_candidates,
    load_streaming_library,
)

INITIAL_RESPONSE_TIMEOUT_SECONDS = 20.0
COMMAND_TIMEOUT_SECONDS = 10.0
PROCESS_EXIT_TIMEOUT_SECONDS = 2.0
TEST_OUTPUT_DAC_RATE_HZ = 1_953_125
TEST_OUTPUT_DAC_BUFFER_SIZE_BYTES = 1_638_400
TEST_OUTPUT_BUFFER_PERIODS = 64
DAC_16_BIT_SAMPLE_ALIGNMENT = 64

FrameHandler = Callable[[RawSampleFrame], None]
EventHandler = Callable[[str, str | None], None]


class NativeWorkerCrashed(ConnectionError):
    """The vendor process exited without returning a Python exception."""


class NativeStreamingWorker:
    """Run the unsafe vendor C++ wrapper outside the engine process."""

    def __init__(
        self,
        settings: Settings,
        on_frame: FrameHandler,
        on_event: EventHandler,
    ) -> None:
        self._settings = settings
        self._on_frame = on_frame
        self._on_event = on_event
        self._connection: Any = None
        self._process: Any = None
        self._pump_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[None]] = {}
        self._next_request_id = 0
        self._command_lock = asyncio.Lock()
        self._closing = False
        self.module_name: str | None = None
        self.module_path: str | None = None

    @property
    def wait_task(self) -> asyncio.Task[None] | None:
        return self._pump_task

    async def connect(self, host: str) -> None:
        if self._process is not None:
            raise RuntimeError("native streaming worker is already connected")
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=True)
        process = context.Process(
            target=_native_worker_entry,
            args=(
                child_connection,
                self._settings.model_dump(mode="json"),
                host,
            ),
            name="redpitaya-native-client",
            daemon=True,
        )
        self._connection = parent_connection
        self._process = process
        process.start()
        child_connection.close()
        try:
            message = await asyncio.to_thread(
                _receive_initial_response,
                parent_connection,
                process,
                INITIAL_RESPONSE_TIMEOUT_SECONDS,
            )
            if not isinstance(message, tuple) or not message:
                raise NativeWorkerCrashed("native streaming worker returned an invalid response")
            if message[0] == "initial-error":
                error_type = str(message[1])
                detail = str(message[2])
                if error_type == "non-recoverable":
                    raise NonRecoverableAcquisitionError(detail)
                raise ConnectionError(detail)
            if message[0] != "ready":
                raise NativeWorkerCrashed("native streaming worker did not become ready")
            self.module_name = str(message[1])
            self.module_path = str(message[2]) if message[2] is not None else None
            self._pump_task = asyncio.create_task(
                self._pump_messages(), name="redpitaya-native-worker-ipc"
            )
        except Exception:
            await self.close()
            raise

    async def start(self) -> None:
        await self._command("start")

    async def stop(self) -> None:
        if self._process is not None and self._process.is_alive():
            await self._command("stop")

    async def set_test_output1_sine(
        self,
        enabled: bool,
        *,
        frequency_hz: float,
        amplitude_volts: float,
    ) -> None:
        await self._command(
            "test-output",
            enabled,
            frequency_hz,
            amplitude_volts,
        )

    async def close(self) -> None:
        process = self._process
        connection = self._connection
        if process is None:
            return
        self._closing = True
        if self._pump_task is not None and process.is_alive():
            try:
                await self._command("disconnect", allow_while_closing=True)
            except Exception:
                pass

        await asyncio.to_thread(process.join, PROCESS_EXIT_TIMEOUT_SECONDS)
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, PROCESS_EXIT_TIMEOUT_SECONDS)

        if connection is not None:
            connection.close()
        pump_task, self._pump_task = self._pump_task, None
        if pump_task is not None and not pump_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(pump_task), timeout=1.0)
            except (TimeoutError, OSError):
                pump_task.cancel()
                await asyncio.gather(pump_task, return_exceptions=True)
        self._fail_pending(ConnectionError("native streaming worker closed"))
        if not process.is_alive():
            process.close()
        self._process = None
        self._connection = None

    async def _command(
        self,
        name: str,
        *arguments: Any,
        allow_while_closing: bool = False,
    ) -> None:
        if self._closing and not allow_while_closing:
            raise ConnectionError("native streaming worker is closing")
        process = self._process
        connection = self._connection
        pump_task = self._pump_task
        if process is None or connection is None or pump_task is None or not process.is_alive():
            raise NativeWorkerCrashed(self._exit_detail())
        async with self._command_lock:
            self._next_request_id += 1
            request_id = self._next_request_id
            future = asyncio.get_running_loop().create_future()
            self._pending[request_id] = future
            try:
                await asyncio.to_thread(
                    connection.send,
                    ("command", request_id, name, arguments),
                )
                await asyncio.wait_for(asyncio.shield(future), timeout=COMMAND_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise ConnectionError(
                    f"native streaming worker timed out during '{name}'"
                ) from exc
            except (BrokenPipeError, EOFError, OSError) as exc:
                raise NativeWorkerCrashed(self._exit_detail()) from exc
            finally:
                self._pending.pop(request_id, None)

    async def _pump_messages(self) -> None:
        connection = self._connection
        if connection is None:
            return
        failure: BaseException | None = None
        try:
            while True:
                message = await asyncio.to_thread(connection.recv)
                if not isinstance(message, tuple) or not message:
                    continue
                kind = message[0]
                if kind == "frame":
                    self._on_frame(message[1])
                elif kind == "event":
                    self._on_event(str(message[1]), message[2])
                elif kind == "response":
                    self._resolve_response(message)
                elif kind == "wait-ended" and not bool(message[1]):
                    self._on_event("error", "Red Pitaya native streaming wait ended")
        except (EOFError, BrokenPipeError, OSError) as exc:
            failure = NativeWorkerCrashed(self._exit_detail())
            if not self._closing:
                self._on_event("error", str(failure))
            failure.__cause__ = exc
        except asyncio.CancelledError:
            raise
        finally:
            if failure is None:
                failure = NativeWorkerCrashed(self._exit_detail())
            self._fail_pending(failure)

    def _resolve_response(self, message: tuple[Any, ...]) -> None:
        request_id = int(message[1])
        future = self._pending.get(request_id)
        if future is None or future.done():
            return
        succeeded = bool(message[2])
        if succeeded:
            future.set_result(None)
            return
        error_type = str(message[3])
        detail = str(message[4])
        if error_type == "non-recoverable":
            future.set_exception(NonRecoverableAcquisitionError(detail))
        else:
            future.set_exception(RuntimeError(detail))

    def _fail_pending(self, error: BaseException) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)

    def _exit_detail(self) -> str:
        process = self._process
        exit_code = process.exitcode if process is not None else None
        suffix = f" (exit code {exit_code})" if exit_code is not None else ""
        return (
            "Red Pitaya native client terminated unexpectedly"
            f"{suffix}; the engine stayed alive and will reconnect"
        )


def should_isolate_native_client(configured_name: str | None) -> bool:
    """Return true for the binary vendor wrapper, but not pure-Python test clients."""

    return _native_library_root(configured_name) is not None


def isolated_client_is_version_3(configured_name: str | None) -> bool:
    root = _native_library_root(configured_name)
    if root is None:
        return False
    if any("rpsa-client-3" in part.lower() for part in root.parts):
        return True
    wrapper = root / "streaming.py"
    try:
        return "ConfigStreamClient" in wrapper.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _native_library_root(configured_name: str | None) -> Path | None:
    candidates: list[Path]
    if configured_name and Path(configured_name).expanduser().exists():
        candidates = _configured_path_candidates(Path(configured_name))
    elif configured_name:
        try:
            specification = importlib.util.find_spec(configured_name)
        except (ImportError, ModuleNotFoundError, ValueError):
            specification = None
        origin = getattr(specification, "origin", None)
        candidates = [Path(origin).resolve().parent] if origin else []
    else:
        candidates = _installed_client_candidates()
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        if any(candidate.glob("_python_lib*.pyd")) or any(candidate.glob("_python_lib*.so")):
            return candidate.resolve()
    return None


def _receive_initial_response(
    connection: Any,
    process: Any,
    timeout_seconds: float,
) -> tuple[Any, ...]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if connection.poll(0.1):
            try:
                message = connection.recv()
            except EOFError as exc:
                raise NativeWorkerCrashed(_process_exit_detail(process)) from exc
            if not isinstance(message, tuple):
                raise NativeWorkerCrashed("native streaming worker returned an invalid response")
            return message
        if not process.is_alive():
            process.join(0.2)
            raise NativeWorkerCrashed(_process_exit_detail(process))
    raise ConnectionError("timed out while starting the Red Pitaya native client")


def _process_exit_detail(process: Any) -> str:
    exit_code = process.exitcode
    suffix = f" (exit code {exit_code})" if exit_code is not None else ""
    return (
        "Red Pitaya native client terminated unexpectedly"
        f"{suffix}; the engine stayed alive and will reconnect"
    )


def _native_worker_entry(
    connection: Any,
    settings_values: dict[str, Any],
    host: str,
) -> None:
    send_lock = threading.Lock()

    def send(message: tuple[Any, ...]) -> bool:
        try:
            with send_lock:
                connection.send(message)
            return True
        except (BrokenPipeError, EOFError, OSError):
            return False

    def send_frame(frame: RawSampleFrame) -> None:
        send(("frame", frame))

    def send_event(event: str, detail: str | None) -> None:
        send(("event", event, detail))

    adapter: StreamingCompatibility | None = None
    test_output_client: Any = None
    running = False
    stopping = threading.Event()
    wait_thread: threading.Thread | None = None

    try:
        settings = Settings(_env_file=None, **settings_values)
        library = load_streaming_library(settings.streaming_module)
        module = library.module
        config_client_type = getattr(module, "ConfigStreamClient", None)
        if config_client_type is not None:
            config_client = config_client_type()
            client = module.ADCStreamClient(config_client)
        else:
            config_client = None
            client = module.ADCStreamClient()
        adapter = StreamingCompatibility(client, config_client)
        callback = create_synchronous_callback(
            module.ADCCallback,
            settings,
            send_frame,
            send_event,
        )
        adapter.connect(host)
        adapter.stop_remote_stream()
        adapter.register_callback(callback)
        adapter.set_verbose(settings.verbose_library_logging)
        adapter.configure(settings)
        if not send(("ready", library.name, library.path)):
            return
    except BaseException as exc:
        send(("initial-error", _error_type(exc), _safe_error(exc)))
        connection.close()
        return

    def wait_for_stream() -> None:
        assert adapter is not None
        try:
            adapter.wait()
        except BaseException as exc:
            send(("event", "error", _safe_error(exc)))
        finally:
            send(("wait-ended", stopping.is_set()))

    try:
        while True:
            try:
                message = connection.recv()
            except (EOFError, OSError):
                break
            if not isinstance(message, tuple) or len(message) != 4 or message[0] != "command":
                continue
            request_id = int(message[1])
            command = str(message[2])
            arguments = message[3]
            try:
                if command == "start":
                    assert adapter is not None
                    stopping.clear()
                    adapter.start()
                    running = True
                    wait_thread = threading.Thread(
                        target=wait_for_stream,
                        name="redpitaya-native-wait",
                        daemon=True,
                    )
                    wait_thread.start()
                elif command == "stop":
                    if running:
                        stopping.set()
                        assert adapter is not None
                        adapter.stop()
                        if wait_thread is not None:
                            wait_thread.join(5.0)
                        running = False
                elif command == "test-output":
                    enabled, frequency_hz, amplitude_volts = arguments
                    if enabled:
                        if test_output_client is None:
                            assert adapter is not None
                            test_output_client = _start_test_output(
                                module,
                                adapter,
                                config_client,
                                float(frequency_hz),
                                float(amplitude_volts),
                            )
                    else:
                        _stop_test_output(test_output_client)
                        test_output_client = None
                elif command == "disconnect":
                    if test_output_client is not None:
                        _stop_test_output(test_output_client)
                        test_output_client = None
                    if running:
                        stopping.set()
                        assert adapter is not None
                        adapter.stop()
                        if wait_thread is not None:
                            wait_thread.join(5.0)
                        running = False
                    assert adapter is not None
                    adapter.disconnect()
                else:
                    raise ValueError(f"unknown native worker command '{command}'")
            except BaseException as exc:
                send(("response", request_id, False, _error_type(exc), _safe_error(exc)))
            else:
                send(("response", request_id, True, "", ""))
            if command == "disconnect":
                break
    finally:
        connection.close()


def _start_test_output(
    module: Any,
    adapter: StreamingCompatibility,
    config_client: Any,
    frequency_hz: float,
    amplitude_volts: float,
) -> Any:
    if config_client is None:
        raise RuntimeError("OUT1 testing requires a Red Pitaya 3.x streaming connection")
    if not hasattr(module, "DACStreamClient"):
        raise RuntimeError("the installed Red Pitaya streaming client has no DAC support")
    if frequency_hz <= 0 or not 0 < amplitude_volts <= 1:
        raise ValueError("test sine frequency and amplitude must be positive and safe")

    host = adapter.host
    if host is None:
        raise RuntimeError("Red Pitaya streaming host is unavailable")
    dac_client = module.DACStreamClient(config_client)
    try:
        for key, value in (
            ("dac_pass_mode", "DAC_NET"),
            ("dac_rate", str(TEST_OUTPUT_DAC_RATE_HZ)),
            ("dac_size", str(TEST_OUTPUT_DAC_BUFFER_SIZE_BYTES)),
        ):
            try:
                result = config_client.sendConfig(host, key, value)
            except TypeError:
                result = config_client.sendConfig(key, value)
            _require_success(f"configure {key}", result)

        unaligned_samples = TEST_OUTPUT_DAC_RATE_HZ * TEST_OUTPUT_BUFFER_PERIODS / frequency_hz
        buffer_samples = max(
            DAC_16_BIT_SAMPLE_ALIGNMENT,
            round(unaligned_samples / DAC_16_BIT_SAMPLE_ALIGNMENT)
            * DAC_16_BIT_SAMPLE_ALIGNMENT,
        )
        phase = np.arange(buffer_samples, dtype=np.float64) * (
            2 * np.pi * TEST_OUTPUT_BUFFER_PERIODS / buffer_samples
        )
        peak_counts = round(np.iinfo(np.int16).max * amplitude_volts)
        samples = (np.sin(phase) * peak_counts).astype(np.int16).tolist()
        _require_success("load OUT1 sine buffer", dac_client.setMemory16Bit(1, samples))
        dac_client.setRepeatInf(True)
        _require_success("start OUT1 DAC stream", dac_client.startStreamingFromMemory(host))
    except BaseException:
        try:
            dac_client.notifyStop()
        except BaseException:
            pass
        raise
    return dac_client


def _stop_test_output(client: Any) -> None:
    if client is None:
        return
    try:
        if hasattr(client, "stopStreaming"):
            client.stopStreaming()
    finally:
        # stopStreaming() stops the board-side DAC stream, while notifyStop()
        # releases the vendor client's local wait loop. Both are required by
        # the 3.x wrapper before wait() can return.
        client.notifyStop()
    client.wait()


def _require_success(operation: str, result: Any) -> None:
    integer_failure = isinstance(result, int) and not isinstance(result, bool) and result < 0
    if result is False or integer_failure:
        raise RuntimeError(f"Red Pitaya could not {operation}")


def _error_type(error: BaseException) -> str:
    return "non-recoverable" if isinstance(error, NonRecoverableAcquisitionError) else "error"


def _safe_error(error: BaseException) -> str:
    return (" ".join(str(error).split()) or type(error).__name__)[:500]
