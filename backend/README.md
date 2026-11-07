# Agentic Platform Backend

Production-oriented backend for a **conversational / autonomous agentic AI
platform**, built to be model-independent: the primary model (Underdog
Saluki 27B) integrates through a gateway adapter, and the shipped
deterministic **test adapter** proves every contract without pretending to
be a real model.

## What this is

* **Conversational mode** — persistent conversations, bounded context
  assembly, file analysis via tools, SSE streaming, tool-call loop,
  message provenance, search, regenerate.
* **Autonomous mode** — objective → validated plan (DAG) → human approval
  → sandboxed tool execution with budgets/checkpoints → rule-based
  verification → artifacts + execution history.
* **Hybrid mode** — chat while tasks run; notes can be injected into a
  live task; pause / resume / cancel propagate into running subprocesses.

## Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic ·
PostgreSQL (prod) / SQLite (dev) · Redis (optional coordination) ·
Docker · structlog · Prometheus metrics · optional OpenTelemetry.
Pinned versions in `requirements.txt`.

## Quick start (local development)

```bash
cd agentic-platform-backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env                        # defaults work for local dev
alembic upgrade head                        # create the SQLite schema

# terminal 1: API with embedded worker
AGP_WORKER_EMBEDDED=true uvicorn --factory app.main:create_app --reload --port 8000

# terminal 2 (alternative to embedded): standalone worker
python -m app.worker
```

Open http://localhost:8000/docs for interactive API documentation.

### Exercise it in 60 seconds

```bash
# register + login
TOKEN=$(curl -s localhost:8000/api/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"Str0ngPassw0rd!","display_name":"Me"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"

# chat (streaming)
curl -N localhost:8000/api/v1/conversations -H "$AUTH" -H 'content-type: application/json' \
  -d '{"title":"first"}'     # -> note the id
curl -N localhost:8000/api/v1/conversations/<id>/messages/stream \
  -H "$AUTH" -H 'content-type: application/json' -d '{"content":"hello"}'

# autonomous task with a client plan (requires approval by default)
curl -s localhost:8000/api/v1/tasks -H "$AUTH" -H 'content-type: application/json' -d '{
  "title": "write report", "objective": "create report.txt",
  "plan": {"steps": [{"id": "w", "title": "write", "tool_key": "fs.write",
    "input": {"path": "report.txt", "content": "hello"}}]}}'
# then: GET /api/v1/approvals -> POST /api/v1/approvals/<id> {"decision":"approve"}
```

## Tests

```bash
python -m pytest tests/ -q          # full suite (unit+integration+security+concurrency+recovery)
python -m pytest tests/unit -q      # fast unit tests
python scripts/benchmark.py         # measured performance on this machine
```

The suite runs the **real** stack: actual Alembic migrations, real SQLite
database, real filesystem storage, real subprocess sandboxes. No mocks on
the critical path.

## Documentation

| Document | Content |
| --- | --- |
| `docs/ARCHITECTURE.md` | subsystems, boundaries, lifecycle, job queue design |
| `docs/API.md` | endpoint reference, streaming protocols, error model |
| `docs/DATABASE.md` | schema, ERD, migrations, backup/restore |
| `docs/DEPLOYMENT_LOCAL.md` | local single-node deployment |
| `docs/DEPLOYMENT_CLOUD.md` | Docker / PostgreSQL / Redis / S3 deployment |
| `docs/SALUKI_INTEGRATION.md` | how to connect and verify the real model |
| `docs/SECURITY.md` | threat model, controls, audit events |
| `docs/OPERATIONS.md` | runbook: health, metrics, jobs, recovery, upgrades |
| `docs/ENGINEERING_REPORT.md` | implementation status (verified / partial / not implemented) |

## Honest scope statement

The model gateway, orchestration, tools, memory and file subsystems are
fully implemented and tested against the deterministic test adapter and a
mock OpenAI-compatible server. **No claim is made about Saluki 27B
behaviour until the real runtime is connected and verified** — see
`docs/SALUKI_INTEGRATION.md`. The Docker sandbox driver is implemented but
was not executed in the CI environment (no Docker daemon); its command
construction is unit-tested and the local process sandbox is exercised
end-to-end.
