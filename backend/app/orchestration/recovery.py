"""Recovery of interrupted tasks and expiry of stale approvals."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog

from app.core.utils import utcnow
from app.db.enums import ApprovalKind, ApprovalStatus, JobStatus, TaskStatus, TERMINAL_TASK_STATUSES
from app.db.models import ApprovalRequest, BackgroundJob, Task
from app.db.session import Database
from app.events.bus import EventBus
from app.jobs.queue import KIND_TASK_EXECUTE
from app.orchestration.states import transition

logger = structlog.stdlib.get_logger("app.recovery")


async def recover_stale_tasks(database: Database, settings, bus: EventBus) -> int:
    """Re-enqueue tasks whose worker died mid-execution.

    A task is stale when RUNNING/RETRYING/VERIFYING with no activity for
    ``task_stale_after_s`` AND no queued/running ``task.execute`` job for it.
    The executor resumes from the checkpoint, so no completed step re-runs.
    """
    cutoff = utcnow() - timedelta(seconds=settings.task_stale_after_s)
    async with database.session() as session:
        stale = (
            (
                await session.execute(
                    sa.select(Task.id).where(
                        Task.status.in_(
                            [TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.VERIFYING]
                        ),
                        Task.updated_at < cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )
        active_jobs = (
            (
                await session.execute(
                    sa.select(BackgroundJob).where(
                        BackgroundJob.kind == KIND_TASK_EXECUTE,
                        BackgroundJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                    )
                )
            )
            .scalars()
            .all()
        )
    active_task_ids = {
        UUID(str((job.payload or {}).get("task_id"))) for job in active_jobs
    }

    requeued = 0
    for task_id in stale:
        if task_id in active_task_ids:
            continue
        async with database.session() as session:
            task = await session.get(Task, task_id)
            if task is None or task.status in TERMINAL_TASK_STATUSES:
                continue
            from app.jobs.queue import JobQueue

            await JobQueue().enqueue(
                session,
                kind=KIND_TASK_EXECUTE,
                payload={"task_id": str(task_id)},
                idempotency_key=f"task-execute:{task_id}:recover:{task.version}",
                max_attempts=2,
            )
            await session.commit()
            requeued += 1
            logger.warning(
                "task_recovery_requeued",
                task_id=str(task_id),
                status=task.status.value,
            )
    return requeued


async def expire_stale_approvals(database: Database, bus: EventBus) -> int:
    """Expire pending approvals past their deadline; fail the task."""
    expired = 0
    now = utcnow()
    async with database.session() as session:
        rows = (
            (
                await session.execute(
                    sa.select(ApprovalRequest).where(
                        ApprovalRequest.status == ApprovalStatus.PENDING,
                        ApprovalRequest.expires_at < now,
                    )
                )
            )
            .scalars()
            .all()
        )
        for approval in rows:
            approval.status = ApprovalStatus.EXPIRED
            approval.decided_at = now
            expired += 1
            task = await session.get(Task, approval.task_id)
            if task is not None and task.status == TaskStatus.AWAITING_APPROVAL:
                await transition(
                    session,
                    bus,
                    task_id=task.id,
                    to_status=TaskStatus.FAILED,
                    reason="approval expired",
                    extra_updates={
                        "error": {"code": "approval_expired", "message": "approval request expired"}
                    },
                )
        if rows:
            await session.commit()
    return expired


async def task_snapshot(database: Database, task_id: UUID) -> dict[str, Any] | None:
    """Current task state for SSE replay before live streaming."""
    async with database.session() as session:
        task = await session.get(Task, task_id)
        if task is None:
            return None
        return {
            "task_id": str(task.id),
            "status": task.status.value,
            "version": task.version,
            "usage": task.usage or {},
            "result_summary": task.result_summary,
        }
