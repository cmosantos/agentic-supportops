# Agentic SupportOps

Agentic SupportOps is a local-first engineering project for controlled IT support investigations. It explores how deterministic workflows and model-guided runtimes can investigate the same incident through governed, read-only capabilities while preserving an auditable execution history.

This is not a general-purpose chatbot. The unit of work is an incident. An investigation gathers factual evidence, and model-guided execution creates a run, records ordered lifecycle/tool events, and finishes with a structured result. Historical runs remain available while existing latest-state APIs stay compatible.

## What is implemented

- React/Vite operator UI for the fictional incident catalog and deterministic or Responses API investigations.
- FastAPI endpoints for incidents, investigations, evidence, latest state, run/event history, health, and AI configuration.
- Declarative playbooks backed by 20 provider-independent, read-only SupportOps tools.
- Optional OpenAI Responses API and comparative OpenAI Agents SDK runtimes.
- Canonical `InvestigationToolRegistry` for definitions, exact argument validation, execution, and normalized results.
- Direct execution by default and an opt-in local MCP stdio transport for three allowlisted capabilities.
- SQLAlchemy/SQLite persistence with append-oriented run/event history, concurrency protection, and atomic terminal persistence.
- Optional application-owned OpenTelemetry spans; persisted events remain the domain source of truth.
- Isolated backend tests and CI gates for backend, MCP, TypeScript, and production builds.

No real infrastructure is queried. The Contoso environment is deterministic fixture data, and every current tool is read-only.

## Engineering goals

- **Controlled execution:** the application validates tool names, schemas, call limits, and results.
- **Shared capability semantics:** all runtimes and transports reuse the same tool implementations.
- **Historical traceability:** new runs do not overwrite completed or failed run records.
- **Transactional integrity:** the required terminal event and terminal run state commit together.
- **Concurrency safety:** SQLite is the final guard against two `RUNNING` executions for the same incident/runtime.
- **Safe interoperability:** MCP exposes a fixed read-only allowlist, not the entire registry.
- **Observable execution:** domain events describe business execution; optional spans add technical correlation.
- **Reproducibility:** committed Python/Node lockfiles and CI use deterministic installation commands.

## Architecture

```mermaid
flowchart TD
    Operator[Operator] --> Web[React / Vite UI]
    Web --> API[FastAPI routes]
    API --> Services[Application services]
    Services --> Deterministic[Deterministic playbooks]
    Services --> Responses[Responses API runtime]
    Services --> Agents[Agents SDK runtime]
    Deterministic --> Registry[InvestigationToolRegistry]
    Responses --> Transport{Tool transport}
    Agents --> Transport
    Transport -->|direct - default| Registry
    Transport -->|MCP opt-in| Client[MCP client]
    Client -->|stdio| Server[Local MCP server]
    Server --> Registry
    Registry --> Capabilities[Read-only SupportOps capabilities]
    Services --> Repository[SQLAlchemy repositories]
    Repository --> SQLite[(SQLite)]
    Repository --> History[Runs and ordered events]
    Services -. optional .-> OTel[OpenTelemetry boundary]
```

The frontend never talks to MCP directly; it calls FastAPI. MCP is an internal alternative transport between an agent runtime and selected existing tools. See [Architecture](docs/architecture.md) for responsibilities, lifecycle details, and transaction boundaries.

## Investigation lifecycle

1. An operator selects an incident and a supported deterministic or model-guided investigation.
2. Deterministic execution resolves a playbook. Model-guided execution creates a persisted run for `manual_responses` or `agents_sdk`.
3. The runtime appends `run_started`, model-turn, and tool lifecycle events in sequence order.
4. Tool calls pass through the canonical registry, which validates exact names and string arguments before executing a read-only capability.
5. Tool observations become evidence and investigation steps. These are the latest materialized view for their incident/runtime, not run-keyed history.
6. A structured result completes the run, or a controlled failure marks it failed. The terminal event and run transition share one transaction.
7. Latest endpoints keep compatibility; historical endpoints retrieve prior runs and event timelines explicitly.

Models produce diagnoses and recommendations only. The project does not execute remediation or approval-gated write actions.

## Direct and MCP execution

`TOOL_TRANSPORT=direct` is the default for Responses API and Agents SDK investigations:

```text
Agent runtime -> InvestigationToolRegistry -> existing capability
```

With `TOOL_TRANSPORT=mcp`, the runtime advertises only the MCP allowlist:

```text
Agent runtime -> MCP client -> stdio -> local MCP server
              -> InvestigationToolRegistry -> same capability
```

The official Python MCP SDK server exposes exactly:

- `get_disk_usage`
- `check_dns_resolution`
- `get_application_health`

The allowlist is fixed in code. The client launches `sys.executable -m integrations.mcp_server` without a shell, applies a bounded timeout, validates `ToolResult`, and closes resources after each call. MCP cannot select arbitrary commands/modules or access files, databases, credentials, or other registry tools.

MCP is not the product API: HTTP endpoints serve the UI and application clients, while MCP is an internal comparative tool transport.

## Persistence guarantees

- Runs are preserved as history; later runs do not replace earlier completed/failed records.
- Events are append-oriented and returned by deterministic `sequence` order.
- Only `RUNNING` records may transition; repeated terminal transitions are rejected.
- A partial unique SQLite index permits one `RUNNING` run per `(incident_id, runtime mode)` while retaining historical terminal runs.
- Database conflicts roll back before a structured HTTP `409` is returned.
- The terminal event is flushed without committing; the corresponding run transition commits both or rolls both back.
- Latest APIs retain existing semantics. History APIs list runs newest-first and expose events by stable run ID.
- Evidence and steps remain latest materialized records. Historical Evidence/Steps keyed by run ID are intentionally not implemented.

SQLite evolution uses a small idempotent compatibility layer at startup; Alembic is not used. Tests cover fresh databases and legacy unique-constraint/index shapes using temporary storage.

## Project structure

```text
agentic-supportops/
├── .github/workflows/ci.yml       backend and frontend validation
├── apps/api/
│   ├── fixtures/                  fictional Contoso data
│   ├── prompts/                   model-investigation instructions
│   ├── src/
│   │   ├── api/                   FastAPI routes and dependencies
│   │   ├── db/                    ORM, sessions, schema compatibility
│   │   ├── domain/                typed execution/API models
│   │   ├── integrations/          OpenAI, Agents SDK, MCP boundaries
│   │   ├── observability/         optional OpenTelemetry boundary
│   │   ├── repositories/          persistence and fixture access
│   │   ├── services/              orchestration and lifecycle rules
│   │   └── tools/                 read-only capabilities
│   ├── tests/                     backend and real MCP stdio tests
│   ├── pyproject.toml
│   └── uv.lock
├── apps/web/                      React, TypeScript, Vite UI
├── data/                          ignored SQLite data (`.gitkeep` only)
└── docs/                          architecture, simulation, readiness
```

## Local development

### Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 24 with npm

Commands start from the repository root and are PowerShell-friendly.

### Backend

Install locked runtime and development dependencies:

```powershell
uv sync --project .\apps\api --frozen --extra dev
```

No variable is required for deterministic execution, imports, or tests. Defaults and optional settings are in [`.env.example`](.env.example). Create an ignored local override only when needed:

```powershell
Copy-Item .env.example .env.local
```

Leave `OPENAI_API_KEY` empty unless intentionally making real model calls. Direct tool transport remains the default.

Start from the repository root so the default relative database path resolves to `data/agentic_supportops.db`:

```powershell
uv run --project .\apps\api --frozen python -m uvicorn main:app --app-dir .\apps\api\src --reload
```

The API is at `http://localhost:8000`; OpenAPI is at `http://localhost:8000/docs`. First startup creates the schema, applies compatible legacy upgrades, and seeds 25 incidents. Normal startup preserves existing data.

Run the MCP server independently:

```powershell
$env:PYTHONPATH = ".\apps\api\src"
uv run --project .\apps\api --frozen python -m integrations.mcp_server
Remove-Item Env:PYTHONPATH
```

### Frontend

```powershell
Set-Location .\apps\web
npm ci
npm run dev
```

The UI is at `http://localhost:5173`. Use `apps/web/.env.local` with `VITE_API_BASE_URL` only when the API is elsewhere.

### Resetting local data

This command drops application tables and restores the fixture baseline. Back up needed local history first.

```powershell
$env:PYTHONPATH = ".\apps\api\src"
uv run --project .\apps\api --frozen python -m simulation.seed --reset
Remove-Item Env:PYTHONPATH
```

## Testing

Backend tests cover HTTP contracts, services, tools, fake provider orchestration, lifecycle invariants, SQLite compatibility, tracing, and real MCP stdio parity.

```powershell
uv lock --project .\apps\api --check
uv pip check --python .\apps\api\.venv\Scripts\python.exe
uv run --project .\apps\api --frozen python -m pytest .\apps\api\tests
uv run --project .\apps\api --frozen python -m pytest .\apps\api\tests\test_mcp_integration.py
```

There is no frontend test runner. Its established gates are:

```powershell
Set-Location .\apps\web
npm run typecheck
npm run build
Set-Location ..\..
```

## Continuous integration

The committed [GitHub Actions workflow](.github/workflows/ci.yml) targets pull requests and pushes to `master`:

- **Backend:** Python 3.12, uv lock check, frozen dev installation, dependency health, application import, full pytest, and separately visible real MCP stdio parity.
- **Frontend:** Node.js 24, `npm ci`, TypeScript, and Vite production build.

No OpenAI credential, external MCP server, persistent database, or production secret is required. The workflow and underlying commands have been reviewed/validated locally. It has **not yet run on GitHub Actions** because publication is deferred to Mission 14.

## API discoverability

FastAPI exposes the complete interactive contract at `/docs`.

| Group | Representative endpoints |
| --- | --- |
| Health/config | `GET /health`, `GET /ai/config` |
| Incidents | `POST /incidents`, `GET /incidents`, `GET /incidents/{incident_id}` |
| Deterministic | `POST /incidents/{incident_id}/investigate`, `GET /incidents/{incident_id}/investigation` |
| Model-guided | `POST /incidents/{incident_id}/investigate-ai`, `POST /incidents/{incident_id}/investigate-agent-sdk` |
| Latest state | `GET /incidents/{incident_id}/ai-investigation`, `GET /incidents/{incident_id}/agent-sdk-investigation` |
| Latest event timeline | `GET /incidents/{incident_id}/investigations/{runtime}/events` |
| Run history | `GET /incidents/{incident_id}/investigation-runs?runtime=manual_responses` |
| Event history | `GET /incidents/{incident_id}/investigation-runs/{run_id}/events` |

References such as `INC-014` and numeric IDs are accepted where `{incident_id}` appears.

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/incidents
Invoke-RestMethod http://localhost:8000/incidents/INC-014/investigation-runs
```

## Current scope and future evolution

The project is currently a single-user local application over simulation data. It demonstrates controlled read-only investigation, optional model orchestration, MCP transport comparison, durable run/event history, and local observability. It does not remediate real systems.

Not yet included:

- authentication, authorization, or multi-user tenancy;
- approval workflows and write/remediation tools;
- remote MCP, Streamable HTTP, OAuth, or persistent MCP pooling;
- PostgreSQL or production database operations;
- external ticketing/infrastructure integrations;
- hosted telemetry, deployment, or cloud infrastructure;
- historical Evidence/Steps keyed by run ID;
- frontend views for complete historical run/event APIs.

See [Publication readiness](docs/publication-readiness.md). Controlled GitHub publication and the first real Actions run are deferred to Mission 14.
