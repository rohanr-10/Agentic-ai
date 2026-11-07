"""Document and data analysis tools (operate on user-owned files)."""

from __future__ import annotations

import asyncio
import csv
import io
import statistics
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.errors import NotFoundError, RequestValidationError, ToolExecutionError
from app.db.enums import FileStatus
from app.storage.backends import get_storage
from app.tools.base import Tool, ToolContext
from app.tools.sandbox import resolve_in_root


class DocumentExtractInput(BaseModel):
    file_id: UUID
    max_chars: int = Field(default=20000, ge=100, le=200_000)


class DocumentExtractOutput(BaseModel):
    file_id: UUID
    filename: str
    mime_type: str
    text: str
    chars: int
    truncated: bool


class DocumentExtractTool(Tool):
    key = "document.extract"
    name = "Extract document text"
    description = "Extract text content from a previously uploaded user file (PDF, DOCX, XLSX, text)."
    category = "documents"
    permissions: ClassVar[list[str]] = ["files:read"]
    InputModel = DocumentExtractInput
    OutputModel = DocumentExtractOutput
    default_timeout_s = 60
    max_timeout_s = 300
    sandboxed: ClassVar[bool] = False
    allowed_in_chat: ClassVar[bool] = True

    async def run(self, ctx: ToolContext, raw_input: dict[str, Any]) -> dict[str, Any]:
        from app.services.files import get_file_for_model

        db_session = ctx.extra.get("db_session")
        if db_session is None:
            raise ToolExecutionError("document.extract requires a database session")

        record = await get_file_for_model(
            db_session, user_id=ctx.user_id, file_id=raw_input["file_id"]
        )
        if record is None:
            raise NotFoundError("file not found (or not owned by you)")
        if record.status == FileStatus.QUARANTINED:
            raise ToolExecutionError("file is quarantined and cannot be read")
        if record.status != FileStatus.READY or not record.extracted_text:
            raise ToolExecutionError(
                "file has not finished processing yet",
                details={"status": record.status.value},
            )

        settings: Settings = ctx.extra["settings"]
        text = record.extracted_text or ""
        truncated = len(text) > raw_input["max_chars"]
        return {
            "file_id": str(record.id),
            "filename": record.original_filename,
            "mime_type": record.mime_type,
            "text": text[: raw_input["max_chars"]],
            "chars": len(text),
            "truncated": truncated,
        }


class DataAnalyzeCsvInput(BaseModel):
    path: str | None = Field(
        default=None, description="CSV path inside the task workspace"
    )
    file_id: UUID | None = Field(default=None, description="Uploaded file id")
    max_rows: int = Field(default=10000, ge=1, le=100_000)


class ColumnStats(BaseModel):
    name: str
    inferred_type: str
    non_null: int
    missing: int
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    stdev: float | None = None
    unique: int | None = None


class DataAnalyzeCsvOutput(BaseModel):
    rows: int
    truncated: bool
    columns: list[ColumnStats]


class DataAnalyzeCsvTool(Tool):
    key = "data.analyze_csv"
    name = "Analyze CSV data"
    description = "Compute per-column statistics for a CSV file (workspace path or uploaded file)."
    category = "data"
    permissions: ClassVar[list[str]] = ["data:analyze"]
    InputModel = DataAnalyzeCsvInput
    OutputModel = DataAnalyzeCsvOutput
    default_timeout_s = 60
    max_timeout_s = 300
    sandboxed: ClassVar[bool] = False  # may run on uploaded files without a workspace
    allowed_in_chat: ClassVar[bool] = True

    async def run(self, ctx: ToolContext, raw_input: dict[str, Any]) -> dict[str, Any]:
        max_rows = raw_input["max_rows"]
        if raw_input.get("file_id") is not None:
            from app.services.files import get_file_for_model

            db_session = ctx.extra.get("db_session")
            if db_session is None:
                raise ToolExecutionError("data.analyze_csv requires a database session")
            record = await get_file_for_model(
                db_session, user_id=ctx.user_id, file_id=raw_input["file_id"]
            )
            if record is None:
                raise NotFoundError("file not found (or not owned by you)")
            settings: Settings = ctx.extra["settings"]
            storage = get_storage(settings)
            data = await storage.read_bounded(record.storage_key, 50 * 1024 * 1024)
            text = data.decode("utf-8", errors="replace")
        elif raw_input.get("path"):
            if ctx.workspace is None:
                raise ToolExecutionError("workspace path requires an active task workspace")
            target = resolve_in_root(ctx.workspace, raw_input["path"])
            if not target.is_file():
                raise ToolExecutionError(f"file not found: {raw_input['path']}")

            def _read() -> str:
                return target.read_text("utf-8", errors="replace")

            text = await asyncio.to_thread(_read)
        else:
            raise RequestValidationError("provide either 'path' or 'file_id'")

        def _analyze() -> dict[str, Any]:
            reader = csv.DictReader(io.StringIO(text))
            if reader.fieldnames is None:
                raise ToolExecutionError("CSV has no header row")
            columns: dict[str, dict[str, list]] = {
                name: {"values": [], "raw": []} for name in reader.fieldnames
            }
            rows = 0
            truncated = False
            for row in reader:
                rows += 1
                if rows > max_rows:
                    truncated = True
                    break
                for name in reader.fieldnames:
                    value = (row.get(name) or "").strip()
                    if value == "":
                        continue
                    columns[name]["raw"].append(value)
                    try:
                        columns[name]["values"].append(float(value))
                    except ValueError:
                        pass

            stats: list[dict[str, Any]] = []
            for name, data in columns.items():
                raw = data["raw"]
                numeric = data["values"]
                is_numeric = raw and len(numeric) >= max(1, int(0.9 * len(raw)))
                entry: dict[str, Any] = {
                    "name": name,
                    "inferred_type": "numeric" if is_numeric and numeric else (
                        "text" if raw else "empty"
                    ),
                    "non_null": len(raw),
                    "missing": rows - len(raw) if rows <= max_rows else None,
                }
                if entry["missing"] is None:
                    entry["missing"] = max_rows - len(raw)
                if is_numeric and numeric:
                    entry.update(
                        min=min(numeric),
                        max=max(numeric),
                        mean=round(statistics.fmean(numeric), 6),
                        median=statistics.median(numeric),
                        stdev=(
                            round(statistics.stdev(numeric), 6)
                            if len(numeric) > 1
                            else 0.0
                        ),
                    )
                elif raw:
                    entry["unique"] = len(set(raw))
                stats.append(entry)
            return {"rows": min(rows, max_rows), "truncated": truncated, "columns": stats}

        return await asyncio.to_thread(_analyze)
