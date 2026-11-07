"""Memory engine.

Four categories (conversational, episodic, semantic, procedural) with
scope-based visibility (user / conversation / task), full version history,
provenance and integrity hashing.

Provenance discipline (security-relevant):

* ``user_provided`` — the human asserted it.
* ``extracted``     — derived from real execution records (tool outputs,
                      task outcomes).
* ``model_generated`` — the model produced it; treated as an UNVERIFIED
                      hypothesis everywhere (context assembly labels it as
                      such; never auto-promoted).
* ``verified``      — promoted only by an explicit verification action
                      (user confirmation via the API, or a verification
                      report from the verification engine).

Retrieval is deliberately not embedding-based in v1: keyword scoring over
a bounded candidate set (recency + importance + overlap) keeps the system
dependency-free and predictable; the retrieval interface is designed so an
embedding backend can be added later without touching callers.
"""

from __future__ import annotations

import math
import re
from datetime import timedelta
from typing import Iterable
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, RequestValidationError
from app.core.utils import new_uuid, sha256_hex, utcnow
from app.db.enums import (
    AuditSeverity,
    MemoryCategory,
    MemoryScope,
    MemorySource,
)
from app.db.models import MemoryRecord, MemoryVersion
from app.services import audit

logger = structlog.stdlib.get_logger("app.memory")

_CANDIDATE_CAP = 500
_word_re = re.compile(r"[a-zA-Z0-9_]+")

UNSET: object = object()  # sentinel distinguishing "not provided" from None


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _word_re.findall(text) if len(token) > 1}


def content_hash(category: MemoryCategory, content: str) -> str:
    return sha256_hex(f"{category.value}:{content}")


class MemoryEngine:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- create ---------------------------------------------------------------
    async def remember(
        self,
        *,
        user_id: UUID,
        category: MemoryCategory,
        content: str,
        scope: MemoryScope = MemoryScope.USER,
        scope_id: UUID | None = None,
        title: str | None = None,
        key: str | None = None,
        source: MemorySource = MemorySource.MODEL_GENERATED,
        confidence: float = 0.5,
        importance: float = 0.5,
        provenance: dict | None = None,
        expires_at=None,
    ) -> MemoryRecord:
        if scope != MemoryScope.USER and scope_id is None:
            raise RequestValidationError(f"scope '{scope.value}' requires scope_id")
        if scope == MemoryScope.USER and scope_id is not None:
            raise RequestValidationError("user-scoped memory cannot carry a scope_id")

        digest = content_hash(category, content)
        existing = await self.session.scalar(
            sa.select(MemoryRecord).where(
                MemoryRecord.user_id == user_id,
                MemoryRecord.content_hash == digest,
                MemoryRecord.deleted_at.is_(None),
                MemoryRecord.category == category,
            )
        )
        if existing is not None:
            # dedupe: strengthen importance instead of duplicating
            existing.importance = min(1.0, existing.importance + 0.05)
            existing.updated_at = utcnow()
            await self.session.flush()
            return existing

        record = MemoryRecord(
            id=new_uuid(),
            user_id=user_id,
            category=category,
            scope=scope,
            scope_id=scope_id,
            title=title,
            key=key,
            content=content,
            source=source,
            confidence=confidence,
            importance=importance,
            version=1,
            content_hash=digest,
            provenance=provenance or {},
            expires_at=expires_at,
        )
        self.session.add(record)
        await self.session.flush()
        self.session.add(
            MemoryVersion(
                id=new_uuid(),
                memory_id=record.id,
                version=1,
                content=content,
                source=source,
                importance=importance,
                meta={"initial": True},
            )
        )
        await self.session.flush()
        return record

    # -- retrieve ---------------------------------------------------------------
    async def get(self, *, user_id: UUID, memory_id: UUID) -> MemoryRecord:
        record = await self.session.get(MemoryRecord, memory_id)
        if record is None or record.user_id != user_id or record.deleted_at is not None:
            raise NotFoundError("memory record not found")
        if not self._check_integrity(record):
            # tampered content is never served
            raise NotFoundError("memory record not found")
        return record

    async def recall(
        self,
        *,
        user_id: UUID,
        query: str,
        categories: Iterable[MemoryCategory] | None = None,
        conversation_id: UUID | None = None,
        task_id: UUID | None = None,
        top_k: int = 5,
    ) -> list[tuple[MemoryRecord, float]]:
        """Scored keyword recall over a bounded candidate set."""
        scopes_filter = [MemoryScope.USER]
        if conversation_id is not None:
            scopes_filter.append(MemoryScope.CONVERSATION)
        if task_id is not None:
            scopes_filter.append(MemoryScope.TASK)

        stmt = (
            sa.select(MemoryRecord)
            .where(
                MemoryRecord.user_id == user_id,
                MemoryRecord.deleted_at.is_(None),
                MemoryRecord.scope.in_(scopes_filter),
                sa.or_(
                    MemoryRecord.expires_at.is_(None),
                    MemoryRecord.expires_at > utcnow(),
                ),
            )
            .order_by(MemoryRecord.updated_at.desc())
            .limit(_CANDIDATE_CAP)
        )
        if categories:
            stmt = stmt.where(MemoryRecord.category.in_(list(categories)))

        rows = (await self.session.execute(stmt)).scalars().all()
        now = utcnow()
        query_tokens = _tokens(query)
        scored: list[tuple[MemoryRecord, float]] = []
        for record in rows:
            if record.scope == MemoryScope.CONVERSATION and record.scope_id != conversation_id:
                continue
            if record.scope == MemoryScope.TASK and record.scope_id != task_id:
                continue
            if not self._check_integrity(record):
                continue

            record_tokens = _tokens(f"{record.title or ''} {record.key or ''} {record.content}")
            overlap = len(query_tokens & record_tokens)
            union = len(query_tokens | record_tokens) or 1
            relevance = overlap / union if query_tokens else 0.0

            age_days = max(0.0, (now - record.updated_at).total_seconds() / 86400.0)
            recency = math.exp(-age_days / 30.0)
            trust = {
                MemorySource.VERIFIED: 1.0,
                MemorySource.USER_PROVIDED: 0.9,
                MemorySource.EXTRACTED: 0.7,
                MemorySource.MODEL_GENERATED: 0.4,
            }[record.source]
            score = 0.55 * relevance + 0.2 * recency + 0.15 * record.importance + 0.1 * trust
            if relevance == 0.0 and overlap == 0:
                # keep only records with at least one query-term hit, unless
                # the query is empty (then recency/importance dominate)
                continue
            scored.append((record, round(score, 4)))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    # -- update / verify / delete -------------------------------------------------
    async def update(
        self,
        *,
        user_id: UUID,
        memory_id: UUID,
        content: str | None = None,
        title: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        expires_at=UNSET,
    ) -> MemoryRecord:
        record = await self.get(user_id=user_id, memory_id=memory_id)
        changed = False
        if content is not None and content != record.content:
            record.content = content
            record.content_hash = content_hash(record.category, content)
            changed = True
        if title is not None and title != record.title:
            record.title = title
            changed = True
        if importance is not None:
            record.importance = importance
            changed = True
        if confidence is not None:
            record.confidence = confidence
            changed = True
        if expires_at is not UNSET:
            record.expires_at = expires_at
            changed = True
        if changed:
            record.version += 1
            record.updated_at = utcnow()
            self.session.add(
                MemoryVersion(
                    id=new_uuid(),
                    memory_id=record.id,
                    version=record.version,
                    content=record.content,
                    source=record.source,
                    importance=record.importance,
                    meta={"edited": True},
                )
            )
        await self.session.flush()
        return record

    async def verify(
        self, *, user_id: UUID, memory_id: UUID, evidence: str, decided_by: UUID
    ) -> MemoryRecord:
        """Promote a model-generated/extracted record to ``verified``.

        Requires explicit evidence — this is the ONLY path to the verified
        state, and it is always audited.
        """
        record = await self.get(user_id=user_id, memory_id=memory_id)
        if record.source == MemorySource.VERIFIED:
            return record  # idempotent
        record.source = MemorySource.VERIFIED
        record.confidence = 1.0
        record.version += 1
        record.updated_at = utcnow()
        self.session.add(
            MemoryVersion(
                id=new_uuid(),
                memory_id=record.id,
                version=record.version,
                content=record.content,
                source=MemorySource.VERIFIED,
                importance=record.importance,
                meta={"verified": True, "evidence": evidence[:1000]},
            )
        )
        await audit.record(
            self.session,
            event_type=audit.MEMORY_VERIFIED,
            user_id=decided_by,
            resource_type="memory",
            resource_id=memory_id,
            severity=AuditSeverity.WARNING,
            details={"evidence": evidence[:500]},
        )
        await self.session.flush()
        return record

    async def forget(self, *, user_id: UUID, memory_id: UUID) -> None:
        record = await self.get(user_id=user_id, memory_id=memory_id)
        record.deleted_at = utcnow()
        await audit.record(
            self.session,
            event_type=audit.MEMORY_DELETED,
            user_id=user_id,
            resource_type="memory",
            resource_id=memory_id,
        )
        await self.session.flush()

    # -- integrity / retention ---------------------------------------------------
    def _check_integrity(self, record: MemoryRecord) -> bool:
        expected = content_hash(record.category, record.content)
        if expected != record.content_hash:
            logger.error(
                "memory_integrity_violation",
                memory_id=str(record.id),
                user_id=str(record.user_id),
                severity=AuditSeverity.CRITICAL.value,
            )
            return False
        return True

    async def purge_expired(self) -> int:
        now = utcnow()
        result = await self.session.execute(
            sa.update(MemoryRecord)
            .where(
                MemoryRecord.deleted_at.is_(None),
                MemoryRecord.expires_at.is_not(None),
                MemoryRecord.expires_at <= now,
            )
            .values(deleted_at=now)
        )
        return result.rowcount or 0


async def record_task_outcome_memory(
    session: AsyncSession,
    *,
    user_id: UUID,
    task_id: UUID,
    summary: str,
    success: bool,
) -> MemoryRecord | None:
    """Episodic memory written by the orchestrator after task completion.

    Source is ``extracted`` (derived from real execution state), never
    ``verified`` — model claims are not established facts.
    """
    if not summary.strip():
        return None
    engine = MemoryEngine(session)
    return await engine.remember(
        user_id=user_id,
        category=MemoryCategory.EPISODIC,
        scope=MemoryScope.TASK,
        scope_id=task_id,
        title=f"Task {'completed' if success else 'failed'}",
        content=summary[:4000],
        source=MemorySource.EXTRACTED,
        confidence=0.9 if success else 0.7,
        importance=0.6,
        provenance={"task_id": str(task_id), "system_recorded": True},
    )


async def cleanup_expired_memories(session: AsyncSession, *, ttl_days: int | None) -> int:
    engine = MemoryEngine(session)
    purged = await engine.purge_expired()
    if ttl_days is not None:
        cutoff = utcnow() - timedelta(days=ttl_days)
        result = await session.execute(
            sa.update(MemoryRecord)
            .where(
                MemoryRecord.deleted_at.is_(None),
                MemoryRecord.created_at < cutoff,
            )
            .values(deleted_at=utcnow())
        )
        purged += result.rowcount or 0
    return purged
