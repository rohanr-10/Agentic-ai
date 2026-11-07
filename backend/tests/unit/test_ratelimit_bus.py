"""Unit tests: rate limiter + event bus."""

from __future__ import annotations

import asyncio

import pytest

from app.core.errors import RateLimitError
from app.core.ratelimit import MemoryRateLimiter, enforce_rate_limit
from app.events.bus import EventBus


class TestMemoryRateLimiter:
    async def test_allows_up_to_limit_then_blocks(self):
        limiter = MemoryRateLimiter()
        for _ in range(5):
            allowed, _ = await limiter.hit("k", 5, window_s=60)
            assert allowed
        allowed, retry_after = await limiter.hit("k", 5, window_s=60)
        assert not allowed
        assert retry_after >= 1

    async def test_keys_are_isolated(self):
        limiter = MemoryRateLimiter()
        for _ in range(5):
            await limiter.hit("a", 5, window_s=60)
        allowed, _ = await limiter.hit("b", 5, window_s=60)
        assert allowed

    async def test_window_slides(self):
        limiter = MemoryRateLimiter()
        for _ in range(2):
            await limiter.hit("k", 2, window_s=0.05)
        allowed, _ = await limiter.hit("k", 2, window_s=0.05)
        assert not allowed
        await asyncio.sleep(0.08)
        allowed, _ = await limiter.hit("k", 2, window_s=0.05)
        assert allowed

    async def test_enforce_raises_rate_limit_error(self):
        limiter = MemoryRateLimiter()
        for _ in range(3):
            await enforce_rate_limit(limiter, scope="s", identity="u", limit=3, window_s=60)
        with pytest.raises(RateLimitError) as exc_info:
            await enforce_rate_limit(limiter, scope="s", identity="u", limit=3, window_s=60)
        assert exc_info.value.retry_after_s >= 1


class TestInMemoryEventBus:
    async def test_publish_reaches_matching_subscriber_only(self):
        from app.core.config import Settings

        bus = EventBus(Settings(environment="test"))
        async with bus.subscribe(["task:1"]) as sub_a, bus.subscribe(["task:2"]) as sub_b:
            await bus.publish("task:1", "task.status", {"x": 1})
            event = await asyncio.wait_for(sub_a.queue.get(), timeout=1)
            assert event.type == "task.status"
            assert event.data == {"x": 1}
            assert sub_b.queue.empty()

    async def test_wildcard_topics_share_events(self):
        from app.core.config import Settings

        bus = EventBus(Settings(environment="test"))
        async with bus.subscribe(["user:1"]) as sub:
            await bus.publish("user:1", "notification", {"n": 1})
            await bus.publish("user:2", "notification", {"n": 2})
            first = await asyncio.wait_for(sub.queue.get(), timeout=1)
            assert first.data == {"n": 1}
            assert sub.queue.empty()

    async def test_sse_encoding(self):
        from app.core.config import Settings

        bus = EventBus(Settings(environment="test"))
        async with bus.subscribe(["t"]) as sub:
            await bus.publish("t", "delta", {"text": "hi"})
            event = await asyncio.wait_for(sub.queue.get(), timeout=1)
            encoded = event.sse_encode()
            assert encoded.startswith("event: delta\n")
            assert '"text": "hi"' in encoded

    async def test_bounded_queue_drops_instead_of_growing(self):
        from app.core.config import Settings

        bus = EventBus(Settings(environment="test"))
        async with bus.subscribe(["t"]) as sub:
            for _ in range(600):  # > queue maxsize (512)
                await bus.publish("t", "spam", {})
            assert sub.dropped > 0
