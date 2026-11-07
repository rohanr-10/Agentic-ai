# API Reference (v1)

Base URL: `/api/v1` · Interactive docs: `/docs` (Swagger) and `/redoc` ·
Machine-readable spec: `/api/openapi.json`

## Conventions

* **Auth**: `Authorization: Bearer <access_token>` (from `/auth/register`
  or `/auth/login`). Sessions are re-validated against the database on
  every request, so logout/rotation take effect immediately.
* **Errors**: uniform envelope — never stack traces, never upstream bodies:

```json
{
  "error": {"code": "not_found", "message": "conversation not found", "details": null},
  "trace_id": "…"
}
```

* **Pagination**: `?limit=&offset=` with `Page<T>` envelopes
  (`items`, `total`, `limit`, `offset`).
* **Cross-user access** returns `404` (existence is not disclosed).

## Endpoints

### Auth
| Method | Path | Notes |
| --- | --- | --- |
| POST | `/auth/register` | 201, returns token pair; password policy enforced |
| POST | `/auth/login` | rate-limited; identical error for unknown user / wrong password |
| POST | `/auth/refresh` | rotates refresh token; reuse of a rotated token revokes ALL sessions |
| POST | `/auth/logout` | revokes the current session |
| GET/PATCH | `/auth/me` | profile read/update |
| POST | `/auth/password-reset` | always 202 (no account enumeration) |
| POST | `/auth/password-reset/confirm` | consumes the token, revokes sessions |
| POST | `/auth/password` | change password (keeps current session) |

### Conversations & messages
| Method | Path | Notes |
| --- | --- | --- |
| GET/POST | `/conversations` | list (filter `status`, search `q`) / create |
| GET/PATCH/DELETE | `/conversations/{id}` | read / rename+settings / delete (cascades) |
| POST | `/conversations/{id}/archive` · `/unarchive` | archived conversations reject messages |
| GET | `/conversations/{id}/messages` | ascending, paginated, `include_inactive` for regenerate history |
| POST | `/conversations/{id}/messages` | send + generate; `client_request_id` makes it idempotent; `attachments` (file ids) |
| POST | `/conversations/{id}/messages/stream` | **SSE** (below) |
| PATCH | `/conversations/{id}/messages/{message_id}` | edit user messages (provenance kept) |
| POST | `/conversations/{id}/messages/{message_id}/regenerate` | old version kept inactive |
| GET | `/conversations/{id}/search?q=` | case-insensitive substring search |

### Files
| Method | Path | Notes |
| --- | --- | --- |
| POST | `/files` | multipart; content-sniffed MIME must match extension; extraction queued |
| GET | `/files` / `/files/{id}` | list / metadata (`include_text=true` for extraction) |
| GET | `/files/{id}/download` | ownership-enforced content stream |
| DELETE | `/files/{id}` | removes storage object + row |

### Models
| Method | Path | Notes |
| --- | --- | --- |
| GET | `/models` | API keys always masked |
| GET | `/models/{id}/health` | live probe of the runtime (cached briefly) |
| POST/PATCH | `/models` | admin only |

### Tasks
| Method | Path | Notes |
| --- | --- | --- |
| GET/POST | `/tasks` | list (filter status/conversation) / create (optional client `plan`, `budgets`) |
| GET/DELETE | `/tasks/{id}` | detail incl. plan, checkpoint, usage, artifacts / delete when terminal |
| POST | `/tasks/{id}/pause` · `/resume` · `/cancel` | cancel propagates into running subprocesses |
| POST | `/tasks/{id}/notes` | hybrid mode: inject instructions into a live task |
| GET | `/tasks/{id}/executions` | per-step attempt history |
| GET | `/tasks/{id}/events?token=…` | **SSE** progress (snapshot replay + live) |

### Approvals / Tools / Memory / Notifications / Admin
| Method | Path | Notes |
| --- | --- | --- |
| GET/POST | `/approvals`, `/approvals/{id}` | decide `{"decision":"approve\|reject"}` re-drives the task |
| GET | `/tools`, `/tools/{key}` | registry with JSON schemas + permission metadata |
| POST | `/tools/{key}/execute` | direct user-initiated run (sandboxed/approval-gated tools refused) |
| GET/POST/PATCH/DELETE | `/memory`, `/memory/{id}` | CRUD; `POST /memory/search` scored recall |
| POST | `/memory/{id}/verify` | promotion to `verified` requires explicit evidence |
| GET | `/notifications`, `POST /notifications/{id}/read` | user notifications |
| GET | `/admin/jobs`, `POST /admin/jobs/{id}/requeue` | dead-letter operations (admin) |
| GET | `/admin/audit`, `/admin/stats` | audit trail + counters (admin) |

### Health & metrics
| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health/live` | process liveness |
| GET | `/health/ready` | DB + migrations + storage (+redis, +model); 503 when not ready |
| GET | `/metrics` | Prometheus exposition |

## Streaming protocols

### Chat SSE (`POST /conversations/{id}/messages/stream`)

```
event: message.created   data: {"message_id": "...", "role": "user", "seq": 1}
event: delta             data: {"text": "..."}                       (repeated)
event: tool_call         data: {"id": "...", "name": "http.request"}  (when the model calls a tool)
event: tool_result       data: {"id": "...", "name": "...", "message_id": "..."}
event: message.completed data: {"message_id": "...", "role": "assistant"}
event: error             data: {"message": "..."}                     (terminal)
event: done              data: {}
```

Client disconnect mid-stream persists the partial assistant message with
`meta.interrupted = true`.

### Task progress SSE (`GET /tasks/{id}/events?token=…`)

`snapshot` first (current status/usage), then live `task.status`,
`task.step`, `task.plan`, `task.note` events, `: keepalive` comments every
15 s, and a final `done` on a terminal state.

### WebSocket (`/api/v1/ws?token=…`)

```json
-> {"action": "subscribe",   "topics": ["task:<uuid>", "conversation:<uuid>", "user:<uuid>"]}
-> {"action": "unsubscribe", "topics": [...]}
<- {"topic": "...", "type": "...", "data": {...}, "timestamp": "..."}
```

Ownership is enforced per topic. Browsers cannot set Authorization headers
on WebSockets, hence the query token — use short-lived access tokens
(30 min default) and treat the URL as sensitive (see docs/SECURITY.md).

## Rate limits (defaults)

| Scope | Limit |
| --- | --- |
| auth endpoints | 10 / minute / identity |
| file uploads | 60 / minute / identity |
| everything else | 600 / minute / identity |

Responses carry `Retry-After` on 429.
