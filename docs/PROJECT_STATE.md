# Project State

Last updated: 2026-10-07

## Current phase
Phases 0-3 are merged to `main` (CI green). Phase 4 (core web product) is complete on `feature/phase-4-web-product`, `main` merged in, PR #26 awaiting CI and merge.
Phases 3 and 4 were built in parallel in separate worktrees (`../abb-worktrees/`); each kept its own plan file.

## Current milestone
M1 is complete once Phase 4 merges and Phase 5 (live streaming) lands; M2 starts with Phase 6 (coding-agent demo).

## Completed work
- Phase 0 foundation, Phase 1 event contract, Phase 2 ingestion/outbox/query API: see `docs/plans/completed/`.
- Phase 3: `packages/sdk-python` (stdlib only, ADR-013), 135 tests, overhead 7-23 us/op (`docs/benchmarks/phase-3-sdk.md`), `make sdk-e2e`.
  KI-020 fixed: `abb_runtime` role (migrations 0007-0008) cannot UPDATE/DELETE `events` or delete their parents; events->runs FK is RESTRICT.
- Phase 4: `apps/web` dashboard, runs list, run detail with virtualized timeline, generated typed client, fixtures, server-side read proxy
  (ADR-020, ADR-021), CSP/security headers, Playwright e2e (fixtures and real run), axe checks. Plan: `docs/plans/completed/phase-4-web-product.md`.

## In-progress work
None.

## Blocked work
- Phase 4 merge needs the user's approval; GitHub CI on the merged Phase 4 branch not yet observed.

## Next actions (exact)
1. Confirm CI green on PR #26 and merge it (user merges).
2. Branch `feature/phase-5-live-streaming` from updated `main`; `plan-change` for Phase 5 (SSE, `Last-Event-ID` resume, reorder buffer). Read
   `docs/KNOWN_ISSUES.md` first: KI-029 (web key exposure, S1) and ADR-020's interim live-run refetch section are the direct inputs.
3. Phase 6 follows (needs SDK and UI). Phases 7 (analytics; resolves KI-028) and 8 (integrations) can run in parallel after that.

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
