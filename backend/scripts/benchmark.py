"""Benchmark important operations (run: python scripts/benchmark.py).

Reports measured throughput/latency on THIS machine — no invented numbers.
Environment: local sandbox (2 vCPU), SQLite WAL storage, deterministic test
adapter (model latency excluded by design; measure model latency separately
against the real runtime).
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from conftest import make_settings, run_migrations  # noqa: E402


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * len(ordered)))
    return ordered[index]


def report(name: str, durations_ms: list[float], items: int) -> None:
    total_s = sum(durations_ms) / 1000
    print(
        f"{name:<42} n={items:<4} "
        f"total={total_s:6.2f}s "
        f"ops/s={items / total_s if total_s else 0:8.1f} "
        f"p50={percentile(durations_ms, 0.5):7.2f}ms "
        f"p95={percentile(durations_ms, 0.95):7.2f}ms "
        f"max={max(durations_ms):7.2f}ms"
    )


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="agp-bench-"))
    settings = make_settings(tmp)
    run_migrations(settings.database_url)

    import httpx

    from app.main import create_app

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://bench") as client:
            # ---------------- registration + login ----------------
            durations = []
            tokens = None
            for i in range(50):
                start = time.perf_counter()
                response = await client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"bench{i}@example.com",
                        "password": "BenchPass123!",
                        "display_name": f"B{i}",
                    },
                )
                durations.append((time.perf_counter() - start) * 1000)
                assert response.status_code == 201
                if i == 0:
                    tokens = response.json()
            report("auth: register (argon2 hash)", durations, 50)

            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            durations = []
            for i in range(50):
                start = time.perf_counter()
                response = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "bench0@example.com", "password": "BenchPass123!"},
                )
                durations.append((time.perf_counter() - start) * 1000)
                assert response.status_code == 200
            report("auth: login (argon2 verify)", durations, 50)

            # ---------------- conversations ----------------
            durations = []
            for i in range(100):
                start = time.perf_counter()
                response = await client.post(
                    "/api/v1/conversations", json={"title": f"Bench {i}"}, headers=headers
                )
                durations.append((time.perf_counter() - start) * 1000)
                assert response.status_code == 201
            report("conversations: create", durations, 100)

            durations = []
            for _ in range(100):
                start = time.perf_counter()
                response = await client.get(
                    "/api/v1/conversations?limit=50", headers=headers
                )
                durations.append((time.perf_counter() - start) * 1000)
                assert response.status_code == 200
            report("conversations: list (50/page)", durations, 100)

            conversation = (
                await client.post(
                    "/api/v1/conversations", json={"title": "msg bench"}, headers=headers
                )
            ).json()

            # ---------------- messages ----------------
            durations = []
            for i in range(100):
                start = time.perf_counter()
                response = await client.post(
                    f"/api/v1/conversations/{conversation['id']}/messages",
                    json={"content": f"benchmark message {i} " + "x" * 200},
                    headers=headers,
                )
                durations.append((time.perf_counter() - start) * 1000)
                assert response.status_code == 201
            report("messages: send + generate (test adapter)", durations, 100)

            durations = []
            for _ in range(50):
                start = time.perf_counter()
                response = await client.get(
                    f"/api/v1/conversations/{conversation['id']}/messages?limit=50",
                    headers=headers,
                )
                durations.append((time.perf_counter() - start) * 1000)
                assert response.status_code == 200
            report("messages: list page (50 msgs)", durations, 50)

            # ---------------- concurrent sends ----------------
            async def send(i: int) -> tuple[int, float, str]:
                start = time.perf_counter()
                response = await client.post(
                    f"/api/v1/conversations/{conversation['id']}/messages",
                    json={"content": f"concurrent {i}"},
                    headers=headers,
                )
                elapsed = (time.perf_counter() - start) * 1000
                error = (
                    response.json().get("error", {}).get("code", "?")
                    if response.status_code != 201
                    else ""
                )
                return response.status_code, elapsed, error

            start = time.perf_counter()
            outcomes = await asyncio.gather(*[send(i) for i in range(30)])
            wall = (time.perf_counter() - start) * 1000
            durations = [elapsed for _, elapsed, _ in outcomes]
            failures = [(status, error) for status, _, error in outcomes if status != 201]
            if failures:
                print(f"  (concurrent sends: {len(failures)} non-201: {failures[:3]})")
            succeeded = len(outcomes) - len(failures)
            print(
                f"{'messages: 30 concurrent sends (wall clock)':<42} n={succeeded:<4} "
                f"total={wall / 1000:6.2f}s throughput={succeeded / (wall / 1000):8.1f} msg/s"
            )

            # ---------------- files ----------------
            payload = b"B" * (1024 * 1024)  # 1 MiB
            durations = []
            for i in range(20):
                start = time.perf_counter()
                response = await client.post(
                    "/api/v1/files",
                    files={"file": (f"bench{i}.txt", payload, "text/plain")},
                    headers=headers,
                )
                durations.append((time.perf_counter() - start) * 1000)
                assert response.status_code == 201
            report("files: upload 1MiB text (hash+spool)", durations, 20)

            # extraction throughput via worker
            from app.workers.runner import Worker

            worker = Worker(app.state.database, settings, app.state.bus, worker_id="bench")
            start = time.perf_counter()
            ran = await worker.drain(max_wait_s=30.0)
            wall = (time.perf_counter() - start) * 1000
            print(
                f"{'files: async extraction of 20 x 1MiB':<42} n={ran:<4} "
                f"total={wall / 1000:6.2f}s throughput={ran / (wall / 1000):8.1f} files/s"
            )

            # ---------------- autonomous tasks ----------------
            app.state.settings.auto_approve_plans = True
            plan = {
                "objective_restated": "bench",
                "steps": [
                    {
                        "id": "compute",
                        "title": "Run python step",
                        "tool_key": "python.execute",
                        "input": {"code": "print(sum(range(1000)))"},
                        "timeout_s": 30,
                    },
                    {
                        "id": "write",
                        "title": "Write result",
                        "tool_key": "fs.write",
                        "input": {"path": "out.txt", "content": "done"},
                        "depends_on": ["compute"],
                        "timeout_s": 30,
                    },
                ],
            }
            durations = []
            for i in range(10):
                start = time.perf_counter()
                response = await client.post(
                    "/api/v1/tasks",
                    json={"title": f"bench task {i}", "objective": "bench", "plan": plan},
                    headers=headers,
                )
                durations.append((time.perf_counter() - start) * 1000)
                assert response.status_code == 201
            report("tasks: create (2-step plan)", durations, 10)

            start = time.perf_counter()
            ran = await worker.drain(max_wait_s=60.0)
            wall = (time.perf_counter() - start) * 1000
            print(
                f"{'tasks: execute 10 x (python+fs) steps':<42} n={ran:<4} "
                f"total={wall / 1000:6.2f}s throughput={ran / (wall / 1000):8.1f} tasks/s"
            )
            app.state.settings.auto_approve_plans = False

            # ---------------- streaming ----------------
            durations = []
            for i in range(20):
                start = time.perf_counter()
                request = client.build_request(
                    "POST",
                    f"/api/v1/conversations/{conversation['id']}/messages/stream",
                    json={"content": f"stream bench {i}"},
                    headers=headers,
                )
                response = await client.send(request, stream=True)
                async for _ in response.aiter_lines():
                    pass
                await response.aclose()
                durations.append((time.perf_counter() - start) * 1000)
            report("streaming: SSE round trip (test adapter)", durations, 20)

    print("\nBenchmark environment:")
    print(f"  python : {sys.version.split()[0]}")
    print(f"  cpus   : {os.cpu_count()}")
    print("  db     : SQLite WAL (aiosqlite)")
    print("  storage: local filesystem")
    print("  model  : deterministic test adapter (latency excluded by design)")

    await app.state.database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
