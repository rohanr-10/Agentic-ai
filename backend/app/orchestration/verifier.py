"""Verification engine.

Rule-based checks operate on REAL execution records (never on model
claims):

* every plan step reached a succeeded execution
* tool executions recorded success
* ``artifact:<path>`` criteria require the file to exist in the workspace
* ``output_contains:<text>`` criteria are matched against the step's
  recorded output
* no budget violation occurred

A model-assisted check is available as ADVISORY ONLY: its output is
recorded as ``model_generated`` evidence and never influences the pass
decision (model self-assessment is not verification).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import utcnow
from app.db.enums import ExecutionStatus, ToolRunStatus
from app.db.models import Task, TaskExecution
from app.orchestration.planner import Plan

logger = structlog.stdlib.get_logger("app.verifier")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class VerificationResult:
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
        }


class CompositeVerifier:
    def __init__(self, workspace: Path | None = None):
        self.workspace = workspace

    async def verify(
        self,
        session: AsyncSession,
        *,
        task: Task,
        plan: Plan,
    ) -> VerificationResult:
        checks: list[CheckResult] = []

        executions = (
            (
                await session.execute(
                    sa.select(TaskExecution)
                    .where(TaskExecution.task_id == task.id)
                    .order_by(TaskExecution.created_at)
                )
            )
            .scalars()
            .all()
        )
        latest_by_step: dict[str, TaskExecution] = {}
        for execution in executions:
            latest_by_step[execution.step_id] = execution

        # 1. every step succeeded
        for step in plan.steps:
            execution = latest_by_step.get(step.id)
            if execution is None:
                checks.append(
                    CheckResult(
                        f"step:{step.id}:executed", False, "no execution recorded"
                    )
                )
            elif execution.status != ExecutionStatus.SUCCEEDED:
                checks.append(
                    CheckResult(
                        f"step:{step.id}:executed",
                        False,
                        f"last attempt status={execution.status.value}",
                    )
                )
            else:
                checks.append(
                    CheckResult(f"step:{step.id}:executed", True, "succeeded")
                )

        # 2. criteria
        for step in plan.steps:
            execution = latest_by_step.get(step.id)
            if execution is None:
                continue
            for criterion in step.success_criteria:
                if criterion.startswith("artifact:"):
                    artifact = criterion[len("artifact:") :].strip()
                    passed = (
                        self.workspace is not None
                        and (self.workspace / artifact).is_file()
                    )
                    checks.append(
                        CheckResult(
                            f"step:{step.id}:criterion",
                            passed,
                            f"artifact '{artifact}' "
                            + ("present" if passed else "missing"),
                        )
                    )
                elif criterion.startswith("output_contains:"):
                    needle = criterion[len("output_contains:") :].strip()
                    haystack = json.dumps(execution.output or {}, default=str)
                    passed = needle.lower() in haystack.lower()
                    checks.append(
                        CheckResult(
                            f"step:{step.id}:criterion",
                            passed,
                            f"output {'contains' if passed else 'does not contain'} {needle!r}",
                        )
                    )
                else:
                    # free-text criteria cannot be machine-checked -> advisory note
                    checks.append(
                        CheckResult(
                            f"step:{step.id}:criterion(unchecked)",
                            True,
                            f"manual criterion accepted without machine check: {criterion}",
                        )
                    )

        # 3. tool execution status cross-check
        failed_tools = [
            execution
            for execution in executions
            if execution.output
            and isinstance(execution.output, dict)
            and execution.output.get("__tool_status") == ToolRunStatus.FAILED.value
        ]
        checks.append(
            CheckResult(
                "tools:all_succeeded",
                not failed_tools,
                f"{len(failed_tools)} failed tool executions",
            )
        )

        # 4. deadline respected
        deadline_ok = task.deadline_at is None or utcnow() <= task.deadline_at
        checks.append(
            CheckResult("budget:deadline", deadline_ok, "deadline respected" if deadline_ok else "deadline exceeded")
        )

        passed = all(check.passed for check in checks)
        failed_count = sum(1 for check in checks if not check.passed)
        return VerificationResult(
            passed=passed,
            checks=checks,
            summary=(
                f"all {len(checks)} checks passed"
                if passed
                else f"{failed_count}/{len(checks)} checks failed"
            ),
        )

    async def model_advise(
        self, manager, task: Task, plan: Plan, result: VerificationResult
    ) -> dict[str, Any] | None:
        """Advisory model review — recorded, never decisive."""
        from app.gateway.base import InferenceMessage, InferenceRequest

        try:
            evidence = json.dumps(
                {
                    "objective": task.objective,
                    "steps": [
                        {"id": s.id, "title": s.title, "tool": s.tool_key}
                        for s in plan.steps
                    ],
                    "rule_checks": result.to_dict()["checks"],
                }
            )
            request = InferenceRequest(
                messages=[
                    InferenceMessage(
                        role="system",
                        content="You review task completion evidence. Answer strictly "
                        "with JSON: {\"complete\": boolean, \"concerns\": [string]}.",
                    ),
                    InferenceMessage(role="user", content=evidence[:16000]),
                ],
                response_schema={
                    "type": "object",
                    "properties": {
                        "complete": {"type": "boolean"},
                        "concerns": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["complete"],
                },
            )
            response = await manager.generate(request)
            return json.loads(response.content)
        except Exception as exc:
            logger.warning("model_advisory_verification_failed", error=str(exc))
            return None


def scan_artifacts(workspace: Path | None) -> dict[str, Any]:
    """Manifest of files the task produced in its workspace."""
    if workspace is None or not workspace.is_dir():
        return {"files": []}
    files = []
    for path in sorted(workspace.rglob("*")):
        if path.is_file() and ".agp_runs" not in path.parts:
            try:
                stat = path.stat()
            except OSError:  # pragma: no cover
                continue
            files.append(
                {
                    "path": path.relative_to(workspace).as_posix(),
                    "size_bytes": stat.st_size,
                }
            )
    return {"files": files}
