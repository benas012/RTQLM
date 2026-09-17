from __future__ import annotations

import socket
from urllib.parse import urlsplit


def streaming_host(value: str) -> str:
    """Return the host form expected by the native streaming client."""

    host = _host_part(value)
    return _resolved_ipv4(host)


def web_base_url(value: str) -> str:
    """Return the Red Pitaya web base URL used by the Bazaar app manager."""

    candidate = value.strip().rstrip("/")
    if "://" in candidate:
        parsed = urlsplit(candidate)
        netloc = parsed.netloc or parsed.path.split("/", 1)[0]
        scheme = parsed.scheme or "http"
        return f"{scheme}://{netloc}"
    return f"http://{candidate.split('/', 1)[0]}"


def _host_part(value: str) -> str:
    candidate = value.strip()
    if "://" in candidate:
        parsed = urlsplit(candidate)
        return parsed.hostname or parsed.netloc or candidate
    return candidate.split("/", 1)[0].rstrip("/")


def _resolved_ipv4(host: str) -> str:
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return host
    for info in infos:
        address = info[4][0]
        if address:
            return str(address)
    return host
