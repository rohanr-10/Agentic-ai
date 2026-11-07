"""Task, approval and execution schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.enums import ApprovalKind, ApprovalStatus, ExecutionStatus, OperatingMode, TaskStatus


class TaskBudgetsIn(BaseModel):
    max_steps: int | None = Field(default=None, ge=1, le=200)
    max_tool_calls: int | None = Field(default=None, ge=1, le=10_000)
    deadline_s: int | None = Field(default=None, ge=10, le=86_400)
    max_plan_revisions: int | None = Field(default=None, ge=0, le=5)


class TaskCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20_000)
    conversation_id: UUID | None = None
    mode: OperatingMode = OperatingMode.AUTONOMOUS
    priority: int = Field(default=5, ge=1, le=9)
    plan: dict[str, Any] | None = Field(
        default=None, description="Optional client-provided plan (still requires approval)"
    )
    budgets: TaskBudgetsIn = Field(default_factory=TaskBudgetsIn)
    auto_start: bool = True


class TaskNoteIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    request_replan: bool = Field(
        default=False, description="Ask the orchestrator to revise the plan with this note"
    )


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    objective: str
    status: TaskStatus
    mode: OperatingMode
    conversation_id: UUID | None
    priority: int
    version: int
    plan_version: int
    usage: dict[str, Any]
    budgets: dict[str, Any]
    cancel_requested: bool
    pause_requested: bool
    deadline_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    result_summary: str | None
    artifacts: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class TaskDetailOut(TaskOut):
    plan: dict[str, Any] | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    additional_context: list[Any] = Field(default_factory=list)


class TaskExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    step_id: str
    attempt: int
    status: ExecutionStatus
    input: dict[str, Any]
    output: dict[str, Any] | None
    error: dict[str, Any] | None
    duration_ms: int | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    kind: ApprovalKind
    step_id: str | None
    plan_version: int | None
    title: str
    payload: dict[str, Any]
    status: ApprovalStatus
    reason: str | None
    requested_at: datetime
    decided_at: datetime | None
    expires_at: datetime | None


class ApprovalDecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reason: str | None = Field(default=None, max_length=2000)
