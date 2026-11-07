"""File endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import DbDep, PrincipalDep, SettingsDep, StorageDep, get_database, rate_limit
from app.core.errors import NotFoundError
from app.db.enums import FileStatus
from app.schemas.common import Page
from app.schemas.files import FileDetailOut, FileOut, file_to_detail, file_to_out
from app.services import files as file_service
from app.storage.backends import get_storage

router = APIRouter(prefix="/files", tags=["files"])


@router.post(
    "",
    response_model=FileOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("file-upload"))],
    summary="Upload a file (multipart); extraction is queued asynchronously",
)
async def upload_file(
    request: Request,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    storage: StorageDep,
    file: UploadFile,
    conversation_id: UUID | None = Query(default=None),
):
    async def stream():
        while chunk := await file.read(1024 * 1024):
            yield chunk

    record = await file_service.store_upload(
        db,
        settings,
        storage,
        user_id=principal.user_id,
        filename=file.filename,
        stream=stream(),
        conversation_id=conversation_id,
        request_meta={"ip": request.client.host if request.client else None},
    )
    return file_to_out(record)


@router.get("", response_model=Page[FileOut], summary="List uploaded files")
async def list_files(
    principal: PrincipalDep,
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: FileStatus | None = Query(default=None, alias="status"),
):
    rows, total = await file_service.list_files(
        db,
        user_id=principal.user_id,
        limit=limit,
        offset=offset,
        status=status_filter,
    )
    return Page(items=[file_to_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{file_id}", response_model=FileDetailOut, summary="Get file metadata")
async def get_file(
    file_id: UUID,
    principal: PrincipalDep,
    db: DbDep,
    include_text: bool = Query(default=False),
):
    record = await file_service.get_file(db, user_id=principal.user_id, file_id=file_id)
    return file_to_detail(record, include_text=include_text)


@router.get(
    "/{file_id}/download",
    summary="Download file content (ownership enforced)",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_file(
    file_id: UUID,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
):
    record = await file_service.get_file(db, user_id=principal.user_id, file_id=file_id)
    storage = get_storage(settings)

    async def content():
        async for chunk in storage.read(record.storage_key):
            yield chunk

    return StreamingResponse(
        content(),
        media_type=record.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{_safe_ascii(record.original_filename)}"',
            "Content-Length": str(record.size_bytes),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{file_id}", status_code=204, summary="Delete a file")
async def delete_file(
    file_id: UUID,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    storage: StorageDep,
):
    await file_service.delete_file(
        db, settings, storage, user_id=principal.user_id, file_id=file_id
    )


def _safe_ascii(name: str) -> str:
    return name.encode("ascii", errors="replace").decode().replace('"', "'")
