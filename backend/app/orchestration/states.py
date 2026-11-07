"""Task lifecycle state machine.

Explicit, validated transitions with terminal-state protection.  All
mutations go through :func:`transition`, which is concurrency-safe via
compare-and-set on the task's ``version`` column (portable across SQLite
and PostgreSQL — no ``SELECT .. FOR UPDATE`` required).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import telemetry
from app.core.errors import ConflictError, NotFoundError
from app.core.utils import utcnow
from app.db.enums import TaskStatus
from app.db.enums import TERMINAL_TASK_STATUSES
from app.db.models import Task
from app.events.bus import EventBus
from app.services import audit

logger = structlog.stdlib.get_logger("app.orchestration")

TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.QUEUED, TaskStatus.CANCELLED},
    TaskStatus.QUEUED: {
        TaskStatus.PLANNING,
        TaskStatus.READY,       # plan already approved / auto-approval
        TaskStatus.PAUSED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.PLANNING: {
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.READY,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.AWAITING_APPROVAL: {
        TaskStatus.READY,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.READY: {TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.PAUSED,
        TaskStatus.VERIFYING,
        TaskStatus.RETRYING,
        TaskStatus.AWAITING_APPROVAL,  # sensitive step gate
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.PAUSED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RETRYING: {
        TaskStatus.RUNNING,
        TaskStatus.READY,
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.VERIFYING: {
        TaskStatus.COMPLETED,
        TaskStatus.RETRYING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


def can_transition(source: TaskStatus, target: TaskStatus) -> bool:
    if source in TERMINAL_TASK_STATUSES:
        return False
    return target in TRANSITIONS.get(source, set())


def assert_transition(source: TaskStatus, target: TaskStatus) -> None:
    from app.core.errors import InvalidTransitionError

    if not can_transition(source, target):
        raise InvalidTransitionError(
            f"illegal task transition {source.value} -> {target.value}",
            details={"from": source.value, "to": target.value},
        )


async def transition(
    session: AsyncSession,
    bus: EventBus | None,
    *,
    task_id: UUID,
    to_status: TaskStatus,
    extra_updates: dict[str, Any] | None = None,
    user_id: UUID | None = None,
    reason: str | None = None,
) -> Task:
    """Concurrency-safe lifecycle transition (CAS on ``version``)."""
    for _attempt in range(3):
        task = await session.get(Task, task_id)
        if task is None:
            raise NotFoundError("task not found")
        source = task.status
        assert_transition(source, to_status)

        values: dict[str, Any] = {
            "status": to_status,
            "version": Task.version + 1,
            "updated_at": utcnow(),
        }
        if to_status == TaskStatus.RUNNING and task.started_at is None:
            values["started_at"] = utcnow()
        if to_status in TERMINAL_TASK_STATUSES:
            values["finished_at"] = utcnow()
        if extra_updates:
            values.update(extra_updates)

        result = await session.execute(
            sa.update(Task)
            .where(Task.id == task_id, Task.version == task.version)
            .values(**values)
        )
        if result.rowcount == 1:
            await audit.record(
                session,
                event_type=audit.TASK_TRANSITION,
                user_id=user_id or task.user_id,
                resource_type="task",
                resource_id=task_id,
                details={
                    "from": source.value,
                    "to": to_status.value,
                    "reason": reason,
                },
            )
            await session.commit()
            await session.refresh(task)
            telemetry.record_task_transition(to_state=to_status.value)
            if bus is not None:
                await bus.publish(
                    f"task:{task_id}",
                    "task.status",
                    {
                        "task_id": str(task_id),
                        "status": to_status.value,
                        "version": task.version,
                    },
                )
                await bus.publish(
                    f"user:{task.user_id}",
                    "task.status",
                    {
                        "task_id": str(task_id),
                        "status": to_status.value,
                    },
                )
            logger.info(
                "task_transition",
                task_id=str(task_id),
                source=source.value,
                target=to_status.value,
                version=task.version,
            )
            return task
        # CAS conflict: reload and retry
        await session.rollback()
    raise ConflictError(
        "concurrent task modification detected; retry",
        details={"task_id": str(task_id)},
    )


async def set_flags(
    session: AsyncSession,
    *,
    task_id: UUID,
    cancel_requested: bool | None = None,
    pause_requested: bool | None = None,
) -> None:
    values: dict[str, Any] = {"updated_at": utcnow()}
    if cancel_requested is not None:
        values["cancel_requested"] = cancel_requested
    if pause_requested is not None:
        values["pause_requested"] = pause_requested
    await session.execute(sa.update(Task).where(Task.id == task_id).values(**values))
    await session.commit()
