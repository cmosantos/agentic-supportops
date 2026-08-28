# Architecture

This document describes the implemented Agentic SupportOps runtime: component boundaries, execution paths, persistence, and observability.

## System context

Agentic SupportOps is local-first and operates over a fictional Contoso environment. React is the operator surface; FastAPI owns the product API and application lifecycle. Deterministic and model-guided investigations reuse the same read-only capabilities.

```mermaid
flowchart LR
    Operator --> Web[React / Vite]
    Web -->|HTTP| API[FastAPI]
    API --> Services[Application services]
    Services --> Runtime[Deterministic / Responses / Agents SDK]
    Runtime --> Registry[InvestigationToolRegistry]
    Registry --> Simulation[Read-only capabilities]
    Simulation --> Fixtures[Contoso fixtures]
    Services --> Repository[SQLAlchemy repositories]
    Repository --> Database[(SQLite)]
    Services -. diagnostic spans .-> Telemetry[OpenTelemetry boundary]
```

The UI launches deterministic and manual Responses API investigations. The Agents SDK runtime and historical run/event APIs are available through FastAPI but lack dedicated frontend screens.

## Component responsibilities

| Component | Responsibility |
| --- | --- |
| `apps/web` | Health, incident catalog, mode selection, execution requests, and evidence/result presentation. |
| `api/routes.py` | HTTP contracts, dependency injection, status translation, and runtime selection. |
| `services/` | Orchestration, loop limits, lifecycle rules, and event recording. |
| `integrations/` | Responses API, Agents SDK, and MCP infrastructure boundaries. |
| `InvestigationToolRegistry` | Canonical catalog, schemas, exact input validation, execution, and normalized `ToolResult`. |
| `tools/` | Deterministic read-only capabilities over fixture-backed repositories. |
| `repositories/` | Persistence queries, transaction ownership, history retrieval, and simulation access. |
| `db/` | SQLAlchemy records/session and idempotent SQLite compatibility. |
| `observability/` | Optional OpenTelemetry spans and safe resource attributes. |

## Execution paths

### Deterministic

The incident category selects a declarative playbook. Steps resolve arguments from incident context and execute directly through the registry. Before starting, the repository replaces the prior deterministic Evidence/Steps materialized view for that incident. Successful observations become evidence; every outcome becomes an investigation step.

Deterministic investigations do not create model-guided run records or model-turn events.

### Manual Responses API

The service creates a `RUNNING` record in mode `ai`, records `run_started`, and requests provider turns through `ResponsesGateway`. Function calls pass through the selected transport. Loop, total-call, repeated-call, timeout, and output limits remain application-owned. Tests use a fake gateway and make no OpenAI call.

### Agents SDK

The comparative service creates a `RUNNING` record in mode `agents_sdk`. Application-created `FunctionTool` adapters invoke `AgentsSDKRunContext`, which applies call limits, records events, dispatches through the selected registry/transport, and normalizes results. Agents SDK provider tracing remains disabled.

## Tool governance and MCP

The registry contains 20 known read-only tools with explicit schemas. Exact argument names and string values are validated. The model cannot choose arbitrary processes, files, database queries, URLs, headers, or credentials.

```text
DIRECT: Runtime -> InvestigationToolRegistry -> capability

MCP:    Runtime -> MCP client -> stdio -> MCP server
                -> InvestigationToolRegistry -> same capability
```

MCP exposes only `get_disk_usage`, `check_dns_resolution`, and `get_application_health`. Thin handlers delegate to the canonical registry; business logic is not copied. The client uses the active Python executable, a fixed module, no shell, a bounded timeout, and per-call cleanup. MCP SDK types stay inside `integrations/`.

MCP adds a standards-based internal transport. It does not replace FastAPI, create a remote server, or bypass registry validation.

## Run and event lifecycle

```mermaid
stateDiagram-v2
    [*] --> RUNNING: create run + run_started
    RUNNING --> COMPLETED: valid result
    RUNNING --> INSUFFICIENT_EVIDENCE: valid terminal result
    RUNNING --> FAILED: controlled failure
    COMPLETED --> [*]
    INSUFFICIENT_EVIDENCE --> [*]
    FAILED --> [*]
```

Only `RUNNING` records may transition. A second terminal transition raises `InvalidInvestigationTransitionError`.

Events store `investigation_id`, runtime, type, and monotonically increasing sequence. Normal execution appends events; retrieval orders them by sequence. Metadata may include safe transport and trace correlation fields, not provider payloads, credentials, headers, or full evidence.

## Evidence-driven investigation

```text
incident -> agent -> tool policy -> approved direct/MCP capability
         -> persisted evidence -> model correlation -> bounded hypothesis
         -> operator recommendation
```

Evidence is a successful, normalized read-only `ToolResult` persisted by the application. Model output is an assessment of that evidence; it is not itself evidence. Each model-guided Evidence and InvestigationStep carries the owning `investigation_id`, and the final result exposes `evidence_ids` that resolve to those committed records. The application replaces any model-supplied identifiers with the IDs actually collected for that investigation. With no successful evidence, it records `insufficient_evidence`, caps confidence, removes unsupported evidence claims, and reports the missing information.

The model can choose only schemas exposed by the application registry. MCP discovery never expands that policy: the MCP transport has its own fixed subset, validates arguments before starting stdio, and normalizes the server response back to `ToolResult` before persistence.

The persisted plan is deliberately lightweight: ordered model/tool events show what category was checked without storing chain-of-thought, scratchpads, prompts, or private deliberation. Recommendations remain human-controlled; no tool can execute the proposed remediation.

## Persistence and transactions

Run records are append-oriented history. Latest lookups order by descending run ID; history lists all runs newest-first. Historical events are explicitly run-scoped.

SQLite enforces:

```text
UNIQUE (incident_id, mode) WHERE status = 'RUNNING'
```

This permits one manual Responses run and one Agents SDK run for the same incident, while rejecting concurrency within one mode. `IntegrityError` handling rolls back the session before returning a controlled conflict.

Terminal consistency uses one transaction. The recorder adds `run_completed` or `run_failed` with `commit=False`; completion/failure updates the run and commits both. Commit failure rolls both changes back.

Evidence and InvestigationSteps are different from events but are now append-oriented for model-guided runs. New records carry the current `AIInvestigationRecord.id` as `investigation_id`; historical artifact retrieval filters by that stable relationship. Legacy rows remain readable with a null association, while deterministic evidence retains its existing incident/origin materialized-view behavior.

## SQLite compatibility

There is no migration framework. Before `Base.metadata.create_all`, `ensure_sqlite_schema_compatibility` performs an idempotent SQLite-only evolution:

- adds legacy Evidence/Step columns, nullable `investigation_id` associations, and indexes when absent;
- rebuilds legacy run tables with obsolete uniqueness;
- preserves run rows during reconstruction;
- creates the runtime-scoped partial unique index.

Tests validate fresh databases, legacy constraint/index forms, preservation, index recreation, and repeated startup using temporary SQLite files.

## Observability

Persisted events are the domain audit timeline. OpenTelemetry is an optional technical view around request, investigation, model, tool, and selected persistence boundaries.

Tracing is off by default. Supported local exporters are `none` and `console`; no collector is required. Safe resource identifiers may be attached to spans, while prompts, evidence payloads, credentials, response bodies, and headers are excluded.

## Operator control and limits

The operator chooses the incident and investigation mode. AI is unavailable in the UI without an OpenAI key. Selecting another incident aborts the browser request/view; it is not a server-side cancellation protocol.

There is currently no authentication, approval workflow, remediation tool, remote MCP deployment, background queue, or multi-user control plane. Recommendations are operator-facing output, not automatically authorized actions.
