"""Memory records and their immutable version history."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import new_uuid, utcnow
from app.db.base import Base, JsonType, SoftDeleteMixin, TimestampMixin, str_enum
from app.db.enums import MemoryCategory, MemoryScope, MemorySource

if TYPE_CHECKING:
    from app.db.models.identity import User


class MemoryRecord(Base, TimestampMixin, SoftDeleteMixin):
    """One memory item.

    Provenance discipline:
    * ``source`` records how the content came to exist.  Content produced by
      the model is ``model_generated`` and is *never* promoted to
      ``verified`` automatically — promotion requires an explicit
      verification action (user confirmation or a verification report).
    * ``content_hash`` lets readers detect tampering with stored content.
    * ``version`` + the ``memory_versions`` table keep full history.
    """

    __tablename__ = "memory_records"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    user_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[MemoryCategory] = mapped_column(
        str_enum(MemoryCategory), nullable=False, index=True
    )
    scope: Mapped[MemoryScope] = mapped_column(
        str_enum(MemoryScope), default=MemoryScope.USER, nullable=False
    )
    scope_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    content: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    key: Mapped[str | None] = mapped_column(sa.String(150), nullable=True)
    source: Mapped[MemorySource] = mapped_column(
        str_enum(MemorySource), default=MemorySource.MODEL_GENERATED, nullable=False, index=True
    )
    confidence: Mapped[float] = mapped_column(sa.Float(), default=0.5, nullable=False)
    importance: Mapped[float] = mapped_column(sa.Float(), default=0.5, nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer(), default=1, nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    provenance: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    versions: Mapped[list["MemoryVersion"]] = relationship(
        back_populates="record", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        sa.Index("ix_memory_user_category", "user_id", "category"),
        sa.Index("ix_memory_scope", "user_id", "scope", "scope_id"),
    )


class MemoryVersion(Base):
    __tablename__ = "memory_versions"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    memory_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("memory_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    content: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    source: Mapped[MemorySource] = mapped_column(str_enum(MemorySource), nullable=False)
    importance: Mapped[float] = mapped_column(sa.Float(), nullable=False)
    meta: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False)

    record: Mapped[MemoryRecord] = relationship(back_populates="versions")
