"""Rate limiting.

Sliding-window limiter with two interchangeable backends:

* ``MemoryRateLimiter``  -- per-process sliding window (single-node / dev /
  tests).  No external dependency.
* ``RedisRateLimiter``   -- fixed-window counters via Redis (multi-process
  deployments).  Used automatically when ``AGP_REDIS_URL`` is configured and
  the client library is importable.

Keys are ``<scope>:<identity>`` where identity is the authenticated user id
when available, otherwise the client IP.  Auth endpoints are additionally
limited per IP because they run before authentication.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Iterable

import structlog

from app.core.config import Settings
from app.core.errors import RateLimitError

logger = structlog.stdlib.get_logger("app.ratelimit")


class RateLimitResult(tuple):
    def __new__(cls, allowed: bool, retry_after_s: int):
        return super().__new__(cls, (allowed, retry_after_s))


class RateLimiter:
    async def hit(self, key: str, limit: int, window_s: float) -> RateLimitResult:
        raise NotImplementedError


class MemoryRateLimiter(RateLimiter):
    """Sliding window over per-key timestamp deques."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def hit(self, key: str, limit: int, window_s: float) -> RateLimitResult:
        now = time.monotonic()
        async with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = deque()
                self._hits[key] = bucket
            cutoff = now - window_s
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(bucket[0] + window_s - now) + 1)
                return RateLimitResult(False, retry_after)
            bucket.append(now)
            if len(self._hits) > 10_000:  # opportunistic hygiene
                self._prune(cutoff)
            return RateLimitResult(True, 0)

    def _prune(self, cutoff: float) -> None:
        for key in list(self._hits.keys()):
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                self._hits.pop(key, None)


class RedisRateLimiter(RateLimiter):
    """Fixed-window counters (INCR + EXPIRE) — atomic and multi-process."""

    def __init__(self, client: object) -> None:  # redis.asyncio.Redis
        self._client = client

    async def hit(self, key: str, limit: int, window_s: float) -> RateLimitResult:
        window = max(1, int(window_s))
        redis_key = f"agp:rl:{key}:{int(time.time() // window)}"
        try:
            count = await self._client.incr(redis_key)  # type: ignore[attr-defined]
            if count == 1:
                await self._client.expire(redis_key, window)  # type: ignore[attr-defined]
            if count > limit:
                ttl = await self._client.ttl(redis_key)  # type: ignore[attr-defined]
                return RateLimitResult(False, max(1, ttl))
            return RateLimitResult(True, 0)
        except Exception as exc:  # Redis unavailable -> fail open, log it
            logger.warning("rate_limiter_redis_unavailable", error=str(exc))
            return RateLimitResult(True, 0)


_limiter: RateLimiter | None = None


def get_rate_limiter(settings: Settings) -> RateLimiter:
    """Process-wide limiter; Redis-backed when configured and importable."""
    global _limiter
    if _limiter is not None:
        return _limiter
    if settings.redis_url:
        try:
            import redis.asyncio as aioredis  # type: ignore

            client = aioredis.from_url(
                settings.redis_url, decode_responses=True, socket_timeout=2
            )
            _limiter = RedisRateLimiter(client)
            return _limiter
        except Exception:  # pragma: no cover - optional dependency
            logger.warning("redis_rate_limiter_unavailable_falling_back_to_memory")
    _limiter = MemoryRateLimiter()
    return _limiter


def reset_rate_limiter() -> None:
    """Test helper."""
    global _limiter
    _limiter = None


async def enforce_rate_limit(
    limiter: RateLimiter,
    *,
    scope: str,
    identity: str,
    limit: int,
    window_s: float = 60.0,
) -> None:
    allowed, retry_after = await limiter.hit(f"{scope}:{identity}", limit, window_s)
    if not allowed:
        raise RateLimitError(
            "too many requests, retry later", retry_after_s=retry_after
        )


def client_identity(request, trust_proxy_headers: bool) -> str:
    """Best-effort client IP for pre-authentication rate limiting."""
    forwarded = request.headers.get("x-forwarded-for")
    if trust_proxy_headers and forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def parse_origins(origins: Iterable[str] | None) -> list[str]:
    return [o.strip() for o in (origins or []) if o.strip()]
