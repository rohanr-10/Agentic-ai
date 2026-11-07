"""Shared API schema primitives."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:  # pragma: no cover - convenience only
        return self.offset + len(self.items) < self.total


class ErrorBody(BaseModel):
    code: str
    message: str
    details: object = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
    trace_id: str | None = None


class OkStatus(BaseModel):
    status: str = "ok"
