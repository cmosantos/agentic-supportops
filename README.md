# Agentic SupportOps

Agentic SupportOps is an IT Support and Operations platform being built incrementally. Its long-term goal is to receive incidents, coordinate investigations, gather evidence, propose diagnoses and recommendations, require human approval for sensitive actions, and expose complete execution history.

## Current scope

The completed missions provide the application baseline, a deterministic fictional Contoso environment, plain Python investigation tools, optional AI-guided investigation through both the OpenAI Responses API and the OpenAI Agents SDK, and a local execution-event timeline. The application can seed incidents, execute predefined or model-guided investigations, and persist factual evidence, tool execution steps, immutable historical runs, and provider-neutral orchestration events.

AI investigation is disabled unless `OPENAI_API_KEY` is configured. The validated manual Responses API runtime remains the default AI path; a separate single-agent OpenAI Agents SDK runtime is available for controlled comparison. Agents SDK tracing remains disabled. Application-owned OpenTelemetry tracing is available as an opt-in local diagnostic boundary and exports nothing by default. There is no MCP integration, RAG, approval workflow, external tracing backend, authentication, background processing, cloud integration, or access to real infrastructure.

## Architecture

The React application calls the FastAPI API. Incident routes use application services and SQLAlchemy repositories. Deterministic investigations use declarative playbooks; optional AI investigations use an isolated Responses API gateway for tool calling. Both paths invoke the same provider-independent Python tools, which read typed fixture data through the simulation repository. Tool results become persisted evidence and investigation steps.

```text
React/Vite -> FastAPI -> Deterministic service -> Investigation tools
                    \-> AI service -> Responses gateway -/
                              |                 |
                              v                 v
                       Evidence/steps    OpenAI Responses API
                              |
                              v
                       SQLAlchemy/SQLite
```

## Repository structure

```text
apps/
  api/
    fixtures/         fictional Contoso environment and incident catalog
    src/
      api/            HTTP routes and dependencies
      core/           application configuration
      db/             SQLAlchemy engine, sessions, and records
      domain/         typed incident, simulation, and investigation models
      repositories/   persistence and simulation access
      services/       incident and investigation orchestration
      simulation/     deterministic seed/reset
      tools/          identity, endpoint, network, and monitoring tools
    tests/             backend tests
  web/                 React, TypeScript, and Vite application
data/                  local SQLite storage; database files are ignored
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

Start the development API:

```powershell
python -m uvicorn main:app --app-dir .\apps\api\src --reload
```

The API is available at `http://localhost:8000` and OpenAPI documentation at `http://localhost:8000/docs`. On a new database, the application creates `data/agentic_supportops.db` and seeds the 25 catalog incidents automatically.

AI investigation is optional. Set `OPENAI_API_KEY` in the process environment or repository-root `.env.local` to enable it. Process environment variables take precedence over `.env.local`. The deterministic API remains available without a key.

OpenTelemetry tracing is also optional and independent of AI configuration:

```text
OTEL_ENABLED=false
OTEL_SERVICE_NAME=agentic-supportops
OTEL_EXPORTER=none
```

Supported exporters are `none` and the local `console` exporter. Tracing is disabled by default, requires no Collector, and never enables the Agents SDK provider tracing.

Run backend tests:

```powershell
python -m pytest .\apps\api\tests
```

### Reset the simulation

The reset command drops all local application tables, recreates them, and restores the exact 25-incident baseline. It never contacts a real system.

> Warning: this deletes all incidents, evidence, and investigation steps in the local SQLite database.

```powershell
$env:PYTHONPATH = ".\apps\api\src"
python -m simulation.seed --reset
Remove-Item Env:PYTHONPATH
```

Expected result: `Simulation reset complete: 25 catalog incidents seeded`.

The application owns a small idempotent SQLite schema-compatibility layer that upgrades legacy local databases during startup while preserving run history. Alembic is not currently part of this repository.

## Frontend setup (PowerShell)

In a second PowerShell terminal:

```powershell
Set-Location .\apps\web
npm install
npm run dev
```

The frontend at `http://localhost:5173` shows backend health, lists the seeded catalog, displays incident details, runs supported deterministic playbooks, and renders factual evidence. To change the API address, create `apps/web/.env.local` with `VITE_API_BASE_URL`.

Validate TypeScript and create a production build:

```powershell
npm run typecheck
npm run build
```

No frontend test runner is included because the current UI remains a thin integration surface. TypeScript validation and production build are its gates.

## API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Report API availability |
| `POST` | `/incidents` | Create an incident with initial `open` status |
| `GET` | `/incidents` | List incidents in creation order |
| `GET` | `/incidents/{incident_id}` | Retrieve an incident |
| `POST` | `/incidents/{incident_id}/investigate` | Run its deterministic playbook |
| `GET` | `/incidents/{incident_id}/evidence` | Retrieve persisted factual evidence |
| `GET` | `/incidents/{incident_id}/investigation` | Retrieve evidence and execution steps |
| `GET` | `/ai/config` | Report whether optional AI investigation is configured |
| `POST` | `/incidents/{incident_id}/investigate-ai` | Run an AI-guided investigation through read-only tools |
| `POST` | `/incidents/{incident_id}/investigate-agent-sdk` | Run the comparative single-agent Agents SDK investigation |
| `GET` | `/incidents/{incident_id}/agent-sdk-investigation` | Read the latest persisted Agents SDK investigation |
| `GET` | `/incidents/{incident_id}/ai-investigation` | Retrieve the latest persisted AI investigation |
| `GET` | `/incidents/{incident_id}/investigations/{runtime}/events` | Retrieve the ordered local timeline for `manual_responses` or `agents_sdk` |
| `GET` | `/incidents/{incident_id}/investigation-runs` | List all historical AI runs, newest first; optionally filter by `runtime` |
| `GET` | `/incidents/{incident_id}/investigation-runs/{run_id}` | Retrieve one historical run without changing latest semantics |
| `GET` | `/incidents/{incident_id}/investigation-runs/{run_id}/events` | Retrieve the append-oriented event timeline for one historical run |

Routes accept either the numeric database ID or catalog references such as `INC-002`. Unknown resources and unsupported investigations return structured errors.

## Simulation, tools, and evidence

The fixture models users, account state, groups, licenses, mailboxes and permissions, workstations, network configuration, services, hosts, alerts, metrics, and applications. It intentionally combines healthy resources with known failure states. See [the simulation reference](docs/simulation.md) for the incident and tool catalogs.

Evidence contains observed tool payloads only. Investigation steps record origin, tool, arguments, target, status, result, and timestamps. The tools are read-only and deterministic in both investigation modes. Evidence and steps do not themselves represent a diagnosis.

Investigation events complement those records with the orchestration timeline: run and model-turn boundaries, tool request/execution boundaries, response identifiers, per-turn token usage, and application-measured durations. Events contain compact metadata rather than raw provider payloads, credentials, headers, or duplicated evidence.

AI run records are append-only history. Starting a new run never replaces a completed or failed run, and legacy retrieval endpoints continue to resolve the newest run for their runtime. SQLite enforces at most one `RUNNING` run per incident and runtime with a partial unique index, so manual Responses and Agents SDK runs remain independent. The terminal event and terminal run-state transition are committed in the same database transaction. Evidence and investigation steps remain the latest materialized view per incident and runtime; their historical audit is available through each run's event timeline.

These records have deliberately separate roles:

- `Evidence` stores observed facts.
- `InvestigationStep` is the application audit of tool execution.
- `InvestigationEvent` is the persisted SupportOps runtime timeline and remains the domain source of truth.
- OpenTelemetry spans describe the technical parent/child execution path and latency. They are diagnostic data, not application state.

When tracing is enabled, event metadata may contain the active `trace_id` and `span_id` for correlation. Prompts, evidence payloads, response bodies, credentials, and HTTP headers are not span attributes.

## Roadmap

Future missions may incrementally evaluate a local Collector/export pipeline, MCP exposure, RAG and knowledge sources, state and memory, human approval, guardrails, and real infrastructure adapters. Those layers do not exist yet.
