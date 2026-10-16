# Runbook: data retention

The full description (modes, kill switch, order of deletion, reports) is in `docs/OPERATIONS.md`, "Data retention" (ADR-159, ADR-160). This page is the monitoring and triage view.
Dashboard row: **Retention and backups**. Alerts: `AbbRetentionFailing`, `AbbRetentionStale`.

The retention process is separate from the API and worker. Its counters and gauges reach Prometheus through a textfile (`ABB_METRICS_TEXTFILE`, node_exporter), written at the end of each tick,
so they are as fresh as the last tick (hourly by default). The API and worker add `retention_mode` and `retention_seconds_since_last_pass` from the newest report in the database.

| Signal | Meaning |
| --- | --- |
| `retention_mode{mode}` | The effective mode of the newest report: `off`, `dry_run`, `enforce`. `dry_run` deletes nothing, so data is kept beyond policy by design |
| `retention_seconds_since_last_pass` | Seconds since the newest report; NaN when none exists yet |
| `retention_passes_total{mode}` | Ticks finished by mode |
| `retention_deleted_total{tier,kind}` | What enforcement removed (`runs`, `events`, `rows`, `bytes`) |
| `retention_dry_run_candidates{tier}` | What a dry run says it would delete |
| `retention_failures_total{code}` | `measure_error`, `step_failed`, `refused`, `budget_exhausted`, `tick_crashed` |

## Retention failures

`AbbRetentionFailing` names the `code`. Data is not lost by a failure: the step is retried on the next tick; it is kept longer than the policy promises.

- `step_failed`: one run or tier failed; the per-run state is in `retention_run_progress` (`SELECT tier, failure_code, attempts, count(*) FROM retention_run_progress GROUP BY 1,2,3 ORDER BY 4 DESC`)
  and the log line `retention step failed` (fields `run_id`, `tier`, `failure_code`). `artifact_store` means the files could not be removed: check the artifact directory and its permissions.
- `measure_error`: counts the errors a tick hit while measuring or processing; one count per error, the database is the suspect ([database saturation](database-saturation.md)).
- `refused`: enforcement needs an artifact store and the tick was given none, so it did nothing; check that the retention service has `ABB_ARTIFACT_DIR` and mounts the artifact volume (compose does).
- `budget_exhausted`: the tick hit its time budget (`ABB_RETENTION_TICK_BUDGET_SECONDS`); the backlog drains over ticks. Frequent exhaustion on a large backlog means the budget or the limits per tick are too small.
- `tick_crashed`: the tick raised; read the process log and `docker compose logs retention`.

## Retention is stale

`AbbRetentionStale`: no report for over 3 hours (three default ticks) while the mode is not `off`. The retention process is not running or cannot write reports.
`docker compose --profile app ps retention` and its log; the database it connects to is configured separately (`ABB_RETENTION_DATABASE_URL`, role `abb_retention`). If retention is deliberately stopped,
set `ABB_RETENTION_MODE=off` so the alert stays quiet. Raise the 3 hours if you set a longer tick interval.

## Before enforcing

A backup and a restore drill come first ([backup and restore](backup-restore.md)). Enforcement is permanent. Watch `retention_dry_run_candidates` for a full tick before switching the mode.
