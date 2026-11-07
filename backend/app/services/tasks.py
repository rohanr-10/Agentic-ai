"""Task lifecycle service (API-facing operations)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, RequestValidationError
from app.core.utils import new_uuid, utcnow
from app.db.enums import ExecutionStatus, TaskStatus, TERMINAL_TASK_STATUSES
from app.db.models import Task, TaskExecution, TaskPlan
from app.events.bus import EventBus
from app.jobs.queue import JobQueue, KIND_TASK_EXECUTE
from app.orchestration import executor as executor_module
from app.orchestration.planner import parse_client_plan, validate_plan
from app.orchestration.states import can_transition, set_flags, transition
from app.services import audit
from app.tools.registry import ToolRegistry

logger = structlog.stdlib.get_logger("app.tasks")

_MAX_NOTES = 50


async def get_task(session: AsyncSession, *, user_id: UUID, task_id: UUID) -> Task:
    task = await session.get(Task, task_id)
    if task is None or task.user_id != user_id:
        raise NotFoundError("task not found")
    return task


async def create_task(
    session: AsyncSession,
    settings: Settings,
    registry: ToolRegistry,
    *,
    user_id: UUID,
    payload,
) -> Task:
    plan = None
    if payload.plan is not None:
        plan = parse_client_plan(payload.plan)
        problems = validate_plan(
            plan, registry=registry, max_steps=settings.task_max_steps
        )
        if problems:
            raise RequestValidationError(
                "invalid plan", details={"problems": problems}
            )

    budgets: dict[str, Any] = {
        "max_steps": payload.budgets.max_steps or settings.task_max_steps,
        "max_tool_calls": payload.budgets.max_tool_calls or 200,
        "deadline_s": payload.budgets.deadline_s or settings.task_default_deadline_s,
        "max_plan_revisions": payload.budgets.max_plan_revisions
        or settings.task_max_plan_revisions,
    }

    if payload.conversation_id is not None:
        from app.services.conversations import get_conversation

        conversation = await get_conversation(
            session, user_id=user_id, conversation_id=payload.conversation_id
        )
        if conversation.mode.value not in ("hybrid", "autonomous"):
            raise RequestValidationError(
                "tasks can only attach to hybrid or autonomous conversations"
            )

    task = Task(
        id=new_uuid(),
        user_id=user_id,
        conversation_id=payload.conversation_id,
        title=payload.title.strip(),
        objective=payload.objective,
        mode=payload.mode,
        priority=payload.priority,
        status=TaskStatus.CREATED,
        budgets=budgets,
        deadline_at=utcnow() + timedelta(seconds=budgets["deadline_s"]),
        plan=plan.model_dump() if plan else None,
        plan_version=1 if plan else 0,
    )
    session.add(task)
    await session.flush()
    if plan is not None:
        session.add(
            TaskPlan(
                id=new_uuid(),
                task_id=task.id,
                version=1,
                steps=plan.model_dump()["steps"],
                objective_restated=plan.objective_restated,
                source="client",
            )
        )
    await audit.record(
        session,
        event_type=audit.TASK_CREATED,
        user_id=user_id,
        resource_type="task",
        resource_id=task.id,
        details={"mode": payload.mode.value, "has_client_plan": plan is not None},
    )
    if payload.auto_start:
        task = await transition(
            session, None, task_id=task.id, to_status=TaskStatus.QUEUED
        )
        await JobQueue().enqueue(
            session,
            kind=KIND_TASK_EXECUTE,
            payload={"task_id": str(task.id)},
            idempotency_key=f"task-execute:{task.id}:v{task.version}",
        )
    await session.commit()
    await session.refresh(task)
    return task


async def list_tasks(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 50,
    offset: int = 0,
    status: TaskStatus | None = None,
    conversation_id: UUID | None = None,
) -> tuple[list[Task], int]:
    query = sa.select(Task).where(Task.user_id == user_id)
    count_query = sa.select(sa.func.count()).select_from(Task).where(Task.user_id == user_id)
    if status is not None:
        query = query.where(Task.status == status)
        count_query = count_query.where(Task.status == status)
    if conversation_id is not None:
        query = query.where(Task.conversation_id == conversation_id)
        count_query = count_query.where(Task.conversation_id == conversation_id)
    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(Task.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def pause_task(
    session: AsyncSession, bus: EventBus, *, user_id: UUID, task_id: UUID
) -> Task:
    task = await get_task(session, user_id=user_id, task_id=task_id)
    if task.status in TERMINAL_TASK_STATUSES:
        raise ConflictError(f"task is already {task.status.value}")
    if task.status == TaskStatus.PAUSED:
        return task
    await set_flags(session, task_id=task_id, pause_requested=True)
    if can_transition(task.status, TaskStatus.PAUSED) and task.status not in (
        TaskStatus.RUNNING,
        TaskStatus.RETRYING,
        TaskStatus.VERIFYING,
    ):
        task = await transition(
            session, bus, task_id=task_id, to_status=TaskStatus.PAUSED, reason="user pause"
        )
    else:
        await session.refresh(task)
    await bus.publish(
        f"task:{task_id}",
        "task.control",
        {"task_id": str(task_id), "action": "pause"},
    )
    await audit.record(
        session,
        event_type=audit.TASK_PAUSED,
        user_id=user_id,
        resource_type="task",
        resource_id=task_id,
    )
    await session.commit()
    return task


async def resume_task(
    session: AsyncSession,
    settings: Settings,
    bus: EventBus,
    *,
    user_id: UUID,
    task_id: UUID,
) -> Task:
    task = await get_task(session, user_id=user_id, task_id=task_id)
    if task.status != TaskStatus.PAUSED:
        raise ConflictError(f"cannot resume a task in state {task.status.value}")
    await set_flags(session, task_id=task_id, pause_requested=False)
    await JobQueue().enqueue(
        session,
        kind=KIND_TASK_EXECUTE,
        payload={"task_id": str(task_id)},
        idempotency_key=f"task-execute:{task_id}:resume:v{task.version}",
    )
    await audit.record(
        session,
        event_type=audit.TASK_RESUMED,
        user_id=user_id,
        resource_type="task",
        resource_id=task_id,
    )
    await session.commit()
    await session.refresh(task)
    await bus.publish(
        f"task:{task_id}",
        "task.control",
        {"task_id": str(task_id), "action": "resume"},
    )
    return task


async def cancel_task(
    session: AsyncSession, bus: EventBus, *, user_id: UUID, task_id: UUID, reason: str | None = None
) -> Task:
    task = await get_task(session, user_id=user_id, task_id=task_id)
    if task.status in TERMINAL_TASK_STATUSES:
        return task  # idempotent
    await set_flags(session, task_id=task_id, cancel_requested=True)
    # kill an in-process sandbox immediately (embedded worker)
    executor_module.cancel_active_control(task_id, reason or "cancelled by user")
    await bus.publish(
        f"task:{task_id}",
        "task.control",
        {"task_id": str(task_id), "action": "cancel"},
    )
    if task.status not in (TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.VERIFYING):
        # no worker involved (or between jobs): finish it here
        await session.execute(
            sa.update(TaskExecution)
            .where(
                TaskExecution.task_id == task_id,
                TaskExecution.status == ExecutionStatus.RUNNING,
            )
            .values(status=ExecutionStatus.CANCELLED, finished_at=utcnow())
        )
        if can_transition(task.status, TaskStatus.CANCELLED):
            task = await transition(
                session,
                bus,
                task_id=task_id,
                to_status=TaskStatus.CANCELLED,
                reason=reason or "cancelled by user",
                extra_updates={
                    "error": {"code": "cancelled", "message": reason or "cancelled by user"}
                },
            )
    else:
        await session.refresh(task)
    await audit.record(
        session,
        event_type=audit.TASK_CANCELLED,
        user_id=user_id,
        resource_type="task",
        resource_id=task_id,
        details={"reason": reason},
    )
    await session.commit()
    return task


async def delete_task(session: AsyncSession, *, user_id: UUID, task_id: UUID) -> None:
    task = await get_task(session, user_id=user_id, task_id=task_id)
    if task.status not in TERMINAL_TASK_STATUSES:
        raise ConflictError("cancel the task before deleting it")
    await session.delete(task)
    await session.commit()


async def add_note(
    session: AsyncSession, bus: EventBus, *, user_id: UUID, task_id: UUID, content: str, request_replan: bool
) -> Task:
    """Hybrid mode: feed new instructions into a running/paused task."""
    task = await get_task(session, user_id=user_id, task_id=task_id)
    if task.status in TERMINAL_TASK_STATUSES:
        raise ConflictError("task already finished")
    notes = list(task.additional_context or [])
    notes.append(
        {
            "content": content,
            "at": utcnow().isoformat(),
            "request_replan": request_replan,
        }
    )
    task.additional_context = notes[-_MAX_NOTES:]
    task.updated_at = utcnow()
    await session.commit()
    await session.refresh(task)
    await bus.publish(
        f"task:{task_id}",
        "task.note",
        {"task_id": str(task_id), "request_replan": request_replan},
    )
    return task


async def list_executions(
    session: AsyncSession, *, user_id: UUID, task_id: UUID, limit: int = 100, offset: int = 0
) -> list[TaskExecution]:
    await get_task(session, user_id=user_id, task_id=task_id)
    rows = (
        (
            await session.execute(
                sa.select(TaskExecution)
                .where(TaskExecution.task_id == task_id)
                .order_by(TaskExecution.created_at.asc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)
