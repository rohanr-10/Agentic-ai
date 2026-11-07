# Architecture

## Design principles

1. **Model independence.** Everything above the gateway programs against
   `ModelAdapter` (see `app/gateway/base.py`). No Saluki/OpenAI/… specifics
   exist outside adapter implementations.
2. **The orchestrator owns the truth.** Models *propose*; validated state
   machines, schema validation and the rule-based verifier *decide*.
   Model-generated content is never treated as an execution result.
3. **Durable by default.** Conversations, tasks, jobs, memory, approvals and
   audit events live in the primary database. Redis (when configured) only
   carries ephemeral fan-out (events, rate limits) and is never the source
   of truth.
4. **Security is structural.** Ownership checks at the service layer,
   uniform 404s for foreign resources, path jails, sandboxed execution,
   SSRF guards, allowlists — enforced by code paths, not by prompts.
5. **No unbounded anything.** Context windows, step counts, tool budgets,
   deadlines, queue sizes, output sizes and event-queue sizes are all
   bounded.

## Subsystem map

```
┌────────────────────────────────────────────────────────────────────┐
│  A. API layer            app/api/          REST + SSE + WebSocket  │
│  B. Auth                 app/services/auth.py + app/core/security  │
│  C/D. Conversations &    app/services/conversations.py             │
│      Messages            + app/services/context.py (assembly)      │
│  E. Files                app/services/files.py + app/storage/      │
│  F. Model gateway        app/gateway/ (base, manager, adapters)    │
│  G/H. Orchestration &    app/orchestration/ (states, planner,      │
│      Planning                executor, verifier, recovery)         │
│  I. Task management      app/services/tasks.py + approvals.py      │
│  J. Memory               app/services/memory.py                    │
│  K/L. Tool registry &    app/tools/ (base, registry, executor,     │
│      execution               sandbox, builtin/)                    │
│  M. Sandbox              app/tools/sandbox.py (local + docker)     │
│  N. Verification         app/orchestration/verifier.py             │
│  O. Background workers   app/workers/runner.py + app/jobs/         │
│  P. Notifications &      app/events/bus.py + SSE/WS routers        │
│      streaming                                                 │
│  Q. Persistence          app/db/ (models, session) + alembic/      │
│  R. Security & audit     app/core/* + app/services/audit.py        │
│  S. Observability &      app/core/telemetry.py + health router +   │
│      administration      admin router                             │
└────────────────────────────────────────────────────────────────────┘
```

Dependency direction: `api → services → (gateway|tools|db)` with
infrastructure (`core`, `events`, `jobs`, `storage`) shared horizontally.
No cycles: orchestration imports tools and gateway, never the reverse;
the job layer imports orchestration only inside handler functions.

## Task lifecycle

```
CREATED ─► QUEUED ─► PLANNING ─► AWAITING_APPROVAL ─► READY ─► RUNNING ─► VERIFYING ─► COMPLETED
              │           ▲             ▲                │        │  ▲        │
              │           └─────────────┼────────────────┘        │  └────────┘ (replan)
              ▼                         │ (step gate)             ▼
            PAUSED ◄────────────────────┘                     FAILED / CANCELLED
```

* Every transition goes through `transition()` — a compare-and-set on the
  `tasks.version` column with 3 retries, so API and worker races converge
  to a legal state. Terminal states are absorbing.
* **Checkpoints** (`tasks.checkpoint`) record completed steps after every
  execution wave; a crashed worker's successor skips finished work.
* Cancellation sets `cancel_requested`, publishes a control event on the
  bus and (same process) kills the sandbox process group instantly;
  cross-process latency is bounded by the bus and checked between waves.

## Background job design

`background_jobs` is a durable queue in the primary database:

* **enqueue** happens inside the caller's transaction (task + job commit
  atomically together); `idempotency_key` collapses duplicate enqueues.
* **claim** is `UPDATE … WHERE status='queued'` (CAS) — at most one
  active run per job, portable across SQLite/PostgreSQL.
* **heartbeat + lease**: workers refresh `locked_at`; a reaper requeues
  jobs whose lease expired — a crashed worker never swallows work.
* **retry**: exponential backoff with full jitter; after `max_attempts`
  the job is **dead-lettered** and visible via `/admin/jobs` (requeueable).
* handlers must be **idempotent** (e.g. `file.extract` replays safely,
  `task.execute` resumes from checkpoints).

## Context assembly (no full-history dumps)

`assemble_context` builds: system prompt → memory block (verified facts
separated from unverified model notes) → live task summary (hybrid mode) →
the last *N* messages within a character budget, oldest dropped first.
Tool outputs are wrapped in `<tool-output untrusted-data="true">` blocks —
a mitigation layer, not the security boundary.

## Event fan-out

`EventBus` has two interchangeable backends: in-process queues (dev /
single node / embedded worker) and Redis pub/sub (multi-process). SSE
endpoints replay a database snapshot before streaming live events;
WebSocket connections subscribe to owned topics with per-topic ownership
checks.

## Where state lives

| State | Store | Lifetime |
| --- | --- | --- |
| Conversations, messages, attachments | PostgreSQL / SQLite | until user delete |
| Files + extracted text | object storage + DB row | until user delete |
| Tasks, plans, executions, checkpoints | PostgreSQL / SQLite | until user delete |
| Approvals | PostgreSQL / SQLite | + 48h expiry |
| Memory records + versions | PostgreSQL / SQLite | retention policy |
| Background jobs | PostgreSQL / SQLite | retention / dead-letter |
| Audit events | PostgreSQL / SQLite | `audit_retention_days` |
| Rate-limit counters / live events | memory or Redis | ephemeral |
