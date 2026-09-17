# Binary sample protocol

The multi-device refactor preserves protocol version 1 byte-for-byte. A device UUID is not added because
the WebSocket URL identifies the device.

Each message is an 80-byte little-endian header using `struct.Struct("<4sBBHQQQQdIIIIQQ")`, followed by
channel 1 samples when enabled, then channel 2 samples when enabled. Samples are signed little-endian
`int16`.

| Offset | Field | Type |
|---:|---|---|
| 0 | magic `RPA1` | 4 bytes |
| 4 | protocol version `1` | uint8 |
| 5 | channel mask | uint8 |
| 6 | header size `80` | uint16 |
| 8 | stream ID | uint64 |
| 16 | sequence | uint64 |
| 24 | source packet ID | uint64 |
| 32 | local capture timestamp ns | uint64 |
| 40 | sample rate Hz | float64 |
| 48 | decimation | uint32 |
| 52 | samples per channel | uint32 |
| 56 | payload bytes | uint32 |
| 60 | flags | uint32 |
| 64 | cumulative FPGA loss CH1 | uint64 |
| 72 | cumulative FPGA loss CH2 | uint64 |

Channel mask bit 0 is CH1 and bit 1 is CH2. Flag bit 0 is packet-ID gap, bit 1 is service queue drop,
bit 2 indicates a locally estimated timestamp, and bits 3/4 reserve channel attenuator state.

Python zero-copy payload views:

```python
import struct
import numpy as np

HEADER = struct.Struct("<4sBBHQQQQdIIIIQQ")
header = HEADER.unpack_from(message)
mask, count, payload_bytes = header[2], header[10], header[11]
payload = memoryview(message)[80:80 + payload_bytes]
offset = 0
if mask & 1:
    ch1 = np.frombuffer(payload, dtype="<i2", count=count, offset=offset)
    offset += count * 2
if mask & 2:
    ch2 = np.frombuffer(payload, dtype="<i2", count=count, offset=offset)
```

In C#, use `BinaryPrimitives.ReadUInt64LittleEndian`, `ReadUInt32LittleEndian`, and
`ReadInt16LittleEndian` over `ReadOnlySpan<byte>`. Validate magic, version, header size, and payload size
before creating channel spans.

