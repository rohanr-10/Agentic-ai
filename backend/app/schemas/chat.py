"""Conversation and message schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.enums import ConversationStatus, MessageRole, OperatingMode


class ConversationCreateIn(BaseModel):
    title: str = Field(default="New conversation", max_length=200)
    mode: OperatingMode = OperatingMode.CONVERSATIONAL
    system_prompt: str | None = Field(default=None, max_length=8000)
    model_config_id: UUID | None = None


class ConversationUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    system_prompt: str | None = Field(default=None, max_length=8000)
    mode: OperatingMode | None = None
    model_config_id: UUID | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    status: ConversationStatus
    mode: OperatingMode
    system_prompt: str | None
    model_config_id: UUID | None
    message_count: int = 0
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AttachmentOut(BaseModel):
    file_id: UUID
    filename: str
    mime_type: str
    size_bytes: int


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    seq: int
    role: MessageRole
    content: str
    parent_message_id: UUID | None
    is_active: bool
    token_count: int | None
    tool_call_id: str | None
    tool_name: str | None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    edited_at: datetime | None
    attachments: list[AttachmentOut] = Field(default_factory=list)


class MessageCreateIn(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    attachments: list[UUID] = Field(default_factory=list, max_length=10)
    client_request_id: str | None = Field(default=None, max_length=128)
    model_config_id: UUID | None = None


class MessageEditIn(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class ChatResponse(BaseModel):
    """Result of a non-streaming send."""

    user_message: MessageOut
    assistant_message: MessageOut
    tool_messages: list[MessageOut] = Field(default_factory=list)


class SearchHit(BaseModel):
    message_id: UUID
    seq: int
    role: MessageRole
    content_snippet: str
    created_at: datetime
