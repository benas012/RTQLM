from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from quantum_light_engine.post_processing import RealtimeClusterProcessor, lab_processing_config
from redpitaya_acquisition.config.settings import Settings
from redpitaya_acquisition.redpitaya.native_worker import NativeStreamingWorker
from redpitaya_acquisition.redpitaya.output_signals import (
    OutputChannelConfig,
    OutputSignalsConfig,
    build_output_buffers,
    dac_sample_rate,
    fit_low_frequency_output,
    start_output_signals,
    stop_output_signals,
)


def pair(phase=-90, **kwargs):  # type: ignore[no-untyped-def]
    return OutputSignalsConfig(
        output1=OutputChannelConfig(enabled=True, frequency_hz=976562.5),
        output2=OutputChannelConfig(enabled=True, frequency_hz=976562.5, phase_degrees=phase),
        **kwargs,
    )


@pytest.mark.parametrize(
    "channel",
    [
        {"amplitudeVolts": 0.8, "offsetVolts": -0.3},
        {"frequencyHz": float("nan")},
        {"samples": [float("inf")]},
        {"samples": [1.01]},
        {"waveform": "arbitrary", "samples": [0]},
        {"phaseDegrees": 361},
        {"dutyCycle": 1},
    ],
)
def test_invalid_signals_are_rejected(channel):  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        OutputChannelConfig.model_validate(channel)


@pytest.mark.parametrize(
    "config",
    [
        {"bufferSamples": 65},
        {"sampleRateHz": 1907},
        {"output1": {"enabled": True, "frequencyHz": 8e6}},
        {"output1": {"enabled": True, "frequencyHz": 0.01}},
    ],
)
def test_invalid_dac_settings_are_rejected(config):  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        OutputSignalsConfig.model_validate(config)


def test_shared_buffers_have_exact_phase_and_report_frequency() -> None:
    config = pair()
    buffers, report = build_output_buffers(config)
    a = np.asarray(buffers[1])
    b = np.asarray(buffers[2])
    assert len(a) == len(b) == 65536
    np.testing.assert_array_equal(b, np.roll(a, 4))
    assert report["channels"][0]["actualFrequencyHz"] == 976562.5
    assert report["actualSampleRateHz"] == 15625000
    assert report["payloadBytesPerSecond"] == 62500000
    assert max(a) == round(32767 * 0.1)
    assert dac_sample_rate(10_000_000) == 9_998_321.533203125


def test_slow_signal_fits_output_clock_and_produces_the_reported_period() -> None:
    config = fit_low_frequency_output({
        "sampleRateHz": 15_625_000, "bufferSamples": 65_536,
        "output1": {"enabled": True, "frequencyHz": 100},
        "output2": {"enabled": True, "frequencyHz": 200, "phaseDegrees": 90},
    })
    buffers, report = build_output_buffers(config)
    assert config.sample_rate_hz == 6_553_650
    assert report["channels"][0]["actualFrequencyHz"] == pytest.approx(100.000761449337)
    assert report["channels"][1]["actualFrequencyHz"] == pytest.approx(200.001522898674)
    for channel, cycles in ((1, 1), (2, 2)):
        spectrum = abs(np.fft.rfft(np.asarray(buffers[channel], dtype=float)))
        assert int(np.argmax(spectrum[1:]) + 1) == cycles


def test_rate_fitting_preserves_valid_manual_timing_and_does_not_bypass_nyquist() -> None:
    config = pair()
    assert fit_low_frequency_output(config.model_dump(by_alias=True)) == config
    with pytest.raises(ValidationError, match="Nyquist"):
        fit_low_frequency_output({
            "output1": {"enabled": True, "frequencyHz": 100},
            "output2": {"enabled": True, "frequencyHz": 5e6},
        })
    with pytest.raises(ValidationError, match="multiple of 64"):
        fit_low_frequency_output({
            "bufferSamples": 65, "output1": {"enabled": True, "frequencyHz": 100},
        })


@pytest.mark.parametrize(
    "waveform", ["sine", "square", "triangle", "sawtooth", "dc", "noise", "arbitrary"]
)
def test_waveforms_and_disabled_output_are_bounded(waveform: str) -> None:
    config = OutputSignalsConfig(
        output1=OutputChannelConfig(
            enabled=True,
            waveform=waveform,
            amplitude_volts=0.2,
            offset_volts=0.1,
            samples=[-1, 0, 1, 0],
        )
    )
    buffers, _ = build_output_buffers(config)
    volts = np.asarray(buffers[1]) / 32767
    assert volts.max() <= 0.3001
    assert volts.min() >= -0.1001
    assert set(buffers[2]) == {0}
    if waveform == "dc":
        assert len(set(buffers[1])) == 1
        assert volts[0] == pytest.approx(0.1, abs=1 / 32767)
    if waveform == "noise":
        assert build_output_buffers(config)[0] == buffers


def test_generated_pair_cancels_the_actual_receiver_lab_nullifier() -> None:
    from dataclasses import replace

    from quantum_light_engine.post_processing import build_lab_mode_template

    # This tone advances by a quarter cycle every four ADC samples: 256 ns.
    config = replace(lab_processing_config(), mode_width_seconds=256e-9)
    template = build_lab_mode_template(
        256e-9, 6e6, 1 / 15625000, oversample=1000, min_samples_per_mode=4
    )
    variances = []
    for phase in (-90, 90):
        buffers, _ = build_output_buffers(pair(phase))
        a = np.asarray(buffers[1], dtype=float).reshape(-1, 4) @ template / 15625000
        b = np.asarray(buffers[2], dtype=float).reshape(-1, 4) @ template / 15625000
        nullifier = a[:-1] + a[1:] + b[:-1] - b[1:]
        variances.append(float(np.var(nullifier)))
        processor = RealtimeClusterProcessor(
            replace(
                config,
                window_mode="continuous",
                variance_estimator="rolling",
                channel_b_delay_samples=0,
            )
        )
        result = processor.feed(np.asarray(buffers[1]), np.asarray(buffers[2]), 15625000)
        assert result is not None
        np.testing.assert_allclose(result.nullifiers["cluster_x"], nullifier, atol=1e-12)
    assert variances[1] > 100_000 * max(variances[0], 1e-30)


class FakeDAC:
    def __init__(self, config):  # type: ignore[no-untyped-def]
        self.calls = config.calls
        self.calls.append(("construct",))

    def setMemory16Bit(self, channel, samples):  # type: ignore[no-untyped-def]
        self.calls.append(("load", channel, len(samples)))
        return True

    def setRepeatInf(self, enabled):  # type: ignore[no-untyped-def]
        self.calls.append(("repeat", enabled))

    def startStreamingFromMemory(self, host):  # type: ignore[no-untyped-def]
        self.calls.append(("start", host))
        return True

    def stopStreaming(self):  # type: ignore[no-untyped-def]
        self.calls.append(("stop",))

    def notifyStop(self):  # type: ignore[no-untyped-def]
        self.calls.append(("notify",))

    def wait(self):  # type: ignore[no-untyped-def]
        self.calls.append(("wait",))


def test_dac_loads_both_channels_before_one_start_and_stops_in_order() -> None:
    calls = []
    client = SimpleNamespace(calls=calls, sendConfig=lambda *args: calls.append(args) or True)
    dac = start_output_signals(
        SimpleNamespace(DACStreamClient=FakeDAC), SimpleNamespace(host="rp"), client, pair()
    )
    assert calls.index(("load", 2, 65536)) < calls.index(("start", "rp"))
    assert ("rp", "dac_rate", "15625000") in calls
    stop_output_signals(dac)
    assert calls[-3:] == [("stop",), ("notify",), ("wait",)]


def test_failed_dac_start_cleans_up() -> None:
    calls = []

    class FailedDAC(FakeDAC):
        def startStreamingFromMemory(self, host):  # type: ignore[no-untyped-def]
            return False

    client = SimpleNamespace(calls=calls, sendConfig=lambda *args: True)
    with pytest.raises(RuntimeError, match="start DAC"):
        start_output_signals(
            SimpleNamespace(DACStreamClient=FailedDAC), SimpleNamespace(host="rp"), client, pair()
        )
    assert calls[-3:] == [("stop",), ("notify",), ("wait",)]


@pytest.mark.parametrize("block_size", [65_536, 131_072, 262_144, 524_288])
def test_dac_reserves_the_board_minimum_for_current_acquisition_blocks(block_size) -> None:
    class MemoryCheckedDAC(FakeDAC):
        def startStreamingFromMemory(self, host):
            reservation = next(int(call[2]) for call in self.calls
                               if len(call) == 3 and call[1] == "dac_size")
            assert reservation >= 12 * (block_size + 128)
            return True

    calls = []
    config_client = SimpleNamespace(
        calls=calls, sendConfig=lambda *args: calls.append(args) or True,
    )
    dac = start_output_signals(
        SimpleNamespace(DACStreamClient=MemoryCheckedDAC), SimpleNamespace(host="rp"),
        config_client, pair(), block_size_bytes=block_size,
    )
    stop_output_signals(dac)


def test_board_memory_error_identifies_the_startup_failure() -> None:
    class MemoryRejectedDAC(FakeDAC):
        def setCallback(self, callback):
            self.callback = callback

        def startStreamingFromMemory(self, host):
            observers[0].dacServerStoppedMemError(config_client, host)
            return True

    observers = []
    config_client = SimpleNamespace(
        calls=[], sendConfig=lambda *args: True,
        addCallback=observers.append, removeCallback=observers.remove,
    )
    with pytest.raises(RuntimeError, match="insufficient board memory"):
        start_output_signals(
            SimpleNamespace(DACStreamClient=MemoryRejectedDAC, DACCallback=object,
                            ConfigCallback=object),
            SimpleNamespace(host="rp"), config_client, pair(),
        )
    assert not observers


def test_successful_start_return_without_upload_is_rejected(monkeypatch) -> None:
    from redpitaya_acquisition.redpitaya import output_signals

    class UnsentDAC(FakeDAC):
        def setCallback(self, callback):
            self.callback = callback

    monkeypatch.setattr(output_signals, "DAC_UPLOAD_TIMEOUT_SECONDS", 0.01)
    calls = []
    config_client = SimpleNamespace(calls=calls, sendConfig=lambda *args: True)
    with pytest.raises(RuntimeError, match="did not upload"):
        start_output_signals(
            SimpleNamespace(DACStreamClient=UnsentDAC, DACCallback=object),
            SimpleNamespace(host="rp"), config_client, pair(),
        )
    assert calls[-3:] == [("stop",), ("notify",), ("wait",)]


def test_start_waits_for_both_uploaded_channels() -> None:
    import threading

    from redpitaya_acquisition.redpitaya.output_signals import start_dac_memory

    uploaded = threading.Event()

    class DelayedDAC:
        def setCallback(self, callback):
            self.callback = callback

        def startStreamingFromMemory(self, host):
            def upload():
                uploaded.set()
                self.callback.sentPack(self, 128, 128)
            self.timer = threading.Timer(0.02, upload)
            self.timer.start()
            return True

    client = DelayedDAC()
    start_dac_memory(SimpleNamespace(DACCallback=object), client, "rp", (1, 2))
    assert uploaded.is_set()
    client.timer.join()


def test_async_connection_failure_is_reported_and_observer_removed() -> None:
    class FailedDAC(FakeDAC):
        def setCallback(self, callback):
            self.callback = callback

        def startStreamingFromMemory(self, host):
            self.callback.error(self, host, 111)
            return True

    observers = []
    calls = []
    config_client = SimpleNamespace(
        calls=calls, sendConfig=lambda *args: True,
        addCallback=observers.append, removeCallback=observers.remove,
    )
    with pytest.raises(RuntimeError, match="connection failed"):
        start_output_signals(
            SimpleNamespace(DACStreamClient=FailedDAC, DACCallback=object, ConfigCallback=object),
            SimpleNamespace(host="rp"), config_client, pair(),
        )
    assert not observers
    assert calls[-3:] == [("stop",), ("notify",), ("wait",)]


@pytest.mark.asyncio
async def test_worker_sends_validated_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = NativeStreamingWorker(
        Settings(_env_file=None), lambda frame: None, lambda event, detail: None
    )
    calls = []

    async def command(*args):  # type: ignore[no-untyped-def]
        calls.append(args)

    monkeypatch.setattr(worker, "_command", command)
    config = pair()
    await worker.set_output_signals(config)
    assert calls == [("output-signals", config.model_dump(mode="json", by_alias=True))]


@pytest.mark.asyncio
async def test_isolated_worker_changes_and_stops_outputs_while_adc_runs() -> None:
    import asyncio

    frames = []
    worker = NativeStreamingWorker(
        Settings(
            _env_file=None,
            source="redpitaya",
            host="rp-test",
            streaming_module="tests.fakes.fake_rp_outputs",
        ),
        frames.append,
        lambda event, detail: None,
    )
    try:
        await worker.connect("rp-test")
        await worker.start()
        await worker.set_output_signals(pair())
        await worker.set_output_signals(pair(90))
        await worker.set_output_signals(OutputSignalsConfig())
        await asyncio.sleep(0.05)
        assert frames
        await worker.stop()
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_stream_source_replaces_both_outputs_and_disconnect_cleans_up() -> None:
    from redpitaya_acquisition.redpitaya.streaming_source import RedPitayaStreamingSource
    from tests.fakes import fake_rp_outputs

    source = RedPitayaStreamingSource(Settings(_env_file=None, host="rp-test"))
    client = fake_rp_outputs.ConfigStreamClient()
    source._library = SimpleNamespace(module=fake_rp_outputs)  # type: ignore[assignment]
    source._config_client = client
    source._adapter = SimpleNamespace(host="rp-test", disconnect=lambda: None)  # type: ignore[assignment]
    await source.set_output_signals(pair())
    first = source._output_client
    await source.set_output_signals(pair(90))
    assert not first.started
    assert source.output_status()["state"] == "running"
    assert source.output_status()["config"]["output2"]["phaseDegrees"] == 90
    second = source._output_client
    await source.disconnect()
    assert not second.started
    assert client.dac_stops == 3
    assert source.output_status()["state"] == "inactive"
