# Project State

Last updated: 2026-10-07

## Current phase
Phase 2 (data model, ingestion, run querying) is complete on `feature/phase-2-ingestion`, pushed, awaiting a PR, CI and merge.
Phases 0 and 1 are merged to `main`.

## Current milestone
M1 data plane done once Phase 2 merges. Next: Phase 3 (Python SDK).

## Completed work
- Phase 0 foundation, Phase 1 event contract: see `docs/plans/completed/`.
- Phase 2: tenant-keyed schema and migrations 0001-0006, API keys and CLI, idempotent ingestion (`/v1/events[/batch]`, gzip, limits,
  rate limiting), outbox worker (leases, heartbeats, retries, dead letters), derived runs and spans, query API (runs, events, spans,
  cursors, project scoping), OpenAPI contract, compose `migrate`/`worker`, `make smoke`/`make bench`. Evidence and defects found:
  `docs/plans/completed/phase-2-ingestion.md`. ADR-002, ADR-012. Benchmark: `docs/benchmarks/phase-2-ingestion.md`.

## In-progress work
None.

## Blocked work
- Merging needs the user's approval for a PR; GitHub CI has not run on the final Phase 2 commit.

## Next actions (exact)
1. With approval: open the Phase 2 PR (`feature/phase-2-ingestion` -> `main`), confirm CI green, merge.
2. Branch `feature/phase-3-python-sdk` from updated `main`; `plan-change` for Phase 3 (`docs/IMPLEMENTATION_PLAN.md`). The plan must decide:
   pydantic in the SDK vs a stdlib builder validated by contract tests (Phase 1 R3), the exporter's retry/backoff against `429`/`503`
   `Retry-After`, and how it uses `POST /v1/events/batch` (gzip, <= 1000 events, 5 MiB). Read `docs/architecture/api-v1.md` first.

## Open decisions
- Remove the `Co-Authored-By` trailers from the 5 earliest commits (needs a force-push; not done).
- How the Phase 4 web UI authenticates to the read API before Phase 15 (server-side `runs:read` key in the Next.js backend is the likely answer).

## Known technical debt
`docs/KNOWN_ISSUES.md` (KI-008, KI-010, KI-013 to KI-026): notably full recomputation of very large active runs (KI-016), no DB privilege
separation yet (KI-020), no quotas (KI-018), no failed-auth throttling (KI-019).

## Last verified test status
2026-10-07: `scripts/quality.sh full` exit 0: event-schema 337, api 329 (98% coverage), web 7; pristine clone also passes.

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
