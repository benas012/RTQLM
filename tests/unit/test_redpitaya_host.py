import socket

import redpitaya_acquisition.redpitaya.host as host_module
from redpitaya_acquisition.redpitaya.host import streaming_host, web_base_url


def test_streaming_host_accepts_plain_ip_or_url() -> None:
    assert streaming_host("https://192.168.1.64/path") == "192.168.1.64"


def test_streaming_host_resolves_local_name_to_ipv4(monkeypatch) -> None:
    def fake_getaddrinfo(host: str, *_args) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        assert host == "rp-f0f9eb.local"
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.162.246", 0))]

    monkeypatch.setattr(host_module.socket, "getaddrinfo", fake_getaddrinfo)

    assert streaming_host("http://rp-f0f9eb.local/") == "169.254.162.246"


def test_streaming_host_falls_back_to_hostname_when_resolution_fails(monkeypatch) -> None:
    def fake_getaddrinfo(*_args) -> None:
        raise socket.gaierror

    monkeypatch.setattr(host_module.socket, "getaddrinfo", fake_getaddrinfo)

    assert streaming_host("rp-test.local") == "rp-test.local"


def test_web_base_url_accepts_plain_host_or_url() -> None:
    assert web_base_url("rp-f0f9eb.local") == "http://rp-f0f9eb.local"
    assert web_base_url("http://rp-f0f9eb.local/") == "http://rp-f0f9eb.local"
