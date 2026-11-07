"""Tool definitions (registry mirror) and tool execution records."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import new_uuid, utcnow
from app.db.base import Base, JsonType, TimestampMixin, str_enum
from app.db.enums import ToolRunStatus


class ToolDefinition(Base, TimestampMixin):
    """Database mirror of a registered tool.

    The in-code registry (``app.tools.registry``) is the source of truth for
    schemas and behaviour; this row records deployment-level state
    (enabled/disabled, description shown to users and to the model).
    Seeded idempotently at startup.
    """

    __tablename__ = "tool_definitions"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    key: Mapped[str] = mapped_column(sa.String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.String(150), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    category: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    input_schema: Mapped[dict] = mapped_column(JsonType(), nullable=False)
    output_schema: Mapped[dict] = mapped_column(JsonType(), nullable=False)
    permissions: Mapped[list] = mapped_column(JsonType(), default=list, nullable=False)
    default_timeout_s: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    max_timeout_s: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    is_builtin: Mapped[bool] = mapped_column(sa.Boolean(), default=True, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(sa.Boolean(), default=True, nullable=False)


class ToolExecution(Base):
    """Audit record of a single tool invocation."""

    __tablename__ = "tool_executions"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    user_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    execution_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("task_executions.id", ondelete="CASCADE"), nullable=True
    )
    tool_key: Mapped[str] = mapped_column(sa.String(100), nullable=False, index=True)
    input: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    output: Mapped[dict | None] = mapped_column(JsonType(), nullable=True)
    status: Mapped[ToolRunStatus] = mapped_column(
        str_enum(ToolRunStatus), default=ToolRunStatus.RUNNING, nullable=False
    )
    error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    sandbox: Mapped[str] = mapped_column(sa.String(20), default="none", nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False, index=True)
