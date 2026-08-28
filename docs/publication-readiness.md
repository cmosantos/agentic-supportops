# Publication readiness

This checklist records local evidence collected before controlled GitHub publication. It does not claim publication or a remote GitHub Actions run.

## Verified locally

- [x] No known credentials, private keys, tokens, or secret-bearing URLs are tracked.
- [x] Local `.env`/`.env.local` files, databases, virtualenvs, caches, build output, `node_modules`, and IDE files are ignored.
- [x] `.env.example` contains placeholders and safe defaults only.
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
- [x] README separates implemented scope from future evolution.
- [x] Repository status and intended publication diff are understood.
- [x] No push, deployment, remote creation, or publication occurred during Mission 13.

## Intentionally pending

- [ ] GitHub Actions has executed on a hosted runner. This requires Mission 14 publication.
- [ ] A public license has been selected and added. License choice requires an explicit owner decision.
- [ ] Frontend automated tests pass. No frontend test runner exists; TypeScript and build are the established gates.

## Owner decisions before publication

Choose a license, confirm the GitHub account/organization and visibility, review final commit history, and approve the first push. After publication, inspect the first Actions run before presenting CI as remotely validated.
