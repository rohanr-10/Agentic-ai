"""Shared test fixtures.

Every test gets an isolated world: its own SQLite database (migrated via
the real Alembic revisions), its own storage and sandbox directories, and a
fully-assembled application instance started through the real lifespan
(seeding included).  Integration tests therefore exercise exactly what
production runs — including migrations — with no mocks on the critical
path.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)


def make_settings(tmp_path: Path, **overrides) -> "Settings":
    from app.core.config import Settings

    defaults = dict(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        storage_local_root=str(tmp_path / "storage"),
        sandbox_root=str(tmp_path / "sandboxes"),
        log_level="WARNING",
        log_json=True,
        rate_limit_enabled=True,
        rate_limit_default_per_minute=100_000,
        rate_limit_auth_per_minute=100_000,
        rate_limit_upload_per_minute=100_000,
        job_retry_backoff_base_s=0.0,
        task_retry_backoff_base_s=0.0,
        auto_approve_plans=False,
        worker_embedded=False,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def run_migrations(database_url: str) -> None:
    """Apply the real Alembic migrations to the test database.

    Safe to call from both sync and async test contexts: alembic's env uses
    asyncio.run(), so inside a running loop it must execute in a thread.
    """
    os.environ["AGP_DATABASE_URL"] = database_url
    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))

    def _upgrade() -> None:
        command.upgrade(cfg, "head")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _upgrade()
        return
    # running inside an async test: execute in a worker thread
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(_upgrade).result()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Per-test limiter isolation (module-level backend cache)."""
    from app.core.ratelimit import reset_rate_limiter

    reset_rate_limiter()
    yield
    reset_rate_limiter()


@pytest.fixture
def test_settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def migrated(test_settings):
    """Sync fixture: Alembic migrations must run outside the asyncio loop."""
    run_migrations(test_settings.database_url)


@pytest_asyncio.fixture
async def app(test_settings, migrated) -> AsyncIterator:
    from app.main import create_app

    application = create_app(test_settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest_asyncio.fixture
async def worker(app):
    """Background worker bound to the app's database + bus, with a drain helper."""
    from app.workers.runner import Worker

    instance = Worker(
        app.state.database, app.state.settings, app.state.bus, worker_id="test-worker"
    )
    return instance


# ----------------------------------------------------------------- helpers

PASSWORD = "Str0ngPassw0rd!"


async def register_user(client: httpx.AsyncClient, email: str | None = None, password: str = PASSWORD) -> dict:
    email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": "Test User"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def auth_headers(client: httpx.AsyncClient, email: str | None = None) -> dict[str, str]:
    tokens = await register_user(client, email)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest_asyncio.fixture
async def headers(client) -> dict[str, str]:
    return await auth_headers(client)


async def create_conversation(client: httpx.AsyncClient, headers: dict, **overrides) -> dict:
    payload = {"title": "Test conversation", "mode": "conversational"}
    payload.update(overrides)
    response = await client.post("/api/v1/conversations", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def create_task(client: httpx.AsyncClient, headers: dict, **overrides) -> dict:
    payload = {
        "title": "Test task",
        "objective": "Do the thing",
        "plan": _simple_plan(),
    }
    payload.update(overrides)
    response = await client.post("/api/v1/tasks", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _simple_plan() -> dict:
    return {
        "objective_restated": "write and verify a file",
        "steps": [
            {
                "id": "write",
                "title": "Write output file",
                "tool_key": "fs.write",
                "input": {"path": "out.txt", "content": "task output"},
                "timeout_s": 30,
            }
        ],
    }


async def drain(worker, *, max_wait_s: float = 5.0) -> int:
    return await worker.drain(max_wait_s=max_wait_s)


async def wait_for_task_status(
    client: httpx.AsyncClient, headers: dict, task_id: str, statuses: set[str], timeout_s: float = 10.0
) -> dict:
    import time

    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        response = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in statuses:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"task did not reach {statuses}; last status={last and last['status']}")
