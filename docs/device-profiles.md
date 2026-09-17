# Device profiles

Profiles live under the configured `QL_DATA_DIR`:

```text
data/
  devices/{uuid}.json
  quarantine/{original-name}.{utc-stamp}.corrupt
```

Example:

```json
{
  "id": "d10972ae-4cc4-4978-b9f0-00b339fd6915",
  "name": "Photon Detector A",
  "source": "redpitaya",
  "host": "192.168.1.120",
  "enabled": true,
  "isDefault": true,
  "channel1Enabled": true,
  "channel2Enabled": true,
  "decimation": 256,
  "blockSizeBytes": 131072,
  "adcBufferSizeBytes": 4194304,
  "streamingModulePath": null,
  "simulatorSamplesPerPacket": 4096,
  "simulatorSeed": 1,
  "createdAtUtc": "2026-06-22T12:00:00Z",
  "updatedAtUtc": "2026-06-22T12:00:00Z"
}
```

IDs are UUIDs and timestamps are UTC. Names are non-empty, at least one channel is enabled, and source is
`redpitaya` or `simulator`. Hardware requires a host; simulation does not. Decimation is 1-65536, block
size is 2048-2097152 bytes, and ADC buffer size is 1048576-104857600 bytes. Only one profile can be the
default.

Writes use a temporary file in `devices/`, flush and `fsync`, then atomic replacement. A corrupt or
filename-mismatched profile is moved to quarantine rather than deleted or allowed to crash startup.
Updates preserve `createdAtUtc` and replace `updatedAtUtc`. Connected devices cannot be deleted.

