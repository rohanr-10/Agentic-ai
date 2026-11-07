"""Conversations, messages and message attachments."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import new_uuid, utcnow
from app.db.base import Base, JsonType, TimestampMixin, str_enum
from app.db.enums import ConversationStatus, MessageRole, OperatingMode

if TYPE_CHECKING:
    from app.db.models.files import FileRecord
    from app.db.models.identity import User


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    user_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False, default="New conversation")
    status: Mapped[ConversationStatus] = mapped_column(
        str_enum(ConversationStatus), default=ConversationStatus.ACTIVE, nullable=False, index=True
    )
    mode: Mapped[OperatingMode] = mapped_column(
        str_enum(OperatingMode), default=OperatingMode.CONVERSATIONAL, nullable=False
    )
    system_prompt: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    model_config_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True
    )
    meta: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)

    user: Mapped["User"] = relationship(back_populates="conversations", foreign_keys=[user_id])
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.seq",
    )

    __table_args__ = (sa.Index("ix_conversations_user_status", "user_id", "status"),)


class Message(Base):
    """A single message with provenance.

    ``role`` distinguishes user / assistant / system / tool payloads.
    Tool calls made by the assistant are stored on the assistant message's
    ``meta``; tool results live on ``role=tool`` rows referencing
    ``tool_call_id``.  Regeneration never destroys history: the previous
    assistant message is flagged ``is_active=False`` and the replacement
    points at the same user message via ``parent_message_id``.
    """

    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    conversation_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    role: Mapped[MessageRole] = mapped_column(str_enum(MessageRole), nullable=False)
    content: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="")
    client_request_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    parent_message_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(sa.Boolean(), default=True, nullable=False)
    model_config_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True
    )
    token_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    meta: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        sa.UniqueConstraint("conversation_id", "seq", name="uq_messages_conversation_seq"),
        sa.UniqueConstraint(
            "conversation_id", "client_request_id", name="uq_messages_client_request"
        ),
        sa.Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )


class Attachment(Base):
    __tablename__ = "attachments"

    message_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    file_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow, nullable=False)

    message: Mapped[Message] = relationship(back_populates="attachments")
    file: Mapped["FileRecord"] = relationship()
