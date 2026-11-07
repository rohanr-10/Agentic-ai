"""Platform-level records: audit events, background jobs, notifications."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import new_uuid, utcnow
from app.db.base import Base, JsonType, str_enum
from app.db.enums import AuditSeverity, JobStatus, NotificationKind


class AuditEvent(Base):
    """Append-only security/business audit trail."""

    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    user_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(sa.String(100), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(sa.String(60), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    severity: Mapped[AuditSeverity] = mapped_column(
        str_enum(AuditSeverity), default=AuditSeverity.INFO, nullable=False
    )
    ip: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    request_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    details: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False, index=True)


class BackgroundJob(Base):
    """Durable background job (DB-backed queue).

    Claiming is an atomic compare-and-set on ``status``; workers heartbeat
    ``locked_at`` and a reaper re-queues jobs whose lease expired, so a
    crashed worker never silently swallows a job.  ``idempotency_key``
    deduplicates enqueues of the same logical work.
    """

    __tablename__ = "background_jobs"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(sa.String(60), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus), default=JobStatus.QUEUED, nullable=False, index=True
    )
    priority: Mapped[int] = mapped_column(sa.Integer(), default=5, nullable=False)
    attempts: Mapped[int] = mapped_column(sa.Integer(), default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(sa.Integer(), default=5, nullable=False)
    run_at: Mapped[datetime] = mapped_column(sa.DateTime(), nullable=False, index=True)
    locked_by: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        sa.String(200), nullable=True, unique=True
    )
    result: Mapped[dict | None] = mapped_column(JsonType(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)

    __table_args__ = (sa.Index("ix_jobs_status_runat_priority", "status", "run_at", "priority"),)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    user_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[NotificationKind] = mapped_column(
        str_enum(NotificationKind), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False, index=True)

    __table_args__ = (sa.Index("ix_notifications_user_read", "user_id", "read_at"),)
