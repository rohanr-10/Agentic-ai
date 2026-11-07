# Backend API Integration Map

Frontend ↔ backend contract, derived from the backend source code
(`app/api/v1/*`, `app/schemas/*`, `app/services/conversations.py`, `app/orchestration/*`, `app/events/bus.py`) — the authoritative reference for every frontend feature.

Base URL: `VITE_API_BASE_URL` (default `/api/v1` through the Vite dev proxy, same-origin — no CORS changes needed on the backend).
Auth: `Authorization: Bearer <access_token>` on every endpoint except `POST /auth/login`, `POST /auth/register`, `POST /auth/refresh`, `POST /auth/password-reset*`.
Error envelope (all non-2xx): `{ "error": { "code": string, "message": string, "details": object|null }, "trace_id": string|null }`.

## 1. Authentication — `POST /auth/*` (rate-limited 10/min)

| Endpoint | Method | Auth | Request | Response | Notes |
|---|---|---|---|---|---|
| `/auth/register` | POST | — | `{email, password, display_name}` | **201** `TokenPair` | open registration; password ≥10 chars, ≥1 letter +1 digit, not common |
| `/auth/login` | POST | — | `{email, password}` | `TokenPair` | `TokenPair = {access_token, refresh_token, token_type:"bearer", expires_in, user: UserOut}` |
| `/auth/refresh` | POST | — | `{refresh_token}` | `TokenPair` | rotation; reuse of an old refresh token revokes ALL sessions (401 → force logout) |
| `/auth/logout` | POST | Bearer | optional `{refresh_token}` | **204** | revokes the current session server-side |
| `/auth/me` | GET | Bearer | — | `UserOut` | `{id, email, display_name, role: "admin"|"user", is_active, created_at, last_login_at}` |
| `/auth/me` | PATCH | Bearer | `{display_name}` | `UserOut` | |
| `/auth/password` | POST | Bearer | `{current_password, new_password}` | **204** | revokes other sessions |

## 2. Conversations & chat — `/conversations`

| Endpoint | Method | Auth | Request | Response | Notes |
|---|---|---|---|---|---|
| `/conversations` | POST | Bearer | `{title?, mode?, system_prompt?, model_config_id?}` (mode: `conversational|autonomous|hybrid`) | **201** `ConversationOut` | `ConversationOut={id,title,status:"active|archived",mode,system_prompt,model_config_id,message_count,last_message_at,created_at,updated_at}` |
| `/conversations` | GET | Bearer | `?limit≤200&offset&status&q` | `ConversationOut[]` | `q` filters by title |
| `/conversations/{id}` | GET/PATCH/DELETE | Bearer | PATCH `{title?, system_prompt?, mode?, model_config_id?}` | `ConversationOut` / **204** | DELETE cascades messages |
| `/conversations/{id}/archive` \| `/unarchive` | POST | Bearer | — | `ConversationOut` | |
| `/{id}/messages` | GET | Bearer | `?limit≤200&offset&include_inactive` | `MessageOut[]` **ascending seq** | `MessageOut={id,conversation_id,seq,role:"user|assistant|system|tool",content,parent_message_id,is_active,token_count,tool_call_id,tool_name,meta:{...},created_at,edited_at,attachments:[{file_id,filename,mime_type,size_bytes}]}` |
| `/{id}/messages` | POST | Bearer | `{content 1..100k, attachments?:uuid[]≤10, client_request_id?, model_config_id?}` | **201** `ChatResponse={user_message, assistant_message, tool_messages[]}` (all `MessageOut`) | assistant `meta`: `{model, adapter, finish_reason, tool_rounds, usage:{prompt_tokens,completion_tokens,estimated}}`; tool-call turn `meta.tool_calls=[{id,name,arguments}]`; archived → 400 |
| `/{id}/messages/stream` | POST | Bearer | same body | **SSE** | see §8 |
| `/{id}/messages/{mid}` | PATCH | Bearer | `{content}` | `MessageOut` | only user messages |
| `/{id}/messages/{mid}/regenerate` | POST | Bearer | — | `ChatResponse` (old message kept inactive) | only the active assistant message |
| `/{id}/search` | GET | Bearer | `?q&limit≤100&offset` | `SearchHit[]={message_id,seq,role,content_snippet,created_at}` | |

## 3. Models — `/models`

| Endpoint | Method | Auth | Request | Response |
|---|---|---|---|---|
| `/models` | GET | Bearer | — | `ModelConfigOut[]` (ordered default-first) — `{id,name,display_name,adapter_type:"test|openai_compatible",endpoint,api_key(masked),model_id,context_window,max_output_tokens,capabilities:{streaming,tool_calls,structured_output},parameters,is_default,is_enabled,created_at,updated_at}` |
| `/models/{id}` | GET | Bearer | — | `ModelConfigOut` |
| `/models/{id}/health` | GET | Bearer | — | `{model_id,name,healthy,detail,checked_at}` (10 s timeout, 15 s cache) |
| `/models` | POST | **admin** | `ModelConfigCreateIn` | **201** `ModelConfigOut` |
| `/models/{id}` | PATCH | **admin** | `ModelConfigUpdateIn` | `ModelConfigOut` |

`ModelConfigCreateIn={name (pattern [a-zA-Z0-9._-]+), display_name, adapter_type, endpoint?, api_key?, model_id?, context_window 256..2M, max_output_tokens 1..1M, capabilities{}, parameters{}, is_default?, is_enabled?}`. `openai_compatible` requires endpoint; `is_default=true` clears the previous default. Seeded: `test-echo` (default, TEST adapter) and `saluki-27b` (boundary, disabled without AGP_SALUKI_ENDPOINT). Registered via admin API at runtime (e.g. user's `sparkx-local` → Ollama). **Disable ≠ hidden**: `GET /models` returns all rows; the selector must filter `is_enabled` client-side. Capability gating: `capabilities.tool_calls/streaming/structured_output` are enforced by the gateway (`supports()`); low `max_output_tokens` is the only clamp.

## 4. Tasks — `/tasks`

| Endpoint | Method | Auth | Request | Response |
|---|---|---|---|---|
| `/tasks` | POST | Bearer | `{title, objective≤20k, conversation_id?, mode, priority 1-9, plan?: {objective_restated?, steps:[{id,title,description?,tool_key?,input{},depends_on[],requires_approval?,max_retries?,timeout_s 1-600,success_criteria[]}], notes?}, budgets:{max_steps≤200,max_tool_calls,deadline_s 10-86400,max_plan_revisions≤5}, auto_start=true}` | **201** `TaskDetailOut` | client plans validated (DAG, unique ids, known tools); `conversation_id` must be a hybrid/autonomous conversation; `auto_start=true` → status queued + orchestrator job |
| `/tasks` | GET | Bearer | `?limit≤200&offset&status&conversation_id` | `Page<TaskOut>` |
| `/tasks/{id}` | GET | Bearer | — | `TaskDetailOut` (adds `plan`, `checkpoint`, `additional_context`) |
| `/tasks/{id}` | DELETE | Bearer | — | **204** — terminal tasks only |
| `/tasks/{id}/pause` | POST | Bearer | — | `TaskOut` |
| `/tasks/{id}/resume` | POST | Bearer | — | `TaskOut` (paused only) |
| `/tasks/{id}/cancel` | POST | Bearer | — | `TaskOut` — propagates into running sandbox processes |
| `/tasks/{id}/notes` | POST | Bearer | `{content, request_replan?}` | `TaskOut` — hybrid-mode instruction injection |
| `/tasks/{id}/executions` | GET | Bearer | `?limit≤500&offset` | `TaskExecutionOut[]={id,step_id,attempt,status:"pending|running|succeeded|failed|skipped|cancelled",input,output,error,duration_ms,started_at,finished_at}` |
| `/tasks/{id}/events` | GET (SSE) | **?token=** | — | see §8 |

`TaskOut={id,title,objective,status,mode,conversation_id,priority,version,plan_version,usage{},budgets{},cancel_requested,pause_requested,deadline_at,started_at,finished_at,result_summary,artifacts,error{code,message},created_at,updated_at}`. Task status enum: `created, queued, planning, awaiting_approval, ready, running, paused, retrying, verifying, completed, failed, cancelled`. Approval policy: `step.requires_approval`, tool-level `requires_approval` (e.g. `terminal.execute`), and auto-approve list `fs.read, fs.write, fs.list, python.execute, data.analyze_csv, document.extract, code.search` — anything else (e.g. `http.request`) needs approval.

## 5. Approvals — `/approvals`

| Endpoint | Method | Auth | Request | Response |
|---|---|---|---|---|
| `/approvals` | GET | Bearer | `?limit≤200&offset&status:"pending|approved|rejected|expired"` | `Page<ApprovalOut>` |
| `/approvals/{id}` | GET | Bearer | — | `ApprovalOut` |
| `/approvals/{id}` | POST | Bearer | `{decision:"approve"|"reject", reason?}` | `ApprovalOut` — re-drives the task (approve) or cancels it (reject) |

`ApprovalOut={id,task_id,kind:"plan|step",step_id?,plan_version?,title,payload{},status,reason,requested_at,decided_at,expires_at}`. `payload.plan` for plan approvals; `payload.step` + `payload.depends_on` for step approvals. Pending approvals expire after 48 h → task failed.

## 6. Files — `/files`

| Endpoint | Method | Auth | Request | Response |
|---|---|---|---|---|
| `/files` | POST | Bearer | multipart `file`, `?conversation_id` | **201** `FileOut` — extraction is a queued job (status `pending→processing→ready/failed`) |
| `/files` | GET | Bearer | `?limit≤200&offset&status` | `Page<FileOut>` |
| `/files/{id}` | GET | Bearer | `?include_text` | `FileDetailOut` (adds `extracted_text`) |
| `/files/{id}/download` | GET | Bearer | — | binary stream |
| `/files/{id}` | DELETE | Bearer | — | **204** |

`FileOut={id,original_filename,mime_type,size_bytes,sha256,status,scan_status,extracted_chars,error,conversation_id,created_at,updated_at}`. Limits: 50 MiB, extension allowlist (.pdf .docx .txt .md .csv .xlsx .json .py .js .ts .html .css .yaml .yml .toml .xml .log .jpeg .jpg .png), magic-byte verification. Only chat-capable tools can read them later (`document.extract`, `data.analyze_csv`).

## 7. Memory, tools, notifications, admin, health

- **Memory** `/memory`: POST `{category:"conversational|episodic|semantic|procedural", scope:"user|conversation|task", scope_id?, title?, key?, content, source:"user_provided|extracted|model_generated|verified", confidence 0-1, importance 0-1, expires_at?, provenance{}}` → **201** `MemoryOut`; GET list `Page<MemoryOut>`; POST `/search` `{query, categories?, conversation_id?, top_k≤50}` → `[{record, score}]`; GET/PATCH `/{id}`; POST `/{id}/verify` `{evidence}` (promotes to `verified`); DELETE `/{id}` (soft, 204).
- **Tools** `/tools`: GET → `[{key,name,description,category,permissions[],default_timeout_s,max_timeout_s,requires_approval,allowed_in_chat,input_schema,output_schema,is_enabled}]`; GET `/{tool_key}` → `describe()`; POST `/{tool_key}/execute` `{input}` → `{tool,output,duration_ms,tool_execution_id}` — **refuses** `requires_approval` tools (403) and sandboxed tools (400); chat-callable: `document.extract`, `data.analyze_csv`, `http.request`.
- **Notifications** `/notifications`: GET `Page<{id,kind,payload{},read_at,created_at}>` (`?unread_only`); POST `/{id}/read` → 204. Kinds: `approval_requested, task_completed, task_failed, task_cancelled, file_ready, file_failed, system`.
- **Admin** `/admin` (role `admin`): GET `/jobs` `Page<JobOut>`, POST `/jobs/{id}/requeue`, GET `/audit` `Page<AuditEventOut>`, GET `/stats` `{users,conversations,messages,tasks_by_status,jobs_by_status,files,memory_records}`.
- **Health** `/health/live`, `/health/ready` (readiness includes model health; 503 when not ready).

## 8. Streaming contracts (exact SSE)

**Chat** `POST /conversations/{id}/messages/stream` — same body as the non-streaming send; response `text/event-stream` with `event:` + `data:` frames, data JSON-stringified (timestamps may arrive as strings — parse defensively):

| event | data |
|---|---|
| `message.created` | `{message_id, role:"user", seq}` |
| `delta` | `{text}` (concatenate; render markdown only on `message.completed`) |
| `tool_call` | `{id, name}` |
| `tool_result` | `{id, name, message_id}` (fetch tool content later via `GET messages` if needed) |
| `message.completed` | `{message_id, seq, role:"assistant"}` — final content persisted server-side; reload the conversation after `done` |
| `done` | `{}` — always last on success |
| `error` | `{message}` — then the stream ends |

Client disconnect mid-stream: backend persists the partial assistant message marked `meta.interrupted`. No server-side cancellation API for chat → the frontend can only abort locally.

**Task progress** `GET /tasks/{id}/events?token=<access_token>` — SSE with `event: snapshot` (`{task_id,status,version,usage,result_summary}`) then live events `task.status`, `task.plan`, `task.step`, `task.control`, `task.note`, then `event: done` when terminal. 15 s keepalives (`: keepalive`). Terminal statuses end the stream.

**WebSocket** `GET /api/v1/ws?token=` — JSON frames: send `{"action":"subscribe","topics":["task:<uuid>","conversation:<uuid>","user:<uuid>"]}`; receive `{topic,type,data,timestamp}`; errors as `{type:"error",...}`. Ownership enforced per topic. The chat SSE stream publishes `message.created`/`message.completed` to `conversation:{id}` topics.

## 9. Frontend feature → support matrix

| Frontend feature | Support | Source |
|---|---|---|
| Login / register / refresh / logout / profile | ✅ full | `/auth/*` |
| Conversation CRUD + search + archive + title edit | ✅ full | `/conversations` |
| Message history, edit user message, regenerate, in-conversation search | ✅ full | `/conversations/{id}/messages*` |
| Attachments in messages | ✅ full (backend verifies ownership; file ids ≤10) | `attachments: []` |
| Non-streaming chat | ✅ full | `POST /messages` |
| Streaming chat (delta + tool events + interrupt persistence) | ✅ full | `/messages/stream` |
| Chat-side cancellation | ⚠️ frontend-abort only (no backend chat-cancel endpoint) — do not advertise server-side cancel | — |
| Model selection (fetched, per-conversation, per-message override) | ✅ full | `model_config_id` on conversation + message |
| Model health, admin create/update | ✅ full | `/models*` |
| Task lifecycle + pause/resume/cancel/notes + delete | ✅ full | `/tasks*` |
| Client-provided plans (approval-gated) | ✅ full | `plan` on create |
| Task SSE progress + WS live events | ✅ full | `/tasks/{id}/events`, `/ws` |
| Approvals list/detail/decide | ✅ full | `/approvals` |
| File upload/list/download/delete/metadata/extracted text | ✅ full | `/files*` |
| Memory create/list/read/update/verify/forget/search | ✅ full | `/memory*` |
| Tool registry listing + direct chat-safe execution | ✅ full | `/tools*` |
| Notifications list + mark read | ✅ full | `/notifications` |
| Admin jobs/audit/stats | ✅ full (admin role) | `/admin/*` |
| Chat tool-calls with sandboxed tools (`python.execute`, `fs.*`) | ❌ backend design: only `allowed_in_chat` tools; the rest run in tasks — surfaced in the Tools page, not faked in chat | `registry.chat_tools()` |
| Editing/deleting single messages beyond the above | ⚠️ only what §2 lists (no message delete API) | — |
| Server-side chat cancellation / reconnection with replay | ❌ not provided (documented, not faked) | — |

## 10. Notable backend behaviors the frontend must respect

1. **Token TTL** 1800 s access / 30-day refresh. 401 from any endpoint → try `/auth/refresh` once; 401 on refresh (reuse detection) → hard logout.
2. **Rate limits** auth 10/min, uploads 60/min, default 600/min → render `error.code="rate_limited"` with `Retry-After`.
3. **404 vs 403**: foreign resources return 404 (ownership model) — treat as not-found, don't leak existence.
4. **Archived conversations** reject new messages (400); unarchive first.
5. **`GET /models` returns disabled rows too** — filter `is_enabled` in the selector; disabled or unhealthy models must be non-selectable with a clear reason.
6. **Error details** may be a list (validation) or object; render generically.
7. **Pagination** — list endpoints cap at 200 per page; use offset-based incremental loading.
8. **`client_request_id`** — send a UUID per message submission; safe retry after connection failure.
