# Testing

Pyramid and commands. Commands below are real as of Phase 0 (`make test lint typecheck`, `scripts/quality.sh`); `make e2e` and load tests arrive later.

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
make test        # event-schema + api (real Postgres) + web unit
make schema-check # generated JSON Schema / TS types are current
make lint        # ruff, eslint, prettier --check
make typecheck   # mypy, tsc
scripts/quality.sh quick   # ruff, mypy, eslint, prettier, tsc, pytest, vitest
scripts/quality.sh full    # quick + migration up/down/up on the TEST database + next build + wheel build
```
Database tests need Postgres (`make db`). `ABB_TEST_DATABASE_URL` names the server and credentials; the
test session creates and drops its own throwaway database (migrated to head), tables are truncated
between tests, and migration tests create one database each. Neither the dev database nor `abb_test`
holds test data.
```text
```
