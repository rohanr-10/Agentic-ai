"""Unit tests: SSRF protection for the HTTP tool."""

from __future__ import annotations

import pytest

from app.core.errors import RequestValidationError
from app.tools.builtin.http import validate_public_http_url


def _resolve_to(ip: str):
    """Patch DNS resolution to a fixed address without network access."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        import socket

        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 80))]

    return fake_getaddrinfo


class TestUrlValidation:
    async def test_public_host_allowed(self, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", _resolve_to("93.184.216.34"))
        url = await validate_public_http_url("https://example.com/path?q=1")
        assert url.startswith("https://example.com")

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",       # loopback
            "10.0.0.5",        # private
            "192.168.1.10",    # private
            "172.16.0.1",      # private
            "169.254.169.254", # link-local (cloud metadata)
            "0.0.0.0",         # unspecified
            "224.0.0.1",       # multicast
            "240.0.0.1",       # reserved
            "::1",             # IPv6 loopback
            "fd00::1",         # IPv6 unique-local
        ],
    )
    async def test_private_and_reserved_targets_blocked(self, monkeypatch, ip):
        monkeypatch.setattr("socket.getaddrinfo", _resolve_to(ip))
        with pytest.raises(RequestValidationError) as exc_info:
            await validate_public_http_url("http://internal.example.com/")
        assert "SSRF" in str(exc_info.value.message) or "blocked" in str(exc_info.value.message)

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/file",
            "gopher://example.com",
            "javascript:alert(1)",
            "http://user:pass@example.com/",  # embedded credentials
            "not a url",
        ],
    )
    async def test_dangerous_schemes_and_creds_rejected(self, url):
        with pytest.raises(RequestValidationError):
            await validate_public_http_url(url)

    async def test_allow_private_escape_hatch(self, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", _resolve_to("127.0.0.1"))
        url = await validate_public_http_url(
            "http://localhost:8080/", allow_private=True
        )
        assert "localhost" in url

    async def test_unresolvable_host_rejected(self, monkeypatch):
        import socket

        def failing(*args, **kwargs):
            raise socket.gaierror("no such host")

        monkeypatch.setattr("socket.getaddrinfo", failing)
        with pytest.raises(RequestValidationError):
            await validate_public_http_url("http://does-not-resolve.invalid/")

    async def test_live_request_to_loopback_blocked_end_to_end(self):
        """Integration with the real resolver: loopback IP literals are blocked
        without any mocking (getaddrinfo returns them directly)."""
        with pytest.raises(RequestValidationError):
            await validate_public_http_url("http://127.0.0.1:8000/")
        with pytest.raises(RequestValidationError):
            await validate_public_http_url("http://169.254.169.254/latest/meta-data/")
