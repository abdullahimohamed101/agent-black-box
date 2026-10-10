# Project State

Last updated: 2026-10-10

## Current phase
Phases 0-8 and 15 are merged to `main` (CI green; Phase 15 is PR #58). The MVP gate (spec §49, §138) is reached but **not accepted**: the user decides after
reviewing the items under "Next actions". No active plan; the next phases to plan are 9, 10, 11 and 16 (independent of each other).

## Current milestone
M0-M3 complete (foundation, ingestion, SDK, web, live streaming, coding-agent demo, cost and analytics). Phase 8 (integrations) is
post-MVP and also done. Phase 15 (auth, RBAC) is done. M4 starts with Phase 9.

## Completed work
- Phases 0-2: foundation, event contract, ingestion/outbox/query API (`docs/plans/completed/`).
- Phase 3: `packages/sdk-python` (stdlib only, ADR-013); KI-020 fixed (`abb_runtime` role, migrations 0007-0008).
- Phase 4: `apps/web` (dashboard, runs list, run detail, typed client, fixtures, server-side read proxy, ADR-020/021).
- Phase 5: SSE live streaming (`GET /v1/runs/{id}/stream`, arrival-time resume, NOTIFY wake-up, ADR-022), polling fallback.
- Phase 6: artifact store (ADR-030, migration 0044), coding telemetry, command risk classifier and secret-safe capture (ADR-031; capture is
  best-effort and opt-in, accepted gaps are listed there), local syntax highlighting (ADR-032), `blackbox.coding`, web story/diff/shell panels,
  `examples/coding-agent`, `scripts/coding-e2e.sh`.
- Phase 7: CostEngine, analytics rollups (migrations 0040-0043), `/v1/analytics/*`, analytics page (ADR-040..043).
- Phase 8: adapters for LangGraph, OpenAI, Anthropic and MCP, conformance suite, SDK 0.2.0 (ADR-050..052).
- Phase 15: OIDC sign-in with API-owned sessions (ADR-060), one `authorize()` path and a role/scope matrix with a route-walking test registry (ADR-061),
  invitations, members, API-key and pricing-override management, append-only audit log (ADR-062, migrations 0045-0047), `payload.read` (VIEWER/BILLING never see content),
  stream re-authentication, web sign-in/switcher/settings, the shared web key removed; resolves KI-029, KI-027, KI-051, KI-033; viewers also never see command text or file paths. Closing evidence is in the plan; the independent security
  review (26 mutation checks, none survived) and its fixes are in `docs/plans/completed/phase-15-security-review.md`.

## In-progress work
None.

## Blocked work
None.

## Next actions (exact)
1. User: the one manual sign-in with a real OIDC provider (AC-14, KI-067; needs your own client id and secret, see `docs/runbooks/auth-and-access.md`), and a compose
   runtime check of the demo login (`make up && make seed`, sign in as `owner@local.test`, `make smoke && make sdk-e2e`; KI-074).
2. User: decide MVP gate acceptance. Remaining gate item: KI-050 (OpenAI prices and tiered models are not in the built-in table; the engine has no price tiers).
3. Plan and run Phases 9, 10, 11 and 16 in parallel worktrees; then 13 and 14 in sequence.

## Open decisions
- MVP acceptance (after the gate items above).
- Remove the `Co-Authored-By` trailers from the 5 earliest commits (needs a force-push; not done).
- The shared web read key is gone (Phase 15), but sign-in has only run against the fake provider (KI-067): configure a real provider and run the manual check before exposing the app.
- Whether to stamp `received_at` with the database clock (multi-instance clock skew vs the stream overlap, ADR-022).

## Known technical debt
`docs/KNOWN_ISSUES.md` (severity, target and GitHub issue per item): notably (Phase 15 additions KI-067..078: real-provider login unverified, no session list, no `email_verified`-less providers, audit retention, trusted proxies, compose login unverified) full recomputation of very large active runs (KI-016), no quotas (KI-018), no failed-auth throttling (KI-019).

## Last verified test status
2026-10-09, Phase 15 branch: `scripts/quality.sh full` run as its parts, all green: event-schema 348, sdk 806 (coverage 94%), examples 12, integrations 17/74/66/61/44, api 799, web 396; migrations 0045-0047 up/down/up; `next build`.
E2E (own databases): `auth-e2e` 8, `e2e-web-real` 2, `coding-e2e` 6, `stream-e2e` 5, `analytics-e2e` 4, `integrations-e2e` passed. Not run: `make smoke`, `make sdk-e2e`, `make up` (compose ports busy; UNVERIFIED (env), KI-074).
Previous (main): 2026-10-09 (Phase 6 branch merged with Phases 7 and 8): `scripts/quality.sh full` exit 0, api 535, web 311, plus event-schema, SDK, example and integration suites; GitHub CI green on PR #49 (18 checks incl. `coding-e2e`, `integrations-e2e`, `stream-e2e`). A manual browser run of the demo (runs list, run page, diff panel, analytics) worked; findings are KI-064..066.

## Last verified build status
Compose stack: last fully verified at Phase 2 (images build; clean-slate `docker compose up --wait` reached alembic 0006 with 15 tables;
`scripts/smoke.sh` passed; 858-request hostile probe: zero 5xx). **UNVERIFIED (env)** since: the compose stack was not rebuilt after Phase 2
(migrations 0007-0044, the `abb_runtime` role, streaming, artifacts, analytics). CI's `containers` job covered Phase 3's compose run; run `make up && make smoke
&& make sdk-e2e` as part of the MVP gate. Later phases were verified outside compose by their own E2E scripts (own API, worker, web and database).

## Commands to verify environment
```bash
colima status || colima start --cpu 2 --memory 4
cp -n .env.example .env && make setup
scripts/quality.sh full
make up && make smoke        # then: make down
```
