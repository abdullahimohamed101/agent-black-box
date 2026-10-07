# Project State

Last updated: 2026-10-07

## Current phase
Phase 1 (canonical telemetry contract) complete; PR #3 open. Phase 2 planned on `feature/phase-2-ingestion` (stacked on the Phase 1 branch).
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
1. PR #3 (Phase 1) is open; merge it on user approval once CI is green, then rebase `feature/phase-2-ingestion` onto `main`.
2. Phase 2 plan is written: `docs/plans/active/phase-2-ingestion.md` (awaiting user review of the decisions D1-D19). Start at its step 1.

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
