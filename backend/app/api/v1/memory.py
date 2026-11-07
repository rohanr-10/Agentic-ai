"""Memory endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbDep, PrincipalDep
from app.schemas.common import Page
from app.schemas.memory import (
    MemoryCreateIn,
    MemoryOut,
    MemorySearchHit,
    MemorySearchIn,
    MemoryUpdateIn,
    MemoryVerifyIn,
)
from app.services.memory import MemoryEngine, UNSET

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED, summary="Store a memory")
async def create_memory(
    payload: MemoryCreateIn, principal: PrincipalDep, db: DbDep
):
    engine = MemoryEngine(db)
    record = await engine.remember(
        user_id=principal.user_id,
        category=payload.category,
        content=payload.content,
        scope=payload.scope,
        scope_id=payload.scope_id,
        title=payload.title,
        key=payload.key,
        source=payload.source,
        confidence=payload.confidence,
        importance=payload.importance,
        provenance=payload.provenance,
        expires_at=payload.expires_at,
    )
    await db.commit()
    await db.refresh(record)
    return MemoryOut.model_validate(record)


@router.get("", response_model=Page[MemoryOut], summary="List memories")
async def list_memory(
    principal: PrincipalDep,
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    import sqlalchemy as sa

    from app.db.models import MemoryRecord

    base = sa.select(MemoryRecord).where(
        MemoryRecord.user_id == principal.user_id, MemoryRecord.deleted_at.is_(None)
    )
    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(MemoryRecord)
        .where(MemoryRecord.user_id == principal.user_id, MemoryRecord.deleted_at.is_(None))
    )
    rows = (
        (await db.execute(base.order_by(MemoryRecord.updated_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return Page(
        items=[MemoryOut.model_validate(r) for r in rows], total=count or 0, limit=limit, offset=offset
    )


@router.post("/search", response_model=list[MemorySearchHit], summary="Scored memory recall")
async def search_memory(payload: MemorySearchIn, principal: PrincipalDep, db: DbDep):
    engine = MemoryEngine(db)
    hits = await engine.recall(
        user_id=principal.user_id,
        query=payload.query,
        categories=payload.categories,
        conversation_id=payload.conversation_id,
        top_k=payload.top_k,
    )
    return [
        MemorySearchHit(record=MemoryOut.model_validate(record), score=score)
        for record, score in hits
    ]


@router.get("/{memory_id}", response_model=MemoryOut, summary="Get a memory")
async def get_memory(memory_id: UUID, principal: PrincipalDep, db: DbDep):
    engine = MemoryEngine(db)
    record = await engine.get(user_id=principal.user_id, memory_id=memory_id)
    return MemoryOut.model_validate(record)


@router.patch("/{memory_id}", response_model=MemoryOut, summary="Update a memory (new version)")
async def update_memory(
    memory_id: UUID, payload: MemoryUpdateIn, principal: PrincipalDep, db: DbDep
):
    engine = MemoryEngine(db)
    changes = payload.model_dump(exclude_unset=True)
    record = await engine.update(user_id=principal.user_id, memory_id=memory_id, **changes)
    await db.commit()
    await db.refresh(record)
    return MemoryOut.model_validate(record)


@router.post(
    "/{memory_id}/verify",
    response_model=MemoryOut,
    summary="Promote a memory to verified (explicit evidence required)",
)
async def verify_memory(
    memory_id: UUID, payload: MemoryVerifyIn, principal: PrincipalDep, db: DbDep
):
    engine = MemoryEngine(db)
    record = await engine.verify(
        user_id=principal.user_id,
        memory_id=memory_id,
        evidence=payload.evidence,
        decided_by=principal.user_id,
    )
    await db.commit()
    await db.refresh(record)
    return MemoryOut.model_validate(record)


@router.delete("/{memory_id}", status_code=204, summary="Forget a memory (soft delete)")
async def forget_memory(memory_id: UUID, principal: PrincipalDep, db: DbDep):
    engine = MemoryEngine(db)
    await engine.forget(user_id=principal.user_id, memory_id=memory_id)
    await db.commit()
