"""Background worker runtime.

Runs claimed jobs with heartbeat-based lease extension, a periodic reaper
(for crashed workers) and graceful shutdown.  Runs either as a standalone
process (``python -m app.worker``) or embedded in the API process for
single-node development (``AGP_WORKER_EMBEDDED=true``).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from datetime import timedelta
from typing import Any

import structlog

from app.core import telemetry
from app.core.config import Settings
from app.core.errors import AppError
from app.core.utils import utcnow
from app.db.enums import JobStatus
from app.db.session import Database
from app.events.bus import EventBus
from app.jobs.handlers import HandlerContext, run_handler
from app.jobs.queue import JobQueue

logger = structlog.stdlib.get_logger("app.worker")


class Worker:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        bus: EventBus,
        *,
        worker_id: str = "worker-1",
        concurrency: int = 1,
    ) -> None:
        self.database = database
        self.settings = settings
        self.bus = bus
        self.worker_id = worker_id
        self.concurrency = max(1, concurrency)
        self.queue = JobQueue(database)
        self._stop_event = asyncio.Event()
        self._running_jobs: set[asyncio.Task] = set()
        self._last_reap = utcnow()
        self.jobs_processed = 0
        self.jobs_failed = 0

    # -- lifecycle --------------------------------------------------------
    async def run(self) -> None:
        """Main loop; returns when :meth:`stop` is called (graceful)."""
        logger.info(
            "worker_started",
            worker_id=self.worker_id,
            concurrency=self.concurrency,
            poll_interval_s=self.settings.worker_poll_interval_s,
        )
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("worker_tick_failed")
            await self._sleep_or_stop(self.settings.worker_poll_interval_s)
        # graceful shutdown: wait for in-flight jobs
        if self._running_jobs:
            logger.info("worker_waiting_for_inflight_jobs", count=len(self._running_jobs))
            await asyncio.gather(*self._running_jobs, return_exceptions=True)
        logger.info("worker_stopped", processed=self.jobs_processed, failed=self.jobs_failed)

    def stop(self) -> None:
        self._stop_event.set()

    async def _sleep_or_stop(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)

    # -- one tick ----------------------------------------------------------
    async def _tick(self) -> None:
        await self._maybe_reap()
        claimed = 0
        while claimed < self.concurrency:
            job = await self.queue.claim_next(self.worker_id)
            if job is None:
                break
            task = asyncio.create_task(self._execute(job.id, job.kind, job.payload))
            self._running_jobs.add(task)
            task.add_done_callback(self._running_jobs.discard)
            claimed += 1
        # opportunistically wait for running tasks so run() observes failures
        if self._running_jobs:
            done, _ = await asyncio.wait(self._running_jobs, timeout=0)
            for task in done:
                task.result() if not task.cancelled() else None

    async def _maybe_reap(self) -> None:
        interval = timedelta(seconds=max(15, self.settings.worker_lease_s // 2))
        if utcnow() - self._last_reap >= interval:
            self._last_reap = utcnow()
            await self.queue.requeue_stale(self.settings.worker_lease_s)

    # -- execution ----------------------------------------------------------
    async def _execute(self, job_id: Any, kind: str, payload: dict) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(job_id))
        started = utcnow()
        try:
            ctx = HandlerContext(
                database=self.database,
                settings=self.settings,
                bus=self.bus,
                worker_id=self.worker_id,
            )
            result = await run_handler(ctx, kind, payload)
            await self.queue.complete(job_id, result)
            self.jobs_processed += 1
            telemetry.record_job(kind=kind, status="succeeded")
            logger.info(
                "background_job_succeeded",
                job_id=str(job_id),
                kind=kind,
                duration_ms=int((utcnow() - started).total_seconds() * 1000),
            )
        except asyncio.CancelledError:
            raise
        except AppError as exc:
            self.jobs_failed += 1
            telemetry.record_job(kind=kind, status="failed")
            status = await self.queue.fail(
                job_id,
                error=f"{exc.code}: {exc.message}",
                backoff_base_s=self.settings.job_retry_backoff_base_s,
            )
            telemetry.record_job(kind=kind, status=str(status))
        except Exception as exc:
            self.jobs_failed += 1
            telemetry.record_job(kind=kind, status="failed")
            logger.exception("background_job_crashed", job_id=str(job_id), kind=kind)
            await self.queue.fail(
                job_id,
                error=f"unhandled:{type(exc).__name__}: {exc}",
                backoff_base_s=self.settings.job_retry_backoff_base_s,
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, job_id: Any) -> None:
        while True:
            await asyncio.sleep(self.settings.worker_heartbeat_s)
            try:
                await self.queue.heartbeat(job_id, self.worker_id)
            except Exception:  # pragma: no cover
                logger.warning("job_heartbeat_failed", job_id=str(job_id))

    # -- test / operational helpers ------------------------------------------
    async def run_once(self) -> bool:
        """Claim and execute exactly one job if available.  Returns whether a
        job ran.  Used by tests and operational runbooks."""
        job = await self.queue.claim_next(self.worker_id)
        if job is None:
            return False
        await self._execute(job.id, job.kind, job.payload)
        return True

    async def drain(self, *, max_jobs: int = 100, max_wait_s: float = 10.0) -> int:
        """Run jobs until the queue is (temporarily) empty.

        Waits briefly for delayed retries whose backoff has not elapsed yet;
        set ``AGP_JOB_RETRY_BACKOFF_BASE_S=0`` in tests for instant retries.
        """
        ran = 0
        waited = 0.0
        while ran < max_jobs:
            if await self.run_once():
                ran += 1
                waited = 0.0
                continue
            if waited >= max_wait_s or ran > 0:
                break
            await asyncio.sleep(0.05)
            waited += 0.05
        return ran


async def run_worker_process(settings: Settings) -> None:
    """Entry point for ``python -m app.worker``."""
    from app.core.logging import configure_logging

    configure_logging(settings)
    database = Database(settings)
    bus = EventBus(settings)
    await bus.start()
    worker = Worker(database, settings, bus, worker_id=f"worker-{utcnow().strftime('%H%M%S')}-{id(database):x}")

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async def _supervise() -> None:
        await stop.wait()
        worker.stop()

    supervisor = asyncio.create_task(_supervise())
    try:
        await worker.run()
    finally:
        supervisor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await supervisor
        await bus.close()
        await database.dispose()
