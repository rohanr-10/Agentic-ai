"""Recovery: worker crashes, interrupted tasks, job retries, app restarts,
model failures."""

from __future__ import annotations

import asyncio
import uuid

import sqlalchemy as sa

from app.core.utils import utcnow
from app.db.enums import JobStatus, TaskStatus
from app.db.models import Task
from tests.conftest import create_task, wait_for_task_status


class TestJobCrashRecovery:
    async def test_interrupted_file_extraction_recovers(self, client, headers):
        """Simulate a worker crash mid-extraction; a new worker finishes it."""
        upload = await client.post(
            "/api/v1/files",
            files={"file": ("crash.txt", b"precious content", "text/plain")},
            headers=headers,
        )
        file_id = upload.json()["id"]
        app = client._transport.app

        # simulate: the extraction job was claimed and the worker died
        async with app.state.database.begin() as session:
            await session.execute(
                sa.text(
                    "UPDATE background_jobs SET status='running', locked_by='dead-worker', "
                    "locked_at=:locked_at WHERE idempotency_key=:key"
                ).bindparams(
                    locked_at=utcnow().replace(year=2020),
                    key=f"file-extract:{file_id}",
                )
            )

        # a fresh worker's reaper requeues the stale job and completes it
        from app.workers.runner import Worker

        worker = Worker(app.state.database, app.state.settings, app.state.bus)
        await worker.queue.requeue_stale(app.state.settings.worker_lease_s)
        await worker.drain(max_wait_s=5.0)

        detail = await client.get(f"/api/v1/files/{file_id}", headers=headers)
        assert detail.json()["status"] == "ready"
        assert detail.json()["extracted_chars"] > 0

    async def test_job_retry_then_success(self, client, headers, test_settings):
        """A flaky handler succeeds on retry (attempts recorded)."""
        app = client._transport.app
        calls = {"n": 0}

        from app.jobs.handlers import HandlerContext, register
        from app.workers.runner import Worker

        @register("test.flaky")
        async def flaky(ctx: HandlerContext, payload: dict):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("flaky failure")
            return {"ok": True}

        from app.jobs.queue import JobQueue

        async with app.state.database.session() as session:
            await JobQueue().enqueue(
                session, kind="test.flaky", payload={}, max_attempts=5
            )
            await session.commit()

        worker = Worker(app.state.database, app.state.settings, app.state.bus)
        ran = await worker.drain(max_wait_s=6.0)
        assert ran >= 1
        assert calls["n"] >= 3

        async with app.state.database.session() as session:
            from app.db.models import BackgroundJob

            job = (
                await session.execute(
                    sa.select(BackgroundJob).where(BackgroundJob.kind == "test.flaky")
                )
            ).scalar_one()
        assert job.status == JobStatus.SUCCEEDED
        assert job.attempts >= 3

    async def test_dead_letter_surfaces_in_admin(self, client):
        app = client._transport.app
        from app.jobs.handlers import HandlerContext, register
        from app.jobs.queue import JobQueue
        from app.workers.runner import Worker

        @register("test.always_fails")
        async def always_fails(ctx: HandlerContext, payload: dict):
            raise RuntimeError("permanent failure")

        async with app.state.database.session() as session:
            await JobQueue().enqueue(
                session, kind="test.always_fails", payload={}, max_attempts=2
            )
            await session.commit()

        worker = Worker(app.state.database, app.state.settings, app.state.bus)
        await worker.drain(max_wait_s=5.0)

        from app.db.models import BackgroundJob

        async with app.state.database.session() as session:
            job = (
                await session.execute(
                    sa.select(BackgroundJob).where(
                        BackgroundJob.kind == "test.always_fails"
                    )
                )
            ).scalar_one()
        assert job.status == JobStatus.DEAD
        assert "permanent failure" in job.last_error


class TestTaskRecovery:
    async def test_crashed_running_task_resumes_from_checkpoint(self, client, headers):
        """A task interrupted mid-run continues without re-executing finished steps."""
        app = client._transport.app
        app.state.settings.auto_approve_plans = True

        # two-step plan; run only partially by draining inside a crashed worker
        task = await create_task(
            client,
            headers,
            plan={
                "objective_restated": "resumable",
                "steps": [
                    {
                        "id": "first",
                        "title": "Write first file",
                        "tool_key": "fs.write",
                        "input": {"path": "first.txt", "content": "first"},
                        "timeout_s": 30,
                    },
                    {
                        "id": "second",
                        "title": "Write second file",
                        "tool_key": "fs.write",
                        "input": {"path": "second.txt", "content": "second"},
                        "depends_on": ["first"],
                        "timeout_s": 30,
                    },
                ],
            },
        )
        task_id = task["id"]

        # run the whole task with a worker, but simulate the crash by
        # re-marking the task RUNNING with only the first step checkpointed
        from app.workers.runner import Worker

        worker = Worker(app.state.database, app.state.settings, app.state.bus)
        await worker.drain(max_wait_s=6.0)
        final = await wait_for_task_status(client, headers, task_id, {"completed"})
        assert final["artifacts"]["files"]

        # now simulate the crash state: task RUNNING (stale) with checkpoint
        # cleared of the SECOND step only
        async with app.state.database.begin() as session:
            await session.execute(
                sa.update(Task)
                .where(Task.id == uuid.UUID(task_id))
                .values(
                    status=TaskStatus.RUNNING,
                    finished_at=None,
                    checkpoint={"completed": {"first": {"attempt": 1}}, "approved_steps": []},
                    updated_at=utcnow().replace(year=2020),  # stale
                )
            )
            # remove the second step's execution record to count re-runs
            from app.db.models import TaskExecution

            await session.execute(
                sa.delete(TaskExecution).where(
                    TaskExecution.task_id == uuid.UUID(task_id),
                    TaskExecution.step_id == "second",
                )
            )

        from app.orchestration.recovery import recover_stale_tasks

        recovered = await recover_stale_tasks(
            app.state.database, app.state.settings, app.state.bus
        )
        assert recovered == 1

        ran = await worker.drain(max_wait_s=6.0)
        assert ran >= 1

        final = await wait_for_task_status(client, headers, task_id, {"completed"})
        # the FIRST step was not re-executed (checkpoint respected)
        executions = await client.get(
            f"/api/v1/tasks/{task_id}/executions", headers=headers
        )
        first_executions = [e for e in executions.json() if e["step_id"] == "first"]
        second_executions = [e for e in executions.json() if e["step_id"] == "second"]
        assert len(first_executions) == 1, "completed step must not re-run"
        assert len(second_executions) == 1
        app.state.settings.auto_approve_plans = False

    async def test_expired_approval_fails_task(self, client, headers, worker):
        task = await create_task(client, headers)
        await worker.drain(max_wait_s=3.0)
        await wait_for_task_status(client, headers, task["id"], {"awaiting_approval"})

        app = client._transport.app
        from app.orchestration.recovery import expire_stale_approvals

        # force-expire the pending approval
        async with app.state.database.begin() as session:
            await session.execute(
                sa.text(
                    "UPDATE approval_requests SET expires_at=:past WHERE status='pending'"
                ).bindparams(past=utcnow().replace(year=2020))
            )
        expired = await expire_stale_approvals(app.state.database, app.state.bus)
        assert expired >= 1

        final = await wait_for_task_status(client, headers, task["id"], {"failed"})
        assert final["error"]["code"] == "approval_expired"


class TestAppRestartPersistence:
    async def test_state_survives_restart(self, tmp_path):
        """Data written by app instance #1 is fully visible to instance #2."""
        import sys

        sys.path.insert(0, str(tmp_path))
        from tests.conftest import make_settings, run_migrations

        settings = make_settings(tmp_path)
        run_migrations(settings.database_url)

        import httpx

        from app.main import create_app

        # --- instance 1 -----------------------------------------------------
        app1 = create_app(settings)
        transport1 = httpx.ASGITransport(app=app1)
        async with app1.router.lifespan_context(app1):
            async with httpx.AsyncClient(transport=transport1, base_url="http://t") as c1:
                registered = await c1.post(
                    "/api/v1/auth/register",
                    json={
                        "email": "restart@example.com",
                        "password": "Str0ngPassw0rd!",
                        "display_name": "R",
                    },
                )
                headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
                conversation = await c1.post(
                    "/api/v1/conversations", json={"title": "Survivor"}, headers=headers
                )
                conversation_id = conversation.json()["id"]
                await c1.post(
                    f"/api/v1/conversations/{conversation_id}/messages",
                    json={"content": "remember this"},
                    headers=headers,
                )
                upload = await c1.post(
                    "/api/v1/files",
                    files={"file": ("keep.txt", b"persisted bytes", "text/plain")},
                    headers=headers,
                )
                file_id = upload.json()["id"]
                task = await create_task(c1, headers)
                await c1.post(
                    "/api/v1/memory",
                    json={"category": "semantic", "content": "restart fact"},
                    headers=headers,
                )
                memory = (await c1.get("/api/v1/memory", headers=headers)).json()["items"][0]

        await app1.state.database.dispose()

        # --- instance 2 (same database) --------------------------------------
        app2 = create_app(settings)
        transport2 = httpx.ASGITransport(app=app2)
        async with app2.router.lifespan_context(app2):
            async with httpx.AsyncClient(transport=transport2, base_url="http://t") as c2:
                # login with the SAME credentials on the new instance
                login = await c2.post(
                    "/api/v1/auth/login",
                    json={"email": "restart@example.com", "password": "Str0ngPassw0rd!"},
                )
                assert login.status_code == 200
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

                conversations = await c2.get("/api/v1/conversations", headers=headers)
                assert any(
                    c["id"] == conversation_id for c in conversations.json()
                ), "conversation must survive restart"

                messages = await c2.get(
                    f"/api/v1/conversations/{conversation_id}/messages", headers=headers
                )
                assert any("remember this" in m["content"] for m in messages.json())

                files = await c2.get("/api/v1/files", headers=headers)
                assert any(f["id"] == file_id for f in files.json()["items"])

                tasks = await c2.get("/api/v1/tasks", headers=headers)
                assert any(t["id"] == task["id"] for t in tasks.json()["items"])

                memories = await c2.get("/api/v1/memory", headers=headers)
                assert any(m["id"] == memory["id"] for m in memories.json()["items"])

        await app2.state.database.dispose()


class TestModelFailures:
    async def test_model_error_surfaces_cleanly_in_chat(self, client, headers):
        """A failing model produces a normalized SSE error, not a crash."""
        import json

        import sqlalchemy as sa

        from app.db.models import ModelConfig

        app = client._transport.app
        async with app.state.database.session() as session:
            config = await session.scalar(
                sa.select(ModelConfig).where(ModelConfig.name == "test-echo")
            )
            config.parameters = {"script": [{"type": "error", "code": "model_timeout"}]}
            await session.commit()
        app.state.model_manager.invalidate(config.id)

        from tests.conftest import create_conversation

        conversation = await create_conversation(client, headers)
        request = client.build_request(
            "POST",
            f"/api/v1/conversations/{conversation['id']}/messages/stream",
            json={"content": "will fail"},
            headers=headers,
        )
        response = await client.send(request, stream=True)
        assert response.status_code == 200
        body = ""
        async for chunk in response.aiter_text():
            body += chunk
        await response.aclose()
        assert "error" in body

    async def test_retryable_step_error_fails_after_retry_budget(self, client, headers, worker):
        """A deterministically failing step fails the task; a TIMEOUT failure
        consumes its retry budget first."""
        app = client._transport.app
        app.state.settings.auto_approve_plans = True
        app.state.settings.task_retry_backoff_base_s = 0.0
        try:
            # deterministic non-zero exit: fatal, single attempt
            task = await create_task(
                client,
                headers,
                plan={
                    "objective_restated": "doomed",
                    "steps": [
                        {
                            "id": "boom",
                            "title": "Always fails",
                            "tool_key": "python.execute",
                            "input": {"code": "import sys; sys.exit(3)"},
                            "max_retries": 2,
                            "timeout_s": 30,
                        }
                    ],
                },
            )
            await worker.drain(max_wait_s=8.0)
            final = await wait_for_task_status(client, headers, task["id"], {"failed"})
            assert final["error"]["code"] == "step_failed"
            assert "exited with code" in final["error"]["message"]
            executions = await client.get(
                f"/api/v1/tasks/{task['id']}/executions", headers=headers
            )
            attempts = [e["attempt"] for e in executions.json()]
            assert attempts == [1], f"deterministic failure must not retry, got {attempts}"

            # timeout: retryable, exhausts the budget
            task2 = await create_task(
                client,
                headers,
                plan={
                    "objective_restated": "timeout retry",
                    "steps": [
                        {
                            "id": "slow",
                            "title": "Always times out",
                            "tool_key": "python.execute",
                            "input": {"code": "import time; time.sleep(30)"},
                            "max_retries": 2,
                            "timeout_s": 1,
                        }
                    ],
                },
            )
            await worker.drain(max_wait_s=15.0)
            final2 = await wait_for_task_status(client, headers, task2["id"], {"failed"})
            assert final2["error"]["code"] == "step_failed"
            assert "timed out" in final2["error"]["message"]
            executions2 = await client.get(
                f"/api/v1/tasks/{task2['id']}/executions", headers=headers
            )
            attempts2 = [e["attempt"] for e in executions2.json()]
            assert attempts2 == [1, 2, 3], f"expected 3 attempts, got {attempts2}"
        finally:
            app.state.settings.auto_approve_plans = False
            app.state.settings.task_retry_backoff_base_s = 0.0
