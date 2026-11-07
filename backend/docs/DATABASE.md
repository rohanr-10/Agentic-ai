# Database

## Overview

* **Engine**: PostgreSQL 16+ in production (`postgresql+asyncpg://…`),
  SQLite (WAL) for local development and tests — same schema, same
  migrations.
* **ORM**: SQLAlchemy 2.x async; every timestamp is naive UTC
  (`TIMESTAMP WITHOUT TIME ZONE`); UUID primary keys; JSON columns use
  JSONB on PostgreSQL.
* **Migrations**: Alembic only — `Base.metadata.create_all()` is never a
  production strategy. `alembic/versions/cd1c5889aa4e_initial_schema.py`
  creates the full initial schema (19 tables + alembic_version).

## Entity relationship overview

```
users ─┬─< auth_sessions
       ├─< password_reset_tokens
       ├─< conversations ──< messages ──< attachments >── files
       ├─< files
       ├─< tasks ─┬─< task_plans
       │          ├─< task_executions ──< tool_executions
       │          ├─< approval_requests
       │          └─< background_jobs (by payload)
       ├─< memory_records ──< memory_versions
       ├─< notifications
       └─< audit_events (SET NULL on delete)
tool_definitions (registry mirror)
model_configs
background_jobs (durable queue)
```

## Table notes

| Table | Purpose / notable constraints |
| --- | --- |
| `users` | unique lowercased email, role (`admin`/`user`), `is_active` |
| `auth_sessions` | refresh-token **hashes** only; `rotated_from_id` chain; revocation columns |
| `conversations` | status/mode, optional system prompt + default model |
| `messages` | `UNIQUE(conversation_id, seq)` ordering; `UNIQUE(conversation_id, client_request_id)` idempotency; `is_active` for regenerate history; roles user/assistant/system/tool |
| `attachments` | composite PK (message, file) |
| `files` | content-verified `mime_type`, sha256, opaque `storage_key`, extraction status |
| `model_configs` | adapter type + endpoint + masked api_key + capabilities + context window |
| `tasks` | status (12-state machine), `version` (CAS), plan snapshot, checkpoint, budgets, usage, deadline |
| `task_plans` | immutable per-version snapshots (source: model/client/replan), approval stamps |
| `task_executions` | one row per step **attempt** |
| `tool_definitions` | DB mirror of the code registry (deployment enable/disable) |
| `tool_executions` | audit of every tool invocation (input/output/status/duration/sandbox) |
| `memory_records` / `memory_versions` | category/scope/source, content hash integrity, full history |
| `approval_requests` | plan/step approvals with expiry + decision trail |
| `audit_events` | append-only security trail (login, transitions, downloads, denials…) |
| `background_jobs` | durable queue: CAS claiming, leases, attempts, dead-letter, idempotency keys |
| `notifications` | user-facing events (approval needed, task finished, file ready) |

## Migrations

```bash
# apply (uses AGP_DATABASE_URL from the environment)
alembic upgrade head

# inspect / roll back
alembic current
alembic history
alembic downgrade -1

# after changing models — REVIEW the generated file before committing
alembic revision --autogenerate -m "describe the change"
```

Migration rules:

1. Never edit an applied revision; add a new one.
2. Test new revisions against BOTH SQLite and PostgreSQL
   (`AGP_DATABASE_URL=postgresql+asyncpg://… alembic upgrade head` on a
   scratch database).
3. Keep revisions additive first (add column → backfill → enforce).

## Backup & restore

### PostgreSQL

```bash
# backup (consistent, includes schema + data)
pg_dump --format=custom --dbname="$DATABASE_URL" > agp-$(date +%F).dump

# restore into a fresh database
createdb agp_restored
pg_restore --dbname=agp_restored --clean --if-exists agp-2026-10-31.dump

# verify migration state matches the code
AGP_DATABASE_URL=postgresql+asyncpg://…/agp_restored alembic current
```

Recommended cadence: nightly full dump + WAL archiving (point-in-time
recovery). Object storage (files) is backed up separately — bucket
versioning or scheduled sync.

### SQLite (local development)

```bash
sqlite3 data/app.db ".backup 'backup-$(date +%F).db'"
# restore: stop the API, replace the file, start, verify /health/ready
```

## Connection pooling

`Database` configures pooling for PostgreSQL (`pool_pre_ping`, sized via
`AGP_DB_POOL_SIZE` / `AGP_DB_MAX_OVERFLOW`); SQLite uses per-session
connections with WAL, `busy_timeout=30000ms` and enforced foreign keys.
Workers and the API each own their pool.
