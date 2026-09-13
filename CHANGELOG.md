# Changelog

All notable project milestones are documented here.

## [v1.0.1] - 2026-09-13

### Changed

- Exposed the persisted `goal_profile` in the operator-facing Investigation Contract.
- Kept Goal Profile, Objective, Success Criteria, Constraints, Human Approval and Investigation Plan visible together as read-only audit metadata.

### Validated

- Completed the final browser smoke test with `INC-026 — User account locked`.
- Confirmed the full governed lifecycle:
  `Investigation -> Proposal -> Human Approval -> Controlled Execution -> Independent Verification -> Human Resolution`.
- Confirmed that approval does not trigger execution automatically.
- Confirmed that a verified technical outcome does not resolve an incident automatically.
- Confirmed explicit operator resolution after independently observed `locked=false` state.

## [v1.0.0] - 2026-09-13

First complete portfolio release of Agentic SupportOps.

### Investigation governance

- Incident-centered investigations with deterministic, OpenAI Responses API and OpenAI Agents SDK runtimes.
- Explicit Investigation Goal and controlled Goal Profiles.
- Persisted Investigation Plan snapshots used as governed runtime input.
- Run-scoped evidence, ordered investigation steps and append-oriented event history.
- Historical investigation review and audit-friendly contract metadata.

### Human-controlled execution

- Structured remediation proposals validated against persisted investigation evidence.
- Explicit human approval or rejection before any mutation.
- Separate controlled execution step using the exact persisted proposal.
- Canonical physical attempt tracking and outcome certainty.
- Unknown-outcome handling without unsafe automatic mutation retry.
- Read-only reconciliation and stale-recovery path.
- Independent post-execution verification.
- Separate human resolution gate after verification.

### Platform and integrations

- FastAPI backend.
- React + TypeScript + Vite operator console.
- SQLAlchemy + SQLite persistence.
- Canonical `InvestigationToolRegistry` with 20 read-only SupportOps capabilities.
- Optional local MCP stdio transport exposing a fixed three-tool allowlist.
- Optional application-owned OpenTelemetry tracing.
- 26 simulated incidents with 8 deeply supported deterministic playbooks.
- Golden journeys for `INC-023`, `INC-024` and `INC-026`.

### Release validation baseline

- 310 backend tests passed.
- 76 frontend tests passed.
- TypeScript typecheck passed.
- Vite production build passed.
- MCP integration checks passed.
- Final focused smoke checks passed before release.

## Project scope

Agentic SupportOps is a local-first engineering and portfolio project. It does not query or remediate real infrastructure. Investigation capabilities are read-only; controlled mutations affect only deterministic local simulation state.
