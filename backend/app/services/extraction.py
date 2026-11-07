"""Content-based file type sniffing and text extraction.

The client-declared MIME type and the file extension are NEVER trusted:
uploads are classified from magic bytes (and, for ZIP containers, from the
container structure), and the classification must be consistent with the
claimed extension before the file is accepted.

Extraction runs off the event loop (``asyncio.to_thread``) and is bounded by
page/row/character caps.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from dataclasses import dataclass

from app.core.errors import RequestValidationError

# extension -> sniffed MIME it must be consistent with
TEXT_MIME_BY_EXTENSION: dict[str, str] = {
    ".txt": "text/plain",
    ".log": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
    ".xml": "application/xml",
}

BINARY_MIME_BY_EXTENSION: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

ALLOWED_MIME_TYPES = set(TEXT_MIME_BY_EXTENSION.values()) | set(
    BINARY_MIME_BY_EXTENSION.values()
)

_TEXT_SNIFF_WINDOW = 8192
_MAX_PDF_PAGES = 500
_MAX_XLSX_ROWS = 5000


@dataclass(frozen=True)
class SniffResult:
    mime_type: str
    extension: str


def sniff_mime(head: bytes, filename: str, *, extension: str) -> SniffResult:
    """Classify content; raise if it conflicts with the claimed extension."""
    expected = BINARY_MIME_BY_EXTENSION.get(extension) or TEXT_MIME_BY_EXTENSION.get(extension)

    detected: str | None = None
    if head.startswith(b"%PDF-"):
        detected = "application/pdf"
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif head.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif head.startswith(b"PK\x03\x04"):
        detected = _classify_zip(head)
    elif expected in TEXT_MIME_BY_EXTENSION.values() and _looks_like_text(head):
        detected = expected

    if detected is None:
        raise RequestValidationError(
            "file content could not be verified against an allowed type",
            details={"extension": extension, "allowed": sorted(ALLOWED_MIME_TYPES)},
        )

    if expected is not None and detected != expected:
        raise RequestValidationError(
            "file content does not match its extension (possible spoofing)",
            details={"extension": extension, "detected": detected},
        )

    if detected not in ALLOWED_MIME_TYPES:
        raise RequestValidationError(
            "file type is not allowed",
            details={"detected": detected, "allowed": sorted(ALLOWED_MIME_TYPES)},
        )
    return SniffResult(mime_type=detected, extension=extension)


def _classify_zip(head: bytes) -> str | None:
    names: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(head)) as archive:
            names = archive.namelist()
    except Exception:
        # Truncated head (central directory beyond the sniff window): fall
        # back to scanning local file headers, whose names appear inline.
        names = _zip_local_header_names(head)
    if any(n.startswith("word/") for n in names):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if any(n.startswith("xl/") for n in names):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return None


def _zip_local_header_names(data: bytes) -> list[str]:
    """Extract file names from ZIP local file headers without the central
    directory (robust against truncated buffers)."""
    names: list[str] = []
    offset = 0
    while len(names) < 256:
        index = data.find(b"PK\x03\x04", offset)
        if index < 0 or index + 30 > len(data):
            break
        name_len = int.from_bytes(data[index + 26 : index + 28], "little")
        if name_len == 0 or name_len > 500:
            break
        names.append(data[index + 30 : index + 30 + name_len].decode("utf-8", "replace"))
        offset = index + 30 + name_len
    return names


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return True
    window = head[:_TEXT_SNIFF_WINDOW]
    if b"\x00" in window:
        return False
    try:
        window.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


async def extract_text(mime_type: str, data: bytes, *, max_chars: int) -> str:
    """Extract text content (bounded) from raw bytes.

    Images return empty text (no OCR — documented platform limitation).
    """
    return await asyncio.to_thread(_extract_sync, mime_type, data, max_chars)


def _extract_sync(mime_type: str, data: bytes, max_chars: int) -> str:
    if mime_type.startswith("text/") or mime_type in (
        "application/json",
        "application/yaml",
        "application/toml",
        "application/xml",
    ):
        return data.decode("utf-8", errors="replace")[:max_chars]

    if mime_type == "application/pdf":
        return _extract_pdf(data, max_chars)

    if mime_type.endswith("wordprocessingml.document"):
        return _extract_docx(data, max_chars)

    if mime_type.endswith("spreadsheetml.sheet"):
        return _extract_xlsx(data, max_chars)

    if mime_type in ("image/png", "image/jpeg"):
        return ""  # images are stored for the model runtime; no OCR in v1

    return ""


def _extract_pdf(data: bytes, max_chars: int) -> str:
    from pypdf import PdfReader

    parts: list[str] = []
    reader = PdfReader(io.BytesIO(data))
    for index, page in enumerate(reader.pages):
        if index >= _MAX_PDF_PAGES:
            parts.append(f"\n…[truncated at {_MAX_PDF_PAGES} pages]")
            break
        parts.append(page.extract_text() or "")
        if sum(len(p) for p in parts) > max_chars:
            break
    return "\n".join(parts)[:max_chars]


def _extract_docx(data: bytes, max_chars: int) -> str:
    from docx import Document

    document = Document(io.BytesIO(data))
    parts: list[str] = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            parts.append(" | ".join(cells))
            if sum(len(p) for p in parts) > max_chars:
                break
    return "\n".join(parts)[:max_chars]


def _extract_xlsx(data: bytes, max_chars: int) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        parts: list[str] = []
        for sheet in workbook.worksheets:
            parts.append(f"# sheet: {sheet.title}")
            for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
                if row_index >= _MAX_XLSX_ROWS:
                    parts.append(f"…[truncated at {_MAX_XLSX_ROWS} rows]")
                    break
                parts.append(" | ".join("" if v is None else str(v) for v in row))
                if sum(len(p) for p in parts) > max_chars:
                    break
    finally:
        workbook.close()
    return "\n".join(parts)[:max_chars]
