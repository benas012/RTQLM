# Quantum Light Engine

The Quantum Light Engine is the repository's headless service. It owns persistent device profiles,
one isolated acquisition session per device, device lifecycle state, and binary sample publication.
The native receiver is a separate client; the engine does not contain browser UI or signal rendering.

The engine also owns per-board DAC output control. Connected physical devices expose
`GET/PUT /api/v1/devices/{deviceId}/outputs` and `POST /api/v1/devices/{deviceId}/outputs/stop`.
Both outputs have configurable waveform, frequency, peak amplitude, offset and phase; arbitrary
normalized samples are supported. See [output signals](../../docs/output-signals.md) for the
stock 3.00-57 DAC backend, stop control, frequency quantization and receiver loopback presets.

## Install and configure

From the repository root, install the runtime and development tools:

```powershell
uv sync --extra dev
Copy-Item .env.example .env   # optional
```

Settings use the `QL_` prefix. The most useful values are `QL_BIND_HOST`, `QL_BIND_PORT`,
`QL_DATA_DIR`, `QL_LOG_DIR`, queue capacities, reconnect delays, `QL_STREAMING_MODULE_PATH`, and the
`QL_AUTO_CONNECT`/`QL_AUTO_START` lifecycle flags. Defaults are documented in
[`.env.example`](../../.env.example).

## CLI

The installed entry point remains `quantum-light-engine = quantum_light_engine.cli:main`.

```powershell
uv run quantum-light-engine run
uv run quantum-light-engine run --host 127.0.0.1 --port 8080
uv run quantum-light-engine validate-config
uv run quantum-light-engine list-devices
```

`--data-dir` and, for `run`, `--log-dir` override their configured locations.

## Simulator and Red Pitaya profiles

Profiles are created through `POST /api/v1/devices` or the native receiver. Use `source: "simulator"`
for a deterministic hardware-free stream; `simulatorSeed` and `simulatorSamplesPerPacket` control it.
For hardware, use `source: "redpitaya"`, set `host`, and configure channel enablement, decimation,
block size, ADC buffer size, and optionally `streamingModulePath`. The engine loads the vendor streaming
module dynamically and can start the board streaming application when configured to do so.

Profiles are JSON files in `<QL_DATA_DIR>/devices`; invalid files are quarantined under
`<QL_DATA_DIR>/quarantine`. Existing `RP_` environment configuration is migrated once when no profiles
exist. Logs use `<QL_LOG_DIR>/engine.log`. Profile formats and migration behavior are preserved; see
[device profiles](../../docs/device-profiles.md).

## Interfaces and lifecycle

The versioned REST API manages profiles and exposes engine/device status. Samples are sent only as
binary protocol-v1 messages at `WS /ws/v1/devices/{deviceId}/samples`. See the complete
[REST/WebSocket reference](../../docs/api.md) and [binary format](../../docs/binary-protocol.md).

`connect` prepares a source, `start` begins streaming (and connects first when necessary), `stop`
returns a session to `ready`, and `disconnect` releases it. Operations are idempotent where documented.
Each device has independent state, queues, counters, and reconnect behavior. Slow WebSocket clients
drop their own oldest queued frame instead of blocking acquisition or another device. Shutdown stops
all sessions within the configured timeout and closes subscribers.

## Tests and quality checks

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy
uv run quantum-light-engine validate-config
```

Integration tests exercise lifecycle operations and WebSocket streaming with the simulator, so physical
hardware is not required. Hardware adapter tests use a fake vendor library.

## Operational and security limits

The service has no authentication, authorization, TLS termination, or network tenancy boundary. Keep
the default loopback binding unless a trusted network layer supplies those controls. Profile writes and
lifecycle calls can control laboratory hardware. Validate the vendor module and Red Pitaya host before
enabling automatic startup. The service streams raw data in memory and does not provide recording.

For ownership and data-flow details, see [architecture](../../docs/architecture.md).
