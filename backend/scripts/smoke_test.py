"""Manual end-to-end smoke check (not part of the test suite)."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="agp-smoke-"))
os.environ.update(
    AGP_ENVIRONMENT="development",
    AGP_DATABASE_URL=f"sqlite+aiosqlite:///{TMP}/app.db",
    AGP_STORAGE_LOCAL_ROOT=str(TMP / "storage"),
    AGP_SANDBOX_ROOT=str(TMP / "sandboxes"),
    AGP_LOG_LEVEL="WARNING",
    AGP_RATE_LIMIT_ENABLED="false",
)

import asyncio  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

# run migrations against the temp database
os.chdir(Path(__file__).resolve().parent.parent)
cfg = AlembicConfig("alembic.ini")
command.upgrade(cfg, "head")
print("migrations applied")

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

settings = Settings(_env_file=None)


async def main() -> None:
    import httpx

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            # health
            r = await client.get("/api/v1/health/live")
            print("live:", r.status_code, r.json())
            r = await client.get("/api/v1/health/ready")
            print("ready:", r.status_code, r.json())

            # auth
            r = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "smoke@example.com",
                    "password": "Str0ngPassw0rd!",
                    "display_name": "Smoke Tester",
                },
            )
            print("register:", r.status_code)
            tokens = r.json()
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}

            r = await client.get("/api/v1/auth/me", headers=headers)
            print("me:", r.status_code, r.json()["email"])

            # conversation + message
            r = await client.post(
                "/api/v1/conversations",
                json={"title": "Smoke", "mode": "conversational"},
                headers=headers,
            )
            print("conversation:", r.status_code)
            conv_id = r.json()["id"]

            r = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "Hello platform"},
                headers=headers,
            )
            print("message:", r.status_code)
            body = r.json()
            print("assistant says:", body["assistant_message"]["content"][:80])

            # streaming
            req = client.build_request(
                "POST",
                f"/api/v1/conversations/{conv_id}/messages/stream",
                json={"content": "Stream me something"},
                headers=headers,
            )
            resp = await client.send(req, stream=True)
            print("stream status:", resp.status_code)
            chunks = 0
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    chunks += 1
            print("stream event lines:", chunks)
            await resp.aclose()

            # files
            r = await client.post(
                "/api/v1/files",
                files={"file": ("notes.txt", b"hello smoke test content", "text/plain")},
                headers=headers,
            )
            print("upload:", r.status_code, r.json()["status"])
            file_id = r.json()["id"]

            # run the embedded worker manually to process extraction
            from app.workers.runner import Worker

            worker = Worker(app.state.database, settings, app.state.bus, worker_id="smoke")
            ran = await worker.drain(max_wait_s=2.0)
            print("worker jobs ran:", ran)

            r = await client.get(f"/api/v1/files/{file_id}", headers=headers)
            print("file after processing:", r.json()["status"], "chars:", r.json()["extracted_chars"])

            # task lifecycle with client plan + auto-approval off
            r = await client.post(
                "/api/v1/tasks",
                json={
                    "title": "Write a report",
                    "objective": "Create report.txt with hello world",
                    "plan": {
                        "objective_restated": "write report",
                        "steps": [
                            {
                                "id": "s1",
                                "title": "Write file",
                                "tool_key": "fs.write",
                                "input": {"path": "report.txt", "content": "hello world"},
                                "timeout_s": 30,
                            }
                        ],
                    },
                },
                headers=headers,
            )
            print("task:", r.status_code, r.json()["status"])
            task_id = r.json()["id"]

            await worker.drain(max_wait_s=2.0)

            r = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
            print("task status after worker:", r.json()["status"])

            # approvals
            r = await client.get("/api/v1/approvals", headers=headers)
            print("approvals:", r.status_code, len(r.json()["items"]))
            if r.json()["items"]:
                approval_id = r.json()["items"][0]["id"]
                r = await client.post(
                    f"/api/v1/approvals/{approval_id}",
                    json={"decision": "approve"},
                    headers=headers,
                )
                print("approve:", r.status_code)
                await worker.drain(max_wait_s=3.0)
                r = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
                print("final task:", r.json()["status"], "| artifacts:", r.json()["artifacts"])

            # memory
            r = await client.post(
                "/api/v1/memory",
                json={
                    "category": "semantic",
                    "content": "The user prefers concise answers",
                    "source": "user_provided",
                },
                headers=headers,
            )
            print("memory:", r.status_code)
            r = await client.post(
                "/api/v1/memory/search",
                json={"query": "concise answers preference"},
                headers=headers,
            )
            print("memory search hits:", len(r.json()))

            # models & tools
            r = await client.get("/api/v1/models", headers=headers)
            print("models:", [m["name"] for m in r.json()])
            r = await client.get("/api/v1/tools", headers=headers)
            print("tools:", len(r.json()))

            # openapi
            r = await client.get("/api/openapi.json")
            print("openapi paths:", len(r.json()["paths"]))

    print("SMOKE TEST COMPLETE")


asyncio.run(main())
