# Publication readiness

This checklist records evidence from local validation and the controlled GitHub publication completed on August 28, 2026.

## Verified

- [x] No known credentials, private keys, tokens, or secret-bearing URLs are tracked.
- [x] Local `.env`/`.env.local` files, databases, virtualenvs, caches, build output, `node_modules`, and IDE files are ignored.
- [x] `.env.example` contains placeholders and safe defaults only.
- [x] The repository uses the MIT License with explicit public reuse terms.
- [x] Setup documentation uses the committed uv and npm lockfiles.
- [x] Architecture and runtime boundaries are documented from source.
- [x] MCP transport, three-tool allowlist, and security boundary are documented.
- [x] Run/event history, concurrency, and terminal transaction guarantees are documented.
- [x] Backend, MCP, typecheck, and build commands are documented.
- [x] CI matches the project and requires no OpenAI secret.
- [x] Backend tests pass locally.
- [x] Real MCP stdio parity tests pass locally.
- [x] Frontend TypeScript validation passes locally.
- [x] Frontend production build passes locally.
- [x] Focused frontend behavioral tests pass locally and run as a mandatory CI gate.
- [x] The public repository was checked for accidental secrets and local/runtime artifacts.
- [x] GitHub Actions executed successfully on a hosted Ubuntu runner.
- [x] Hosted backend CI and real MCP stdio parity passed.
- [x] Hosted frontend TypeScript validation and production build passed.
- [x] README separates implemented scope from future evolution.
- [x] Repository status and published history are understood.
