"""Task endpoints: lifecycle, control, executions, SSE progress stream."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import (
    BusDep,
    DbDep,
    PrincipalDep,
    RegistryDep,
    SettingsDep,
    get_database,
    rate_limit,
)
from app.db.enums import TaskStatus
from app.orchestration.recovery import task_snapshot
from app.schemas.common import Page
from app.schemas.tasks import (
    TaskCreateIn,
    TaskDetailOut,
    TaskExecutionOut,
    TaskNoteIn,
    TaskOut,
)
from app.services import tasks as task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _task_out(row) -> TaskOut:
    return TaskOut(
        id=row.id,
        title=row.title,
        objective=row.objective,
        status=row.status,
        mode=row.mode,
        conversation_id=row.conversation_id,
        priority=row.priority,
        version=row.version,
        plan_version=row.plan_version,
        usage=dict(row.usage or {}),
        budgets=dict(row.budgets or {}),
        cancel_requested=row.cancel_requested,
        pause_requested=row.pause_requested,
        deadline_at=row.deadline_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        result_summary=row.result_summary,
        artifacts=row.artifacts,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _task_detail(row) -> TaskDetailOut:
    return TaskDetailOut(
        **_task_out(row).model_dump(),
        plan=row.plan,
        checkpoint=dict(row.checkpoint or {}),
        additional_context=list(row.additional_context or []),
    )


@router.post(
    "",
    response_model=TaskDetailOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("task-create"))],
    summary="Create an autonomous task (optionally with a client plan)",
)
async def create_task(
    payload: TaskCreateIn,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    registry: RegistryDep,
):
    row = await task_service.create_task(
        db, settings, registry, user_id=principal.user_id, payload=payload
    )
    return _task_detail(row)


@router.get("", response_model=Page[TaskOut], summary="List tasks")
async def list_tasks(
    principal: PrincipalDep,
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    conversation_id: UUID | None = Query(default=None),
):
    rows, total = await task_service.list_tasks(
        db,
        user_id=principal.user_id,
        limit=limit,
        offset=offset,
        status=status_filter,
        conversation_id=conversation_id,
    )
    return Page(items=[_task_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{task_id}", response_model=TaskDetailOut, summary="Get task details")
async def get_task(task_id: UUID, principal: PrincipalDep, db: DbDep):
    row = await task_service.get_task(db, user_id=principal.user_id, task_id=task_id)
    return _task_detail(row)


@router.delete("/{task_id}", status_code=204, summary="Delete a finished task")
async def delete_task(task_id: UUID, principal: PrincipalDep, db: DbDep):
    await task_service.delete_task(db, user_id=principal.user_id, task_id=task_id)


@router.post("/{task_id}/pause", response_model=TaskOut, summary="Pause a task")
async def pause_task(task_id: UUID, principal: PrincipalDep, db: DbDep, bus: BusDep):
    row = await task_service.pause_task(
        db, bus, user_id=principal.user_id, task_id=task_id
    )
    return _task_out(row)


@router.post("/{task_id}/resume", response_model=TaskOut, summary="Resume a paused task")
async def resume_task(
    task_id: UUID, principal: PrincipalDep, db: DbDep, settings: SettingsDep, bus: BusDep
):
    row = await task_service.resume_task(
        db, settings, bus, user_id=principal.user_id, task_id=task_id
    )
    return _task_out(row)


@router.post(
    "/{task_id}/cancel",
    response_model=TaskOut,
    summary="Cancel a task (propagates into running sandbox processes)",
)
async def cancel_task(task_id: UUID, principal: PrincipalDep, db: DbDep, bus: BusDep):
    row = await task_service.cancel_task(
        db, bus, user_id=principal.user_id, task_id=task_id
    )
    return _task_out(row)


@router.post(
    "/{task_id}/notes",
    response_model=TaskOut,
    summary="Add instructions/context to a task (hybrid mode)",
)
async def add_note(
    task_id: UUID, payload: TaskNoteIn, principal: PrincipalDep, db: DbDep, bus: BusDep
):
    row = await task_service.add_note(
        db,
        bus,
        user_id=principal.user_id,
        task_id=task_id,
        content=payload.content,
        request_replan=payload.request_replan,
    )
    return _task_out(row)


@router.get(
    "/{task_id}/executions",
    response_model=list[TaskExecutionOut],
    summary="Execution history (per step attempt)",
)
async def list_executions(
    task_id: UUID,
    principal: PrincipalDep,
    db: DbDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    rows = await task_service.list_executions(
        db, user_id=principal.user_id, task_id=task_id, limit=limit, offset=offset
    )
    return [TaskExecutionOut.model_validate(r) for r in rows]


@router.get(
    "/{task_id}/events",
    summary="Stream task progress (SSE; snapshot replay + live events)",
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def task_events(
    task_id: UUID,
    request: Request,
    token: str = Query(description="Access token (EventSource cannot set headers)"),
):
    from app.api.deps import principal_from_token
    from app.core.errors import AuthenticationError, NotFoundError

    try:
        principal = await principal_from_token(request.app, token)
    except AuthenticationError:
        raise NotFoundError("task not found")

    database = get_database(request)
    bus = request.app.state.bus

    async def verify() -> bool:
        async with database.session() as session:
            try:
                await task_service.get_task(
                    session, user_id=principal.user_id, task_id=task_id
                )
                return True
            except NotFoundError:
                return False

    if not await verify():
        raise NotFoundError("task not found")

    async def event_stream():
        snapshot = await task_snapshot(database, task_id)
        if snapshot is not None:
            yield f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"
        terminal = {"completed", "failed", "cancelled"}
        if snapshot and snapshot["status"] in terminal:
            yield f"event: done\ndata: {json.dumps(snapshot, default=str)}\n\n"
            return
        async with bus.subscribe([f"task:{task_id}"]) as subscription:
            while True:
                try:
                    event = await asyncio.wait_for(subscription.queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                payload = json.dumps(event.ws_encode(), default=str)
                yield f"event: {event.type}\ndata: {payload}\n\n"
                if (
                    event.type == "task.status"
                    and event.data.get("status") in terminal
                ):
                    yield "event: done\ndata: {}\n\n"
                    return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
