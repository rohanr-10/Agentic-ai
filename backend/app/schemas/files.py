"""File schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.enums import FileStatus, ScanStatus
from app.db.models import FileRecord


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: FileStatus
    scan_status: ScanStatus
    extracted_chars: int
    error: str | None = None
    conversation_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class FileDetailOut(FileOut):
    extracted_text: str | None = Field(
        default=None, description="Extracted text (only when include_text=true)"
    )


class FileUploadAccepted(BaseModel):
    file: FileOut
    processing: bool = Field(description="true when extraction is queued")


class FileProcessStatus(BaseModel):
    status: FileStatus
    extracted_chars: int
    error: str | None = None


def file_to_out(record: FileRecord) -> FileOut:
    return FileOut(
        id=record.id,
        original_filename=record.original_filename,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        status=record.status,
        scan_status=record.scan_status,
        extracted_chars=record.extracted_chars,
        error=record.error,
        conversation_id=record.conversation_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def file_to_detail(record: FileRecord, *, include_text: bool) -> FileDetailOut:
    return FileDetailOut(
        **file_to_out(record).model_dump(),
        extracted_text=record.extracted_text if include_text else None,
    )
