# API version 1

Raw sample payloads use WebSocket binary messages. REST responses contain profiles, status, frame
metadata, and optional derived nullifier results.

For boards enabled by `QL_NATIVE_NULLIFIER_ENABLED` and `QL_NATIVE_NULLIFIER_HOSTS`, the same
nullifier routes forward commands to the installed ARM C processor. Device status identifies
`nullifierBackend: redpitaya-arm-c`. Latest results include streaming loss/overrun counters and
`displayStride` for sampled display arrays; `modePeriodSeconds` remains the true mode duration.

## Engine

- `GET /api/v1/engine/status` - version, uptime, device and client counts.
- `GET /health/live` - event loop is serving requests.
- `GET /health/ready` - repository, manager, and API initialization completed.

## Devices

- `GET /api/v1/devices` - profiles plus concise runtime state.
- `POST /api/v1/devices` - create a profile; the server assigns UUID and UTC timestamps.
- `POST /api/v1/devices/discover` - bounded saved-profile discovery; results are not saved or connected.
- `GET /api/v1/devices/{deviceId}` - profile and runtime state.
- `PUT /api/v1/devices/{deviceId}` - replace configuration while disconnected or ready.
- `DELETE /api/v1/devices/{deviceId}` - delete only while disconnected; returns 204.
- `POST /api/v1/devices/{deviceId}/connect` - idempotent.
- `POST /api/v1/devices/{deviceId}/disconnect` - idempotent; stops first when necessary.
- `POST /api/v1/devices/{deviceId}/start` - idempotent and automatically connects.
- `POST /api/v1/devices/{deviceId}/stop` - idempotent; remains connected and ready.
- `GET /api/v1/devices/{deviceId}/status` - counters and lifecycle state.
- `GET /api/v1/devices/{deviceId}/latest` - latest frame metadata, or 404 before a frame.
- `GET /api/v1/devices/{deviceId}/trigger` - cached external digital level and edge count.
- `PUT /api/v1/devices/{deviceId}/trigger` - configure slow external pin polling;
  see [firmware requirements and example](external-trigger.md).
- `GET /api/v1/devices/{deviceId}/nullifiers` - processing configuration, revision, calibration
  progress, result availability and analysis errors.
- `POST /api/v1/devices/{deviceId}/nullifiers/commands` - configure formulas and processing settings,
  control calibration, reset results or disable processing. See [command examples](nullifier-commands.md).
- `GET /api/v1/devices/{deviceId}/nullifiers/latest` - latest derived results and the configuration
  revision used to calculate them; 404 until complete modes have been processed.
- `WS /ws/v1/devices/{deviceId}/samples` - binary frames for exactly one device.

Busy profile updates and connected deletion return 409. Unknown IDs return 404. Missing vendor streaming
components return 503. Problem responses use `type`, `title`, `status`, `detail`, and, where applicable,
`deviceId`. Validation errors use FastAPI's standard 422 response. Stack traces are never returned.
