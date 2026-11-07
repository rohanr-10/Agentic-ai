"""Integration: file upload, processing, download, deletion."""

from __future__ import annotations

import pytest


class TestUpload:
    async def test_upload_and_metadata(self, client, headers):
        response = await client.post(
            "/api/v1/files",
            files={"file": ("report.txt", b"file body content", "text/plain")},
            headers=headers,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["original_filename"] == "report.txt"
        assert body["mime_type"] == "text/plain"
        assert body["size_bytes"] == len(b"file body content")
        assert body["status"] == "pending"
        assert len(body["sha256"]) == 64

    async def test_extraction_via_worker(self, client, headers, worker):
        upload = await client.post(
            "/api/v1/files",
            files={"file": ("notes.md", b"# Title\n\nsome markdown", "text/markdown")},
            headers=headers,
        )
        file_id = upload.json()["id"]
        await worker.drain(max_wait_s=3.0)

        detail = await client.get(f"/api/v1/files/{file_id}", headers=headers)
        body = detail.json()
        assert body["status"] == "ready"
        assert body["extracted_chars"] > 0

        with_text = await client.get(
            f"/api/v1/files/{file_id}?include_text=true", headers=headers
        )
        assert "some markdown" in with_text.json()["extracted_text"]

    async def test_docx_and_xlsx_processing(self, client, headers, worker):
        import io

        from docx import Document
        from openpyxl import Workbook

        document = Document()
        document.add_paragraph("docx content here")
        docx_buffer = io.BytesIO()
        document.save(docx_buffer)

        workbook = Workbook()
        workbook.active.append(["hello", "world"])
        xlsx_buffer = io.BytesIO()
        workbook.save(xlsx_buffer)

        docx_upload = await client.post(
            "/api/v1/files",
            files={
                "file": (
                    "file.docx",
                    docx_buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=headers,
        )
        xlsx_upload = await client.post(
            "/api/v1/files",
            files={
                "file": (
                    "file.xlsx",
                    xlsx_buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            headers=headers,
        )
        assert docx_upload.status_code == xlsx_upload.status_code == 201
        await worker.drain(max_wait_s=3.0)

        docx_detail = await client.get(f"/api/v1/files/{docx_upload.json()['id']}", headers=headers)
        xlsx_detail = await client.get(f"/api/v1/files/{xlsx_upload.json()['id']}", headers=headers)
        assert docx_detail.json()["status"] == "ready"
        assert docx_detail.json()["extracted_chars"] > 0
        assert xlsx_detail.json()["status"] == "ready"

    async def test_download_roundtrip(self, client, headers):
        payload = b"downloadable bytes " * 10
        upload = await client.post(
            "/api/v1/files",
            files={"file": ("blob.bin.txt", payload, "text/plain")},
            headers=headers,
        )
        file_id = upload.json()["id"]
        download = await client.get(f"/api/v1/files/{file_id}/download", headers=headers)
        assert download.status_code == 200
        assert download.content == payload
        assert download.headers["content-disposition"].startswith('attachment; filename="blob.bin.txt"')

    async def test_delete(self, client, headers):
        upload = await client.post(
            "/api/v1/files",
            files={"file": ("temp.txt", b"temporary", "text/plain")},
            headers=headers,
        )
        file_id = upload.json()["id"]
        deleted = await client.delete(f"/api/v1/files/{file_id}", headers=headers)
        assert deleted.status_code == 204
        gone = await client.get(f"/api/v1/files/{file_id}", headers=headers)
        assert gone.status_code == 404

    async def test_list_and_filter(self, client, headers, worker):
        await client.post(
            "/api/v1/files",
            files={"file": ("a.txt", b"aaa", "text/plain")},
            headers=headers,
        )
        await client.post(
            "/api/v1/files",
            files={"file": ("b.txt", b"bbb", "text/plain")},
            headers=headers,
        )
        await worker.drain(max_wait_s=3.0)

        listing = await client.get("/api/v1/files", headers=headers)
        assert listing.status_code == 200
        body = listing.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

        ready = await client.get("/api/v1/files?status=ready", headers=headers)
        assert ready.json()["total"] == 2

        pending = await client.get("/api/v1/files?status=pending", headers=headers)
        assert pending.json()["total"] == 0

    async def test_conversation_association(self, client, headers):
        from tests.conftest import create_conversation

        conversation = await create_conversation(client, headers)
        upload = await client.post(
            f"/api/v1/files?conversation_id={conversation['id']}",
            files={"file": ("conv.txt", b"associated", "text/plain")},
            headers=headers,
        )
        assert upload.json()["conversation_id"] == conversation["id"]

    async def test_notification_emitted_after_processing(self, client, headers, worker):
        upload = await client.post(
            "/api/v1/files",
            files={"file": ("notify.txt", b"content", "text/plain")},
            headers=headers,
        )
        await worker.drain(max_wait_s=3.0)
        notifications = await client.get("/api/v1/notifications", headers=headers)
        kinds = [n["kind"] for n in notifications.json()["items"]]
        assert "file_ready" in kinds
