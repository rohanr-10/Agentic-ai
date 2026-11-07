"""Audit trail service — append-only record of security-relevant events."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import utcnow
from app.db.enums import AuditSeverity
from app.db.models import AuditEvent

logger = structlog.stdlib.get_logger("app.audit")

# Well-known event types (documented in docs/SECURITY.md)
AUTH_REGISTER = "auth.register"
AUTH_LOGIN = "auth.login"
AUTH_LOGIN_FAILED = "auth.login_failed"
AUTH_LOGOUT = "auth.logout"
AUTH_REFRESH = "auth.refresh"
AUTH_REFRESH_REUSE = "auth.refresh_reuse_detected"
AUTH_PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"
AUTH_PASSWORD_RESET_COMPLETED = "auth.password_reset_completed"
AUTH_PASSWORD_CHANGED = "auth.password_changed"
AUTH_SESSIONS_REVOKED = "auth.sessions_revoked"

USER_UPDATED = "user.updated"

CONVERSATION_CREATED = "conversation.created"
CONVERSATION_UPDATED = "conversation.updated"
CONVERSATION_DELETED = "conversation.deleted"

FILE_UPLOADED = "file.uploaded"
FILE_DOWNLOADED = "file.downloaded"
FILE_DELETED = "file.deleted"
FILE_QUARANTINED = "file.quarantined"

TASK_CREATED = "task.created"
TASK_TRANSITION = "task.transition"
TASK_CANCELLED = "task.cancelled"
TASK_PAUSED = "task.paused"
TASK_RESUMED = "task.resumed"

APPROVAL_DECIDED = "approval.decided"

TOOL_EXECUTED = "tool.executed"
TOOL_DENIED = "tool.denied"

ACCESS_DENIED = "access.denied"

MEMORY_VERIFIED = "memory.verified"
MEMORY_DELETED = "memory.deleted"


async def record(
    session: AsyncSession,
    *,
    event_type: str,
    user_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | UUID | None = None,
    severity: AuditSeverity = AuditSeverity.INFO,
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit event.  The caller owns the transaction."""
    event = AuditEvent(
        event_type=event_type,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        severity=severity,
        ip=ip,
        user_agent=user_agent,
        request_id=request_id,
        details=details or {},
    )
    session.add(event)
    if severity == AuditSeverity.CRITICAL:
        logger.error(
            "audit_critical_event",
            event_type=event_type,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
        )
    return event


async def list_events(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditEvent], int]:
    query = select(AuditEvent).order_by(AuditEvent.created_at.desc())
    count_query = select(func.count()).select_from(AuditEvent)
    if user_id is not None:
        query = query.where(AuditEvent.user_id == user_id)
        count_query = count_query.where(AuditEvent.user_id == user_id)
    if event_type:
        query = query.where(AuditEvent.event_type == event_type)
        count_query = count_query.where(AuditEvent.event_type == event_type)
    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (await session.execute(query.limit(limit).offset(offset))).scalars().all()
    )
    return list(rows), total


async def prune_before(session: AsyncSession, *, before: object) -> int:
    """Retention: delete audit events older than ``before`` (naive UTC)."""
    result = await session.execute(
        AuditEvent.__table__.delete().where(AuditEvent.created_at < before)
    )
    return result.rowcount or 0


def _utcnow() -> object:  # small helper used by maintenance jobs
    return utcnow()
