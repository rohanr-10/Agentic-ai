"""Memory schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.enums import MemoryCategory, MemoryScope, MemorySource


class MemoryCreateIn(BaseModel):
    category: MemoryCategory
    scope: MemoryScope = MemoryScope.USER
    scope_id: UUID | None = None
    title: str | None = Field(default=None, max_length=200)
    key: str | None = Field(default=None, max_length=150)
    content: str = Field(min_length=1, max_length=32_000)
    source: MemorySource = MemorySource.USER_PROVIDED
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    expires_at: datetime | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class MemoryUpdateIn(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=32_000)
    title: str | None = Field(default=None, max_length=200)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    expires_at: datetime | None = None


class MemoryVerifyIn(BaseModel):
    evidence: str = Field(min_length=3, max_length=2000)


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category: MemoryCategory
    scope: MemoryScope
    scope_id: UUID | None
    title: str | None
    key: str | None
    content: str
    source: MemorySource
    confidence: float
    importance: float
    version: int
    provenance: dict[str, Any]
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemorySearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    categories: list[MemoryCategory] | None = None
    conversation_id: UUID | None = None
    top_k: int = Field(default=5, ge=1, le=50)


class MemorySearchHit(BaseModel):
    record: MemoryOut
    score: float
