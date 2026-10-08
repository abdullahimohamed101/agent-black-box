# Project State

Last updated: 2026-10-07

## Current phase
Phases 0-4 are merged to `main` (CI green). Phase 5 (live streaming) is complete on `feature/phase-5-live-streaming` (pushed once at step 2, later commits local):
plan and evidence in `docs/plans/completed/phase-5-live-streaming.md`. Awaiting the user's approval to push, open the PR and watch CI.

## Current milestone
M1 is complete once Phase 5 merges; M2 starts with Phase 6 (coding-agent demo).

## Completed work
- Phases 0-2: foundation, event contract, ingestion/outbox/query API (`docs/plans/completed/`).
- Phase 3: `packages/sdk-python` (stdlib only, ADR-013); KI-020 fixed (`abb_runtime` role, migrations 0007-0008).
- Phase 4: `apps/web` (dashboard, runs list, run detail, typed client, fixtures, server-side read proxy, ADR-020/021).
- Phase 5: `GET /v1/runs/{id}/stream` (SSE; resume by arrival time; Postgres NOTIFY wake-up; index `ix_events_run_arrival`, migration 0009;
  ADR-022), per-process stream limits, web proxy relay, `EventSource` client, canonical-order merge with Python golden parity, live bar and
  status line, polling fallback. E2E `make stream-e2e` (5 tests, p95 about 7 ms), fan-out benchmark (`STREAM_E2E_MODE=bench`),
  runbook `docs/runbooks/stream-issues.md`. Independent verify + review done, findings fixed.

## In-progress work
None.

## Blocked work
- Push, PR and CI for Phase 5 need the user's approval. The new `stream-e2e` CI job has never run on GitHub (Chrome, `.env` copy).

## Next actions (exact)
1. With approval: push `feature/phase-5-live-streaming`, open the PR, confirm CI green (watch the new `stream-e2e` job), user merges.
2. Then choose: Phase 6 (needs Phase 5 merged), or Phases 7 (analytics; resolves KI-028) and 8 (integrations) in parallel worktrees.
   Read `docs/KNOWN_ISSUES.md` first (S1: KI-018, 019, 029).

## Open decisions
- Remove the `Co-Authored-By` trailers from the 5 earliest commits (needs a force-push; not done).
- Web auth before Phase 15: ADR-021 (server-side `runs:read` key); the web app must not be publicly exposed (KI-029).
- Whether to stamp `received_at` with the database clock (multi-instance clock skew vs the stream overlap, ADR-022).

## Known technical debt
`docs/KNOWN_ISSUES.md` (severity, target and GitHub issue per item): notably full recomputation of very large active runs (KI-016), no quotas (KI-018), no failed-auth throttling (KI-019).

## Last verified test status
2026-10-07: `scripts/quality.sh full` exit 0 on Phase 5: event-schema 339, sdk 135, api 400, web 275; `scripts/stream-e2e.sh` 5/5.

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
