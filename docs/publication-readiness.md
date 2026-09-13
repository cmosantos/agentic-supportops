# Publication readiness

This checklist records the final publication and validation baseline for **Agentic SupportOps v1.0.1**, completed on September 13, 2026.

## Release status

- [x] `master` is the published default branch.
- [x] `v1.0.0` records the initial complete V1 release baseline.
- [x] `v1.0.1` includes the final operator-console Goal Profile visibility fix.
- [x] The V1 scope is closed; further capabilities belong to future backlog/V2 work.

## Repository hygiene

- [x] No known credentials, private keys, tokens, or secret-bearing URLs are tracked.
- [x] Local `.env`/`.env.local` files, databases, virtualenvs, caches, build output, `node_modules`, and IDE files are ignored.
- [x] `.env.example` contains placeholders and safe defaults only.
- [x] The repository uses the MIT License with explicit public reuse terms.
- [x] Setup documentation uses the committed uv and npm lockfiles.
- [x] README separates implemented scope from future evolution.
- [x] Architecture, simulation boundaries and operator-console responsibilities are documented from source.

## Architecture and safety boundaries

- [x] Investigation Goal, Goal Profile and Investigation Plan are application-owned governed inputs.
- [x] Persisted Goal/Plan snapshots remain auditable after execution.
- [x] Deterministic, Responses API and Agents SDK runtimes reuse governed application contracts.
- [x] Tool calls are validated through the canonical `InvestigationToolRegistry`.
- [x] MCP exposes a fixed three-tool read-only allowlist rather than the complete registry.
- [x] Investigation capabilities do not perform real remediation.
- [x] Human approval does not automatically start execution.
- [x] Controlled execution replays the exact persisted proposal.
- [x] Unknown mutation outcomes are not automatically retried.
- [x] Verification performs an independent read after execution.
- [x] `VERIFIED` does not automatically imply `INCIDENT RESOLVED`.
- [x] Incident resolution requires an explicit human decision.

## Automated validation

Final release baseline:

- [x] Backend full suite: **310 passed**.
- [x] Focused MCP integration checks passed.
- [x] Frontend Vitest suite: **76 passed**.
- [x] TypeScript typecheck passed.
- [x] Vite production build passed.
- [x] `uv lock --check` passed.
- [x] `uv pip check` passed.
- [x] FastAPI application import passed.
- [x] `git diff --check` passed.
- [x] CI requires no OpenAI secret for its deterministic validation baseline.

## Final smoke validation

- [x] Backend and frontend started successfully with the documented local commands.
- [x] Health endpoint, incident loading and operator UI responded successfully.
- [x] Goal, Goal Profile and Investigation Plan were visible as governed audit metadata.
- [x] Investigation evidence and history remained scoped to the correct investigation run.
- [x] `INC-026 — User account locked` was manually validated in the browser end to end.
- [x] The AI investigation produced a bounded `unlock_simulated_user` proposal for `USR-FRANK`.
- [x] Human approval was recorded without automatic execution.
- [x] Explicit controlled execution completed with a persisted physical attempt.
- [x] Independent verification observed the account as unlocked while the incident remained open.
- [x] A separate human resolution decision changed the incident to resolved.

## Published scope

The V1 is a **local-first, simulated SupportOps engineering project** intended to demonstrate controlled agentic investigation, auditability and human-governed execution. It does not connect to or remediate real production infrastructure.

See also:

- [README](../README.md)
- [Architecture](architecture.md)
- [Simulation](simulation.md)
- [Operator console design](operator-console-design.md)
- [Changelog](../CHANGELOG.md)
