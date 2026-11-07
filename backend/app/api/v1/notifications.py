"""Notification endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict

from app.api.deps import DbDep, PrincipalDep
from app.db.models import Notification
from app.schemas.common import Page

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    payload: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


@router.get("", response_model=Page[NotificationOut], summary="List notifications")
async def list_notifications(
    principal: PrincipalDep,
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    unread_only: bool = Query(default=False),
):
    base = sa.select(Notification).where(Notification.user_id == principal.user_id)
    count_base = (
        sa.select(sa.func.count())
        .select_from(Notification)
        .where(Notification.user_id == principal.user_id)
    )
    if unread_only:
        base = base.where(Notification.read_at.is_(None))
        count_base = count_base.where(Notification.read_at.is_(None))
    total = (await db.execute(count_base)).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return Page(
        items=[NotificationOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{notification_id}/read",
    status_code=204,
    summary="Mark a notification as read",
)
async def mark_read(notification_id: UUID, principal: PrincipalDep, db: DbDep):
    notification = await db.get(Notification, notification_id)
    if notification is None or notification.user_id != principal.user_id:
        from app.core.errors import NotFoundError

        raise NotFoundError("notification not found")
    from app.core.utils import utcnow

    notification.read_at = utcnow()
    await db.commit()
