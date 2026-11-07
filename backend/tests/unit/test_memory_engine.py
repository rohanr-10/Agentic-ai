"""Unit tests: memory engine (provenance, versioning, recall)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.core.errors import NotFoundError, RequestValidationError
from app.core.utils import new_uuid
from app.db.enums import MemoryCategory, MemoryScope, MemorySource
from app.db.models import MemoryRecord, MemoryVersion, User
from app.services.memory import MemoryEngine, content_hash


@pytest.fixture
async def session(test_settings, migrated):
    from app.db.session import Database

    database = Database(test_settings)
    async with database.session() as s:
        user = User(
            id=new_uuid(),
            email="mem@example.com",
            password_hash="x",
            display_name="Mem",
        )
        s.add(user)
        await s.commit()
        s.current_user = user  # type: ignore[attr-defined]
        yield s
    await database.dispose()


class TestRemember:
    async def test_creates_v1_with_hash_and_provenance(self, session):
        engine = MemoryEngine(session)
        record = await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.SEMANTIC,
            content="User lives in Berlin",
            source=MemorySource.USER_PROVIDED,
            provenance={"origin": "test"},
        )
        assert record.version == 1
        assert record.content_hash == content_hash(MemoryCategory.SEMANTIC, "User lives in Berlin")
        versions = (
            (await session.execute(sa.select(MemoryVersion).where(MemoryVersion.memory_id == record.id)))
            .scalars()
            .all()
        )
        assert len(versions) == 1

    async def test_duplicates_strengthen_instead_of_duplicating(self, session):
        engine = MemoryEngine(session)
        first = await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.SEMANTIC,
            content="likes coffee",
            importance=0.5,
        )
        second = await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.SEMANTIC,
            content="likes coffee",
        )
        assert second.id == first.id
        assert second.importance == pytest.approx(0.55)
        count = (
            await session.execute(
                sa.select(sa.func.count()).select_from(MemoryRecord)
            )
        ).scalar_one()
        assert count == 1

    async def test_scoped_memory_requires_scope_id(self, session):
        engine = MemoryEngine(session)
        with pytest.raises(RequestValidationError):
            await engine.remember(
                user_id=session.current_user.id,
                category=MemoryCategory.CONVERSATIONAL,
                content="x",
                scope=MemoryScope.CONVERSATION,
            )


class TestProvenancePromotion:
    async def test_model_generated_is_never_auto_verified(self, session):
        engine = MemoryEngine(session)
        record = await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.SEMANTIC,
            content="maybe the user likes tea",
            source=MemorySource.MODEL_GENERATED,
        )
        assert record.source == MemorySource.MODEL_GENERATED

    async def test_verify_requires_evidence_and_audits(self, session):
        engine = MemoryEngine(session)
        record = await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.SEMANTIC,
            content="user's birthday is May 1",
            source=MemorySource.MODEL_GENERATED,
        )
        verified = await engine.verify(
            user_id=session.current_user.id,
            memory_id=record.id,
            evidence="User confirmed in chat 2026-10-31",
            decided_by=session.current_user.id,
        )
        assert verified.source == MemorySource.VERIFIED
        assert verified.confidence == 1.0
        assert verified.version == 2
        # audit trail written
        from app.db.models import AuditEvent

        events = (
            (
                await session.execute(
                    sa.select(AuditEvent).where(AuditEvent.event_type == "memory.verified")
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1

    async def test_verify_is_idempotent(self, session):
        engine = MemoryEngine(session)
        record = await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.EPISODIC,
            content="visited Paris",
            source=MemorySource.EXTRACTED,
        )
        first = await engine.verify(
            user_id=session.current_user.id,
            memory_id=record.id,
            evidence="e1",
            decided_by=session.current_user.id,
        )
        second = await engine.verify(
            user_id=session.current_user.id,
            memory_id=record.id,
            evidence="e2",
            decided_by=session.current_user.id,
        )
        assert second.version == first.version


class TestRecall:
    async def test_keyword_recall_ranks_relevant_first(self, session):
        engine = MemoryEngine(session)
        await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.SEMANTIC,
            content="The deployment uses PostgreSQL version 16",
        )
        await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.SEMANTIC,
            content="User enjoys hiking on weekends",
        )
        hits = await engine.recall(
            user_id=session.current_user.id,
            query="which postgresql version is deployed",
            top_k=2,
        )
        assert hits
        assert "PostgreSQL" in hits[0][0].content

    async def test_conversation_scope_isolated(self, session):
        engine = MemoryEngine(session)
        other_conversation = new_uuid()
        my_conversation = new_uuid()
        await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.CONVERSATIONAL,
            content="secret project alpha",
            scope=MemoryScope.CONVERSATION,
            scope_id=other_conversation,
        )
        hits = await engine.recall(
            user_id=session.current_user.id,
            query="secret project alpha",
            conversation_id=my_conversation,
        )
        assert hits == []

    async def test_soft_deleted_records_are_hidden(self, session):
        engine = MemoryEngine(session)
        record = await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.SEMANTIC,
            content="to be forgotten",
        )
        await engine.forget(user_id=session.current_user.id, memory_id=record.id)
        await session.commit()
        with pytest.raises(NotFoundError):
            await engine.get(user_id=session.current_user.id, memory_id=record.id)


class TestIntegrity:
    async def test_tampered_content_is_detected_on_read(self, session):
        engine = MemoryEngine(session)
        record = await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.SEMANTIC,
            content="original content",
        )
        # tamper directly at the storage level
        await session.execute(
            sa.update(MemoryRecord)
            .where(MemoryRecord.id == record.id)
            .values(content="tampered content")
        )
        await session.commit()
        with pytest.raises(NotFoundError):  # integrity failure => treated as missing
            await engine.get(user_id=session.current_user.id, memory_id=record.id)


class TestUpdate:
    async def test_update_creates_new_version(self, session):
        engine = MemoryEngine(session)
        record = await engine.remember(
            user_id=session.current_user.id,
            category=MemoryCategory.PROCEDURAL,
            content="v1 content",
        )
        updated = await engine.update(
            user_id=session.current_user.id,
            memory_id=record.id,
            content="v2 content",
        )
        assert updated.version == 2
        assert updated.content == "v2 content"
        assert updated.content_hash == content_hash(MemoryCategory.PROCEDURAL, "v2 content")
