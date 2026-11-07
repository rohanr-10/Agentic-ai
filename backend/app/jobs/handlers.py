"""Background job handlers.

Handlers are async callables registered by ``kind``.  They must be
idempotent: the queue guarantees at-most-one active run, but a worker crash
after partial work causes a re-run after the lease expires.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import UUID

import structlog

from app.core.config import Settings
from app.core.errors import JobError
from app.db.session import Database
from app.events.bus import EventBus
from app.jobs.queue import (
    KIND_CLEANUP,
    KIND_FILE_EXTRACT,
    KIND_NOTIFY,
    KIND_REAP,
    KIND_TASK_EXECUTE,
    KIND_TASK_RECOVER,
)

logger = structlog.stdlib.get_logger("app.jobs.handlers")

Handler = Callable[["HandlerContext", dict[str, Any]], Awaitable[dict[str, Any] | None]]


@dataclass
class HandlerContext:
    """Per-run context handed to every handler."""

    database: Database
    settings: Settings
    bus: EventBus
    worker_id: str = "embedded"
    services: dict[str, Any] = field(default_factory=dict)


_HANDLERS: dict[str, Handler] = {}
_TIMEOUTS: dict[str, float | None] = {}


def register(kind: str, *, timeout_s: float | None = None) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        _TIMEOUTS[kind] = timeout_s
        return fn

    return decorator


def registered_kinds() -> list[str]:
    return sorted(_HANDLERS.keys())


async def run_handler(ctx: HandlerContext, kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise JobError(f"no handler registered for job kind '{kind}'")
    timeout = _TIMEOUTS.get(kind)
    if timeout is None:
        return await handler(ctx, payload)
    try:
        return await asyncio.wait_for(handler(ctx, payload), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise JobError(f"job kind '{kind}' exceeded {timeout}s") from exc


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------

@register(KIND_NOTIFY)
async def _notify(ctx: HandlerContext, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Persist a notification and push it on the user's event topic."""
    from app.db.models import Notification
    from app.db.enums import NotificationKind

    user_id = UUID(payload["user_id"])
    kind = NotificationKind(payload["kind"])
    async with ctx.database.begin() as session:
        notification = Notification(
            user_id=user_id,
            kind=kind,
            payload=payload.get("payload", {}),
        )
        session.add(notification)
        await session.flush()
        notification_id = notification.id
    await ctx.bus.publish(
        f"user:{user_id}",
        "notification",
        {"id": str(notification_id), "kind": kind.value, **payload.get("payload", {})},
    )
    return {"notification_id": str(notification_id)}


@register(KIND_TASK_EXECUTE)
async def _task_execute(ctx: HandlerContext, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Run the orchestrator for one task (resumable via checkpoints)."""
    from app.orchestration.executor import TaskExecutor

    task_id = UUID(payload["task_id"])
    executor = TaskExecutor(ctx.database, ctx.settings, ctx.bus)
    outcome = await executor.run(task_id, worker_id=ctx.worker_id)
    return {"status": outcome.status, "detail": outcome.detail}


@register(KIND_TASK_RECOVER)
async def _task_recover(ctx: HandlerContext, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Scan for stale RUNNING/RETRYING tasks and requeue them."""
    from app.orchestration.recovery import recover_stale_tasks

    recovered = await recover_stale_tasks(ctx.database, ctx.settings, ctx.bus)
    return {"recovered": recovered}


@register(KIND_FILE_EXTRACT)
async def _file_extract(ctx: HandlerContext, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract text from an uploaded file (idempotent by file id)."""
    from app.services.files import process_file_extraction

    file_id = UUID(payload["file_id"])
    async with ctx.database.session() as session:
        result = await process_file_extraction(session, ctx.settings, file_id)
        await session.commit()
    return result


@register(KIND_CLEANUP)
async def _cleanup(ctx: HandlerContext, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Retention: expired sessions/tokens/memories, old audit events."""
    from app.services.maintenance import run_maintenance

    return await run_maintenance(ctx.database, ctx.settings)


@register(KIND_REAP)
async def _reap(ctx: HandlerContext, payload: dict[str, Any]) -> dict[str, Any] | None:
    requeued = await JobQueue(ctx.database).requeue_stale(ctx.settings.worker_lease_s)
    return {"requeued": requeued}


async def notify_user(
    ctx: HandlerContext, user_id: UUID, kind: str, payload: dict[str, Any], *, dedupe_key: str
) -> None:
    """Helper used by services to enqueue a durable notification job.

    ``dedupe_key`` makes the notification idempotent (e.g. derived from the
    task id and its terminal status)."""
    from app.jobs.queue import JobQueue

    async with ctx.database.session() as session:
        queue = JobQueue(ctx.database)
        await queue.enqueue(
            session,
            kind=KIND_NOTIFY,
            payload={"user_id": str(user_id), "kind": kind, "payload": payload},
            idempotency_key=f"notify:{kind}:{user_id}:{dedupe_key}",
        )
        await session.commit()
