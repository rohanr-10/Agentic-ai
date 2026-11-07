"""Model configuration schemas (API-masked)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.utils import mask_secret
from app.db.enums import ModelAdapterType
from app.db.models import ModelConfig


class ModelConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_name: str
    adapter_type: ModelAdapterType
    endpoint: str | None = None
    api_key: str | None = None  # always masked
    model_id: str | None = None
    context_window: int
    max_output_tokens: int
    capabilities: dict[str, Any]
    parameters: dict[str, Any]
    is_default: bool
    is_enabled: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_config(cls, config: ModelConfig) -> "ModelConfigOut":
        return cls(
            id=config.id,
            name=config.name,
            display_name=config.display_name,
            adapter_type=config.adapter_type,
            endpoint=config.endpoint,
            api_key=mask_secret(config.api_key) if config.api_key else None,
            model_id=config.model_id,
            context_window=config.context_window,
            max_output_tokens=config.max_output_tokens,
            capabilities=dict(config.capabilities or {}),
            parameters=_sanitize_parameters(config.parameters),
            is_default=config.is_default,
            is_enabled=config.is_enabled,
            created_at=config.created_at,
            updated_at=config.updated_at,
        )


def _sanitize_parameters(parameters: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(parameters or {})
    params.pop("script", None)  # internal test scripting is not API-visible
    return params


class ModelConfigCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    adapter_type: ModelAdapterType
    endpoint: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    model_id: str | None = Field(default=None, max_length=200)
    context_window: int = Field(default=8192, ge=256, le=2_000_000)
    max_output_tokens: int = Field(default=2048, ge=1, le=1_000_000)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    is_enabled: bool = True


class ModelConfigUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    endpoint: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    model_id: str | None = Field(default=None, max_length=200)
    context_window: int | None = Field(default=None, ge=256, le=2_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    capabilities: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None
    is_default: bool | None = None
    is_enabled: bool | None = None


class ModelHealthOut(BaseModel):
    model_id: UUID
    name: str
    healthy: bool
    detail: str
    checked_at: str
