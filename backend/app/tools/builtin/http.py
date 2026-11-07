"""HTTP request tool with SSRF protection.

Every URL is validated before contact:

* scheme must be http/https; userinfo forbidden
* the hostname is resolved and ALL resolved addresses must be public
  (loopback / private / link-local / reserved / multicast are rejected)
* redirects are followed manually, re-validating every hop
* response bodies are size-capped

``allow_private`` exists for explicitly trusted internal deployments and is
never enabled by default.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx
import structlog
from pydantic import BaseModel, Field

from app.core.errors import RequestValidationError, ToolRetryableError
from app.tools.base import Tool, ToolContext

logger = structlog.stdlib.get_logger("app.tools.http")

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
_BLOCKED_RESPONSE_HEADERS = {"set-cookie", "www-authenticate"}
_MAX_REDIRECTS = 3


def _is_blocked_ip(ip: str) -> bool:
    import ipaddress

    address = ipaddress.ip_address(ip)
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


async def validate_public_http_url(url: str, *, allow_private: bool = False) -> str:
    """Validate a URL for outbound requests; returns the normalised URL."""
    if not isinstance(url, str) or len(url) > 2000 or "\x00" in url or " " in url:
        raise RequestValidationError("invalid URL")

    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise RequestValidationError(
            f"scheme '{parts.scheme}' is not allowed", details={"allowed": sorted(_ALLOWED_SCHEMES)}
        )
    if not parts.hostname:
        raise RequestValidationError("URL has no hostname")
    if parts.username or parts.password:
        raise RequestValidationError("URLs with embedded credentials are not allowed")
    if parts.port is not None and not (1 <= parts.port <= 65535):
        raise RequestValidationError("invalid port")

    if allow_private:
        return url

    # resolve DNS off the event loop and check every address
    def _resolve() -> list[str]:
        try:
            infos = socket.getaddrinfo(parts.hostname, parts.port or 80, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise RequestValidationError(f"cannot resolve host '{parts.hostname}'") from exc
        return list({info[4][0] for info in infos})

    addresses = await asyncio.to_thread(_resolve)
    blocked = [ip for ip in addresses if _is_blocked_ip(ip)]
    if blocked:
        raise RequestValidationError(
            "requests to private, loopback or reserved addresses are blocked (SSRF protection)",
            details={"host": parts.hostname, "blocked_addresses": blocked},
        )
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, parts.fragment))


class HttpRequestInput(BaseModel):
    method: str = Field(default="GET", pattern="^(GET|POST|PUT|PATCH|DELETE|HEAD)$")
    url: str = Field(max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, max_length=256 * 1024)
    timeout_s: int = Field(default=30, ge=1, le=120)
    max_response_bytes: int = Field(default=65536, ge=256, le=1024 * 1024)


class HttpRequestOutput(BaseModel):
    status_code: int
    headers: dict[str, str]
    body: str
    elapsed_ms: int
    final_url: str


class HttpRequestTool(Tool):
    key = "http.request"
    name = "HTTP request"
    description = "Perform an outbound HTTP/HTTPS request to a public URL."
    category = "network"
    permissions: ClassVar[list[str]] = ["net:http"]
    InputModel = HttpRequestInput
    OutputModel = HttpRequestOutput
    default_timeout_s = 45
    max_timeout_s = 150
    requires_approval: ClassVar[bool] = False  # tasks: governed by auto-approve policy
    allowed_in_chat: ClassVar[bool] = True

    async def run(self, ctx: ToolContext, raw_input: dict[str, Any]) -> dict[str, Any]:
        method = raw_input["method"].upper()
        if method not in _ALLOWED_METHODS:  # pragma: no cover - schema enforced
            raise RequestValidationError("method not allowed")

        # header hygiene
        clean_headers: dict[str, str] = {}
        for name, value in (raw_input.get("headers") or {}).items():
            if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
                raise RequestValidationError("invalid header (CR/LF forbidden)")
            if name.lower() in {"host", "content-length", "transfer-encoding", "connection"}:
                continue
            clean_headers[name[:100]] = value[:2000]

        url = await validate_public_http_url(raw_input["url"])
        started = time.perf_counter()
        redirects = 0

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(raw_input["timeout_s"]),
            follow_redirects=False,
        ) as client:
            while True:
                response = await client.request(
                    method,
                    url,
                    headers=clean_headers,
                    content=raw_input.get("body"),
                )
                if response.is_redirect and response.has_redirect_location():
                    if redirects >= _MAX_REDIRECTS:
                        raise ToolRetryableError("too many redirects")
                    redirects += 1
                    next_url = str(response.headers["location"])
                    url = await validate_public_http_url(
                        next_url if "://" in next_url else str(response.url.join(next_url))
                    )
                    continue
                break

        body_bytes = response.content[: raw_input["max_response_bytes"] + 1]
        truncated = len(body_bytes) > raw_input["max_response_bytes"]
        headers = {
            name: value[:500]
            for name, value in response.headers.items()
            if name.lower() not in _BLOCKED_RESPONSE_HEADERS
        }
        return {
            "status_code": response.status_code,
            "headers": headers,
            "body": body_bytes[: raw_input["max_response_bytes"]].decode(
                "utf-8", errors="replace"
            )
            + ("…[truncated]" if truncated else ""),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "final_url": str(response.url),
        }
