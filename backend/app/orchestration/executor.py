"""Task executor — the autonomous execution engine.

Runs inside a background job (``task.execute``).  Properties:

* **Resumable**: progress checkpoints after every step-wave; a crashed
  worker's successor continues without re-running completed steps.
* **Budgeted**: step count, tool-call count and a wall-clock deadline are
  enforced; every step has its own timeout and retry budget.
* **Controlled**: cancellation/pause are checked between waves and
  cancellation propagates INTO running subprocesses (process-group kill)
  via control events and the in-process control registry.
* **Approval-gated**: generated plans, client-provided plans and revised
  plans all require human approval before execution (unless the operator
  explicitly enables auto-approval); sensitive steps pause execution again.
* **Verified**: completion requires the rule-based verifier to pass; model
  claims never count as verification.

There is no unbounded agent loop — only a finite, validated step DAG with
bounded parallelism per dependency wave.
"""

from __future__ import annotations

import asyncio
import json
import time
import weakref
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ModelGatewayError, OrchestrationError, ToolRetryableError
from app.core.utils import new_uuid, truncate_text, utcnow
from app.db.enums import (
    ApprovalKind,
    ApprovalStatus,
    ExecutionStatus,
    TaskStatus,
    TERMINAL_TASK_STATUSES,
)
from app.db.models import ApprovalRequest, Task, TaskExecution
from app.db.session import Database
from app.events.bus import EventBus
from app.gateway.manager import ModelManager
from app.orchestration.planner import (
    Plan,
    PlanContext,
    Planner,
    StepSpec,
    topological_groups,
    validate_plan,
)
from app.orchestration.states import can_transition, transition
from app.orchestration.verifier import CompositeVerifier, scan_artifacts
from app.tools.base import ToolContext
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.sandbox import ControlHandle

logger = structlog.stdlib.get_logger("app.executor")

# Per-process control registry (embedded worker: API can kill instantly)
_ACTIVE_CONTROLS: dict[str, ControlHandle] = {}

# Shared ModelManager per Database instance (adapter + health caches)
_SHARED_MANAGERS: "weakref.WeakKeyDictionary[Database, ModelManager]" = (
    weakref.WeakKeyDictionary()
)

_CHECKPOINT_OUTPUT_CAP = 16 * 1024


@dataclass
class ExecutionOutcome:
    status: str
    detail: str


class _PausedSignal(Exception):
    """Internal control-flow signal: pause acknowledged."""


class _CancelledSignal(Exception):
    """Internal control-flow signal: cancellation acknowledged."""


def get_shared_manager(database: Database, settings: Settings) -> ModelManager:
    manager = _SHARED_MANAGERS.get(database)
    if manager is None:
        manager = ModelManager(database, settings)
        _SHARED_MANAGERS[database] = manager
    return manager


def cancel_active_control(task_id: UUID, reason: str = "cancelled") -> bool:
    handle = _ACTIVE_CONTROLS.get(str(task_id))
    if handle is None:
        return False
    handle.cancel(reason)
    return True


class TaskExecutor:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        bus: EventBus,
        *,
        registry: ToolRegistry | None = None,
        planner: Planner | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.bus = bus
        self.registry = registry or _default_registry()
        self.tool_executor = ToolExecutor(database, settings, self.registry)
        self.manager = get_shared_manager(database, settings)
        self.planner = planner

    # ------------------------------------------------------------------ run
    async def run(self, task_id: UUID, *, worker_id: str = "worker") -> ExecutionOutcome:
        control = ControlHandle()
        _ACTIVE_CONTROLS[str(task_id)] = control
        try:
            async with self.bus.subscribe([f"task:{task_id}"]) as subscription:
                watcher = asyncio.create_task(self._watch_control(subscription, control))
                try:
                    return await self._run_phases(task_id, worker_id, control)
                finally:
                    watcher.cancel()
                    try:
                        await watcher
                    except asyncio.CancelledError:
                        pass
        finally:
            _ACTIVE_CONTROLS.pop(str(task_id), None)

    async def _watch_control(self, subscription, control: ControlHandle) -> None:
        while True:
            event = await subscription.queue.get()
            if event.type == "task.control" and event.data.get("action") == "cancel":
                control.cancel("cancelled by user")
                return

    # -------------------------------------------------------------- phases
    async def _run_phases(
        self, task_id: UUID, worker_id: str, control: ControlHandle
    ) -> ExecutionOutcome:
        async with self.database.session() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return ExecutionOutcome("missing", "task not found")
            if task.status in TERMINAL_TASK_STATUSES:
                return ExecutionOutcome(task.status.value, "already terminal")
            if task.cancel_requested:
                await self._cancel(session, task, "cancelled before execution")
                return ExecutionOutcome("cancelled", "cancel requested before start")
            if task.pause_requested and task.status in (
                TaskStatus.QUEUED,
                TaskStatus.READY,
            ):
                await self._pause(session, task)
                return ExecutionOutcome("paused", "pause requested before start")

            # ---------------- planning (no plan yet) --------------------
            if task.plan is None:
                if task.status == TaskStatus.CREATED:
                    task = await transition(
                        session, self.bus, task_id=task_id, to_status=TaskStatus.QUEUED
                    )
                if task.status != TaskStatus.QUEUED:
                    raise OrchestrationError(
                        f"cannot plan from state {task.status.value}"
                    )
                task = await transition(
                    session, self.bus, task_id=task_id, to_status=TaskStatus.PLANNING
                )
                plan = await self._make_plan(task)
                problems = validate_plan(
                    plan, registry=self.registry, max_steps=self.settings.task_max_steps
                )
                if problems:
                    await self._fail(session, task, "invalid_plan", "; ".join(problems[:10]))
                    return ExecutionOutcome(
                        "failed", "plan validation failed: " + "; ".join(problems[:5])
                    )
                task = await self._store_plan(session, task, plan, source="model")
                if self._approval_required():
                    await self._request_plan_approval(session, task, plan)
                    return ExecutionOutcome("awaiting_approval", "plan requires human approval")
                task = await transition(
                    session, self.bus, task_id=task_id, to_status=TaskStatus.READY
                )

            # ---------------- approval reconciliation --------------------
            if task.status == TaskStatus.AWAITING_APPROVAL:
                plan_approval = await self._latest_plan_approval(session, task)
                if plan_approval is not None:
                    if plan_approval.status == ApprovalStatus.REJECTED:
                        await self._cancel(session, task, "plan rejected by user")
                        return ExecutionOutcome("cancelled", "plan rejected")
                    if plan_approval.status == ApprovalStatus.PENDING:
                        return ExecutionOutcome("awaiting_approval", "plan still awaiting approval")
                    await self._mark_plan_approved(session, task)
                # no plan approval row (auto-approval) or plan approved:
                # continue; pending STEP approvals re-gate inside execution
                task = await transition(
                    session, self.bus, task_id=task_id, to_status=TaskStatus.READY
                )

            # client-provided plan arriving fresh in QUEUED
            if task.status == TaskStatus.QUEUED:
                if self._approval_required() and not await self._plan_already_approved(session, task):
                    # QUEUED -> PLANNING -> AWAITING_APPROVAL (validated path)
                    task = await transition(
                        session, self.bus, task_id=task_id, to_status=TaskStatus.PLANNING
                    )
                    plan = Plan.model_validate(task.plan)
                    problems = validate_plan(
                        plan, registry=self.registry, max_steps=self.settings.task_max_steps
                    )
                    if problems:
                        await self._fail(session, task, "invalid_plan", "; ".join(problems[:10]))
                        return ExecutionOutcome("failed", "client plan invalid")
                    await self._request_plan_approval(session, task, plan)
                    return ExecutionOutcome(
                        "awaiting_approval", "client plan requires human approval"
                    )
                # already approved (or auto-approval enabled)
                task = await transition(
                    session, self.bus, task_id=task_id, to_status=TaskStatus.READY
                )

            # ---------------- execute -------------------------------------
            allowed = (
                TaskStatus.READY,
                TaskStatus.RUNNING,
                TaskStatus.RETRYING,
                TaskStatus.PAUSED,
                TaskStatus.VERIFYING,  # crashed verification can be re-run
            )
            if task.status not in allowed:
                raise OrchestrationError(f"cannot execute from state {task.status.value}")
            if task.pause_requested:
                await self._pause(session, task)
                return ExecutionOutcome("paused", "pause requested")
            if task.status not in (TaskStatus.RUNNING, TaskStatus.VERIFYING):
                task = await transition(
                    session, self.bus, task_id=task_id, to_status=TaskStatus.RUNNING
                )

            try:
                return await self._execute_steps(session, task, control)
            except _PausedSignal:
                return ExecutionOutcome("paused", "paused between steps")
            except _CancelledSignal:
                return ExecutionOutcome("cancelled", "cancelled between steps")

    def _approval_required(self) -> bool:
        return not self.settings.auto_approve_plans

    # ------------------------------------------------------------ planning
    async def _make_plan(self, task: Task) -> Plan:
        context = PlanContext(
            objective=task.objective,
            available_tools=self._tool_summaries(),
            additional_notes=list(task.additional_context or []),
        )
        if self.planner is not None:
            return await self.planner.plan(context)
        from app.orchestration.planner import ModelPlanner

        planner = ModelPlanner(self.manager, registry=self.registry)
        return await planner.plan(context)

    def _tool_summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.key,
                "description": tool.description,
                "input_schema": tool.input_json_schema(),
                "requires_approval": tool.requires_approval,
            }
            for tool in self.registry.all_tools()
        ]

    async def _store_plan(
        self, session: AsyncSession, task: Task, plan: Plan, *, source: str
    ) -> Task:
        from app.db.models import TaskPlan

        task.plan_version += 1
        task.plan = plan.model_dump()
        session.add(
            TaskPlan(
                id=new_uuid(),
                task_id=task.id,
                version=task.plan_version,
                steps=plan.model_dump()["steps"],
                objective_restated=plan.objective_restated,
                source=source,
            )
        )
        await session.commit()
        await session.refresh(task)
        await self.bus.publish(
            f"task:{task.id}",
            "task.plan",
            {
                "task_id": str(task.id),
                "plan_version": task.plan_version,
                "steps": len(plan.steps),
            },
        )
        return task

    async def _request_plan_approval(
        self, session: AsyncSession, task: Task, plan: Plan
    ) -> None:
        existing = await session.scalar(
            sa.select(ApprovalRequest).where(
                ApprovalRequest.task_id == task.id,
                ApprovalRequest.kind == ApprovalKind.PLAN,
                ApprovalRequest.plan_version == task.plan_version,
                ApprovalRequest.status == ApprovalStatus.PENDING,
            )
        )
        if existing is None:
            session.add(
                ApprovalRequest(
                    id=new_uuid(),
                    task_id=task.id,
                    user_id=task.user_id,
                    kind=ApprovalKind.PLAN,
                    plan_version=task.plan_version,
                    title=f"Approve plan: {task.title}",
                    payload={"objective": task.objective, "plan": plan.model_dump()},
                    expires_at=utcnow() + timedelta(hours=48),
                )
            )
            await session.commit()
        await self._notify(task.user_id, "approval_requested", task)
        await transition(
            session, self.bus, task_id=task.id, to_status=TaskStatus.AWAITING_APPROVAL
        )

    async def _latest_plan_approval(
        self, session: AsyncSession, task: Task
    ) -> ApprovalRequest | None:
        """Latest plan-level approval for the CURRENT plan version (None when
        plan approval was never required, e.g. auto-approval)."""
        task_id = task.id
        plan_version = task.plan_version
        return await session.scalar(
            sa.select(ApprovalRequest)
            .where(
                ApprovalRequest.task_id == task_id,
                ApprovalRequest.kind == ApprovalKind.PLAN,
                ApprovalRequest.plan_version == plan_version,
            )
            .order_by(ApprovalRequest.requested_at.desc())
        )

    async def _plan_already_approved(self, session: AsyncSession, task: Task) -> bool:
        from app.db.models import TaskPlan

        row = await session.scalar(
            sa.select(TaskPlan)
            .where(TaskPlan.task_id == task.id, TaskPlan.version == task.plan_version)
            .order_by(TaskPlan.version.desc())
        )
        return row is not None and row.approved_at is not None

    async def _mark_plan_approved(self, session: AsyncSession, task: Task) -> None:
        from app.db.models import TaskPlan

        await session.execute(
            sa.update(TaskPlan)
            .where(TaskPlan.task_id == task.id, TaskPlan.version == task.plan_version)
            .values(approved_at=utcnow(), approved_by=task.user_id)
        )
        await session.commit()

    # ------------------------------------------------------------ execution
    async def _execute_steps(
        self, session: AsyncSession, task: Task, control: ControlHandle
    ) -> ExecutionOutcome:
        plan = Plan.model_validate(task.plan)
        workspace = self.tool_executor.workspace_manager.ensure_task_workspace(task.id)
        checkpoint: dict[str, Any] = dict(task.checkpoint or {})
        completed: dict[str, Any] = checkpoint.setdefault("completed", {})
        approved_steps: list[str] = checkpoint.setdefault("approved_steps", [])
        usage: dict[str, Any] = dict(task.usage or {})
        budgets = self._budgets_for(task)
        deadline_at = task.deadline_at or (
            utcnow() + timedelta(seconds=budgets["deadline_s"])
        )
        fresh_task = task

        for group in topological_groups(plan):
            await self._check_control(session, fresh_task.id, control)

            runnable = [s for s in group if s.id not in completed]
            if not runnable:
                continue

            if usage.get("steps_executed", 0) + len(runnable) > budgets["max_steps"]:
                await self._fail(session, fresh_task, "budget_exceeded", "step budget exceeded")
                return ExecutionOutcome("failed", "step budget exceeded")
            if utcnow() > deadline_at:
                await self._fail(session, fresh_task, "deadline_exceeded", "task deadline exceeded")
                return ExecutionOutcome("failed", "deadline exceeded")
            if usage.get("tool_calls", 0) > budgets["max_tool_calls"]:
                await self._fail(session, fresh_task, "budget_exceeded", "tool-call budget exceeded")
                return ExecutionOutcome("failed", "tool-call budget exceeded")

            # approval gates for this wave
            gated = [
                s
                for s in runnable
                if self._step_needs_approval(s) and s.id not in approved_steps
            ]
            if gated:
                await self._request_step_approvals(session, fresh_task, gated, plan)
                return ExecutionOutcome(
                    "awaiting_approval", f"{len(gated)} step(s) require approval"
                )

            # bounded parallel execution; each step gets its OWN db session
            semaphore = asyncio.Semaphore(self.settings.task_max_parallel_steps)

            async def _run(step: StepSpec) -> tuple[str, dict[str, Any]]:
                async with semaphore:
                    return step.id, await self._run_step(
                        fresh_task, step, workspace, control, set(approved_steps), usage
                    )

            results = await asyncio.gather(
                *[_run(step) for step in runnable], return_exceptions=True
            )
            for result in results:
                if isinstance(result, BaseException):
                    # cancellation outranks step failure (a killed subprocess
                    # reports a non-zero exit)
                    if control.cancel_event.is_set():
                        await self._cancel(
                            session, fresh_task, "cancelled during execution"
                        )
                        return ExecutionOutcome(
                            "cancelled", "cancelled during step execution"
                        )
                    await self._fail(
                        session,
                        fresh_task,
                        "step_failed",
                        f"{type(result).__name__}: {result}",
                    )
                    return ExecutionOutcome("failed", f"step failed: {result}")
                step_id, output = result
                completed[step_id] = output
                usage["steps_executed"] = usage.get("steps_executed", 0) + 1
                await self.bus.publish(
                    f"task:{fresh_task.id}",
                    "task.step",
                    {"task_id": str(fresh_task.id), "step_id": step_id, "status": "succeeded"},
                )

            # checkpoint after each wave
            checkpoint["completed"] = completed
            checkpoint["approved_steps"] = approved_steps
            await session.execute(
                sa.update(Task)
                .where(Task.id == fresh_task.id)
                .values(checkpoint=checkpoint, usage=usage, updated_at=utcnow())
            )
            await session.commit()

        # ---------------- verification -------------------------------
        fresh_task = await transition(
            session, self.bus, task_id=fresh_task.id, to_status=TaskStatus.VERIFYING
        )
        verifier = CompositeVerifier(workspace=workspace)
        verification = await verifier.verify(session, task=fresh_task, plan=plan)
        if verification.passed:
            artifacts = scan_artifacts(workspace)
            fresh_task = await transition(
                session,
                self.bus,
                task_id=fresh_task.id,
                to_status=TaskStatus.COMPLETED,
                extra_updates={
                    "result_summary": truncate_text(verification.summary, 4000),
                    "artifacts": artifacts,
                    "usage": usage,
                },
            )
            await self._write_outcome_memory(session, fresh_task, success=True)
            await self._notify(fresh_task.user_id, "task_completed", fresh_task)
            return ExecutionOutcome("completed", verification.summary)

        revisions = usage.get("revisions", 0)
        if revisions < budgets["max_plan_revisions"] and self.planner is not None:
            usage["revisions"] = revisions + 1
            fresh_task = await transition(
                session, self.bus, task_id=fresh_task.id, to_status=TaskStatus.RETRYING
            )
            context = PlanContext(
                objective=fresh_task.objective,
                available_tools=self._tool_summaries(),
                additional_notes=[
                    *(fresh_task.additional_context or []),
                    "previous plan failed verification: " + verification.summary,
                ],
            )
            new_plan = await self.planner.replan(
                context, plan, [c.detail for c in verification.checks if not c.passed]
            )
            problems = validate_plan(
                new_plan, registry=self.registry, max_steps=self.settings.task_max_steps
            )
            if problems:
                await self._fail(session, fresh_task, "invalid_replan", "; ".join(problems[:10]))
                return ExecutionOutcome("failed", "revised plan invalid")
            fresh_task = await self._store_plan(
                session, fresh_task, new_plan, source="replan"
            )
            await session.execute(
                sa.update(Task)
                .where(Task.id == fresh_task.id)
                .values(
                    checkpoint={"completed": {}, "approved_steps": []},
                    usage=usage,
                )
            )
            await session.commit()
            # revised plans require fresh approval (new actions)
            if self._approval_required():
                await self._request_plan_approval(session, fresh_task, new_plan)
                return ExecutionOutcome("awaiting_approval", "revised plan awaiting approval")
            fresh_task = await transition(
                session, self.bus, task_id=fresh_task.id, to_status=TaskStatus.READY
            )
            return await self._execute_steps(session, fresh_task, control)

        await self._fail(
            session,
            fresh_task,
            "verification_failed",
            verification.summary,
            extra={"verification": verification.to_dict()},
        )
        await self._notify(fresh_task.user_id, "task_failed", fresh_task)
        return ExecutionOutcome("failed", verification.summary)

    def _budgets_for(self, task: Task) -> dict[str, Any]:
        budgets = dict(task.budgets or {})
        return {
            "max_steps": budgets.get("max_steps", self.settings.task_max_steps),
            "max_tool_calls": budgets.get("max_tool_calls", 200),
            "deadline_s": budgets.get("deadline_s", self.settings.task_default_deadline_s),
            "max_plan_revisions": budgets.get(
                "max_plan_revisions", self.settings.task_max_plan_revisions
            ),
        }

    def _step_needs_approval(self, step: StepSpec) -> bool:
        if step.requires_approval:
            return True
        if step.tool_key is None:
            return False
        tool = self.registry.try_get(step.tool_key)
        if tool is None:
            return True
        if tool.requires_approval:
            return True
        return step.tool_key not in self.settings.tool_auto_approved

    async def _request_step_approvals(
        self, session: AsyncSession, task: Task, steps: list[StepSpec], plan: Plan
    ) -> None:
        by_id = {s.id: s for s in plan.steps}
        for step in steps:
            existing = await session.scalar(
                sa.select(ApprovalRequest).where(
                    ApprovalRequest.task_id == task.id,
                    ApprovalRequest.kind == ApprovalKind.STEP,
                    ApprovalRequest.step_id == step.id,
                    ApprovalRequest.status == ApprovalStatus.PENDING,
                )
            )
            if existing is not None:
                continue
            session.add(
                ApprovalRequest(
                    id=new_uuid(),
                    task_id=task.id,
                    user_id=task.user_id,
                    kind=ApprovalKind.STEP,
                    step_id=step.id,
                    plan_version=task.plan_version,
                    title=f"Approve step '{step.title}'",
                    payload={
                        "step": step.model_dump(),
                        "depends_on": [
                            by_id[dep].title for dep in step.depends_on if dep in by_id
                        ],
                    },
                    expires_at=utcnow() + timedelta(hours=48),
                )
            )
        await session.commit()
        await transition(
            session, self.bus, task_id=task.id, to_status=TaskStatus.AWAITING_APPROVAL
        )
        await self._notify(task.user_id, "approval_requested", task)

    async def _run_step(
        self,
        task: Task,
        step: StepSpec,
        workspace: Path,
        control: ControlHandle,
        approved_steps: set[str],
        usage: dict[str, Any],
    ) -> dict[str, Any]:
        """One step with retries; runs in its OWN database session so waves
        can execute in parallel safely."""
        attempt = 0
        while True:
            attempt += 1
            async with self.database.session() as session:
                execution = TaskExecution(
                    id=new_uuid(),
                    task_id=task.id,
                    step_id=step.id,
                    attempt=attempt,
                    status=ExecutionStatus.RUNNING,
                    input={"tool": step.tool_key, "input": step.input},
                )
                session.add(execution)
                await session.commit()
                started = time.perf_counter()
                try:
                    if step.tool_key is not None:
                        ctx = ToolContext(
                            user_id=task.user_id,
                            task_id=task.id,
                            workspace=workspace,
                            approved_tools=(
                                {step.tool_key} if step.id in approved_steps else set()
                            ),
                            origin="task",
                            control=control,
                        )
                        outcome = await self.tool_executor.execute(
                            tool_key=step.tool_key,
                            input_data=step.input,
                            ctx=ctx,
                            timeout_s=step.timeout_s,
                        )
                        usage["tool_calls"] = usage.get("tool_calls", 0) + 1
                        output = {
                            "tool": step.tool_key,
                            "output": outcome.output,
                            "tool_execution_id": str(outcome.tool_execution_id),
                        }
                    else:
                        from app.gateway.base import InferenceMessage, InferenceRequest

                        request = InferenceRequest(
                            messages=[
                                InferenceMessage(
                                    role="system",
                                    content="You are executing one step of an approved "
                                    "plan. Produce the requested content precisely.",
                                ),
                                InferenceMessage(
                                    role="user",
                                    content=(
                                        f"OBJECTIVE:\n{task.objective}\n\n"
                                        f"STEP {step.id}: {step.title}\n{step.description}\n\n"
                                        "USER NOTES:\n"
                                        + "\n".join(task.additional_context or [])
                                    ),
                                ),
                            ],
                            max_tokens=2048,
                        )
                        response = await self.manager.generate(request)
                        output = {"model_step": True, "content": response.content}
                    execution.status = ExecutionStatus.SUCCEEDED
                    execution.output = _cap(output)
                    execution.duration_ms = int((time.perf_counter() - started) * 1000)
                    execution.finished_at = utcnow()
                    await session.commit()
                    return {"attempt": attempt, "output": _cap(output, _CHECKPOINT_OUTPUT_CAP)}
                except (ToolRetryableError, ModelGatewayError) as exc:
                    execution.status = (
                        ExecutionStatus.CANCELLED
                        if control.cancel_event.is_set()
                        else ExecutionStatus.FAILED
                    )
                    execution.error = {
                        "code": getattr(exc, "code", "retryable"),
                        "message": str(exc),
                    }
                    execution.duration_ms = int((time.perf_counter() - started) * 1000)
                    execution.finished_at = utcnow()
                    await session.commit()
                    if attempt <= step.max_retries:
                        backoff = self.settings.task_retry_backoff_base_s * (
                            2 ** (attempt - 1)
                        )
                        logger.warning(
                            "task_step_retry",
                            task_id=str(task.id),
                            step_id=step.id,
                            attempt=attempt,
                            backoff_s=backoff,
                        )
                        await asyncio.sleep(max(0.0, backoff))
                        continue
                    raise
                except Exception as exc:
                    execution.status = (
                        ExecutionStatus.CANCELLED
                        if control.cancel_event.is_set()
                        else ExecutionStatus.FAILED
                    )
                    execution.error = {
                        "code": "fatal",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                    execution.duration_ms = int((time.perf_counter() - started) * 1000)
                    execution.finished_at = utcnow()
                    await session.commit()
                    raise

    # ------------------------------------------------------------ controls
    async def _check_control(
        self, session: AsyncSession, task_id: UUID, control: ControlHandle
    ) -> None:
        await session.rollback()  # drop any stale read transaction
        fresh = await session.get(Task, task_id)
        if fresh is None:
            raise OrchestrationError("task disappeared during execution")
        if fresh.cancel_requested or control.cancel_event.is_set():
            await self._cancel(session, fresh, "cancelled during execution")
            raise _CancelledSignal()
        if fresh.pause_requested and can_transition(fresh.status, TaskStatus.PAUSED):
            await self._pause(session, fresh)
            raise _PausedSignal()

    async def _cancel(self, session: AsyncSession, task: Task, reason: str) -> None:
        task_id = task.id  # snapshot before rollback expires the instance
        await session.rollback()
        fresh = await session.get(Task, task_id)
        if fresh is None or fresh.status in TERMINAL_TASK_STATUSES:
            return
        if not can_transition(fresh.status, TaskStatus.CANCELLED):
            return
        await session.execute(
            sa.update(TaskExecution)
            .where(
                TaskExecution.task_id == task_id,
                TaskExecution.status == ExecutionStatus.RUNNING,
            )
            .values(status=ExecutionStatus.CANCELLED, finished_at=utcnow())
        )
        await transition(
            session,
            self.bus,
            task_id=task_id,
            to_status=TaskStatus.CANCELLED,
            reason=reason,
            extra_updates={"error": {"code": "cancelled", "message": reason}},
        )
        await self._notify(fresh.user_id, "task_cancelled", fresh)

    async def _pause(self, session: AsyncSession, task: Task) -> None:
        task_id = task.id  # snapshot before rollback expires the instance
        await session.rollback()
        fresh = await session.get(Task, task_id)
        if fresh is None or not can_transition(fresh.status, TaskStatus.PAUSED):
            return
        await transition(
            session, self.bus, task_id=task_id, to_status=TaskStatus.PAUSED, reason="user pause"
        )

    async def _fail(
        self,
        session: AsyncSession,
        task: Task,
        code: str,
        message: str,
        extra: dict | None = None,
    ) -> None:
        task_id = task.id  # snapshot before rollback expires the instance
        await session.rollback()
        fresh = await session.get(Task, task_id)
        if fresh is None or fresh.status in TERMINAL_TASK_STATUSES:
            return
        error: dict[str, Any] = {"code": code, "message": message[:2000]}
        if extra:
            error["extra"] = extra
        if can_transition(fresh.status, TaskStatus.FAILED):
            await transition(
                session,
                self.bus,
                task_id=task_id,
                to_status=TaskStatus.FAILED,
                reason=code,
                extra_updates={"error": error},
            )
        else:
            await session.execute(
                sa.update(Task).where(Task.id == task_id).values(error=error)
            )
            await session.commit()
        await self._write_outcome_memory(session, fresh, success=False)
        await self._notify(fresh.user_id, "task_failed", fresh)

    async def _write_outcome_memory(
        self, session: AsyncSession, task: Task, *, success: bool
    ) -> None:
        try:
            from app.services.memory import record_task_outcome_memory

            if isinstance(task.error, dict):
                summary = task.error.get("message") or task.result_summary or task.title
            else:
                summary = task.result_summary or task.title
            await record_task_outcome_memory(
                session,
                user_id=task.user_id,
                task_id=task.id,
                summary=str(summary)[:2000],
                success=success,
            )
            await session.commit()
        except Exception:  # pragma: no cover - memory must not fail the task
            logger.exception("task_outcome_memory_failed", task_id=str(task.id))

    async def _notify(self, user_id, kind: str, task: Task) -> None:
        from app.db.enums import NotificationKind
        from app.db.models import Notification

        kind_map = {
            "approval_requested": NotificationKind.APPROVAL_REQUESTED,
            "task_completed": NotificationKind.TASK_COMPLETED,
            "task_failed": NotificationKind.TASK_FAILED,
            "task_cancelled": NotificationKind.TASK_CANCELLED,
        }
        try:
            async with self.database.begin() as notify_session:
                notify_session.add(
                    Notification(
                        user_id=user_id,
                        kind=kind_map.get(kind, NotificationKind.SYSTEM),
                        payload={"task_id": str(task.id), "title": task.title},
                    )
                )
        except Exception:  # pragma: no cover
            logger.exception("task_notification_failed", task_id=str(task.id))


def _cap(data: Any, limit: int = 32 * 1024) -> Any:
    try:
        serialized = json.dumps(data, default=str)
        if len(serialized) <= limit:
            return data
        return {"truncated": True, "preview": serialized[:limit]}
    except Exception:  # pragma: no cover
        return {"unserializable": True}


def _default_registry() -> ToolRegistry:
    from app.tools.registry import get_registry, register_builtin_tools

    registry = get_registry()
    if len(registry) == 0:
        register_builtin_tools(registry)
    return registry
