"""Autonomous tasks, plans, step executions and approval requests."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import new_uuid, utcnow
from app.db.base import Base, JsonType, TimestampMixin, str_enum
from app.db.enums import (
    ApprovalKind,
    ApprovalStatus,
    ExecutionStatus,
    OperatingMode,
    TaskStatus,
)

if TYPE_CHECKING:
    from app.db.models.identity import User


class Task(Base, TimestampMixin):
    """An autonomous objective owned by a user.

    ``version`` is an optimistic-concurrency counter: every lifecycle
    transition issues ``UPDATE ... WHERE id = :id AND version = :version``
    and treats zero affected rows as a conflict.  This makes state
    transitions safe under concurrent API/worker access on both SQLite and
    PostgreSQL without relying on ``SELECT .. FOR UPDATE``.

    ``checkpoint`` holds resumable progress (completed steps, outputs,
    approvals) so a crashed worker's successor can continue without
    re-running finished work.
    """

    __tablename__ = "tasks"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    user_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    objective: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    mode: Mapped[OperatingMode] = mapped_column(
        str_enum(OperatingMode), default=OperatingMode.AUTONOMOUS, nullable=False
    )
    status: Mapped[TaskStatus] = mapped_column(
        str_enum(TaskStatus), default=TaskStatus.CREATED, nullable=False, index=True
    )
    priority: Mapped[int] = mapped_column(sa.Integer(), default=5, nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer(), default=1, nullable=False)

    plan: Mapped[dict | None] = mapped_column(JsonType(), nullable=True)
    plan_version: Mapped[int] = mapped_column(sa.Integer(), default=0, nullable=False)
    checkpoint: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)

    budgets: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    usage: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    additional_context: Mapped[list] = mapped_column(JsonType(), default=list, nullable=False)

    cancel_requested: Mapped[bool] = mapped_column(sa.Boolean(), default=False, nullable=False)
    pause_requested: Mapped[bool] = mapped_column(sa.Boolean(), default=False, nullable=False)

    deadline_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    artifacts: Mapped[dict | None] = mapped_column(JsonType(), nullable=True)
    error: Mapped[dict | None] = mapped_column(JsonType(), nullable=True)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])

    __table_args__ = (sa.Index("ix_tasks_user_status", "user_id", "status"),)


class TaskPlan(Base):
    """Immutable snapshot of a plan version (audit + approval payload)."""

    __tablename__ = "task_plans"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    task_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    steps: Mapped[list] = mapped_column(JsonType(), nullable=False)
    objective_restated: Mapped[str] = mapped_column(sa.Text(), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(sa.String(32), default="model", nullable=False)

    __table_args__ = (sa.UniqueConstraint("task_id", "version", name="uq_task_plans_version"),)


class TaskExecution(Base):
    """One attempt of one plan step."""

    __tablename__ = "task_executions"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    task_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(sa.Integer(), default=1, nullable=False)
    status: Mapped[ExecutionStatus] = mapped_column(
        str_enum(ExecutionStatus), default=ExecutionStatus.PENDING, nullable=False
    )
    input: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    output: Mapped[dict | None] = mapped_column(JsonType(), nullable=True)
    error: Mapped[dict | None] = mapped_column(JsonType(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False)

    __table_args__ = (sa.Index("ix_task_executions_task_step", "task_id", "step_id"),)


class ApprovalRequest(Base):
    """Human-in-the-loop approval for a plan or a sensitive step."""

    __tablename__ = "approval_requests"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    task_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ApprovalKind] = mapped_column(str_enum(ApprovalKind), nullable=False)
    step_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    plan_version: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        str_enum(ApprovalStatus), default=ApprovalStatus.PENDING, nullable=False, index=True
    )
    reason: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
