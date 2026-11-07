"""Durable background-job queue backed by the primary database.

Guarantees:

* **Durability** — a job row is committed before any work happens; a queued
  job survives process crashes and server restarts.
* **At-most-one-active-run** — claiming is an atomic compare-and-set on
  ``status``; two workers can never run the same job simultaneously.
* **Crash safety** — workers heartbeat ``locked_at``; ``requeue_stale``
  returns jobs whose lease expired to the queue (handlers must therefore be
  idempotent — see docs/ARCHITECTURE.md).
* **Retry + dead-letter** — failures back off exponentially; after
  ``max_attempts`` the job moves to ``dead`` and surfaces in /admin/jobs.
* **Idempotent enqueue** — an ``idempotency_key`` makes duplicate enqueues
  of the same logical work collapse into one row.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import JobError
from app.core.utils import utcnow
from app.db.enums import JobStatus
from app.db.models import BackgroundJob
from app.db.session import Database

logger = structlog.stdlib.get_logger("app.jobs")

# Kinds registered by the platform (see app.jobs.handlers)
KIND_FILE_EXTRACT = "file.extract"
KIND_TASK_EXECUTE = "task.execute"
KIND_TASK_RECOVER = "task.recover"
KIND_NOTIFY = "notify.dispatch"
KIND_CLEANUP = "maintenance.cleanup"
KIND_REAP = "maintenance.reap"


class JobQueue:
    def __init__(self, database: Database | None = None) -> None:
        """``database`` may be omitted when only :meth:`enqueue` is used
        (it operates on the caller's session)."""
        self.database = database

    # -- enqueue ---------------------------------------------------------
    async def enqueue(
        self,
        session: AsyncSession,
        *,
        kind: str,
        payload: dict[str, Any] | None = None,
        run_at: datetime | None = None,
        priority: int = 5,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
    ) -> BackgroundJob:
        """Enqueue within the caller's transaction (atomic with the caller's
        own writes, e.g. "create task + queue its execution")."""
        if idempotency_key:
            existing = await session.scalar(
                sa.select(BackgroundJob).where(
                    BackgroundJob.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                return existing
        job = BackgroundJob(
            kind=kind,
            payload=payload or {},
            status=JobStatus.QUEUED,
            priority=priority,
            run_at=run_at or utcnow(),
            max_attempts=max_attempts or 5,
            idempotency_key=idempotency_key,
        )
        session.add(job)
        await session.flush()
        return job

    # -- claiming --------------------------------------------------------
    async def claim_next(self, worker_id: str) -> BackgroundJob | None:
        """Atomically claim the next runnable job (own transaction).

        Retries a few times when a racing worker wins the CAS on the same
        candidate — under contention this happens while jobs are still
        available, and returning None would needlessly idle a worker.
        """
        for _race_attempt in range(4):
            async with self.database.begin() as session:
                candidates = (
                    (
                        await session.execute(
                            sa.select(BackgroundJob.id)
                            .where(BackgroundJob.status == JobStatus.QUEUED)
                            .where(BackgroundJob.run_at <= utcnow())
                            .order_by(BackgroundJob.priority, BackgroundJob.run_at)
                            .limit(5)
                        )
                    )
                    .scalars()
                    .all()
                )
                for job_id in candidates:
                    result = await session.execute(
                        sa.update(BackgroundJob)
                        .where(
                            BackgroundJob.id == job_id,
                            BackgroundJob.status == JobStatus.QUEUED,
                        )
                        .values(
                            status=JobStatus.RUNNING,
                            locked_by=worker_id,
                            locked_at=utcnow(),
                            attempts=BackgroundJob.attempts + 1,
                        )
                    )
                    if result.rowcount == 1:
                        await session.commit()
                        job = await session.get(BackgroundJob, job_id)
                        assert job is not None
                        return job
            # all candidates lost the race: if the queue still has runnable
            # jobs the next iteration will see them
        return None

    # -- lifecycle -------------------------------------------------------
    async def complete(
        self, job_id: UUID, result: dict[str, Any] | None = None
    ) -> None:
        async with self.database.begin() as session:
            await session.execute(
                sa.update(BackgroundJob)
                .where(BackgroundJob.id == job_id)
                .values(
                    status=JobStatus.SUCCEEDED,
                    result=result,
                    finished_at=utcnow(),
                    locked_by=None,
                )
            )

    async def fail(
        self,
        job_id: UUID,
        *,
        error: str,
        backoff_base_s: float,
        max_attempts: int | None = None,
    ) -> JobStatus:
        """Record a failure: retry with exponential backoff or dead-letter."""
        async with self.database.begin() as session:
            job = await session.get(BackgroundJob, job_id)
            if job is None:
                raise JobError(f"job {job_id} not found")
            effective_max = max_attempts or job.max_attempts
            job.last_error = error[:4000]
            if job.attempts >= effective_max:
                job.status = JobStatus.DEAD
                job.finished_at = utcnow()
                job.locked_by = None
                status = JobStatus.DEAD
            else:
                backoff = backoff_base_s * (2 ** (job.attempts - 1))
                backoff *= 0.5 + random.random()  # full jitter
                job.status = JobStatus.QUEUED
                job.run_at = utcnow() + timedelta(seconds=max(0.0, backoff))
                job.locked_by = None
                job.locked_at = None
                status = JobStatus.QUEUED
            await session.commit()
            if status == JobStatus.DEAD:
                logger.error("background_job_dead_lettered", job_id=str(job_id), kind=job.kind, error=error[:200])
            else:
                logger.warning(
                    "background_job_retry_scheduled",
                    job_id=str(job_id),
                    kind=job.kind,
                    attempt=job.attempts,
                    run_at=job.run_at.isoformat(),
                )
            return status

    async def heartbeat(self, job_id: UUID, worker_id: str) -> None:
        async with self.database.begin() as session:
            await session.execute(
                sa.update(BackgroundJob)
                .where(
                    BackgroundJob.id == job_id,
                    BackgroundJob.status == JobStatus.RUNNING,
                    BackgroundJob.locked_by == worker_id,
                )
                .values(locked_at=utcnow())
            )

    # -- recovery --------------------------------------------------------
    async def requeue_stale(self, lease_s: int) -> int:
        """Return lease-expired running jobs to the queue (crash recovery)."""
        cutoff = utcnow() - timedelta(seconds=lease_s)
        async with self.database.begin() as session:
            result = await session.execute(
                sa.update(BackgroundJob)
                .where(
                    BackgroundJob.status == JobStatus.RUNNING,
                    BackgroundJob.locked_at < cutoff,
                )
                .values(
                    status=JobStatus.QUEUED,
                    locked_by=None,
                    locked_at=None,
                )
            )
            count = result.rowcount or 0
        if count:
            logger.warning("background_jobs_requeued_after_lease_expiry", count=count)
        return count

    async def cancel_queued(self, session: AsyncSession, *, idempotency_key: str) -> bool:
        result = await session.execute(
            sa.update(BackgroundJob)
            .where(
                BackgroundJob.idempotency_key == idempotency_key,
                BackgroundJob.status == JobStatus.QUEUED,
            )
            .values(status=JobStatus.CANCELLED, finished_at=utcnow())
        )
        return (result.rowcount or 0) > 0

    async def stats(self) -> dict[str, int]:
        async with self.database.session() as session:
            rows = (
                await session.execute(
                    sa.select(BackgroundJob.status, sa.func.count())
                    .group_by(BackgroundJob.status)
                )
            ).all()
            return {status.value: count for status, count in rows}
