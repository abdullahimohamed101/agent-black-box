# Testing

Pyramid and commands. Commands become real in Phase 0; until then this is the contract.

| Layer | Scope | Tooling |
| --- | --- | --- |
| Unit | event validation, redaction, pricing, policy matching, sequence logic, detectors, authz | pytest / vitest |
| Repository | real PostgreSQL: tenant scoping, dedup, cursors, transactions, migrations | pytest + Postgres (Compose or native) |
| Contract | SDK fixtures vs JSON Schema; adapter golden canonical-event fixtures; schema compat vs previous minor | pytest |
| Integration | SDK -> API -> DB -> worker -> query | pytest |
| Browser E2E | demo agent -> SDK -> API -> Postgres -> SSE -> UI; filters; drawers; reconnect | Playwright |
| Load | realistic payloads, bursty starts, long-lived streams; results to `docs/benchmarks/` | locust/k6 (decide in Phase 18 plan) |

Rules: never skip/weaken a failing test; no mocks where a real Postgres test is feasible; UI
changes are exercised in a browser; fixtures are deterministic (fixed IDs/timestamps).

Commands (targets):
```bash
make test        # unit + repository + contract (+ web unit)
make lint        # ruff, eslint, prettier --check
make typecheck   # mypy, tsc
make e2e         # Playwright against the Compose stack
scripts/quality.sh quick   # format check, lint, typecheck, fast tests
scripts/quality.sh full    # quick + integration + migration check + builds
```
