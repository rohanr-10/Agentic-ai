"""Uploaded files."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import new_uuid
from app.db.base import Base, TimestampMixin, str_enum
from app.db.enums import FileStatus, ScanStatus


class FileRecord(Base, TimestampMixin):
    """A stored user file plus its asynchronous processing state.

    ``original_filename`` is display-only (sanitised); the actual object is
    addressed by the opaque ``storage_key``.  ``mime_type`` is the value
    verified by content sniffing — never the client-declared type.
    """

    __tablename__ = "files"

    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_uuid)
    user_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.String(300), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    status: Mapped[FileStatus] = mapped_column(
        str_enum(FileStatus), default=FileStatus.PENDING, nullable=False, index=True
    )
    scan_status: Mapped[ScanStatus] = mapped_column(
        str_enum(ScanStatus), default=ScanStatus.SKIPPED, nullable=False
    )
    extracted_text: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    extracted_chars: Mapped[int] = mapped_column(sa.Integer(), default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    conversation_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
