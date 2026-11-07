"""Admin endpoints: job inspection, audit trail, platform statistics."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.api.deps import AdminDep, DbDep, QueueDep
from app.db.enums import JobStatus
from app.db.models import BackgroundJob
from app.schemas.common import Page
from app.services.audit import list_events

router = APIRouter(prefix="/admin", tags=["admin"])


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    status: JobStatus
    priority: int
    attempts: int
    max_attempts: int
    run_at: datetime
    locked_by: str | None
    last_error: str | None
    idempotency_key: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    event_type: str
    resource_type: str | None
    resource_id: str | None
    severity: str
    ip: str | None
    details: dict[str, Any]
    created_at: datetime


class StatsOut(BaseModel):
    users: int
    conversations: int
    messages: int
    tasks_by_status: dict[str, int]
    jobs_by_status: dict[str, int]
    files: int
    memory_records: int


@router.get("/jobs", response_model=Page[JobOut], summary="Inspect background jobs")
async def list_jobs(
    admin: AdminDep,
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: JobStatus | None = Query(default=None, alias="status"),
    kind: str | None = Query(default=None),
):
    query = sa.select(BackgroundJob)
    count_query = sa.select(sa.func.count()).select_from(BackgroundJob)
    if status_filter is not None:
        query = query.where(BackgroundJob.status == status_filter)
        count_query = count_query.where(BackgroundJob.status == status_filter)
    if kind:
        query = query.where(BackgroundJob.kind == kind)
        count_query = count_query.where(BackgroundJob.kind == kind)
    total = (await db.execute(count_query)).scalar_one()
    rows = (
        (
            await db.execute(
                query.order_by(BackgroundJob.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return Page(items=[JobOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.post("/jobs/{job_id}/requeue", response_model=JobOut, summary="Requeue a dead job")
async def requeue_job(job_id: UUID, admin: AdminDep, db: DbDep, queue: QueueDep):
    from app.core.errors import ConflictError, NotFoundError
    from app.core.utils import utcnow

    job = await db.get(BackgroundJob, job_id)
    if job is None:
        raise NotFoundError("job not found")
    if job.status not in (JobStatus.DEAD, JobStatus.FAILED):
        raise ConflictError(f"cannot requeue a job in state {job.status.value}")
    job.status = JobStatus.QUEUED
    job.run_at = utcnow()
    job.attempts = 0
    job.last_error = None
    job.locked_by = None
    job.locked_at = None
    await db.commit()
    await db.refresh(job)
    return JobOut.model_validate(job)


@router.get("/audit", response_model=Page[AuditEventOut], summary="Query the audit trail")
async def audit_events(
    admin: AdminDep,
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = Query(default=None),
    user_id: UUID | None = Query(default=None),
):
    rows, total = await list_events(
        db,
        user_id=user_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[AuditEventOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=StatsOut, summary="Platform statistics")
async def stats(admin: AdminDep, db: DbDep):
    from app.db.models import Conversation, FileRecord, MemoryRecord, Message, Task, User

    async def count(model, *conditions):
        stmt = sa.select(sa.func.count()).select_from(model)
        if conditions:
            stmt = stmt.where(*conditions)
        return (await db.execute(stmt)).scalar_one() or 0

    task_rows = (
        await db.execute(sa.select(Task.status, sa.func.count()).group_by(Task.status))
    ).all()
    return StatsOut(
        users=await count(User),
        conversations=await count(Conversation),
        messages=await count(Message),
        tasks_by_status={status.value: n for status, n in task_rows},
        jobs_by_status=await _queue_stats(db),
        files=await count(FileRecord),
        memory_records=await count(MemoryRecord, MemoryRecord.deleted_at.is_(None)),
    )


async def _queue_stats(db) -> dict[str, int]:
    rows = (
        await db.execute(
            sa.select(BackgroundJob.status, sa.func.count()).group_by(BackgroundJob.status)
        )
    ).all()
    return {status.value: n for status, n in rows}
