"""File management service.

Security properties:

* Filenames are sanitised to a basename; storage keys are opaque UUIDs
  (path traversal impossible by construction, plus a storage-side jail).
* MIME is verified from content magic bytes and cross-checked against the
  extension; mismatches are rejected (anti-spoofing).
* Size limits enforced while streaming — files are spooled to a temp file,
  hashed on the fly, and never fully buffered in memory.
* Strict per-user ownership on every read/download/delete.
* Extraction happens asynchronously through the durable job queue.
* Malware scanning is an explicit integration point (``scan_status``).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import tempfile
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import NotFoundError, PayloadTooLargeError, RequestValidationError
from app.core.utils import new_uuid, utcnow
from app.db.enums import FileStatus, ScanStatus
from app.db.models import FileRecord
from app.jobs.queue import JobQueue
from app.services import audit
from app.services.extraction import extract_text, sniff_mime
from app.storage.backends import StorageBackend, get_storage, new_storage_key

logger = structlog.stdlib.get_logger("app.files")

_UPLOAD_CHUNK = 1024 * 1024
_UNSAFE_FILENAME_CHARS = re.compile(r"[^\w.\- ()\[\]]")
_FORBIDDEN_NAMES = {"", ".", ".."}


def sanitize_filename(raw: str | None) -> str:
    """Reduce a client filename to a safe basename."""
    if not raw:
        raise RequestValidationError("filename is required")
    # only the final path component, no traversal
    name = raw.replace("\\", "/").split("/")[-1]
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip()
    if name.lower() in _FORBIDDEN_NAMES:
        raise RequestValidationError("invalid filename")
    if len(name) > 200:
        stem, _, ext = name.rpartition(".")
        if ext and len(ext) <= 10:
            name = name[: 200 - len(ext) - 1] + "." + ext
        else:
            name = name[:200]
    if not name or name.startswith("."):
        raise RequestValidationError("invalid filename")
    return name


def validate_extension(filename: str, allowed_extensions: list[str]) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in allowed_extensions:
        raise RequestValidationError(
            "file extension is not allowed",
            details={"extension": extension, "allowed": sorted(allowed_extensions)},
        )
    return extension


async def store_upload(
    session: AsyncSession,
    settings: Settings,
    storage: StorageBackend,
    *,
    user_id: UUID,
    filename: str,
    stream: AsyncIterator[bytes],
    conversation_id: UUID | None = None,
    request_meta: dict | None = None,
) -> FileRecord:
    """Stream an upload to storage with verification; returns a pending row."""
    filename = sanitize_filename(filename)
    extension = validate_extension(filename, settings.file_allowed_extensions)

    max_size = settings.file_max_size_bytes
    hasher = hashlib.sha256()
    head = bytearray()
    size = 0

    # spool to disk (bounded) — never buffer the whole file in memory
    tmp = tempfile.NamedTemporaryFile(prefix="agp-upload-", delete=False)
    try:
        async for chunk in stream:
            size += len(chunk)
            if size > max_size:
                raise PayloadTooLargeError(
                    "file exceeds the maximum allowed size",
                    details={"max_bytes": max_size},
                )
            hasher.update(chunk)
            if len(head) < 16384:
                head.extend(chunk[: 16384 - len(head)])
            await asyncio.to_thread(tmp.write, chunk)
        await asyncio.to_thread(tmp.close)

        sniffed = sniff_mime(bytes(head), filename, extension=extension)

        key = new_storage_key(user_id)

        async def _replay() -> AsyncIterator[bytes]:
            def _open() -> BinaryIO:
                return open(tmp.name, "rb")

            fh = await asyncio.to_thread(_open)
            try:
                while True:
                    chunk = await asyncio.to_thread(fh.read, _UPLOAD_CHUNK)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await asyncio.to_thread(fh.close)

        written = await storage.save(key, _replay(), expected_size=size)
        if written != size:  # pragma: no cover - defensive
            await storage.delete(key)
            raise RequestValidationError("upload stream ended unexpectedly")
    finally:
        await asyncio.to_thread(lambda: Path(tmp.name).unlink(missing_ok=True))

    scan_status = ScanStatus.PENDING if settings.file_malware_scan_enabled else ScanStatus.SKIPPED

    record = FileRecord(
        id=new_uuid(),
        user_id=user_id,
        original_filename=filename,
        storage_key=key,
        mime_type=sniffed.mime_type,
        size_bytes=size,
        sha256=hasher.hexdigest(),
        status=FileStatus.PENDING,
        scan_status=scan_status,
        conversation_id=conversation_id,
    )
    session.add(record)
    await audit.record(
        session,
        event_type=audit.FILE_UPLOADED,
        user_id=user_id,
        resource_type="file",
        resource_id=record.id,
        details={
            "filename": filename,
            "mime_type": sniffed.mime_type,
            "size_bytes": size,
            "sha256": record.sha256,
        },
        **(request_meta or {}),
    )
    # durable extraction job, atomic with the row insert
    from app.jobs.queue import JobQueue

    await JobQueue().enqueue(
        session,
        kind="file.extract",
        payload={"file_id": str(record.id)},
        idempotency_key=f"file-extract:{record.id}",
        max_attempts=3,
    )
    await session.commit()
    await session.refresh(record)
    return record


async def process_file_extraction(
    session: AsyncSession, settings: Settings, file_id: UUID
) -> dict:
    """Job handler: extract text for a stored file (idempotent)."""
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise NotFoundError("file not found")
    if record.status == FileStatus.READY:
        return {"status": "ready", "chars": record.extracted_chars}  # idempotent replay
    if record.status == FileStatus.QUARANTINED:
        return {"status": "quarantined"}

    record.status = FileStatus.PROCESSING
    record.error = None
    await session.commit()

    storage = get_storage(settings)
    try:
        data = await storage.read_bounded(record.storage_key, settings.file_max_extract_bytes)
        text = await extract_text(
            record.mime_type, data, max_chars=settings.file_extract_text_max_chars
        )
        record.extracted_text = text
        record.extracted_chars = len(text)
        record.status = FileStatus.READY
        await session.commit()
        await _notify_file_processed(session, settings, record, ok=True)
        return {"status": "ready", "chars": record.extracted_chars}
    except Exception as exc:
        await session.rollback()
        record = await session.get(FileRecord, file_id)
        assert record is not None
        record.status = FileStatus.FAILED
        record.error = f"{type(exc).__name__}: {exc}"[:1000]
        await session.commit()
        await _notify_file_processed(session, settings, record, ok=False)
        raise


async def _notify_file_processed(
    session: AsyncSession, settings: Settings, record: FileRecord, *, ok: bool
) -> None:
    from app.db.enums import NotificationKind
    from app.db.models import Notification

    session.add(
        Notification(
            user_id=record.user_id,
            kind=NotificationKind.FILE_READY if ok else NotificationKind.FILE_FAILED,
            payload={
                "file_id": str(record.id),
                "filename": record.original_filename,
                "error": record.error,
            },
        )
    )
    await session.commit()


async def get_file(session: AsyncSession, *, user_id: UUID, file_id: UUID) -> FileRecord:
    record = await session.get(FileRecord, file_id)
    if record is None or record.user_id != user_id:
        # uniform 404: never confirm another user's file ids
        raise NotFoundError("file not found")
    return record


async def get_file_for_model(
    session: AsyncSession, *, user_id: UUID, file_id: UUID
) -> FileRecord | None:
    """Ownership-aware lookup that returns None instead of raising (tool path)."""
    record = await session.get(FileRecord, file_id)
    if record is None or record.user_id != user_id:
        return None
    return record


async def list_files(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 50,
    offset: int = 0,
    status: FileStatus | None = None,
) -> tuple[list[FileRecord], int]:
    query = sa.select(FileRecord).where(FileRecord.user_id == user_id)
    count_query = sa.select(sa.func.count()).select_from(FileRecord).where(
        FileRecord.user_id == user_id
    )
    if status is not None:
        query = query.where(FileRecord.status == status)
        count_query = count_query.where(FileRecord.status == status)
    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(FileRecord.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def delete_file(
    session: AsyncSession,
    settings: Settings,
    storage: StorageBackend,
    *,
    user_id: UUID,
    file_id: UUID,
) -> None:
    record = await get_file(session, user_id=user_id, file_id=file_id)
    await storage.delete(record.storage_key)
    await session.delete(record)
    await audit.record(
        session,
        event_type=audit.FILE_DELETED,
        user_id=user_id,
        resource_type="file",
        resource_id=file_id,
    )
    await session.commit()


async def quarantine_file(session: AsyncSession, record: FileRecord, reason: str) -> None:
    """Malware-scan integration point: quarantine on detection."""
    record.status = FileStatus.QUARANTINED
    record.error = reason[:1000]
    await audit.record(
        session,
        event_type=audit.FILE_QUARANTINED,
        user_id=record.user_id,
        resource_type="file",
        resource_id=record.id,
        severity="critical",
        details={"reason": reason},
    )
    await session.commit()


async def prune_failed_files(session: AsyncSession, *, older_than_days: int) -> int:
    cutoff = utcnow() - timedelta(days=older_than_days)
    rows = (
        (
            await session.execute(
                sa.select(FileRecord).where(
                    FileRecord.status == FileStatus.FAILED, FileRecord.created_at < cutoff
                )
            )
        )
        .scalars()
        .all()
    )
    for record in rows:
        await session.delete(record)
    return len(rows)
