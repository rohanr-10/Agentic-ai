"""Declarative base, shared column types and mixins.

Portability rules applied everywhere:

* UUID primary keys via ``sa.Uuid`` (CHAR(32) hex on SQLite, native on PG).
* All timestamps are naive UTC (``TIMESTAMP WITHOUT TIME ZONE``).
* Enum columns are non-native VARCHAR-backed so both dialects match.
* JSON columns use JSONB on PostgreSQL, JSON elsewhere.
* A fixed constraint naming convention keeps Alembic migrations
  deterministic across dialects.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Type

import sqlalchemy as sa
from sqlalchemy import MetaData
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.utils import utcnow

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class JsonType(sa.types.TypeDecorator):
    """JSON column: JSONB on PostgreSQL, JSON elsewhere."""

    impl = sa.JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: sa.Dialect) -> sa.types.TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.JSONB())
        return dialect.type_descriptor(sa.JSON())


def str_enum(enum_cls: Type[enum.Enum], *, length: int = 32) -> sa.Enum:
    """Portable enum column storing the enum *values*."""
    return sa.Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda c: [e.value for e in c],
        validate_strings=True,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(), default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(), default=None, nullable=True
    )
