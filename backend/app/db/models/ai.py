"""Model runtime configurations (gateway-managed, never model-specific logic)."""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import new_uuid
from app.db.base import Base, JsonType, TimestampMixin, str_enum
from app.db.enums import ModelAdapterType


class ModelConfig(Base, TimestampMixin):
    """Declarative description of one connectable model runtime.

    ``adapter_type`` selects the gateway adapter; ``endpoint`` / ``api_key``
    are the connection coordinates.  Nothing in the core backend reads these
    fields directly — the gateway resolves them into an adapter instance.
    API responses always mask ``api_key``.
    """

    __tablename__ = "model_configs"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    adapter_type: Mapped[ModelAdapterType] = mapped_column(
        str_enum(ModelAdapterType), nullable=False
    )
    endpoint: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    api_key: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    model_id: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    context_window: Mapped[int] = mapped_column(sa.Integer(), default=8192, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(sa.Integer(), default=2048, nullable=False)
    capabilities: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    parameters: Mapped[dict] = mapped_column(JsonType(), default=dict, nullable=False)
    is_default: Mapped[bool] = mapped_column(sa.Boolean(), default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(sa.Boolean(), default=True, nullable=False)
