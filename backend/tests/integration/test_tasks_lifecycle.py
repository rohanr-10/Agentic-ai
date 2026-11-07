"""Integration: autonomous task lifecycle (the core acceptance path)."""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.conftest import auth_headers, create_task, wait_for_task_status


def _write_read_plan(path="out.txt", content="autonomous output") -> dict:
    return {
        "objective_restated": "write then verify a file",
        "steps": [
            {
                "id": "write",
                "title": "Write the output file",
                "tool_key": "fs.write",
                "input": {"path": path, "content": content},
                "timeout_s": 30,
            },
            {
                "id": "verify",
                "title": "Read it back",
                "tool_key": "fs.read",
                "input": {"path": path},
                "depends_on": ["write"],
                "timeout_s": 30,
                "success_criteria": ["output_contains:" + content],
            },
        ],
    }


class TestFullLifecycle:
    async def test_client_plan_requires_approval_then_completes(self, client, headers, worker):
        task = await create_task(client, headers, plan=_write_read_plan())
        assert task["status"] in ("queued", "planning")

        await worker.drain(max_wait_s=3.0)
        task = await wait_for_task_status(client, headers, task["id"], {"awaiting_approval"})

        # approval request visible with the plan payload
        approvals = await client.get("/api/v1/approvals?status=pending", headers=headers)
        assert approvals.status_code == 200
        items = approvals.json()["items"]
        assert len(items) == 1
        assert items[0]["task_id"] == task["id"]
        assert items[0]["kind"] == "plan"
        assert items[0]["payload"]["plan"]["steps"][0]["tool_key"] == "fs.write"

        decision = await client.post(
            f"/api/v1/approvals/{items[0]['id']}",
            json={"decision": "approve"},
            headers=headers,
        )
        assert decision.status_code == 200

        await worker.drain(max_wait_s=5.0)
        final = await wait_for_task_status(client, headers, task["id"], {"completed"})
        assert final["result_summary"]
        assert final["artifacts"]["files"], "artifact manifest must list workspace files"
        assert any(f["path"] == "out.txt" for f in final["artifacts"]["files"])
        assert final["usage"]["steps_executed"] == 2
        assert final["usage"]["tool_calls"] == 2

        # per-step execution history recorded
        executions = await client.get(
            f"/api/v1/tasks/{task['id']}/executions", headers=headers
        )
        assert executions.status_code == 200
        steps = {e["step_id"]: e for e in executions.json()}
        assert set(steps) == {"write", "verify"}
        assert all(e["status"] == "succeeded" for e in steps.values())
        assert steps["verify"]["output"]["output"]["content"] == "autonomous output"

        # completion notification delivered
        notifications = await client.get("/api/v1/notifications", headers=headers)
        kinds = [n["kind"] for n in notifications.json()["items"]]
        assert "task_completed" in kinds

    async def test_rejection_cancels_task(self, client, headers, worker):
        task = await create_task(client, headers, plan=_write_read_plan("never.txt"))
        await worker.drain(max_wait_s=3.0)
        await wait_for_task_status(client, headers, task["id"], {"awaiting_approval"})

        approvals = await client.get("/api/v1/approvals?status=pending", headers=headers)
        approval_id = approvals.json()["items"][0]["id"]
        rejected = await client.post(
            f"/api/v1/approvals/{approval_id}",
            json={"decision": "reject", "reason": "not today"},
            headers=headers,
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"

        final = await wait_for_task_status(client, headers, task["id"], {"cancelled"})
        assert final["error"]["code"] == "approval_rejected"

    async def test_auto_approval_mode(self, client, headers, worker):
        """Operators may enable auto-approval for trusted single-user deployments."""
        task = await create_task(client, headers, plan=_write_read_plan("auto.txt"))
        task_id = task["id"]
        # flip the setting on the live app
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            await worker.drain(max_wait_s=5.0)
            final = await wait_for_task_status(client, headers, task_id, {"completed"})
            assert final["artifacts"]["files"]
        finally:
            client._transport.app.state.settings.auto_approve_plans = False


class TestStepApprovals:
    async def test_sensitive_tool_step_requires_separate_approval(self, client, headers, worker):
        plan = {
            "objective_restated": "run a terminal command",
            "steps": [
                {
                    "id": "list",
                    "title": "List files via terminal",
                    "tool_key": "terminal.execute",
                    "input": {"command": ["ls", "-la"]},
                    "timeout_s": 30,
                }
            ],
        }
        task = await create_task(client, headers, plan=plan)
        await worker.drain(max_wait_s=3.0)
        task = await wait_for_task_status(client, headers, task["id"], {"awaiting_approval"})

        # approve the PLAN first
        approvals = await client.get("/api/v1/approvals?status=pending", headers=headers)
        plan_approval = next(
            a for a in approvals.json()["items"] if a["kind"] == "plan"
        )
        await client.post(
            f"/api/v1/approvals/{plan_approval['id']}",
            json={"decision": "approve"},
            headers=headers,
        )
        await worker.drain(max_wait_s=5.0)

        # now the STEP approval appears (terminal.execute is approval-gated)
        await wait_for_task_status(client, headers, task["id"], {"awaiting_approval"})
        approvals = await client.get("/api/v1/approvals?status=pending", headers=headers)
        step_approval = next(a for a in approvals.json()["items"] if a["kind"] == "step")
        assert step_approval["step_id"] == "list"

        await client.post(
            f"/api/v1/approvals/{step_approval['id']}",
            json={"decision": "approve"},
            headers=headers,
        )
        await worker.drain(max_wait_s=5.0)
        final = await wait_for_task_status(client, headers, task["id"], {"completed"})
        executions = await client.get(
            f"/api/v1/tasks/{task['id']}/executions", headers=headers
        )
        assert executions.json()[0]["status"] == "succeeded"


class TestPythonExecution:
    async def test_python_step_executes_in_sandbox(self, client, headers, worker):
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            plan = {
                "objective_restated": "compute and persist",
                "steps": [
                    {
                        "id": "compute",
                        "title": "Run python computation",
                        "tool_key": "python.execute",
                        "input": {
                            "code": (
                                "import json\n"
                                "data = {'sum': sum(range(101))}\n"
                                "open('result.json', 'w').write(json.dumps(data))\n"
                                "print('done')\n"
                            )
                        },
                        "timeout_s": 60,
                    }
                ],
            }
            task = await create_task(client, headers, plan=plan)
            await worker.drain(max_wait_s=8.0)
            final = await wait_for_task_status(client, headers, task["id"], {"completed"})
            assert any(
                f["path"] == "result.json" for f in final["artifacts"]["files"]
            )
            executions = await client.get(
                f"/api/v1/tasks/{task['id']}/executions", headers=headers
            )
            output = executions.json()[0]["output"]["output"]
            assert output["exit_code"] == 0
            assert "done" in output["stdout"]
        finally:
            client._transport.app.state.settings.auto_approve_plans = False

    async def test_parallel_steps_share_no_workspace_corruption(self, client, headers, worker):
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            plan = {
                "objective_restated": "parallel writes",
                "steps": [
                    {
                        "id": f"write{i}",
                        "title": f"Write file {i}",
                        "tool_key": "fs.write",
                        "input": {"path": f"file{i}.txt", "content": f"content-{i}" * 100},
                        "timeout_s": 30,
                    }
                    for i in range(4)
                ],
            }
            task = await create_task(client, headers, plan=plan)
            await worker.drain(max_wait_s=8.0)
            final = await wait_for_task_status(client, headers, task["id"], {"completed"})
            paths = {f["path"] for f in final["artifacts"]["files"]}
            assert paths == {f"file{i}.txt" for i in range(4)}
        finally:
            client._transport.app.state.settings.auto_approve_plans = False


class TestVerification:
    async def test_failing_criterion_fails_the_task(self, client, headers, worker):
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            plan = {
                "objective_restated": "verify strictness",
                "steps": [
                    {
                        "id": "write",
                        "title": "Write file",
                        "tool_key": "fs.write",
                        "input": {"path": "v.txt", "content": "actual"},
                        "timeout_s": 30,
                        "success_criteria": ["output_contains:this-text-was-never-written"],
                    }
                ],
            }
            task = await create_task(client, headers, plan=plan)
            await worker.drain(max_wait_s=5.0)
            final = await wait_for_task_status(client, headers, task["id"], {"failed"})
            assert final["error"]["code"] == "verification_failed"
            assert "checks failed" in final["error"]["message"]
        finally:
            client._transport.app.state.settings.auto_approve_plans = False

    async def test_missing_artifact_criterion_fails(self, client, headers, worker):
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            plan = {
                "objective_restated": "artifact check",
                "steps": [
                    {
                        "id": "noop",
                        "title": "Do nothing",
                        "tool_key": "fs.list",
                        "input": {"path": "."},
                        "timeout_s": 30,
                        "success_criteria": ["artifact:must-exist.txt"],
                    }
                ],
            }
            task = await create_task(client, headers, plan=plan)
            await worker.drain(max_wait_s=5.0)
            final = await wait_for_task_status(client, headers, task["id"], {"failed"})
            assert final["error"]["code"] == "verification_failed"
        finally:
            client._transport.app.state.settings.auto_approve_plans = False


class TestPauseResumeCancel:
    async def test_pause_before_execution_then_resume(self, client, headers, worker):
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            plan = {
                "objective_restated": "pauseable",
                "steps": [
                    {
                        "id": f"s{i}",
                        "title": f"Step {i}",
                        "tool_key": "python.execute",
                        "input": {"code": "import time; time.sleep(0.05); print('ok')"},
                        "timeout_s": 30,
                    }
                    for i in range(6)
                ],
            }
            task = await create_task(client, headers, plan=plan)
            await asyncio.sleep(0.3)  # let the worker start
            paused = await client.post(f"/api/v1/tasks/{task['id']}/pause", headers=headers)
            assert paused.status_code == 200
            final = await wait_for_task_status(
                client, headers, task["id"], {"paused", "completed"}, timeout_s=10
            )
            if final["status"] == "paused":
                resumed = await client.post(
                    f"/api/v1/tasks/{task['id']}/resume", headers=headers
                )
                assert resumed.status_code == 200
                await worker.drain(max_wait_s=10.0)
                final = await wait_for_task_status(client, headers, task["id"], {"completed"})
            assert final["status"] == "completed"
        finally:
            client._transport.app.state.settings.auto_approve_plans = False

    async def test_cancel_mid_execution_kills_subprocess(self, client, headers, worker):
        """Cancellation must propagate into the running sandbox process."""
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            plan = {
                "objective_restated": "long-running",
                "steps": [
                    {
                        "id": "long",
                        "title": "Sleep for a long time",
                        "tool_key": "python.execute",
                        "input": {"code": "import time; time.sleep(60)"},
                        "timeout_s": 120,
                    }
                ],
            }
            task = await create_task(client, headers, plan=plan)

            # run the worker concurrently so the step is live while we cancel
            worker_task = asyncio.create_task(worker.run())
            try:
                import time

                deadline = time.monotonic() + 15
                running = False
                while time.monotonic() < deadline:
                    executions = await client.get(
                        f"/api/v1/tasks/{task['id']}/executions", headers=headers
                    )
                    if any(e["status"] == "running" for e in executions.json()):
                        running = True
                        break
                    await asyncio.sleep(0.05)
                assert running, "step did not start running"

                cancel_started = time.monotonic()
                cancelled = await client.post(
                    f"/api/v1/tasks/{task['id']}/cancel", headers=headers
                )
                assert cancelled.status_code == 200

                final = await wait_for_task_status(
                    client, headers, task["id"], {"cancelled"}
                )
                elapsed = time.monotonic() - cancel_started
                # the sandbox subprocess must be killed promptly, not after 60s
                assert elapsed < 15, f"cancellation took {elapsed:.1f}s to propagate"

                executions = await client.get(
                    f"/api/v1/tasks/{task['id']}/executions", headers=headers
                )
                assert executions.json()[0]["status"] == "cancelled"
            finally:
                worker.stop()
                await asyncio.wait_for(worker_task, timeout=15)
        finally:
            client._transport.app.state.settings.auto_approve_plans = False

    async def test_hybrid_notes_recorded(self, client, headers, worker):
        task = await create_task(client, headers, plan=_write_read_plan())
        noted = await client.post(
            f"/api/v1/tasks/{task['id']}/notes",
            json={"content": "also include the word banana in the file", "request_replan": False},
            headers=headers,
        )
        assert noted.status_code == 200
        detail = await client.get(f"/api/v1/tasks/{task['id']}", headers=headers)
        assert any(
            "banana" in str(note) for note in detail.json()["additional_context"]
        )


class TestBudgets:
    async def test_step_budget_exceeded_fails_task(self, client, headers, worker):
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            plan = {
                "objective_restated": "too many steps",
                "steps": [
                    {"id": f"s{i}", "title": f"step {i}", "tool_key": "fs.list", "input": {}}
                    for i in range(5)
                ],
            }
            task = await create_task(client, headers, plan=plan, budgets={"max_steps": 2})
            await worker.drain(max_wait_s=5.0)
            final = await wait_for_task_status(client, headers, task["id"], {"failed"})
            assert final["error"]["code"] == "budget_exceeded"
        finally:
            client._transport.app.state.settings.auto_approve_plans = False

    async def test_invalid_plan_rejected_at_creation(self, client, headers):
        response = await client.post(
            "/api/v1/tasks",
            json={
                "title": "bad plan",
                "objective": "x",
                "plan": {
                    "steps": [
                        {"id": "a", "title": "A", "tool_key": "no.such.tool", "input": {}}
                    ]
                },
            },
            headers=headers,
        )
        assert response.status_code == 400
        assert "unknown tool" in json.dumps(response.json()["error"]["details"])

    async def test_task_listing_and_filter(self, client, headers, worker):
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            await create_task(client, headers, plan=_write_read_plan("list1.txt"))
            await create_task(client, headers, plan=_write_read_plan("list2.txt"))
            listing = await client.get("/api/v1/tasks", headers=headers)
            assert listing.json()["total"] == 2
            completed = await client.get(
                "/api/v1/tasks?status=completed", headers=headers
            )
            assert completed.json()["total"] == 0
        finally:
            client._transport.app.state.settings.auto_approve_plans = False
