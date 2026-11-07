"""Unit tests: file type sniffing and text extraction."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.core.errors import RequestValidationError
from app.services.extraction import sniff_mime


class TestSniffing:
    def test_pdf_detected(self):
        result = sniff_mime(b"%PDF-1.7 ...", "doc.pdf", extension=".pdf")
        assert result.mime_type == "application/pdf"

    def test_png_detected(self):
        result = sniff_mime(b"\x89PNG\r\n\x1a\n....", "img.png", extension=".png")
        assert result.mime_type == "image/png"

    def test_jpeg_detected(self):
        result = sniff_mime(b"\xff\xd8\xff\xe0....", "img.jpg", extension=".jpg")
        assert result.mime_type == "image/jpeg"

    def test_docx_detected_via_zip_structure(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", "<doc/>")
        result = sniff_mime(buffer.getvalue(), "file.docx", extension=".docx")
        assert "wordprocessingml" in result.mime_type

    def test_xlsx_detected_via_zip_structure(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/workbook.xml", "<wb/>")
        result = sniff_mime(buffer.getvalue(), "file.xlsx", extension=".xlsx")
        assert "spreadsheetml" in result.mime_type

    def test_text_detected(self):
        result = sniff_mime(b"plain text content", "notes.txt", extension=".txt")
        assert result.mime_type == "text/plain"

    def test_extension_spoof_rejected(self):
        """A real PDF renamed to .txt must be rejected."""
        with pytest.raises(RequestValidationError) as exc_info:
            sniff_mime(b"%PDF-1.7 fake", "evil.pdf.txt", extension=".txt")
        assert "does not match" in str(exc_info.value.message)

    def test_text_with_binary_content_rejected(self):
        with pytest.raises(RequestValidationError):
            sniff_mime(b"\x00\x01\x02binary", "blob.txt", extension=".txt")

    def test_disallowed_type_rejected(self):
        with pytest.raises(RequestValidationError):
            sniff_mime(b"PK\x03\x04generic zip", "archive.zip", extension=".zip")

    def test_executable_rejected(self):
        # real ELF magic contains NUL bytes -> not text
        with pytest.raises(RequestValidationError):
            sniff_mime(b"\x7fELF\x02\x01\x01\x00\x00\x00", "program.py", extension=".py")


class TestExtraction:
    async def test_extract_plain_text(self):
        from app.services.extraction import extract_text

        text = await extract_text("text/plain", b"hello extraction", max_chars=100)
        assert text == "hello extraction"

    async def test_extract_csv(self):
        from app.services.extraction import extract_text

        text = await extract_text("text/csv", b"a,b\n1,2\n3,4", max_chars=1000)
        assert "a | b" in text or "a,b" in text

    async def test_extract_pdf(self):
        from pypdf import PdfWriter

        from app.services.extraction import extract_text

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        # pypdf cannot easily inject text without a real generator; blank page
        # extraction returning empty is still a valid contract check
        buffer = io.BytesIO()
        writer.write(buffer)
        text = await extract_text("application/pdf", buffer.getvalue(), max_chars=1000)
        assert isinstance(text, str)

    async def test_extract_docx(self):
        from docx import Document

        from app.services.extraction import extract_text

        document = Document()
        document.add_paragraph("Hello from docx")
        document.add_paragraph("Second paragraph")
        buffer = io.BytesIO()
        document.save(buffer)
        text = await extract_text(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            buffer.getvalue(),
            max_chars=10_000,
        )
        assert "Hello from docx" in text
        assert "Second paragraph" in text

    async def test_extract_xlsx(self):
        from openpyxl import Workbook

        from app.services.extraction import extract_text

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Data"
        sheet.append(["name", "value"])
        sheet.append(["alpha", 1])
        buffer = io.BytesIO()
        workbook.save(buffer)
        text = await extract_text(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            buffer.getvalue(),
            max_chars=10_000,
        )
        assert "Data" in text
        assert "alpha" in text

    async def test_char_cap_respected(self):
        from app.services.extraction import extract_text

        text = await extract_text("text/plain", b"x" * 5000, max_chars=100)
        assert len(text) == 100
