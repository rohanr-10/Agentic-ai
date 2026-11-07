# Agentic AI Platform

A modular platform for building, managing, and executing AI-powered workflows through a unified backend and web interface.

The project brings together conversational AI, model integration, task orchestration, tool execution, file processing, and persistent conversation context in a single application.

## Overview

The Agentic AI Platform is designed to provide a structured environment for interacting with AI models and coordinating multi-step tasks.

Rather than treating model interaction as an isolated chat experience, the platform provides supporting infrastructure for managing conversations, executing tasks, handling tools, and tracking activity.

The repository contains both the backend services and the frontend application.

## Core Capabilities

- **Conversational Interface** — Interact with configured AI models through a web-based chat interface.
- **Model Gateway** — Connect to compatible model providers through a unified integration layer.
- **Task Orchestration** — Create and manage multi-step tasks with planning, execution, and verification components.
- **Tool Management** — Register and manage built-in tools, with execution controls and approval workflows.
- **Persistent Context** — Maintain conversation history and supporting memory.
- **File Management** — Upload and process supported file formats.
- **Activity Tracking** — View platform activity and task-related updates.
- **Authentication and Administration** — Manage access and platform-level settings.

## Architecture

The project is organized as a monorepo containing two primary applications:

```text
Agentic-ai/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── db/
│   │   ├── events/
│   │   ├── gateway/
│   │   ├── jobs/
│   │   ├── orchestration/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── storage/
│   │   ├── tools/
│   │   └── workers/
│   │
│   ├── alembic/
│   ├── docs/
│   ├── scripts/
│   ├── tests/
│   └── requirements.txt
│
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── features/
│   │   ├── hooks/
│   │   ├── services/
│   │   ├── styles/
│   │   └── types/
│   │
│   ├── tests/
│   └── package.json
│
└── .gitignore
```

### Backend

The backend is built with Python and FastAPI. It provides the API layer, model gateway, database integration, orchestration services, background workers, and tool execution infrastructure.

Key technologies include:

- Python
- FastAPI
- SQLAlchemy
- Alembic
- SQLite
- Pydantic
- Pytest

### Frontend

The frontend is a TypeScript application built with React and Vite. It provides the user interface for conversations, tasks, models, files, tools, approvals, memory, and settings.

Key technologies include:

- React
- TypeScript
- Vite
- TanStack Query
- Tailwind CSS

## Getting Started

### Prerequisites

Install the following before running the project:

- Python 3.11 or later
- Node.js 20 or later
- npm
- Git

### 1. Clone the repository

```bash
git clone https://github.com/rohanr-10/Agentic-ai.git
cd Agentic-ai
```

### 2. Backend setup

Navigate to the backend directory:

```bash
cd backend
```

Create and activate a virtual environment:

**Windows PowerShell**

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Create a local environment file:

```powershell
Copy-Item .env.example .env
```

Configure the required environment variables before starting the application.

Run the backend:

```powershell
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

The API will be available at:

```text
http://127.0.0.1:8000
```

API documentation:

```text
http://127.0.0.1:8000/docs
```

### 3. Frontend setup

Open another terminal and navigate to the frontend directory:

```bash
cd frontend
```

Install dependencies:

```bash
npm ci
```

Create the local environment file:

```bash
```

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

Start the development server:

```bash
npm run dev
```

The Vite development server will display its local address in the terminal.

## Configuration

Environment variables are managed separately for the backend and frontend.

- `backend/.env.example` contains backend configuration.
- `frontend/.env.example` contains frontend configuration.

Create local `.env` files from these templates and configure the values required by your environment.

Do not commit real credentials, API keys, passwords, or other secrets.

## Testing

The backend test suite is located in:

```text
backend/tests/
```

The frontend test suite is located in:

```text
frontend/tests/
```

Run backend tests from the backend directory:

```bash
python -m pytest
```

Run frontend tests from the frontend directory:

```bash
npm test
```

Additional verification and operational information is available in the respective project documentation.

## Documentation

Detailed documentation is maintained within each application:

**Backend**
- `backend/docs/ARCHITECTURE.md`
- `backend/docs/API.md`
- `backend/docs/DATABASE.md`
- `backend/docs/SECURITY.md`
- `backend/docs/OPERATIONS.md`
- `backend/docs/DEPLOYMENT_LOCAL.md`
- `backend/docs/DEPLOYMENT_CLOUD.md`

**Frontend**
- `frontend/docs/BACKEND_API_INTEGRATION_MAP.md`
- `frontend/docs/VERIFICATION_REPORT.md`

## Project Status

The repository currently contains the initial integrated backend and frontend codebase.

Development, testing, integration, and deployment work may continue as the platform evolves. Refer to the project documentation for implementation details and known limitations.

## License

No open-source license has been specified yet. All rights are reserved by the project owner unless a license is added to this repository.
