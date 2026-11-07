# Operations Runbook

## Health & monitoring

| Probe | Endpoint | Meaning |
| --- | --- | --- |
| liveness | `GET /api/v1/health/live` | process is up (no dependencies) |
| readiness | `GET /api/v1/health/ready` | DB reachable, migrations current, storage writable (+ Redis, + default model when configured); 503 + component detail when not |
| metrics | `GET /metrics` | Prometheus exposition |

Key metrics: `agp_http_requests_total`, `agp_http_request_duration_seconds`,
`agp_model_requests_total{model,adapter,status}`, `agp_model_request_duration_seconds`,
`agp_tool_executions_total{tool,status}`, `agp_task_transitions_total{to_state}`,
`agp_background_jobs_total{kind,status}`.

Structured logs (JSON in production) carry `request_id`, `user_id`,
`route`, `status`, `duration_ms` on every request. Give the log platform
the `request_id` from any error envelope to find the matching lines.

## Background jobs

Inspect (admin token): `GET /api/v1/admin/jobs?status=dead`.
Requeue a dead job: `POST /api/v1/admin/jobs/{id}/requeue`.

Worker semantics: jobs claim atomically, heartbeat their lease, retry with
exponential backoff + jitter, and dead-letter after `max_attempts`. The
reaper re-queues jobs whose worker died (lease expiry). Handlers are
idempotent (file extraction replays; task execution resumes from
checkpoints).

## Task recovery

* A task stuck `running` with no worker heartbeat for
  `AGP_TASK_STALE_AFTER_S` (default 300 s) is re-enqueued by
  `recover_stale_tasks` (invoked from the recovery job / worker tick) and
  resumes from its checkpoint — completed steps are not re-executed.
* Pending approvals expire after 48 h; the task then fails with
  `approval_expired`.
* VERIFYING-state crashes re-run verification on the next execution pass.

## Common incidents

| Symptom | Diagnosis | Action |
| --- | --- | --- |
| readiness 503 "migrations NOT current" | code newer than DB | run `alembic upgrade head` (the `migrate` compose service does this) |
| readiness 503 "storage probe failed" | volume full/RO or S3 misconfig | check disk / bucket credentials |
| jobs piling up `queued` | worker down or saturated | `docker compose ps worker`; scale replicas; check `agp_background_jobs_total` |
| job `dead` with `model_*` error | model runtime unavailable | fix runtime, then requeue the job |
| task `failed` with `budget_exceeded` | by design | raise budgets per task or split the objective |
| 429s in bursts | rate limits working | adjust `AGP_RATE_LIMIT_*` if legitimate traffic |
| "database is locked" (SQLite only) | concurrent writers on SQLite | expected ceiling of local mode; move to PostgreSQL for real load |

## Maintenance jobs

`maintenance.cleanup` (durable job) prunes expired sessions/reset tokens,
expired memories (TTL + retention), old audit events
(`audit_retention_days`), failed files (30 days) and expired approvals.
Trigger it manually by enqueuing the job or wait for the scheduled run.

## Upgrade procedure

1. Snapshot the database (`pg_dump`) and object storage.
2. `git pull && docker compose build`.
3. `docker compose run --rm migrate` (additive, reviewed migration).
4. `docker compose up -d api worker`.
5. Watch `/health/ready`, error rates and `agp_task_transitions_total`.

Rollback: redeploy the previous images; only revert a migration if the
new code absolutely requires it (`alembic downgrade -1`).

## Manual job sweep / recovery triggers

```bash
# enqueue a recovery scan + maintenance pass (requires a DB session; or
# restart the worker which runs them on its tick)
docker compose restart worker
```
