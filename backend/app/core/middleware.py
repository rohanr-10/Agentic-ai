"""Pure-ASGI middleware.

Implemented as raw ASGI middleware (not ``BaseHTTPMiddleware``) so that
streaming responses (SSE) and client-disconnect detection behave correctly.
"""

from __future__ import annotations

import time
from typing import Any, Callable
from uuid import UUID

import structlog
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import telemetry
from app.core.utils import uuid_str

logger = structlog.stdlib.get_logger("app.http")

_SAFE_ID_MAX = 128


def _clean_request_id(raw: str | None) -> str:
    if raw and len(raw) <= _SAFE_ID_MAX and all(32 <= ord(c) < 127 for c in raw):
        return raw
    return uuid_str()


class RequestContextMiddleware:
    """Correlation ids, request logging and latency metrics.

    * Honours an inbound ``X-Request-ID`` (validated) or generates one.
    * Binds ``request_id`` to the structlog context for the whole request.
    * Emits one structured log line per request and records Prometheus
      metrics (failures here never break the request).
    """

    def __init__(self, app: ASGIApp, *, metrics_enabled: bool = True) -> None:
        self.app = app
        self.metrics_enabled = metrics_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = _clean_request_id(headers.get("x-request-id"))
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=scope.get("method"),
            path=scope.get("path"),
        )
        start = time.perf_counter()
        status_holder: dict[str, int] = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode("ascii")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            route = scope.get("route")
            route_path = getattr(route, "path", scope.get("path", "")) if route else scope.get("path", "")
            level = "info"
            status = status_holder["status"]
            if status >= 500:
                level = "error"
            elif status >= 400:
                level = "warning"
            log_fn = getattr(logger, level)
            log_fn(
                "http_request",
                status=status,
                duration_ms=round(duration_ms, 2),
                route=route_path,
            )
            if self.metrics_enabled:
                try:
                    telemetry.record_http(
                        route=route_path or "unmatched",
                        method=scope.get("method", ""),
                        status=status,
                        duration_s=duration_ms / 1000.0,
                    )
                except Exception:  # pragma: no cover - metrics must not break
                    pass
            structlog.contextvars.unbind_contextvars("request_id", "method", "path")


class BodySizeLimitMiddleware:
    """Reject request bodies above the configured limit with HTTP 413.

    Works for both ``Content-Length`` and chunked bodies.  The limit must be
    configured at or above the file-upload limit so multipart uploads can
    reach the file service, which enforces its own per-file cap.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                await self._reject(send)
                return

        received = 0

        async def receive_wrapper() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Drain nothing further; the caller is disconnected by
                    # responding immediately with 413.
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, receive_wrapper, send)
        except _BodyTooLarge:
            await self._reject(send)

    async def _reject(self, send: Send) -> None:
        body = b'{"error":{"code":"payload_too_large","message":"request body exceeds the configured limit"}}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    pass


class SecurityHeadersMiddleware:
    """Baseline security response headers for an API-only service."""

    def __init__(self, app: ASGIApp, *, hsts_enabled: bool = False) -> None:
        self.app = app
        self.hsts_enabled = hsts_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = message.setdefault("headers", [])
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"referrer-policy", b"no-referrer"))
                headers.append(
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
                )
                headers.append((b"cache-control", b"no-store"))
                if self.hsts_enabled:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def bind_user_to_logs(user_id: UUID | str | None) -> None:
    """Bind (or clear) the acting user id on the logging context."""
    if user_id is None:
        structlog.contextvars.unbind_contextvars("user_id")
    else:
        structlog.contextvars.bind_contextvars(user_id=str(user_id))


# Re-exported for convenient typing in tests
AnyCallable = Callable[..., Any]
