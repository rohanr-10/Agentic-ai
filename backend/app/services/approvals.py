"""Approval workflow service (human-in-the-loop)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.utils import utcnow
from app.db.enums import ApprovalKind, ApprovalStatus, TaskStatus
from app.db.models import ApprovalRequest, Task
from app.events.bus import EventBus
from app.jobs.queue import JobQueue, KIND_TASK_EXECUTE
from app.orchestration.states import can_transition, transition
from app.services import audit


async def list_approvals(
    session: AsyncSession,
    *,
    user_id: UUID,
    status: ApprovalStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ApprovalRequest], int]:
    query = sa.select(ApprovalRequest).where(ApprovalRequest.user_id == user_id)
    count_query = (
        sa.select(sa.func.count()).select_from(ApprovalRequest).where(ApprovalRequest.user_id == user_id)
    )
    if status is not None:
        query = query.where(ApprovalRequest.status == status)
        count_query = count_query.where(ApprovalRequest.status == status)
    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(ApprovalRequest.requested_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_approval(
    session: AsyncSession, *, user_id: UUID, approval_id: UUID
) -> ApprovalRequest:
    approval = await session.get(ApprovalRequest, approval_id)
    if approval is None or approval.user_id != user_id:
        raise NotFoundError("approval request not found")
    return approval


async def decide_approval(
    session: AsyncSession,
    bus: EventBus,
    *,
    user_id: UUID,
    approval_id: UUID,
    decision: str,
    reason: str | None,
) -> ApprovalRequest:
    """Record the decision and re-drive the task through the orchestrator."""
    approval = await get_approval(session, user_id=user_id, approval_id=approval_id)
    if approval.status != ApprovalStatus.PENDING:
        raise ConflictError(f"approval already {approval.status.value}")

    approved = decision == "approve"
    approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    approval.decided_at = utcnow()
    approval.decided_by = user_id
    approval.reason = reason

    task = await session.get(Task, approval.task_id)
    if task is None:
        raise NotFoundError("task for approval no longer exists")

    if approved:
        if approval.kind == ApprovalKind.STEP and approval.step_id:
            checkpoint = dict(task.checkpoint or {})
            approved_steps = list(checkpoint.get("approved_steps", []))
            if approval.step_id not in approved_steps:
                approved_steps.append(approval.step_id)
            checkpoint["approved_steps"] = approved_steps
            task.checkpoint = checkpoint
        await JobQueue().enqueue(
            session,
            kind=KIND_TASK_EXECUTE,
            payload={"task_id": str(task.id)},
            idempotency_key=f"task-execute:{task.id}:approval:{approval.id}",
        )
    else:
        if can_transition(task.status, TaskStatus.CANCELLED):
            await transition(
                session,
                bus,
                task_id=task.id,
                to_status=TaskStatus.CANCELLED,
                reason="approval rejected",
                extra_updates={
                    "error": {
                        "code": "approval_rejected",
                        "message": reason or "approval rejected by user",
                    }
                },
            )

    await audit.record(
        session,
        event_type=audit.APPROVAL_DECIDED,
        user_id=user_id,
        resource_type="approval",
        resource_id=approval.id,
        details={"decision": decision, "task_id": str(task.id), "reason": reason},
    )
    await session.commit()
    await session.refresh(approval)
    return approval
