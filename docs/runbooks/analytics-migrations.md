# Runbook: analytics migrations (0040-0043) on a database with data

## What runs and what it costs
| Revision | Work | Lock | Measured |
| --- | --- | --- | --- |
| 0040 | create `cost_calculations`, `pricing_overrides` | brief | milliseconds |
| 0041 | add typed columns to `runs`, `project_id` and `run_started_at` to `spans`; **backfill both from `runs`/`summary` in one `UPDATE`**; create `ix_spans_window`, `ix_runs_workspace_started` | `ACCESS EXCLUSIVE` on `runs` and `spans` for the whole transaction (Alembic runs it in one transaction) | **3 min 5 s** on 700,000 runs / 5,948,006 spans (`abb_p7`, Colima VM, 2 vCPU); the updated tables roughly double in size until vacuumed |
| 0042 | create five empty rollup tables | brief | milliseconds |
| 0043 | widen money columns to `numeric(38,9)` | brief per column, no table rewrite | seconds |

## Before migrating a large database
1. Plan a maintenance window of at least **twice the measured time per 6 million spans** (run `ingest` and the worker stopped; reads of runs/spans block during 0041).
2. Make sure there is free disk for about 2x the `runs` and `spans` tables (the backfill rewrites every row once); run `VACUUM (ANALYZE) runs, spans` afterwards.
3. `alembic upgrade head`, then for each workspace: `python -m abb_api.cli refresh-analytics --workspace <slug>` (rollups start empty) and,
   to price old runs with the cost engine, `python -m abb_api.cli rebuild-costs --workspace <slug>` (it prints a warning if it stopped at `--limit`; repeat with `--since`).
4. If a window is impossible: apply 0041's `ADD COLUMN`s first, backfill in batches with your own `UPDATE ... WHERE id IN (...)`, then stamp and create the indexes with `CREATE INDEX CONCURRENTLY`. This is not automated (KI-056).

## Failure modes
- Interrupted 0041: the transaction rolls back; nothing is half-applied; rerun.
- Analytics empty after upgrade: rollups were not built; run `refresh-analytics`.
- A day looks wrong: `refresh-analytics --workspace <slug> --since <date>` rewrites it from `runs`/`spans`/`cost_calculations`.
