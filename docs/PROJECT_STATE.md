# Project State

Last updated: 2026-10-07

## Current phase
Phases 0-4 are merged to `main` (CI green). Phase 5 (live streaming) is active on `feature/phase-5-live-streaming`: plan `docs/plans/active/phase-5-live-streaming.md`, steps 1-4 of 9 done.

## Current milestone
M1 is complete once Phase 5 lands; M2 starts with Phase 6 (coding-agent demo).

## Completed work
- Phase 0 foundation, Phase 1 event contract, Phase 2 ingestion/outbox/query API: see `docs/plans/completed/`.
- Phase 3: `packages/sdk-python` (stdlib only, ADR-013), 135 tests, overhead 7-23 us/op (`docs/benchmarks/phase-3-sdk.md`), `make sdk-e2e`.
  KI-020 fixed: `abb_runtime` role (migrations 0007-0008) cannot UPDATE/DELETE `events` or delete their parents; events->runs FK is RESTRICT.
- Phase 4: `apps/web` dashboard, runs list, run detail with virtualized timeline, generated typed client, fixtures, server-side read proxy
  (ADR-020, ADR-021), CSP/security headers, Playwright e2e (fixtures and real run), axe checks. Plan: `docs/plans/completed/phase-4-web-product.md`.

## In-progress work
Phase 5 step 5 next: web proxy streaming, `ordering.ts`, `stream.ts` (`apps/web`).

## Blocked work
None.

## Next actions (exact)
1. Phase 5 plan step 5 (see the plan's ordered steps).
2. Then Phase 6 (needs SDK, UI and streaming for its E2E). Phases 7 (analytics; resolves KI-028) and 8 (integrations) can run in parallel with 5/6
   in separate worktrees.

## Open decisions
- Remove the `Co-Authored-By` trailers from the 5 earliest commits (needs a force-push; not done).
- Web auth before Phase 15: decided in ADR-021 (server-side `runs:read` key); the web app must not be publicly exposed (KI-029).

## Known technical debt
`docs/KNOWN_ISSUES.md` (severity, target and GitHub issue per item): notably full recomputation of very large active runs (KI-016), no quotas (KI-018), no failed-auth throttling (KI-019).

## Last verified test status
2026-10-07: `scripts/quality.sh full` exit 0 on Phase 4 with main merged: event-schema 337, sdk 135, api 348, web 81; e2e 9 passed; real-run e2e passed.

## Last verified build status
2026-10-07: images build; clean-slate `docker compose up --wait` reaches alembic 0006 with 15 tables; `scripts/smoke.sh` passes;
858-request hostile probe: zero 5xx.

## Commands to verify environment
```bash
colima status || colima start --cpu 2 --memory 4
cp -n .env.example .env && make setup
scripts/quality.sh full
make up && make smoke        # then: make down
```
