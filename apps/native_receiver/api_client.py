"""Engine transport and device decoding, independent of the desktop UI."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from apps.native_receiver.constants import ENGINE_HEALTH_TIMEOUT_SECONDS, UI_LOGGER
from apps.native_receiver.protocol import Device


class ApiError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EngineClient:
    base_url: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.strip().rstrip("/"))

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 10,
    ) -> Any:
        started = time.monotonic()
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        status: int | str = "error"
        detail = ""
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                status = response.status
                if status == 204:
                    return None
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            status = exc.code
            detail = exc.reason
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail", detail)
            except Exception:
                pass
            raise ApiError(str(detail)) from exc
        except TimeoutError as exc:
            detail = f"Timed out after {timeout_seconds:g}s"
            raise ApiError(detail) from exc
        except URLError as exc:
            detail = str(exc.reason)
            raise ApiError(detail) from exc
        finally:
            UI_LOGGER.info(
                "api %s %s -> %s elapsed_ms=%s%s",
                method,
                path,
                status,
                int((time.monotonic() - started) * 1000),
                f" error={detail}" if detail else "",
            )

    def is_running(self) -> bool:
        try:
            request = Request(f"{self.base_url}/health/live", method="GET")
            with urlopen(request, timeout=ENGINE_HEALTH_TIMEOUT_SECONDS) as response:
                running: bool = response.status == 200
                response.read()
                return running
        except Exception:
            return False

    def wait_until_running(self, *, attempts: int, delay_seconds: float) -> bool:
        for attempt in range(attempts):
            if self.is_running():
                return True
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
        return False

    def devices(self) -> list[Device]:
        return [
            Device(
                id=item["id"],
                name=item["name"],
                host=item.get("host") or "",
                state=(item.get("runtime") or {}).get("state", "-"),
                channel_1_enabled=bool(item.get("channel1Enabled")),
                channel_2_enabled=bool(item.get("channel2Enabled")),
                decimation=int(item.get("decimation") or 256),
                block_size_bytes=int(item.get("blockSizeBytes") or 131_072),
                acquisition_mode=item.get("acquisitionMode", "streaming"),
                capture_samples=int(item.get("captureSamples", 625_024)),
                buffered_processing=item.get("bufferedProcessing", "arm"),
                server_stream_processing=item.get("serverStreamProcessing", False),
            )
            for item in self.request("GET", "/api/v1/devices")
            if item.get("source") == "redpitaya"
        ]

    def websocket_url(self, device_id: str) -> str:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}/ws/v1/devices/{device_id}/samples"
