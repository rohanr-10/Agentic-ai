"""Unit tests: durable job queue (claim, retry, dead-letter, reaper)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.core.utils import utcnow
from app.db.enums import JobStatus
from app.db.models import BackgroundJob
from app.jobs.queue import JobQueue


@pytest.fixture
async def database(test_settings, migrated):
    from app.db.session import Database

    db = Database(test_settings)
    yield db
    await db.dispose()


@pytest.fixture
async def session(database):
    async with database.session() as s:
        yield s


class TestEnqueue:
    async def test_enqueue_within_caller_transaction(self, database, session):
        queue = JobQueue()
        job = await queue.enqueue(session, kind="test.job", payload={"x": 1})
        await session.commit()
        assert job.id is not None
        assert job.status == JobStatus.QUEUED

    async def test_idempotency_key_deduplicates(self, session):
        queue = JobQueue()
        first = await queue.enqueue(
            session, kind="test.job", payload={}, idempotency_key="same-key"
        )
        second = await queue.enqueue(
            session, kind="test.job", payload={}, idempotency_key="same-key"
        )
        assert first.id == second.id


class TestClaiming:
    async def test_claim_marks_running_and_increments_attempts(self, database, session):
        queue = JobQueue(database)
        await queue.enqueue(session, kind="test.job", payload={})
        await session.commit()

        job = await queue.claim_next("worker-a")
        assert job is not None
        assert job.status == JobStatus.RUNNING
        assert job.attempts == 1
        assert job.locked_by == "worker-a"

    async def test_only_one_worker_claims_a_job(self, database, session):
        queue = JobQueue(database)
        await queue.enqueue(session, kind="test.job", payload={})
        await session.commit()

        first = await queue.claim_next("worker-a")
        second = await queue.claim_next("worker-b")
        assert first is not None
        assert second is None

    async def test_future_jobs_are_not_claimed(self, database, session):
        queue = JobQueue(database)
        await queue.enqueue(
            session, kind="test.job", payload={}, run_at=utcnow() + timedelta(hours=1)
        )
        await session.commit()
        assert await queue.claim_next("worker-a") is None

    async def test_priority_order(self, database, session):
        queue = JobQueue(database)
        await queue.enqueue(session, kind="test.low", payload={}, priority=9)
        await queue.enqueue(session, kind="test.high", payload={}, priority=1)
        await session.commit()
        job = await queue.claim_next("worker-a")
        assert job is not None and job.kind == "test.high"


class TestRetryAndDeadLetter:
    async def test_failure_requeues_with_backoff(self, database, session):
        queue = JobQueue(database)
        job = await queue.enqueue(session, kind="test.job", payload={})
        await session.commit()
        claimed = await queue.claim_next("w")
        assert claimed is not None

        status = await queue.fail(job.id, error="boom", backoff_base_s=0.0)
        assert status == JobStatus.QUEUED

        # immediately claimable again (zero backoff)
        again = await queue.claim_next("w")
        assert again is not None and again.attempts == 2

    async def test_dead_letter_after_max_attempts(self, database, session):
        queue = JobQueue(database)
        job = await queue.enqueue(session, kind="test.job", payload={}, max_attempts=2)
        await session.commit()

        for _ in range(2):
            claimed = await queue.claim_next("w")
            assert claimed is not None
            status = await queue.fail(job.id, error="boom", backoff_base_s=0.0)
        assert status == JobStatus.DEAD

        assert await queue.claim_next("w") is None  # dead jobs never re-run


class TestReaper:
    async def test_stale_running_jobs_are_requeued(self, database, session):
        queue = JobQueue(database)
        job = await queue.enqueue(session, kind="test.job", payload={})
        await session.commit()
        await queue.claim_next("crashed-worker")

        # simulate crash: locked_at far in the past
        async with database.begin() as s:
            await s.execute(
                BackgroundJob.__table__.update()
                .where(BackgroundJob.id == job.id)
                .values(locked_at=utcnow() - timedelta(seconds=9999))
            )

        requeued = await queue.requeue_stale(lease_s=120)
        assert requeued == 1
        again = await queue.claim_next("new-worker")
        assert again is not None and again.id == job.id

    async def test_fresh_lease_is_not_reaped(self, database, session):
        queue = JobQueue(database)
        await queue.enqueue(session, kind="test.job", payload={})
        await session.commit()
        await queue.claim_next("live-worker")
        assert await queue.requeue_stale(lease_s=120) == 0


class TestConcurrency:
    async def test_concurrent_claims_never_overlap(self, database, session):
        """Two workers racing the same queue each get distinct jobs."""
        queue = JobQueue(database)
        for i in range(10):
            await queue.enqueue(session, kind=f"test.job.{i}", payload={})
        await session.commit()

        claimed_ids = await asyncio.gather(
            *[queue.claim_next(f"worker-{i % 2}") for i in range(10)]
        )
        ids = [job.id for job in claimed_ids if job is not None]
        assert len(ids) == len(set(ids)), "each job must be claimed at most once"
        assert len(ids) == 10
