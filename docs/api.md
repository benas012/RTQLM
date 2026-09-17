# API version 1

All sample payloads use WebSocket binary messages. REST responses contain only profiles, status, and
frame metadata.

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
- `WS /ws/v1/devices/{deviceId}/samples` - binary frames for exactly one device.

Busy profile updates and connected deletion return 409. Unknown IDs return 404. Missing vendor streaming
components return 503. Problem responses use `type`, `title`, `status`, `detail`, and, where applicable,
`deviceId`. Validation errors use FastAPI's standard 422 response. Stack traces are never returned.

## Temporary compatibility API

`/api/status`, `/api/config`, `/api/start`, `/api/stop`, `/api/latest`, and `/ws/samples` map to the one
profile marked `isDefault`. REST responses carry `X-QuantumLight-Deprecated: true`. They do not contain a
second acquisition implementation and will be removed in a future API version.

