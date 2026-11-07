"""Pub/sub event bus for streaming and cross-component notifications.

Two interchangeable backends:

* ``InMemoryEventBus`` — asyncio queues, single process (dev / tests /
  single-node deployments with the embedded worker).
* ``RedisEventBus``    — Redis pub/sub so the API process and separate
  worker processes share events (production).

Events are ephemeral by design; durable state always lives in PostgreSQL /
SQLite.  Consumers that need guaranteed delivery read state from the
database (SSE endpoints replay a snapshot before streaming live events).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from app.core.config import Settings
from app.core.utils import utcnow

logger = structlog.stdlib.get_logger("app.events")


@dataclass(frozen=True)
class Event:
    topic: str
    type: str
    data: dict[str, Any]
    timestamp: datetime = field(default_factory=utcnow)

    def sse_encode(self) -> str:
        payload = {"type": self.type, "data": self.data, "timestamp": self.timestamp.isoformat()}
        return f"event: {self.type}\ndata: {json.dumps(payload, default=str)}\n\n"

    def ws_encode(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }


class Subscription:
    """One consumer's queue; bounded to avoid unbounded memory growth."""

    def __init__(self, topics: list[str], maxsize: int = 512) -> None:
        self.topics = set(topics)
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0
        self.closed = False

    def matches(self, topic: str) -> bool:
        return topic in self.topics

    def offer(self, event: Event) -> bool:
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False


class InMemoryEventBus:
    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []

    async def publish(self, topic: str, type_: str, data: dict[str, Any] | None = None) -> None:
        event = Event(topic=topic, type=type_, data=data or {})
        for sub in list(self._subscriptions):
            if sub.matches(topic) and not sub.closed:
                sub.offer(event)

    @contextlib.asynccontextmanager
    async def subscribe(self, topics: list[str]) -> AsyncIterator[Subscription]:
        sub = Subscription(topics)
        self._subscriptions.append(sub)
        try:
            yield sub
        finally:
            sub.closed = True
            with contextlib.suppress(ValueError):
                self._subscriptions.remove(sub)

    async def close(self) -> None:
        for sub in list(self._subscriptions):
            sub.closed = True


class RedisEventBus:
    """Redis pub/sub backed bus.

    Publishing is fire-and-forget with local subscriber fan-out; a
    background listener task pumps Redis messages into local subscriptions.
    If Redis is unavailable the bus logs and continues (events are not the
    source of truth).
    """

    def __init__(self, client: Any, *, channel_prefix: str = "agp:events") -> None:
        self._client = client
        self._prefix = channel_prefix
        self._local = InMemoryEventBus()
        self._listener_task: asyncio.Task | None = None
        self._pubsub: Any = None

    async def start(self) -> None:
        try:
            self._pubsub = self._client.pubsub()
            await self._pubsub.psubscribe(f"{self._prefix}:*")
            self._listener_task = asyncio.create_task(
                self._listen(), name="redis-bus-listener"
            )
        except Exception as exc:
            logger.warning("redis_event_bus_start_failed", error=str(exc))
            self._pubsub = None

    async def _listen(self) -> None:
        try:
            assert self._pubsub is not None
            async for message in self._pubsub.listen():
                if message.get("type") not in ("pmessage", "message"):
                    continue
                try:
                    payload = json.loads(message["data"])
                    event = Event(
                        topic=payload["topic"],
                        type_=payload["type"],
                        data=payload.get("data", {}),
                        timestamp=datetime.fromisoformat(payload["timestamp"]),
                    )
                except Exception:
                    continue
                for sub in list(self._local._subscriptions):
                    if sub.matches(event.topic) and not sub.closed:
                        sub.offer(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - redis dropped
            logger.warning("redis_event_bus_listener_stopped", error=str(exc))

    async def publish(self, topic: str, type_: str, data: dict[str, Any] | None = None) -> None:
        event = Event(topic=topic, type=type_, data=data or {})
        payload = json.dumps(
            {"topic": topic, "type": type_, "data": data or {}, "timestamp": event.timestamp.isoformat()}
        )
        try:
            await self._client.publish(f"{self._prefix}:{topic}", payload)
        except Exception as exc:
            logger.warning("redis_event_bus_publish_failed", error=str(exc))
        # local subscribers in this process still get it
        for sub in list(self._local._subscriptions):
            if sub.matches(topic) and not sub.closed:
                sub.offer(event)

    @contextlib.asynccontextmanager
    async def subscribe(self, topics: list[str]) -> AsyncIterator[Subscription]:
        async with self._local.subscribe(topics) as sub:
            yield sub

    async def close(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
        if self._pubsub:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
        await self._local.close()


class EventBus:
    """Facade selecting the backend once per process."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._backend: InMemoryEventBus | RedisEventBus | None = None

    def _ensure(self) -> InMemoryEventBus | RedisEventBus:
        if self._backend is None:
            if self.settings.redis_url:
                try:
                    import redis.asyncio as aioredis  # type: ignore

                    client = aioredis.from_url(
                        self.settings.redis_url, decode_responses=True
                    )
                    self._backend = RedisEventBus(client)
                except Exception:  # pragma: no cover
                    logger.warning("redis_bus_unavailable_using_memory")
                    self._backend = InMemoryEventBus()
            else:
                self._backend = InMemoryEventBus()
        return self._backend

    async def start(self) -> None:
        backend = self._ensure()
        if isinstance(backend, RedisEventBus):
            await backend.start()

    async def publish(self, topic: str, type_: str, data: dict[str, Any] | None = None) -> None:
        await self._ensure().publish(topic, type_, data)

    @contextlib.asynccontextmanager
    async def subscribe(self, topics: list[str]) -> AsyncIterator[Subscription]:
        async with self._ensure().subscribe(topics) as sub:
            yield sub

    async def close(self) -> None:
        if self._backend is not None:
            await self._backend.close()
            self._backend = None
