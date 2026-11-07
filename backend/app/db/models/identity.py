"""Users, authentication sessions and password-reset tokens."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import new_uuid, utcnow
from app.db.base import Base, TimestampMixin, str_enum
from app.db.enums import UserRole

if TYPE_CHECKING:
    from app.db.models.chat import Conversation


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(sa.String(320), nullable=False, unique=True, index=True)
    # always lowercased by the service layer
    password_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        str_enum(UserRole), default=UserRole.USER, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(sa.Boolean(), default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)

    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class AuthSession(Base):
    """A refresh-token session.

    Only the SHA-256 digest of the refresh token is stored.  Rotation is
    recorded through ``rotated_from_id``; presenting an already-revoked
    token triggers revocation of every session of the owning user
    (reuse detection — see ``app.services.auth``).
    """

    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    user_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, unique=True
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    rotated_from_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("auth_sessions.id", ondelete="SET NULL"), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    ip: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="sessions", foreign_keys=[user_id])


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    user_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False)
