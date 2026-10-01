# Quantum Light Engine and Native Receiver

This repository contains two applications for multi-device Red Pitaya acquisition:

- [Quantum Light Engine](src/quantum_light_engine/README.md) — the headless acquisition service,
  device-profile store, REST API, and binary WebSocket publisher.
- [Native Receiver](apps/native_receiver/README.md) — the Tk desktop client for connecting to the
  engine, viewing live signals, spectra and configurable 1D nullifiers, and running post-processing
  tools. The analysis area has Spectrum analyzer and Nullifiers tabs, with configurable collection
  windows for sample-hold experiments. See the [nullifier guide](docs/nullifiers.md).

The shared `src/redpitaya_acquisition` package is the hardware-facing acquisition core used by the
engine. It contains the simulator, Red Pitaya integration, frame model, binary encoder, loss metrics,
and bounded streaming queues; it is not a third application.

## Quick start

```powershell
uv sync --extra dev
uv run quantum-light-engine run
```

In a second terminal:

```powershell
uv run quantum-light-receiver
```

The receiver can also launch a local engine automatically. For a hardware-free check, use a simulator
profile as described in the [engine guide](src/quantum_light_engine/README.md).

## Development checks

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy
```
