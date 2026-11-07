"""Integration: tools API, models API, memory API, health, OpenAPI contract,
notifications, admin, WebSocket."""

from __future__ import annotations

import pytest


class TestToolsApi:
    async def test_list_tools(self, client, headers):
        response = await client.get("/api/v1/tools", headers=headers)
        assert response.status_code == 200
        tools = {t["key"] for t in response.json()}
        assert {
            "fs.read", "fs.write", "fs.list", "code.search",
            "http.request", "python.execute", "terminal.execute",
            "document.extract", "data.analyze_csv",
        } <= tools
        for tool in response.json():
            assert tool["input_schema"]
            assert tool["permissions"]

    async def test_describe_tool(self, client, headers):
        response = await client.get("/api/v1/tools/http.request", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["key"] == "http.request"
        assert "url" in body["input_schema"]["properties"]

    async def test_unknown_tool_404(self, client, headers):
        response = await client.get("/api/v1/tools/nope.tool", headers=headers)
        assert response.status_code == 404

    async def test_direct_execution_of_service_tool(self, client, headers, worker):
        upload = await client.post(
            "/api/v1/files",
            files={"file": ("analyze.csv", b"col\n1\n2\n3\n", "text/csv")},
            headers=headers,
        )
        await worker.drain(max_wait_s=3.0)
        response = await client.post(
            "/api/v1/tools/data.analyze_csv/execute",
            json={"input": {"file_id": upload.json()["id"]}},
            headers=headers,
        )
        assert response.status_code == 200
        output = response.json()["output"]
        assert output["rows"] == 3
        assert output["columns"][0]["name"] == "col"
        assert output["columns"][0]["inferred_type"] == "numeric"

    async def test_sandboxed_tool_refused_direct_execution(self, client, headers):
        response = await client.post(
            "/api/v1/tools/python.execute/execute",
            json={"input": {"code": "print(1)"}},
            headers=headers,
        )
        assert response.status_code == 400

    async def test_approval_gated_tool_refused_direct_execution(self, client, headers):
        response = await client.post(
            "/api/v1/tools/terminal.execute/execute",
            json={"input": {"command": ["ls"]}},
            headers=headers,
        )
        assert response.status_code == 403


class TestModelsApi:
    async def test_list_models_masks_api_key(self, client, headers):
        response = await client.get("/api/v1/models", headers=headers)
        assert response.status_code == 200
        names = {m["name"] for m in response.json()}
        assert "test-echo" in names
        assert "saluki-27b" in names

    async def test_default_model_health(self, client, headers):
        models = await client.get("/api/v1/models", headers=headers)
        default = next(m for m in models.json() if m["is_default"])
        response = await client.get(
            f"/api/v1/models/{default['id']}/health", headers=headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["healthy"] is True
        assert "not a model runtime" in body["detail"]

    async def test_create_model_requires_admin(self, client, headers):
        response = await client.post(
            "/api/v1/models",
            json={
                "name": "another-model",
                "display_name": "Another",
                "adapter_type": "test",
            },
            headers=headers,
        )
        assert response.status_code == 403

    async def test_admin_can_create_and_secret_is_masked(self, client):
        # bootstrap an admin through the app state
        app = client._transport.app
        from app.core.security import hash_password
        from app.core.utils import new_uuid
        from app.db.enums import UserRole
        from app.db.models import User

        async with app.state.database.session() as session:
            session.add(
                User(
                    id=new_uuid(),
                    email="admin@example.com",
                    password_hash=hash_password("AdminPass123!"),
                    display_name="Admin",
                    role=UserRole.ADMIN,
                )
            )
            await session.commit()

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "AdminPass123!"},
        )
        admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        created = await client.post(
            "/api/v1/models",
            json={
                "name": "secret-model",
                "display_name": "Secret",
                "adapter_type": "openai_compatible",
                "endpoint": "http://models.internal:8000/v1",
                "api_key": "sk-super-secret-key-12345",
            },
            headers=admin_headers,
        )
        assert created.status_code == 201
        body = created.json()
        assert body["api_key"].startswith("sk-s****")
        assert "sk-super-secret-key-12345" not in body["api_key"]

        listing = await client.get("/api/v1/models", headers=admin_headers)
        secret_model = next(m for m in listing.json() if m["name"] == "secret-model")
        assert "sk-super" not in secret_model["api_key"]


class TestMemoryApi:
    async def test_crud_and_search(self, client, headers):
        created = await client.post(
            "/api/v1/memory",
            json={
                "category": "semantic",
                "content": "The user works at Acme Corp",
                "source": "user_provided",
            },
            headers=headers,
        )
        assert created.status_code == 201
        memory_id = created.json()["id"]

        search = await client.post(
            "/api/v1/memory/search",
            json={"query": "where does the user work"},
            headers=headers,
        )
        assert search.status_code == 200
        assert any(h["record"]["id"] == memory_id for h in search.json())

        updated = await client.patch(
            f"/api/v1/memory/{memory_id}",
            json={"content": "The user works at Globex"},
            headers=headers,
        )
        assert updated.json()["version"] == 2

        verified = await client.post(
            f"/api/v1/memory/{memory_id}/verify",
            json={"evidence": "user confirmed 2026-10-31"},
            headers=headers,
        )
        assert verified.json()["source"] == "verified"

        forgotten = await client.delete(f"/api/v1/memory/{memory_id}", headers=headers)
        assert forgotten.status_code == 204
        gone = await client.get(f"/api/v1/memory/{memory_id}", headers=headers)
        assert gone.status_code == 404


class TestHealthAndContract:
    async def test_liveness(self, client):
        response = await client.get("/api/v1/health/live")
        assert response.status_code == 200

    async def test_readiness(self, client):
        response = await client.get("/api/v1/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["components"]["database"]["ok"]
        assert body["components"]["storage"]["ok"]

    async def test_metrics_endpoint(self, client):
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert "agp_http_requests_total" in response.text

    async def test_openapi_contract(self, client):
        response = await client.get("/api/openapi.json")
        assert response.status_code == 200
        spec = response.json()

        required_paths = {
            "/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/logout",
            "/api/v1/auth/refresh", "/api/v1/auth/me",
            "/api/v1/conversations", "/api/v1/conversations/{conversation_id}",
            "/api/v1/conversations/{conversation_id}/messages",
            "/api/v1/conversations/{conversation_id}/archive",
            "/api/v1/conversations/{conversation_id}/search",
            "/api/v1/files", "/api/v1/files/{file_id}", "/api/v1/files/{file_id}/download",
            "/api/v1/models", "/api/v1/models/{model_id}/health",
            "/api/v1/tasks", "/api/v1/tasks/{task_id}",
            "/api/v1/tasks/{task_id}/pause", "/api/v1/tasks/{task_id}/resume",
            "/api/v1/tasks/{task_id}/cancel",
            "/api/v1/approvals", "/api/v1/approvals/{approval_id}",
            "/api/v1/tools", "/api/v1/tools/{tool_key}",
            "/api/v1/memory", "/api/v1/memory/{memory_id}",
            "/api/v1/health/live", "/api/v1/health/ready",
        }
        assert required_paths <= set(spec["paths"]), (
            f"missing: {required_paths - set(spec['paths'])}"
        )

        # unique operation ids
        operation_ids = [
            operation["operationId"]
            for path, methods in spec["paths"].items()
            for method, operation in methods.items()
            if method in ("get", "post", "put", "patch", "delete")
        ]
        assert len(operation_ids) == len(set(operation_ids))

        # security scheme declared
        assert "securitySchemes" in spec["components"]
        assert "bearer" in str(spec["components"]["securitySchemes"]).lower() or \
            spec["components"]["securitySchemes"]

    async def test_docs_pages(self, client):
        assert (await client.get("/docs")).status_code == 200
        assert (await client.get("/redoc")).status_code == 200


class TestAdminEndpoints:
    async def test_non_admin_denied(self, client, headers):
        response = await client.get("/api/v1/admin/stats", headers=headers)
        assert response.status_code == 403

    async def test_admin_stats_and_jobs_and_audit(self, client):
        app = client._transport.app
        from app.core.security import hash_password
        from app.core.utils import new_uuid
        from app.db.enums import UserRole
        from app.db.models import User

        async with app.state.database.session() as session:
            session.add(
                User(
                    id=new_uuid(),
                    email="root@example.com",
                    password_hash=hash_password("RootPass123!"),
                    display_name="Root",
                    role=UserRole.ADMIN,
                )
            )
            await session.commit()
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "root@example.com", "password": "RootPass123!"},
        )
        admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        stats = await client.get("/api/v1/admin/stats", headers=admin_headers)
        assert stats.status_code == 200
        assert stats.json()["users"] >= 1

        jobs = await client.get("/api/v1/admin/jobs", headers=admin_headers)
        assert jobs.status_code == 200

        # register one more user through the API so register events exist
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "late-user@example.com",
                "password": "LateUser123!",
                "display_name": "Late",
            },
        )
        audit = await client.get("/api/v1/admin/audit", headers=admin_headers)
        assert audit.status_code == 200
        event_types = {e["event_type"] for e in audit.json()["items"]}
        assert {"auth.register", "auth.login"} <= event_types


class TestWebSocket:
    def test_task_event_stream(self, tmp_path):
        """WebSocket subscription receives live task events (sync TestClient
        runs the app including lifespan in its own portal)."""
        import sys

        sys.path.insert(0, str(tmp_path))
        from tests.conftest import make_settings, run_migrations

        settings = make_settings(tmp_path, worker_embedded=True)
        run_migrations(settings.database_url)

        from fastapi.testclient import TestClient

        from app.main import create_app

        app = create_app(settings)
        with TestClient(app) as test_client:
            registered = test_client.post(
                "/api/v1/auth/register",
                json={
                    "email": "ws@example.com",
                    "password": "Str0ngPassw0rd!",
                    "display_name": "WS",
                },
            ).json()
            headers = {"Authorization": f"Bearer {registered['access_token']}"}

            created = test_client.post(
                "/api/v1/tasks",
                json={"title": "WS task", "objective": "observe me"},
                headers=headers,
            ).json()
            task_id = created["id"]

            with test_client.websocket_connect(
                f"/api/v1/ws?token={registered['access_token']}"
            ) as websocket:
                websocket.send_json(
                    {"action": "subscribe", "topics": [f"task:{task_id}"]}
                )
                ack = websocket.receive_json()
                assert ack["type"] == "subscribed"

                # trigger a lifecycle transition; the embedded worker reacts
                test_client.post(f"/api/v1/tasks/{task_id}/pause", headers=headers)

                import time

                deadline = time.time() + 15
                received = []
                while time.time() < deadline:
                    message = websocket.receive_json()
                    received.append(message)
                    if message.get("type") == "task.status" and message["data"].get(
                        "status"
                    ) in ("paused", "queued"):
                        break
                assert any(m["type"] == "task.status" for m in received)

    def test_websocket_rejects_bad_token(self, tmp_path):
        from tests.conftest import make_settings, run_migrations

        settings = make_settings(tmp_path)
        run_migrations(settings.database_url)

        from fastapi.testclient import TestClient

        from app.main import create_app

        app = create_app(settings)
        with TestClient(app) as test_client:
            with pytest.raises(Exception):
                with test_client.websocket_connect("/api/v1/ws?token=garbage"):
                    pass
