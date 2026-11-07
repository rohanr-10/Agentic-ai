# Engineering Report — Agentic Platform Backend

**Date:** 2026-10-31 · **Version:** 0.1.0 · **Stack:** Python 3.12 / FastAPI /
Pydantic v2 / SQLAlchemy 2 (async) / Alembic / PostgreSQL|SQLite / Docker

This report separates **VERIFIED** (demonstrated by the executed test
suite), **PARTIALLY IMPLEMENTED** (works with documented caveats) and
**NOT IMPLEMENTED** functionality. Test results below are the actual
output of the final run on this machine.

---

## A. Implemented components

| # | Component | Location | Status |
|---|---|---|---|
| 1 | API layer (REST + SSE + WebSocket, 64 routes) | `app/api/` | VERIFIED |
| 2 | Authentication & authorization | `app/services/auth.py`, `app/core/security.py` | VERIFIED |
| 3 | Conversation management | `app/services/conversations.py` | VERIFIED |
| 4 | Message management (+ regenerate, edit, idempotency) | same | VERIFIED |
| 5 | File management & processing | `app/services/files.py`, `app/services/extraction.py`, `app/storage/` | VERIFIED |
| 6 | Model gateway (contract + manager) | `app/gateway/base.py`, `manager.py` | VERIFIED |
| 7 | Test model adapter | `app/gateway/adapters/test_adapter.py` | VERIFIED |
| 8 | OpenAI-compatible adapter (Saluki boundary) | `app/gateway/adapters/openai_compatible.py` | VERIFIED against mock server; NOT verified against a real Saluki runtime |
| 9 | Agent orchestration (state machine, planning, scheduling) | `app/orchestration/` | VERIFIED |
| 10 | Autonomous execution engine | `app/orchestration/executor.py` | VERIFIED |
| 11 | Verification engine | `app/orchestration/verifier.py` | VERIFIED |
| 12 | Task management + approvals | `app/services/tasks.py`, `approvals.py` | VERIFIED |
| 13 | Memory engine | `app/services/memory.py` | VERIFIED |
| 14 | Tool registry + execution framework | `app/tools/` | VERIFIED |
| 15 | Execution sandbox (local process) | `app/tools/sandbox.py` | VERIFIED end-to-end |
| 16 | Execution sandbox (Docker driver) | `app/tools/sandbox.py` | PARTIALLY IMPLEMENTED (command construction unit-tested; no Docker daemon in CI environment) |
| 17 | Background workers (durable queue, reaper, heartbeats) | `app/jobs/`, `app/workers/` | VERIFIED |
| 18 | Notifications & streaming (SSE/WS/event bus) | `app/events/bus.py`, routers | VERIFIED |
| 19 | Persistence & migrations (19 tables) | `app/db/`, `alembic/` | VERIFIED |
| 20 | Security implementation | `app/core/*`, service layers | VERIFIED |
| 21 | Observability (structured logs, metrics, health, optional OTel) | `app/core/telemetry.py`, `logging.py` | VERIFIED (metrics/logs/health); OTel = integration-verified import path only |
| 22 | Docker configuration | `Dockerfile`, `Dockerfile.worker`, `docker-compose.yml` | PARTIALLY IMPLEMENTED (no Docker daemon in CI environment to execute; files follow production practice) |
| 23 | Documentation set | `docs/`, `README.md` | VERIFIED |

## B. Repository structure

```
agentic-platform-backend/
├── app/
│   ├── api/            # routers, DI, SSE/WS endpoints (deps.py, v1/*)
│   ├── core/           # config, errors, security, logging, middleware,
│   │                   # rate limiting, telemetry, utils
│   ├── db/             # Base, session, enums, models/ (8 modules, 19 tables)
│   ├── events/         # pub/sub bus (in-memory + Redis)
│   ├── gateway/        # adapter contract, manager, adapters/ (test, openai-compat)
│   ├── jobs/           # durable queue + handler registry
│   ├── orchestration/  # states, planner, executor, verifier, recovery
│   ├── schemas/        # Pydantic request/response models (10 modules)
│   ├── services/       # auth, conversations(+context), files(+extraction),
│   │                   # memory, tasks, approvals, models, audit, maintenance
│   ├── storage/        # local + S3 backends
│   ├── tools/          # base, registry, executor, sandbox, builtin/ (9 tools)
│   ├── workers/        # worker runtime
│   ├── main.py         # application factory + lifespan
│   └── worker.py       # standalone worker entry point
├── alembic/            # async migrations (initial schema: 19 tables)
├── tests/              # unit / integration / security / concurrency / recovery
├── docs/               # architecture, API, database, deployments, Saluki,
│                       # security, operations, this report
├── scripts/            # benchmark.py
├── Dockerfile, Dockerfile.worker, docker-compose.yml
├── .env.example, Makefile, pyproject.toml, requirements*.txt
```

91 Python source files (14,036 lines) · 23 test files (4,756 lines).

## C. Architecture decisions

1. **Model independence via the gateway** — the entire backend programs
   against `ModelAdapter`; the Saluki boundary is one adapter module plus
   configuration. Connecting a differently-shaped API means a new adapter
   class, zero core changes (the central requirement of the directive).
2. **The orchestrator owns lifecycle truth** — a 12-state machine with
   compare-and-set transitions (`tasks.version`), terminal-state
   protection, and human approval gates for every plan (client- or
   model-authored) and for sensitive steps. Model proposals are always
   validated; model claims are never treated as results.
3. **Database as the durable backbone** — the job queue lives in the
   primary database (atomic enqueue with domain writes, CAS claims,
   leases, dead-letters, idempotency keys) instead of importing a broker;
   Redis remains an optional coordination layer, never the source of truth.
4. **Checkpoints, not hope** — task execution persists progress after every
   step-wave; crashed workers' successors resume without re-running work
   (test-verified).
5. **Structural security** — service-layer ownership checks with uniform
   404s, path jails, argv-only allowlisted execution, SSRF validation at
   the DNS layer, savepoint-based concurrency control, scrubbed sandbox
   environments, rlimits and budgets. Prompt-level controls are explicitly
   treated as mitigations, not boundaries.
6. **Bounded everything** — context assembly windows, step/tool/token
   budgets, deadlines, queue and event-queue sizes, output caps.
7. **Conflict resolution documented, not hidden** — e.g. the SQLite
   single-writer constraint led to commit-before-tool-execution and
   savepoint+refresh patterns (commented in code); the seq-race retry
   loop converges to dense ordering (test-verified under 20/30-way
   concurrency).

## D. Database schema

19 tables: `users`, `auth_sessions`, `password_reset_tokens`,
`conversations`, `messages`, `attachments`, `files`, `model_configs`,
`tasks`, `task_plans`, `task_executions`, `approval_requests`,
`tool_definitions`, `tool_executions`, `memory_records`,
`memory_versions`, `audit_events`, `background_jobs`, `notifications`
(+ `alembic_version`). Full notes, constraints, ERD, migration and
backup/restore procedures: `docs/DATABASE.md`. One Alembic revision
(`cd1c5889aa4e`) creates everything; upgrade **and** downgrade verified
on SQLite; the same migration targets PostgreSQL via the asyncpg URL.

## E. API documentation location

* Live interactive: `/docs` (Swagger UI), `/redoc`
* Machine-readable: `/api/openapi.json` (64 routes, bearer security
  scheme, unique operation ids, response schemas — contract-tested)
* Written reference incl. SSE/WS protocols: `docs/API.md`

## F. Security controls

Full threat model and control list: `docs/SECURITY.md`. Highlights:
argon2id + hashed/rotating refresh tokens with replay response; per-request
session revalidation; uniform cross-user 404s; content-verified uploads
with extension cross-check and size caps; workspace-jailed, rlimit-capped,
secret-free sandbox with process-group kill; allowlisted argv-only terminal
tool; SSRF guard covering loopback/private/link-local/reserved addresses
and cloud metadata IPs; rate limiting per scope with Redis option; secret
redaction in logs and error envelopes; append-only audit trail; approval
gates for plans and sensitive tools; bounded queues/outputs everywhere.

## G. Test commands

```bash
cd agentic-platform-backend
python -m pytest tests/ -q            # full suite
python -m pytest tests/unit -q        # unit only
python -m pytest tests/security -q    # isolation + attacks
python scripts/benchmark.py           # performance measurements
```

## H. Actual test results (final run, this machine)

```
265 passed, 8 warnings in 56.89s
```

| Category | Tests | Result |
|---|---|---|
| unit | 149 | all passed |
| integration | 74 | all passed |
| security | 29 | all passed |
| concurrency | 5 | all passed |
| recovery | 8 | all passed |

The suite runs the real stack — genuine Alembic migrations, real SQLite
(WAL) database, real filesystem storage, real subprocess sandboxes — with
mocks only where the boundary is external by definition (DNS resolution,
the OpenAI mock server, scripted model behaviour).

**Measured benchmarks** (2 vCPU sandbox, SQLite, local storage,
deterministic test adapter — model latency excluded by design; script:
`scripts/benchmark.py`):

```
auth: register (argon2 hash)               12.1 ops/s   p50= 85.0ms  p95= 91.7ms
auth: login (argon2 verify)                11.9 ops/s   p50= 86.9ms  p95= 92.4ms
conversations: create                     178.9 ops/s   p50=  4.6ms  p95= 15.9ms
conversations: list (50/page)             185.3 ops/s   p50=  5.4ms  p95=  5.7ms
messages: send + generate (test adapter)   57.9 ops/s   p50= 16.9ms  p95= 21.7ms
messages: list page (50 msgs)              28.9 ops/s   p50= 27.9ms  p95= 52.1ms
messages: 30 concurrent sends (wall)       28.0 msg/s   total= 1.07s
files: upload 1MiB (hash+spool)            85.3 ops/s   p50= 11.1ms  p95= 17.7ms
files: async extraction 20 x 1MiB          77.8 files/s total= 0.26s
tasks: create (2-step plan)                95.3 ops/s   p50=  9.3ms  p95= 15.8ms
tasks: execute 10 x (python+fs) steps      18.0 tasks/s total= 0.56s
streaming: SSE round trip (test adapter)   64.5 ops/s   p50= 14.6ms  p95= 20.2ms
```

Auth throughput is dominated by the deliberate Argon2id cost (~85 ms) —
correct for password hashing, not a defect. These are single-node SQLite
figures; PostgreSQL deployments differ and should be re-measured in situ.

## I. Known limitations

1. **Saluki 27B is not connected.** By directive, no inference behaviour
   was fabricated. The OpenAI-compatible adapter is contract-tested
   against a mock; real integration follows `docs/SALUKI_INTEGRATION.md`.
2. **Docker sandbox driver not executed here** (no Docker daemon in the
   build environment). Command construction, isolation flags and the
   timeout wrapper are unit-tested; run it on a Docker host before
   enabling `AGP_SANDBOX_BACKEND=docker`.
3. **Local sandbox shares the host kernel** — appropriate for trusted
   single-tenant use; use the Docker sandbox for untrusted plans.
4. **Memory retrieval is keyword-based** (bounded candidate scoring:
   relevance + recency + importance + provenance trust). The interface is
   designed for a later embedding backend; no vector store was added
   because the requirement did not yet justify one.
5. **Images are stored but not analysed** (no OCR/vision pre-processing
   in v1); models receive them only through future multimodal adapters.
6. **Malware scanning is an integration point** (`scan_status`,
   `quarantine_file`) — no AV engine is wired; treat uploads as
   untrusted content.
7. **Password-reset email delivery** is a structured-log integration
   point; SMTP is out of scope for the backend.
8. **Rate limiting is per-process** unless Redis is configured.
9. **Chat tool loop is bounded to 3 rounds** by default; deeper tool
   chains belong to autonomous tasks by design.
10. **SQLite concurrency ceiling** — safe under tested load (dense
    ordering, idempotency, no writer corruption) but single-writer by
    nature; use PostgreSQL for multi-user production (enforced by config
    validation in production mode).
11. **Model-assisted verification is advisory-only** by design (never
    decides completion); rule-based checks are the deciding authority.

## J. Unimplemented requirements (explicit)

* Real Saluki 27B adapter verification (boundary + procedure provided).
* Email/SMS notification delivery channels (in-app notifications exist).
* Malware scanner binding (integration point exists).
* Embedding/vector retrieval (interface designed, deliberately deferred).
* Multimodal (image) model input pre-processing.
* Kubernetes manifests (a deployment sketch is documented; images are
  plain processes).
* Frontend (out of scope by directive).

## K. Integration instructions for Saluki 27B

See `docs/SALUKI_INTEGRATION.md` for the full procedure. Summary:

1. If Saluki exposes an OpenAI-compatible `chat/completions` API: set
   `AGP_SALUKI_ENDPOINT` (+ key, model id, verified context window),
   restart, probe `/models/{id}/health`, then set the model default and
   its verified capability flags.
2. Otherwise: implement one new `ModelAdapter` subclass (contract in
   `app/gateway/base.py`), register the adapter type, add a model config
   row — no core changes required.
3. Run the six-step verification checklist (health, non-streaming,
   streaming, structured planning, context window, usage reporting)
   before declaring the model production-ready.

## L. Deployment instructions

* **Local (dev / single node)**: `docs/DEPLOYMENT_LOCAL.md` — SQLite or
  embedded-worker quick start, separate worker, local Docker stack.
* **Cloud**: `docs/DEPLOYMENT_CLOUD.md` — the compose topology (api,
  worker, migrate, PostgreSQL, Redis, optional MinIO), hardening
  checklist, sandbox backend choice, Kubernetes sketch,
  upgrade/rollback procedures.
* **Operations**: `docs/OPERATIONS.md` — probes, metrics, job
  administration, incident table, maintenance, backup pointers
  (`docs/DATABASE.md`).

---

## Acceptance-gate summary (per the directive)

| Requirement | Status | Evidence |
|---|---|---|
| Application starts successfully | VERIFIED | smoke script + 265 tests boot the real app through its lifespan |
| Database migrations work | VERIFIED | real `alembic upgrade head` in every test; downgrade verified |
| Authentication works | VERIFIED | 15 auth integration tests |
| User isolation verified | VERIFIED | cross-user suites over conversations/files/tasks/memory/approvals/notifications |
| Conversations persist across restarts | VERIFIED | `test_state_survives_restart` (two app instances, same DB) |
| Message creation & retrieval work | VERIFIED | incl. ordering, idempotency, concurrency, regenerate |
| File upload & retrieval work securely | VERIFIED | MIME spoofing/size/traversal tests + ownership checks |
| Streaming contracts work with the test adapter | VERIFIED | SSE lifecycle, tool-call round trip, interrupt persistence |
| Model gateway contracts documented & tested | VERIFIED | this report + adapter contract suites (incl. mock OpenAI server) |
| Autonomous task lifecycle works | VERIFIED | full lifecycle: create → plan approval → execution → verification → artifacts |
| Task cancellation & recovery work | VERIFIED | mid-run subprocess kill < 15 s; checkpoint resume without re-execution; lease-expiry requeue |
| Tool execution appropriately isolated | VERIFIED | local sandbox suite (jail, env scrubbing, rlimits, kill, no shell) + allowlists + SSRF guard |
| Memory operations persist correctly | VERIFIED | engine + API suites incl. versioning, verification promotion, integrity |
| API documentation generated | VERIFIED | `/docs`, `/redoc`, `/api/openapi.json` (contract-tested) |
| Automated tests executed | VERIFIED | 265/265 passed (results above) |
| Security checks performed | VERIFIED | 29 dedicated tests + redaction/error-hygiene checks |
| Local deployment documented | VERIFIED | `docs/DEPLOYMENT_LOCAL.md` |
| Cloud deployment configuration supplied | PARTIALLY VERIFIED | compose/Dockerfiles supplied and reviewed; not executed here (no Docker daemon) |

**This backend is NOT declared production-ready merely because it runs
locally.** It is a complete, independently testable, security-engineered
foundation whose remaining pre-production items are: real Saluki
integration + verification (§K), executing the Docker sandbox driver and
compose stack on Docker-enabled infrastructure, and PostgreSQL-stage load
testing.
