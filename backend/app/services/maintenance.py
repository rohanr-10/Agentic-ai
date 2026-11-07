"""Scheduled maintenance (retention, expiry, hygiene)."""

from __future__ import annotations

from datetime import timedelta

import sqlalchemy as sa
import structlog

from app.core.utils import utcnow
from app.db.models import AuthSession, PasswordResetToken
from app.db.session import Database
from app.events.bus import EventBus

logger = structlog.stdlib.get_logger("app.maintenance")


async def run_maintenance(database: Database, settings) -> dict[str, int]:
    results: dict[str, int] = {}
    now = utcnow()

    # 1. expired refresh sessions
    async with database.begin() as session:
        result = await session.execute(
            sa.delete(AuthSession).where(AuthSession.expires_at < now)
        )
        results["sessions_purged"] = result.rowcount or 0

    # 2. expired password reset tokens
    async with database.begin() as session:
        result = await session.execute(
            sa.delete(PasswordResetToken).where(PasswordResetToken.expires_at < now)
        )
        results["reset_tokens_purged"] = result.rowcount or 0

    # 3. memory retention (TTL + configured retention window)
    async with database.session() as session:
        from app.services.memory import cleanup_expired_memories

        results["memories_expired"] = await cleanup_expired_memories(
            session, ttl_days=settings.memory_default_ttl_days
        )
        await session.commit()

    # 4. audit retention
    async with database.begin() as session:
        from app.services.audit import prune_before

        results["audit_events_pruned"] = await prune_before(
            session, before=now - timedelta(days=settings.audit_retention_days)
        )

    # 5. failed file cleanup (30 days)
    async with database.session() as session:
        from app.services.files import prune_failed_files

        results["failed_files_pruned"] = await prune_failed_files(
            session, older_than_days=30
        )
        await session.commit()

    # 6. stale approval expiry (fails the awaiting task)
    from app.orchestration.recovery import expire_stale_approvals

    bus = EventBus(settings)
    try:
        results["approvals_expired"] = await expire_stale_approvals(database, bus)
    finally:
        await bus.close()

    logger.info("maintenance_completed", **results)
    return results
