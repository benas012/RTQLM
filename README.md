# Quantum Light Acquisition Engine

`quantum-light-engine` is a manually launched, multi-device acquisition service for Red Pitaya
STEMlab ADC streams and deterministic simulators. It exposes device lifecycle and metadata over REST
and raw signed `int16` samples over binary WebSockets. It does not include a GUI or recording.

Each registered device has an independent session, source, bounded frame queue, sequence, loss counters,
serializer, broadcaster, and per-client queues. A failed or slow device cannot block another device.
Acquisition continues when no WebSocket clients are connected.

## Install and run

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are recommended:

```powershell
uv sync --extra dev
Copy-Item .env.example .env
uv run quantum-light-engine run --host 127.0.0.1 --port 8080 --data-dir ./data --log-dir ./logs
```

Equivalent Uvicorn command:

```powershell
uv run uvicorn quantum_light_engine.main:app --host 127.0.0.1 --port 8080
```

Stop cleanly with `Ctrl+C`. SIGINT and SIGTERM are handled by Uvicorn; engine shutdown concurrently
stops and disconnects every device with a bounded timeout.

Other CLI commands do not start acquisition:

```powershell
uv run quantum-light-engine validate-config --data-dir ./data
uv run quantum-light-engine list-devices --data-dir ./data
```

The service binds to loopback by default and has no authentication. Authentication and TLS are required
before exposing it to another computer or an untrusted network.

## Register and stream a simulator

```powershell
$device = curl.exe -s -X POST http://127.0.0.1:8080/api/v1/devices `
  -H "Content-Type: application/json" `
  -d '{"name":"Simulator A","source":"simulator","isDefault":true,"channel1Enabled":true,"channel2Enabled":true,"decimation":256,"blockSizeBytes":131072,"adcBufferSizeBytes":4194304}' | ConvertFrom-Json

curl.exe -X POST "http://127.0.0.1:8080/api/v1/devices/$($device.id)/connect"
curl.exe -X POST "http://127.0.0.1:8080/api/v1/devices/$($device.id)/start"
curl.exe "http://127.0.0.1:8080/api/v1/devices/$($device.id)/status"
```

Connect a binary client to:

```text
ws://127.0.0.1:8080/ws/v1/devices/{deviceId}/samples
```

Opening a WebSocket never starts acquisition. Closing the final client never stops it.

For a quick browser plotter, open `http://127.0.0.1:8080/receiver`. It lists registered Red Pitaya
profiles, can add a new profile, starts the selected device, connects to the sample WebSocket, decodes
the binary `RPA1` frames, and plots the latest CH1/CH2 packet on a canvas. The standalone source is
[examples/live_receiver.html](examples/live_receiver.html).

For a native desktop receiver instead of the browser UI:

```powershell
uv run python examples/native_receiver.py
```

It uses Python's built-in Tkinter toolkit plus the existing WebSocket dependency. The native receiver
has a persisted `Launch engine on app start` setting stored in `data/native_receiver_settings.json`.
When enabled, it checks `/health/live` and launches the local engine only if it is not already running.
Python signal scripting in the native receiver is disabled by default. To enable the local signal
script editor for a trusted session:

```powershell
$env:QL_NATIVE_RECEIVER_SIGNAL_SCRIPT="1"
uv run python examples/native_receiver.py
```

### Live 1D-cluster nullifiers

The native receiver includes a stateful two-channel post-processing pipeline. Open **Signal → 1D
Cluster Nullifiers** (or use the **Cluster Nullifiers** button) to configure the temporal mode,
channel-B sample delay, optional lock/noise rejection threshold, rolling-variance duration, an
explicit mode template, and arbitrary nullifier terms/offsets/weights. Configuration is stored in
`data/cluster_processing.json`.

For shot-noise normalization, stream a shot-noise measurement, select **Start shot-noise
calibration**, collect at least 100 complete modes, and select **Finish calibration**. Then switch to
the cluster-state measurement. The monitor plots rolling nullifier variance in dB relative to the
shot-noise reference implied by each nullifier's weights. Packet gaps reset incomplete modes instead
of joining samples across missing data; an optional absolute-voltage gate rejects lock/noise modes.

The default 6 MHz, 0.308 µs analytic mode requires at least 16 samples per temporal mode and therefore
rejects the low-rate decimation presets. Lowering that guard is possible in the monitor but should be
done only after validating the sampled mode against lab data. Two-channel raw streaming at the rate
needed for 16 samples per mode can exceed Gigabit Ethernet throughput; high-fidelity continuous use
may require acquisition-board filtering or another faster data path.

## Red Pitaya profiles

Register a profile with `source: "redpitaya"` and a non-empty `host`. Install the official Python
streaming wrapper supplied with the Red Pitaya streaming client. It must match the OS version installed
on the board. The loader uses a profile's `streamingModulePath`, then the engine-level
`QL_STREAMING_MODULE_PATH`, or tries `rp_stream` and `streaming`.

On Windows, download the matching `rpsa_client-...-win.zip` from the board's Streaming application,
extract it outside this repository, and set `QL_STREAMING_MODULE_PATH` to the extracted `python_lib`
directory. The adapter supports the official `setReciveDataFunction`, `recievePack`, and `sendConfig`
surface as well as older wrapper variants.

Example for Red Pitaya OS `2.07-48`:

```powershell
$env:QL_STREAMING_MODULE_PATH="$env:LOCALAPPDATA\RedPitaya\rpsa-client-2.07-48\python_lib"
uv run quantum-light-engine run --host 127.0.0.1 --port 8080
```

The service accepts either `rp-f0f9eb.local` or `http://rp-f0f9eb.local/` in a device profile. The
native streaming client receives the normalized host name. On connection, the service also asks the
Red Pitaya web app manager to start `streaming_manager` before loading the native client.

If connection still fails with `actively refused`, verify the board-side streaming server with the
official client:

```powershell
& "$env:LOCALAPPDATA\RedPitaya\rpsa-client-2.07-48\rpsa_client.exe" -d -t 5
& "$env:LOCALAPPDATA\RedPitaya\rpsa-client-2.07-48\rpsa_client.exe" -c -h 169.254.237.216 -g V -v
```

`Found boards:` with no entries, HTTP 502 from `ws://<board>/wss`, or `actively refused` means the
Python module is installed but the Red Pitaya streaming backend is not running on the board.

The private Red Pitaya protocol is not reimplemented. Wrapper differences remain isolated in
`redpitaya_acquisition/redpitaya/compatibility.py`.

## Storage and migration

Profiles are stored as one atomic JSON file per UUID under `data/devices/`. Invalid files are moved to
`data/quarantine/` with their original contents preserved. Runtime status and samples are not persisted.

If storage is empty and legacy `RP_SOURCE`, `RP_HOST`, channel, decimation, block-size, or ADC-buffer
environment settings exist, the engine creates one default profile once. Existing profiles are never
overwritten, and legacy settings are not used for device runtime configuration after migration.

Engine-wide settings use the `QL_` prefix; see [.env.example](.env.example). Both `QL_AUTO_CONNECT` and
`QL_AUTO_START` default to false. Automatic startup failures are isolated per device.

## Compatibility and diagnostics

Temporary `/api/status`, `/api/config`, `/api/start`, `/api/stop`, `/api/latest`, and `/ws/samples`
routes resolve the profile marked `isDefault`. REST responses include
`X-QuantumLight-Deprecated: true`. Without a default profile they fail clearly.

Monitor FPGA cumulative loss, packet-ID gaps, and service queue drops independently. This engine is for
live monitoring, not guaranteed lossless archival. Use wired Gigabit Ethernet for hardware streaming.

## Development

```powershell
uv run pytest
uv run ruff check .
uv run mypy src
```

The tests require no hardware and cover persistence, quarantine, migration, multi-session isolation,
three concurrent simulators, lifecycle APIs, legacy routes, WebSockets, and byte-compatible framing.

Further documentation:

- [Architecture](docs/architecture.md)
- [REST and WebSocket API](docs/api.md)
- [Device profiles](docs/device-profiles.md)
- [Binary protocol](docs/binary-protocol.md)
