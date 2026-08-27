# Agentic SupportOps

Agentic SupportOps is an IT Support and Operations platform being built incrementally. Its long-term goal is to receive incidents, coordinate investigations, gather evidence, propose diagnoses and recommendations, require human approval for sensitive actions, and expose complete execution history.

## Current scope

Mission 01 establishes only the application baseline: an incident API backed by SQLite, a minimal React page, deterministic backend tests, and local developer setup. No AI, agent, MCP, RAG, authentication, background processing, cloud, or observability functionality is included.

## Architecture

The browser loads the React/Vite application and calls the FastAPI HTTP API. API routes delegate incident behavior to a small service, which uses a repository to persist SQLAlchemy records in SQLite. Pydantic validates request and response data at the API boundary.

```text
React/Vite -> FastAPI routes -> Incident service -> Repository -> SQLAlchemy -> SQLite
```

The layers are intentionally small. They separate HTTP, application behavior, and persistence without introducing speculative interfaces or infrastructure.

## Repository structure

```text
apps/
  api/
    src/
      api/           HTTP routes and dependencies
      core/          application configuration
      db/            SQLAlchemy engine, session, and records
      domain/        incident schemas and enums
      repositories/  incident persistence operations
      services/      incident use cases
    tests/            API tests
  web/                React, TypeScript, and Vite application
data/                  local SQLite storage (database files are ignored)
docs/                  project documentation
tests/                 reserved for future cross-application tests
```

## Backend setup (PowerShell)

From the repository root, create and activate a Python 3.12+ virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".\apps\api[dev]"
```

This installs the API and its test dependencies into an isolated environment. Start the development API with:

```powershell
python -m uvicorn main:app --app-dir .\apps\api\src --reload
```

The API will be available at `http://localhost:8000`; interactive OpenAPI documentation is at `http://localhost:8000/docs`. The SQLite file is created in `data/agentic_supportops.db` when the application starts.

Run backend tests:

```powershell
python -m pytest .\apps\api\tests
```

## Frontend setup (PowerShell)

In a second PowerShell terminal:

```powershell
Set-Location .\apps\web
npm install
npm run dev
```

The frontend will be available at `http://localhost:5173` and will query the backend health endpoint. To use another API address, copy `.env.example` to `.env` at the repository root and expose `VITE_API_BASE_URL` to the frontend environment, or create `apps/web/.env.local` with that variable.

Validate TypeScript and create a production build:

```powershell
npm run typecheck
npm run build
```

No frontend test runner is included in this baseline because the page has no application behavior beyond a small health request. TypeScript validation and the production build are the current frontend gates.

## API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Report API availability |
| `POST` | `/incidents` | Create an incident with initial `open` status |
| `GET` | `/incidents` | List incidents in creation order |
| `GET` | `/incidents/{incident_id}` | Retrieve an incident or return a structured 404 |

## Roadmap

Future missions may incrementally add simulated IT infrastructure, the OpenAI Responses API, tool calling, MCP, the OpenAI Agents SDK, RAG and knowledge sources, state and memory, human approval, guardrails, tracing, OpenTelemetry, and real infrastructure integrations. Each capability will be introduced only when its mission requires it.

