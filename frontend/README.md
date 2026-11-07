# Agentic Platform — Frontend

A production-oriented React frontend for the agentic AI platform backend
(conversational + autonomous modes). It integrates exclusively with the
supplied backend's REST / SSE / WebSocket APIs — no mock layer, no simulated
behaviour. See `docs/BACKEND_API_INTEGRATION_MAP.md` for the full contract
and `docs/VERIFICATION_REPORT.md` for what was tested against the real
backend.

## Stack

React 19 · TypeScript 5.9 (strict) · Vite 7 · Tailwind CSS 4 · React Router 7 ·
TanStack Query 5 · Vitest 3 + Testing Library. All versions pinned in
`package.json` / `package-lock.json`.

## Prerequisites

- Node.js ≥ 20 (verified on Node 24) — `node --version` to check.
- Your FastAPI backend running at `http://127.0.0.1:8000` (see its README:
  `alembic upgrade head`, then start uvicorn; the embedded worker is
  recommended for local use: `AGP_WORKER_EMBEDDED=true`).
- Windows PowerShell 5.1 or PowerShell 7 (all commands below are PowerShell).

## Windows PowerShell — extract, install, configure, start

```powershell
# 0) Extract the archive (adjust the path to where you saved it)
Expand-Archive -Path .\agentic-platform-frontend.zip -DestinationPath . -Force
cd .\agentic-platform-frontend

# 1) Verify Node.js
node --version          # must be >= 20

# 2) Install pinned dependencies (uses package-lock.json)
npm ci
#    (or: npm install — `npm ci` is the clean, reproducible option)

# 3) Configure the frontend
Copy-Item .env.example .env
#    .env defaults already point the Vite proxy at http://127.0.0.1:8000 —
#    no edit needed for a local backend on port 8000. To use another host/port:
#    notepad .env   →  set VITE_BACKEND_ORIGIN=http://127.0.0.1:8000

# 4) Start the dev server
npm run dev
#    → http://localhost:5173  (the /api proxy forwards to the backend)
```

## Running alongside your existing backend

Terminal 1 — the backend (start it first):

```powershell
cd .\agentic-platform-backend
.\.venv\Scripts\Activate.ps1
alembic upgrade head
$env:AGP_WORKER_EMBEDDED = "true"
uvicorn --factory app.main:create_app --port 8000
```

Terminal 2 — the frontend:

```powershell
cd .\agentic-platform-frontend
npm run dev
```

Open http://localhost:5173. Register an account in the UI (open
registration) or sign in — the workspace loads your conversations, tasks,
approvals, files and models from the real database.

How the connection works (no backend change required): the Vite dev server
proxies `/api/**` (REST, SSE and WebSocket upgrades) to
`VITE_BACKEND_ORIGIN` (default `http://127.0.0.1:8000`). The frontend talks
same-origin, so no CORS configuration is needed on the backend. If your
backend runs on a different port, set `VITE_BACKEND_ORIGIN` in `.env`.

## Verification commands (PowerShell)

```powershell
npm run typecheck    # strict TypeScript across app + tests   (0 errors)
npm run lint         # ESLint (typescript-eslint + react-hooks)
npm run test         # Vitest unit + component suite (28 tests)
npm run build        # production build → dist\
npm run preview      # serve dist\ on http://localhost:4173 (same /api proxy)
```

All four gates pass in this delivery.

## Production deployment

`npm run build` emits `dist\`. Either serve `dist\` from the same origin as
the backend (reverse proxy forwarding `/api` to uvicorn), or set
`VITE_API_BASE_URL` in `.env` to an absolute backend URL and add the
frontend origin to the backend's `AGP_CORS_ORIGINS`.

## Feature map

| Area | What it does | Backend surface |
|---|---|---|
| Auth | register / login / silent refresh / logout, in-memory tokens | `/auth/*` |
| Chat | conversations CRUD, message history, SSE streaming with tool events, attachments, search, rename/archive/delete, regenerate | `/conversations*` |
| Models | fetched model list with health, per-conversation selection, admin register/edit | `/models*` |
| Tasks | creation (incl. client plans), lifecycle controls, plan timeline, execution history, live SSE progress | `/tasks*` |
| Approvals | pending list with plan/step payloads, approve/reject with reason | `/approvals*` |
| Files | upload (drag & drop), extraction status polling, download, delete, extracted text | `/files*` |
| Memory | store, scored recall search, verify-with-evidence, forget | `/memory*` |
| Tools | registry with policy flags; direct execution of chat-safe tools | `/tools*` |
| Activity | live task/notification feed over WebSocket, pending-approval badge | `/ws`, `/tasks/{id}/events` |

Every control performs its advertised action against the backend; where the
backend has no endpoint (see `docs/VERIFICATION_REPORT.md`), the UI says so
instead of pretending.

## Project layout

```
src/
  app/          App entry, router, providers, guards, error boundary
  components/   layout, navigation, chat, messages, models, tasks,
                approvals, files, activity, ui primitives
  features/     auth, chat, models, tasks, approvals, files,
                memory, tools, settings, activity (query hooks live here)
  hooks/        shared hooks (media queries)
  services/     api client (auth-aware, silent refresh), SSE, WebSocket
  types/        wire types mirroring backend Pydantic schemas
  utils/        formatting helpers, class merge
  styles/       design tokens + Tailwind theme
tests/          Vitest suites (client, SSE parser, utils, login, messages)
docs/           backend integration map, verification report
```

## Security notes

- Tokens are held in memory only (no localStorage) — the backend's
  documented trade-off; sessions don't survive a hard page reload.
- 401 → single-flight refresh; refresh-token reuse detection (revokes all
  sessions) hard-logs the user out.
- No credentials, keys or backend secrets in frontend source. Authorization
  is always re-validated by the backend; frontend role checks are UX only.
- Attachments, downloads and tool runs go through backend-validated,
  ownership-checked endpoints.
