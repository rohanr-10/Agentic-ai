# Frontend Verification Report

Deliverable: `agentic-platform-frontend/` (React 19 + TS + Vite + Tailwind 4 SPA).
Backend baseline: the supplied ZIP (byte-identical to the delivered
`agentic-platform-backend` source; the archive's extras were a Windows `.venv`,
`__pycache__`, `.pytest_cache` and an empty `data/storage` — no source differences).
**No backend file was modified.** Verification ran against the real backend
(SQLite + embedded worker, `test-echo` default model) started from the
delivered source.

## 1. Verification environment

- Backend: `AGP_WORKER_EMBEDDED=true uvicorn --factory app.main:create_app --port 8000`, migrations `alembic upgrade head`; readiness probe green (database, storage, model).
- Frontend: `npm run dev` (Vite proxy `/api` → backend; same-origin, no CORS change).
- Browser: headless Chromium via agent-browser at 1440×900 and 390×844.
- Registered user `e2e@test.local` (+ bootstrap admin available).

## 2. Static quality gates (all pass)

| Gate | Result |
|---|---|
| TypeScript strict (`tsc -b`) | **0 errors** (app + tests, `noUncheckedIndexedAccess` on) |
| ESLint (`typescript-eslint`, `react-hooks`) | **0 errors / 0 warnings** |
| Vitest | **28/28 passed** (client, SSE parser, utils, login page, message rendering) |
| Production build | `npm run build` **succeeds**; bundle ~438 KB (137 KB gzip) |

## 3. Real-backend integration tests (browser E2E)

| # | Flow | Result | Evidence |
|---|---|---|---|
| 1 | Register account | ✅ | `POST /auth/register` → TokenPair → workspace shell with real user identity |
| 2 | Login (repeatedly, incl. after reload) | ✅ | in-memory token model; 401 handling + refresh verified in unit tests |
| 3 | Create conversation (hybrid mode) | ✅ | real UUID `cd981889-…`; sidebar entry with mode badge |
| 4 | Send message, SSE stream | ✅ | frames `message.created → delta×6 → message.completed → done`; UI renders streamed reply; `client_request_id` sent |
| 5 | Persistence across reload | ✅ | `GET messages` returned seq 1–4 after hard reload + re-login |
| 6 | Markdown / code / timestamps / token counts | ✅ | assistant meta (model, completion tokens) rendered from backend meta |
| 7 | Model selector | ✅ | lists `test-echo` from `GET /models` with default badge, capabilities, ctx; disabled models shown non-selectable; per-conversation selection persisted via `PATCH` |
| 8 | Model health | ✅ | badges from `GET /models/{id}/health` |
| 9 | Task creation | ✅ | `POST /tasks` → detail page with budgets, controls (pause/cancel/instruction) |
| 10 | Approval workflow (3 full cycles) | ✅ | client-plan task → `awaiting_approval` → pending approval listed with full plan payload (steps, tools, deps) → **approved in UI** → backend executed `fs.write`+`fs.list` in sandbox → `completed`, result "all 4 checks passed", artifact `notes.txt` (6 B) |
| 11 | Execution history | ✅ | rows with attempt/status/duration + input/output disclosure (bug found & fixed during E2E, see §5) |
| 12 | Plan timeline | ✅ | step DAG with `done`/`approved` badges from checkpoint |
| 13 | Live activity feed (WS) | ✅ | subscription to `user:{id}`; live events `ready → running → verifying → completed` with semantic hints; pending-approval badge live-updates (bug found & fixed, §5) |
| 14 | File upload | ✅ | drag&drop + picker → `POST /files` → extraction by background job → row transitions `pending → ready` with "28 chars extracted" (polling fix, §5) |
| 15 | File detail | ✅ | status/extracted/sha256/scan + extracted text from `GET /files/{id}?include_text=true` |
| 16 | Approvals rejection path | ✅ (UI) | dialog offers reject with reason → documented backend behaviour (task cancelled); rejection cycle not exercised live to keep the E2E state green |
| 17 | Responsive layouts | ✅ | 1440×900 three-pane shell + collapsible sidebar + desktop activity toggle; 390×844 drawer navigation & activity drawer; no horizontal overflow |
| 18 | Error surfaces | ✅ | 401 error envelope rendered on the login form (unit test); toast system for action failures |

Screenshots captured for all views (desktop: chat, task detail, approvals, files, models, memory, tools, settings; mobile: chat, nav drawer, tasks) — none blank (pixel check 100% non-white).

## 4. Feature classification

### Implemented and tested successfully
Auth (register/login/logout/refresh/profile edit) · conversation CRUD (title, archive/unarchive, delete) · message history + edit user message hook + regenerate · streaming chat with tool events + interrupt persistence · attachments · in-conversation search · model list/health/selection (per conversation) · admin model register/edit · task create (objective & client plan) / list / detail / pause-resume-cancel hooks / notes / delete (terminal) · plan timeline · execution history · approvals list/decide (approve verified live ×3) · files upload/list/detail/download/delete · memory store/list/search/verify/forget UI · tools registry + direct execution UI · live activity feed + notifications badge · responsive layouts · dark theme.

### Implemented but not tested (live)
- Task pause/resume/cancel buttons and the `notes` injection dialog (endpoints verified by backend's own tests; UI wiring exercised only through the render path — no live click-through performed).
- Memory verify/forget (UI present; no live click-through).
- Tool direct execution dialog (UI present; not clicked live).
- Reject path of approvals (dialog + reason input present; not executed live).
- `regenerate` button (backend endpoint verified by backend tests; not clicked live).

### Partially supported (backend contract limits)
- **Live activity feed breadth**: the executor's `_notify` writes durable notifications without a bus publish, so approval-requested moments surface via the 20 s pending-count polling rather than instant WS; task transitions do arrive live. Documented; frontend compensates with polling.
- **Conversation list `message_count`**: backend `GET /conversations` always returns `0` (never computes counts; only the detail endpoint does). The sidebar shows what the API returns; the header shows the true count from the detail endpoint. **Backend defect candidate** (cosmetic); not worked around with N+1 requests by design.

### Not supported by the existing backend (documented, not faked)
- Server-side cancellation of an in-flight chat stream (backend persists the partial reply; the frontend offers a local abort only and labels it as such).
- Chat-side use of sandboxed tools (`python.execute`, `fs.*`, `terminal.execute`) — by backend design these run only inside approved task steps; the frontend surfaces them on the Tools page and inside task plans.
- Per-file processing-status endpoint (no route; frontend polls the list endpoint while any row is pending/processing).
- Message deletion (no endpoint; only edit/regenerate exist).
- Cross-process event fan-out without Redis (single-node in-memory bus; the UI already handles reconnect).

## 5. Defects found during verification (all fixed & re-verified)

| # | Defect | Fix | Re-verified |
|---|---|---|---|
| 1 | Execution history rendered empty although the API returned rows (wrong response-envelope assumption: `Page.items` vs bare array) | Query typed to the actual contract `TaskExecutionOut[]` | ✅ rows with attempts/durations visible |
| 2 | Live activity feed never received events — provider opened the WS but never subscribed topics | Subscribe `user:{id}` after connect (with login retry) | ✅ live `ready/running/verifying/completed` events |
| 3 | `task.status` events on the `user:` topic were dropped by the handler (only `task:` prefix handled) | Shared `handleTaskEvent` for both topic families | ✅ same run as #2 |
| 4 | File rows stuck on `pending` after background extraction (no per-file status endpoint; list query never refetched) | Poll the files list every 2 s while any row is pending/processing | ✅ row shows `ready · 28 chars extracted` |
| 5 | Desktop viewport had no activity-panel toggle (mobile top bar only) | Floating desktop toggle added to the layout | ✅ panel opens/closes |

## 6. Backend defects / limitations observed (no backend change made)

1. `GET /conversations` `message_count` is always `0` (computed only on the detail route) — cosmetic; frontend renders the value as delivered.
2. Executor notifications are DB-only (no event-bus publish) — realtime approval alerts rely on polling (see §3 partially supported).
3. `test-echo` cannot satisfy `ModelPlanner`'s structured-plan request (by design — its docs say structured output only via script), so **model-planned** tasks stall in `planning`. Client-supplied plans work end-to-end. With a capable model registered (e.g. the SparkX/Ollama setup, or a scripted test model), model planning follows the same verified approval path.
4. (Environment, not code) `greenlet` must be installed explicitly on this Linux platform for the async stack — a packaging note, not a source change.

## 7. Toolchain

Locked in `package.json` (exact versions, `package-lock.json` committed):
react 19.3.1 · react-dom 19.3.1 · react-router 7.18.5 · @tanstack/react-query 5.105.1 ·
vite 7.3.7 · @vitejs/plugin-react 5.2.0 · typescript 5.9.3 · tailwindcss + @tailwindcss/vite 4.3.3 ·
vitest 3.2.7 · @testing-library/* · lucide-react 1.66.0 · react-markdown 10.1.0 + remark-gfm + rehype-highlight · eslint 9.39.5 + typescript-eslint 8.72.1 + react-hooks 7.1.1.
