# Architecture

```text
FastAPI engine
    |
    v
DeviceSessionManager
    |-- DeviceSession A -- AcquisitionManager -- source/queue/broadcaster
    |-- DeviceSession B -- AcquisitionManager -- source/queue/broadcaster
    `-- DeviceSession C -- AcquisitionManager -- source/queue/broadcaster
```

The separate `apps/native_receiver` application depends on the engine's versioned HTTP and WebSocket
interfaces. The engine depends on the `redpitaya_acquisition` core; the acquisition core does not depend
on either application. This one-way dependency keeps UI, service lifecycle, and hardware I/O isolated.

Persistent `DeviceProfile` objects describe configuration. Runtime `DeviceSession` objects own state,
tasks, sources, counters, and transport. Keeping these separate prevents packet-path work from touching
profile storage and allows a process restart without persisting transient state.

Every session creates the tested acquisition core from `redpitaya_acquisition`: immutable raw frames,
simulator or official Red Pitaya source, bounded internal queue, serializer, loss metrics, and
broadcaster. There is no global frame queue or acquisition lock. Session lifecycle locks are independent,
and engine shutdown operates on all sessions concurrently.

Each accepted frame enters only its owning manager. It is serialized once into immutable `bytes`, then
the same object is offered to every subscriber for that device. Each subscriber has a bounded queue and
drops its own oldest message when slow. Acquisition and other devices never wait for that subscriber.

Raw arrays remain one-dimensional, contiguous, read-only little-endian signed `int16`. No packet path
uses JSON, Python lists, per-sample loops, profile persistence, or per-packet logging.

`connect` establishes and retains a prepared source. `start` consumes that source and automatically
connects first if needed. `stop` ends acquisition and prepares a connected source again, leaving the
session `ready`. `disconnect` stops first when needed and releases the source.

Individual manager reconnect and fault state is projected into device status. A fault affects only its
session. Engine readiness means settings, repository, session manager, and API are initialized; a device
fault does not make the engine unready.
