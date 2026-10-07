# Project State

Last updated: 2026-10-07

## Current phase
Phase 1 (canonical telemetry contract) complete locally on `feature/phase-1-event-contract`; not pushed, CI not yet run on it.
Phase 0 is merged to `main` (PRs #1, #2; CI green).

## Current milestone
M0 done once Phase 1 is merged. Next: Phase 2 (data model, ingestion, run querying).

## Completed work
- Phase 0 foundation: see `docs/plans/completed/phase-0-foundation.md`.
- Phase 1: `packages/event-schema` (IDs, `EventIn`/`Event`, 43-type registry, strict parsing, ordering, dedup,
  span derivation, generated JSON Schema + TS types, 26 invalid / 43 valid fixtures, fuzz test, 335 tests).
  Evidence: `docs/plans/completed/phase-1-event-contract.md`. ADR-001, 003, 006, 007, 011.

## In-progress work
None.

## Blocked work
- CI on the Phase 1 branch needs a push (approval). Nothing else.

## Next actions (exact)
1. With approval: push `feature/phase-1-event-contract`, open a PR, confirm CI green, merge.
2. Branch `feature/phase-2-ingestion` from updated `main`; run `plan-change` for Phase 2
   (`docs/IMPLEMENTATION_PLAN.md` Phase 2). Phase 2 plan must: wire `abb-event-schema` into `apps/api`
   (Docker build context moves to repo root), resolve KI-011/KI-012, decide raw-event retention, and use real PostgreSQL tests.

## Open decisions
- Remove the `Co-Authored-By` trailers from the 5 early commits (needs a force-push; not done).
- Phase 3 will decide whether the SDK depends on pydantic or ships a stdlib builder validated by contract tests.

## Known technical debt
See `docs/KNOWN_ISSUES.md` (KI-008, KI-010..013).

## Last verified test status
2026-10-07: `scripts/quality.sh full` OK: event-schema 335 passed, api 15 passed (real Postgres), web 7 passed.

## Last verified build status
2026-10-07: `next build`, API wheel, `make schema-check` OK.

## Commands to verify environment
```bash
colima status || colima start --cpu 2 --memory 4
cp -n .env.example .env && make setup
scripts/quality.sh full
```
