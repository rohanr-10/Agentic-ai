"""Security: injection attempts, upload attacks, auth abuse, error hygiene."""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import PASSWORD, auth_headers


class TestSqlInjection:
    async def test_search_with_injection_payloads(self, client, headers):
        from tests.conftest import create_conversation

        conversation = await create_conversation(client, headers)
        for payload in (
            "'; DROP TABLE messages; --",
            "' OR '1'='1",
            "%",
            "'; UPDATE users SET role='admin'; --",
            "x' UNION SELECT password_hash FROM users --",
        ):
            response = await client.get(
                f"/api/v1/conversations/{conversation['id']}/search",
                params={"q": payload},
                headers=headers,
            )
            assert response.status_code == 200, payload

        # the messages table still exists and works
        send = await client.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            json={"content": "still alive"},
            headers=headers,
        )
        assert send.status_code == 201

    async def test_login_with_injection_payloads(self, client):
        for payload in (
            "' OR 1=1 --",
            "admin'--",
            "'; DROP TABLE users; --",
        ):
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": payload, "password": payload},
            )
            assert response.status_code in (401, 422)

        me = await client.post("/api/v1/auth/login", json={"email": "x@y.z", "password": "nope1234"})
        assert me.status_code == 401


class TestPathTraversal:
    async def test_upload_filename_traversal_sanitised(self, client, headers):
        for evil_name in (
            "../../etc/passwd",
            "..\\..\\windows\\system32\\evil.txt",
            "sub/../../escape.txt",
            ".../....//hidden.txt",
        ):
            response = await client.post(
                "/api/v1/files",
                files={"file": (evil_name, b"content", "text/plain")},
                headers=headers,
            )
            # either rejected or stored under a sanitised basename
            if response.status_code == 201:
                assert "/" not in response.json()["original_filename"]
                assert ".." not in response.json()["original_filename"]

    async def test_storage_root_not_escaped(self, client, headers):
        """The stored object lives under the configured root."""
        upload = await client.post(
            "/api/v1/files",
            files={"file": ("probe.txt", b"data", "text/plain")},
            headers=headers,
        )
        assert upload.status_code == 201
        app = client._transport.app
        import sqlalchemy as sa
        import uuid as uuid_module
        from pathlib import Path

        from app.db.models import FileRecord

        async with app.state.database.session() as session:
            record = await session.get(FileRecord, uuid_module.UUID(upload.json()["id"]))
        storage_key = record.storage_key
        assert not storage_key.startswith("/")
        assert ".." not in storage_key
        root = Path(app.state.settings.storage_local_root).resolve()
        stored = (root / storage_key).resolve()
        assert stored == root or root in stored.parents

    async def test_fs_tool_traversal_blocked_in_task(self, client, headers, worker):
        """A plan trying to escape the workspace must fail cleanly."""
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            plan = {
                "objective_restated": "escape",
                "steps": [
                    {
                        "id": "escape",
                        "title": "Read outside workspace",
                        "tool_key": "fs.read",
                        "input": {"path": "../../../etc/passwd"},
                        "timeout_s": 10,
                    }
                ],
            }
            from tests.conftest import create_task, wait_for_task_status

            task = await create_task(client, headers, plan=plan)
            await worker.drain(max_wait_s=5.0)
            final = await wait_for_task_status(client, headers, task["id"], {"failed"})
            assert final["error"]["code"] == "step_failed"
            assert "escapes the execution workspace" in final["error"]["message"]
        finally:
            client._transport.app.state.settings.auto_approve_plans = False


class TestCommandInjection:
    async def test_terminal_tool_rejects_non_allowlisted_commands(self, client, headers):
        response = await client.post(
            "/api/v1/tools/terminal.execute/execute",
            json={"input": {"command": ["ls; rm -rf /"]}},
            headers=headers,
        )
        assert response.status_code == 403  # approval-gated tool refused outright

    async def test_shell_metacharacters_have_no_effect_in_sandbox(self, tmp_path):
        """Executed via the sandbox directly: argv, never a shell."""
        from app.tools.sandbox import LocalProcessSandbox, SandboxCommand

        sandbox = LocalProcessSandbox()
        (tmp_path / "ws").mkdir()
        result = await sandbox.run(
            SandboxCommand(argv=["/bin/echo", "value", ";", "rm", "-rf", "/"], timeout_s=10),
            workspace=tmp_path / "ws",
        )
        assert result.exit_code == 0
        assert "rm -rf" in result.stdout
        assert (tmp_path / "ws").exists()


class TestUploadAttacks:
    async def test_spoofed_mime_rejected(self, client, headers):
        """A PDF renamed to .txt is rejected by content sniffing."""
        response = await client.post(
            "/api/v1/files",
            files={"file": ("innocent.txt", b"%PDF-1.7 malicious content", "text/plain")},
            headers=headers,
        )
        assert response.status_code == 400
        assert "spoofing" in response.json()["error"]["message"]

    async def test_disallowed_extension_rejected(self, client, headers):
        response = await client.post(
            "/api/v1/files",
            files={"file": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
            headers=headers,
        )
        assert response.status_code == 400

    async def test_oversized_upload_rejected(self, client, headers):
        app = client._transport.app
        app.state.settings.file_max_size_bytes = 1024
        big = b"x" * 4096
        response = await client.post(
            "/api/v1/files",
            files={"file": ("big.txt", big, "text/plain")},
            headers=headers,
        )
        assert response.status_code == 413

    async def test_zip_bomb_style_rejection(self, client, headers):
        """Generic zip containers (not docx/xlsx) are not allowed."""
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("random.bin", b"\x00" * 100)
        response = await client.post(
            "/api/v1/files",
            files={"file": ("thing.zip", buffer.getvalue(), "application/zip")},
            headers=headers,
        )
        assert response.status_code == 400


class TestAuthAbuse:
    async def test_missing_token(self, client):
        assert (await client.get("/api/v1/auth/me")).status_code == 401

    async def test_malformed_authorization_header(self, client):
        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        assert response.status_code == 401

    def test_rate_limiting_on_login(self, tmp_path):
        """Hammering the login endpoint yields 429 with Retry-After."""
        import sys

        sys.path.insert(0, str(tmp_path))
        from tests.conftest import make_settings, run_migrations

        settings = make_settings(tmp_path, rate_limit_auth_per_minute=5)
        run_migrations(settings.database_url)

        from fastapi.testclient import TestClient

        from app.main import create_app

        app = create_app(settings)
        with TestClient(app) as test_client:
            statuses = []
            for _ in range(10):
                response = test_client.post(
                    "/api/v1/auth/login",
                    json={"email": "nobody@example.com", "password": "Wrong1234"},
                )
                statuses.append(response.status_code)
            assert 429 in statuses
            assert statuses[-1] == 429
            limited = test_client.post(
                "/api/v1/auth/login",
                json={"email": "nobody@example.com", "password": "Wrong1234"},
            )
            assert limited.status_code == 429
            assert limited.headers.get("retry-after")

    async def test_disabled_user_rejected(self, client, headers):
        app = client._transport.app
        import sqlalchemy as sa

        from app.db.models import User

        async with app.state.database.session() as session:
            user = (await session.execute(sa.select(User))).scalars().first()
            user.is_active = False
            await session.commit()

        response = await client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 401


class TestErrorHygiene:
    def test_no_stack_traces_in_error_responses(self, tmp_path):
        """Force an internal error and check the response stays opaque.

        TestClient(raise_server_exceptions=False) mirrors a real server:
        the client receives the handler's JSON body, not the exception."""
        import sys

        sys.path.insert(0, str(tmp_path))
        from tests.conftest import make_settings, run_migrations

        settings = make_settings(tmp_path)
        run_migrations(settings.database_url)

        from fastapi.testclient import TestClient

        from app.main import create_app

        app = create_app(settings)

        class BrokenManager:
            async def generate(self, *args, **kwargs):
                raise RuntimeError("SECRET internal detail: /etc/passwd contents")

            async def stream(self, *args, **kwargs):
                raise RuntimeError("SECRET internal detail")
                yield  # pragma: no cover

        with TestClient(app, raise_server_exceptions=False) as test_client:
            registered = test_client.post(
                "/api/v1/auth/register",
                json={
                    "email": "hygiene@example.com",
                    "password": "Str0ngPassw0rd!",
                    "display_name": "H",
                },
            ).json()
            headers = {"Authorization": f"Bearer {registered['access_token']}"}
            app.state.model_manager = BrokenManager()

            conversation = test_client.post(
                "/api/v1/conversations", json={"title": "t"}, headers=headers
            ).json()
            response = test_client.post(
                f"/api/v1/conversations/{conversation['id']}/messages",
                json={"content": "trigger the failure"},
                headers=headers,
            )
            assert response.status_code >= 500
            body = response.text
            assert "Traceback" not in body
            assert "SECRET" not in body
            assert "internal error" in body

    async def test_security_headers_present(self, client, headers):
        response = await client.get("/api/v1/health/live")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"

    async def test_request_id_correlation(self, client):
        response = await client.get(
            "/api/v1/health/live", headers={"X-Request-Id": "trace-123-abc"}
        )
        assert response.headers["x-request-id"] == "trace-123-abc"
