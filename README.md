# Agentic SupportOps

Agentic SupportOps is an IT Support and Operations platform being built incrementally. Its long-term goal is to receive incidents, coordinate investigations, gather evidence, propose diagnoses and recommendations, require human approval for sensitive actions, and expose complete execution history.

## Current scope

Missions 01–04 provide the application baseline, a deterministic fictional Contoso environment, plain Python investigation tools, and optional AI-guided investigation through the OpenAI Responses API. The application can seed incidents, execute predefined or model-guided investigations, and persist factual evidence and execution steps.

AI investigation is disabled unless `OPENAI_API_KEY` is configured. The validated manual Responses API runtime remains the default AI path; a separate single-agent OpenAI Agents SDK runtime is available for controlled comparison. There is no MCP integration, RAG, approval workflow, application OpenTelemetry instrumentation, authentication, background processing, cloud integration, or access to real infrastructure.

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

Because this project has no production data or migrations yet, the Mission 01 SQLite file should be reset once to adopt the expanded schema. Alembic remains unnecessary at this stage.

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

Routes accept either the numeric database ID or catalog references such as `INC-002`. Unknown resources and unsupported investigations return structured errors.

## Simulation, tools, and evidence

The fixture models users, account state, groups, licenses, mailboxes and permissions, workstations, network configuration, services, hosts, alerts, metrics, and applications. It intentionally combines healthy resources with known failure states. See [the simulation reference](docs/simulation.md) for the incident and tool catalogs.

Evidence contains observed tool payloads only. Investigation steps record origin, tool, arguments, target, status, result, and timestamps. The tools are read-only and deterministic in both investigation modes. Evidence and steps do not themselves represent a diagnosis.

## Roadmap

Mission 04 adds optional OpenAI Responses API tool calling over the existing deterministic capabilities. Future missions may incrementally add MCP exposure, the OpenAI Agents SDK, RAG and knowledge sources, state and memory, human approval, guardrails, tracing, OpenTelemetry, and real infrastructure adapters. Those later layers do not exist yet.
