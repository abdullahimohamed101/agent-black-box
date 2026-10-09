# Project State

Last updated: 2026-10-09

## Current phase
Phases 0-8 are merged to `main` (CI green). The MVP gate (spec §49, §138) is reached but **not accepted**; it closes when the items under
"Next actions" are done and the user reviews it. No active plan.

## Current milestone
M0-M3 complete (foundation, ingestion, SDK, web, live streaming, coding-agent demo, cost and analytics). Phase 8 (integrations) is
post-MVP and also done. M4 starts with Phase 9, after Phase 15.

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

## In-progress work
None.

## Blocked work
None.

## Next actions (exact)
1. Web polish branch `fix/web-demo-polish`: KI-064 (one-day Daily spend chart), KI-065 (status widget blocked by the CSP), KI-066 (reproduce the 404).
2. MVP gate: KI-050 (real vendor prices), KI-054 (re-measure ingest p99 and summarizer cost), README clean-machine run; then the user decides on acceptance.
3. Phase 15 (auth, workspaces, RBAC; resolves KI-029). Then Phases 9, 10, 11 and 16 can run in parallel worktrees.

## Open decisions
- MVP acceptance (after the gate items above).
- Remove the `Co-Authored-By` trailers from the 5 earliest commits (needs a force-push; not done).
- Web auth before Phase 15: ADR-021 (server-side `runs:read` key); the web app must not be publicly exposed (KI-029).
- Whether to stamp `received_at` with the database clock (multi-instance clock skew vs the stream overlap, ADR-022).

## Known technical debt
`docs/KNOWN_ISSUES.md` (severity, target and GitHub issue per item): notably full recomputation of very large active runs (KI-016), no quotas (KI-018), no failed-auth throttling (KI-019).

## Last verified test status
2026-10-09 (Phase 6 branch merged with Phases 7 and 8): `scripts/quality.sh full` exit 0, api 535, web 311, plus event-schema, SDK, example and integration suites; GitHub CI green on PR #49 (18 checks incl. `coding-e2e`, `integrations-e2e`, `stream-e2e`). A manual browser run of the demo (runs list, run page, diff panel, analytics) worked; findings are KI-064..066.

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
