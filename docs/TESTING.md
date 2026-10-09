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

What the Phase 2 suite looks like (all against real PostgreSQL, ~310 API tests):

- **Constraint tests** prove the database itself rejects cross-tenant references, duplicate events and bad enum values.
- **Store tests** cover idempotency, same-id-different-content conflicts, 50-way concurrent identical batches, overlapping batches
  (deterministic proof of stable write order by capturing the SQL), atomicity, and append-only code.
- **API tests** go through the real application: authentication matrix, body/gzip limits (including a memory-bounded zip bomb),
  partial rejection, rate limiting, post-auth failures, log hygiene, fuzzed batches, and the query API with tenancy matrices.
- **Equivalence tests** assert that run state equals a from-scratch derivation under random batching, ordering, duplication and
  worker timing; **order-sensitive** scenarios make sure ordering bugs cannot hide.
- **Worker tests** cover claiming (`SKIP LOCKED` with two concurrent claimers), leases, retries, dead letters, lost leases, and the
  real worker process (start, work, SIGTERM, exit 0).
- **OpenAPI tests** keep `openapi.json` current and its auth/error documentation honest.
- `make smoke` (containers) and `make bench` (latency baseline, `docs/benchmarks/`) are not part of `make test`.
- Every test has a 90 s timeout so a deadlock fails fast. Security-relevant logic is **mutation-checked** during review
  (change the code, confirm a test fails); tests that survive a mutant are strengthened, not kept.

### Web (Phase 4)
- **Vitest + Testing Library** (`apps/web/tests`): pure timeline/dashboard/format logic, the read proxy (allowlist, key never forwarded
  to the browser, upstream auth failure mapped to 502), the fixture API (cursor paging, filters), and components (run detail, runs list,
  dashboard, drawers) with every loading / empty / error / not-found state, keyboard navigation and hostile payloads rendered as text.
  The typed client stays honest through `pnpm --filter @abb/web gen:api:check` (part of `scripts/quality.sh`).
- **Fixtures** (`apps/web/src/fixtures`): success, failure+retry, expensive, running, awaiting approval and a 10,000-event stress run,
  deterministic and served by the same read routes when `ABB_WEB_DATA_SOURCE=fixtures`.
- **Playwright** (`make e2e`, port 3100, system Chrome so no browser download): fixture smoke, axe (WCAG A/AA, no serious/critical),
  focus return, a bounded DOM for 10,000 events, mobile overflow, screenshots to `docs/screenshots/phase-4/`.
  `make e2e-real` (`scripts/e2e-web-real.sh`) starts an API (:8110) and worker against a dedicated `abb_p4` database, ingests runs over
  HTTP and drives the UI through the web proxy (:3102). It never touches the shared dev/test databases.
- **Streaming E2E** (`make stream-e2e`, `scripts/stream-e2e.sh`, CI job `stream-e2e`): API (:8120) + worker on a dedicated `abb_p5`
  database, the built web server (:3103), Chrome via Playwright, and `scripts/stream_driver.py` writing runs (the Python SDK, or plain HTTP).
  Covers: the SDK example's trace appearing live with no page reload and ending cleanly, axe and console errors on a live page, a browser
  refresh mid-run (every event once), a connection cut by a fault-injecting TCP proxy (partial-data notice, recovery, no loss), and the
  accept-to-display latency gate (p95 < 1 s; recorded in `docs/benchmarks/phase-5-streaming.md`). Screenshots: `docs/screenshots/phase-5/`.
  Server-side streaming is tested over a real socket (`apps/api/tests/test_stream_api.py`), because an in-process ASGI transport buffers
  whole responses.

### Auth, workspaces and RBAC (Phase 15)
- **Authorization generator** (`apps/api/tests/authz/`): `registry.py` lists every route the application serves with the expected outcome for each of
  27 actors (13 key kinds, six roles, and people who are not members, were removed, were demoted, hold an expired or revoked session, send a session as a
  bearer or a key as a cookie, or omit the workspace header). `test_registry_is_complete.py` walks `app.routes` (including HEAD/OPTIONS) and fails for a
  route without a case; `test_role_matrix.py` runs the table against a real database and socket; a literal copy of the matrix in the tests is compared
  with `authz/matrix.py` in both directions (`test_matrix_data.py`); `test_cross_workspace.py` scans every body and SSE frame for another workspace's
  canary values and compares analytics magnitudes; `test_no_scattered_checks.py` is an AST scan against role or scope inspection outside `authz/`.
- **Sign-in** (`tests/test_auth_sessions.py` with `tests/fake_oidc.py`, an in-process OIDC provider with knobs for every bad token, nonce, key, audience,
  issuer and unverified email): state/cookie binding, identity linking and conflict, discovery and JWKS rules, open redirect, CSRF, expiry, revocation,
  downgrade and removal taking effect at once, database-down 503, logout idempotence, log hygiene.
- **Administration**: `test_members_api.py` (last-owner rule under concurrency, owner changes, bounds, invitations), `test_api_keys_api.py`,
  `test_pricing_api.py`, `test_audit.py`/`test_audit_api.py` (one row per action, denial flood bounded, no secret in `details`),
  `test_payload_access.py`, `test_runtime_role.py` (append-only tables), `test_stream_reauth.py` (a revoked key or session, or a removed member, ends an
  open stream within the re-check interval), `test_cli_access.py` (gated `create-session`, dev owner, `relink-user`).
- **Web unit** (`apps/web/tests`): the proxy forwards exactly the header allowlist and no shared credential, rejects cross-origin writes, sign-in handlers
  relay `Location` and `Set-Cookie` without following redirects, session helper and gate, settings pages in every state.
- **Auth E2E** (`make auth-e2e`, `scripts/auth-e2e.sh`, CI job `auth-e2e`): dedicated `abb_p15` database, API (:8165, stream re-check every 2 s), the
  DEVELOPMENT-ONLY fake provider (:8166, `scripts/fake-oidc.sh`), a worker and the built web server (:3160). `apps/web/e2e/auth.spec.ts` drives a real
  browser: redirect to sign-in and login through the provider, an HttpOnly cookie, workspace switch and cross-workspace 404, owner sees a payload,
  invitation link and key token shown once, an invited person joins, role downgrade to VIEWER withholds payloads and hides settings, removing a person ends
  their live stream (`STREAM_UNAUTHORIZED`), sign-out; axe on the login and settings pages. Screenshots: `docs/screenshots/phase-15/` (committed).
- **The other E2E scripts no longer use a shared read key**: `e2e-web-real`, `coding-e2e`, `stream-e2e` and `analytics-e2e` mint a development session for a
  throwaway workspace owner (`create-session`, only with `ABB_ENVIRONMENT=development|test` and `ABB_ALLOW_DEV_SESSIONS=1`, which the scripts set for that one
  command) and pass it to Playwright as `E2E_SESSION_TOKEN` (`apps/web/e2e/session.ts` puts it in the browser as the HttpOnly cookie) with `E2E_WORKSPACE`.
  Specs that call the proxy directly add `X-ABB-Workspace` (`workspaceHeader`).

Rules: never skip/weaken a failing test; no mocks where a real Postgres test is feasible; UI
changes are exercised in a browser; fixtures are deterministic (fixed IDs/timestamps).

Commands (targets):
```bash
make test        # event-schema + api (real Postgres) + web unit
make schema-check # generated JSON Schema / TS types are current
make lint        # ruff, eslint, prettier --check
make typecheck   # mypy, tsc
scripts/quality.sh quick   # schema + openapi currency, ruff, mypy, eslint, prettier, tsc, pytest, vitest
scripts/quality.sh full    # quick + migration up/down/up on the TEST database + next build + wheel build
```
Database tests need Postgres (`make db`). `ABB_TEST_DATABASE_URL` names the server and credentials; the
test session creates and drops its own throwaway database (migrated to head), tables are truncated
between tests, and migration tests create one database each. Neither the dev database nor `abb_test`
holds test data.
```text
```

- **Coding-agent E2E** (`make coding-e2e`, `scripts/coding-e2e.sh`, CI job `coding-e2e`): dedicated `abb_p6` database, API (:8140) with a
  throwaway artifact directory and a worker; the scripted demo agent (`examples/coding-agent`) writes a real run through the SDK; a
  server-side scan (`scripts/coding_e2e_check.py`) searches events, `artifacts.name/kind/uri` and every artifact file for the planted
  secrets; Playwright (`apps/web/e2e/coding.spec.ts`, web on :3140) checks the story, diff, lazy shell output, chunked loading and that no
  planted secret reaches any page or API response. The script refuses to start if the port is already serving and kills the whole
  process tree on exit. **The Playwright spec writes screenshots into the tracked `docs/screenshots/phase-6/`**: commit them when they changed
  meaningfully, otherwise `git checkout` them. Example tests: `uv run --project packages/sdk-python pytest examples/coding-agent/tests`.
