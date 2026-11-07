# Local Deployment

Three supported shapes, from smallest to most complete.

## 1. Single-process development (SQLite + embedded worker)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
AGP_WORKER_EMBEDDED=true uvicorn --factory app.main:create_app --reload --port 8000
```

Everything (API + worker + events) runs in one process — good for
development, acceptable for a single-user private box. Multi-user
isolation remains fully active.

## 2. API + separate worker (still SQLite)

```bash
# terminal 1
uvicorn --factory app.main:create_app --port 8000
# terminal 2
python -m app.worker
```

Jobs queue in the database; the worker claims and executes them. Start
more workers by running the command again (claims are CAS-safe).

## 3. Local stack with Docker (PostgreSQL + Redis)

```bash
cp .env.example .env
# set at least: POSTGRES_PASSWORD, AGP_SECRET_KEY
docker compose up -d          # api + worker + migrate + postgres + redis
docker compose logs -f api worker
```

Verify: `curl localhost:8000/api/v1/health/ready` → 200 with
`database.migrations current`.

## Single-user private deployment notes

The architecture keeps multi-user isolation even for one user. For a
private box you may additionally:

* bootstrap an admin on first start:
  `AGP_BOOTSTRAP_ADMIN_EMAIL=… AGP_BOOTSTRAP_ADMIN_PASSWORD=…`
* enable plan auto-approval to skip the approval round-trip
  (`AGP_AUTO_APPROVE_PLANS=true`) — only for trusted single-user use;
  step-level approval for sensitive tools (terminal) stays active.
* keep the local sandbox backend (process isolation with rlimits) or
  switch to `AGP_SANDBOX_BACKEND=docker` if Docker is available.

## Operations quick reference

| Task | Command |
| --- | --- |
| run tests | `python -m pytest tests/ -q` |
| benchmarks | `python scripts/benchmark.py` |
| apply migrations | `alembic upgrade head` |
| run a job sweep manually | start `python -m app.worker` |
| inspect jobs | `GET /api/v1/admin/jobs` (admin token) |
| health | `GET /api/v1/health/live` · `GET /api/v1/health/ready` |
| metrics | `GET /metrics` |

## Data locations (default local layout)

```
data/
├── app.db              # SQLite database (dev)
├── storage/            # uploaded objects (content-addressed layout)
└── sandboxes/<task>/   # per-task execution workspaces
```

Back these up together with the database (see docs/DATABASE.md).
