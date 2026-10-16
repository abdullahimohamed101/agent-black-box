# Retention runbook (operator)

How long Agent Black Box keeps data, how to turn deletion on safely, how to read what the job did, how to stop it and what to do when it
is interrupted or fails. Mechanism and order: ADR-159, ADR-160; purge commands: ADR-163; backups: `docs/runbooks/backup-restore.md`.
The settings and the table of environment variables are in `docs/OPERATIONS.md` ("Data retention"). This page is the procedure.

**Enforced deletion is permanent.** A deleted event, artifact file, search entry or run record is gone from Postgres, from the artifact
directory and from the rollups. Nothing in the product brings it back; only a restore of a backup taken before it does. Take a backup,
**perform a restore drill**, and only then enable enforcement.

## What the job keeps and for how long

Per workspace, in days after a run ended (the audit log: after the entry). Owners and admins edit them on **Settings, Retention** or with
`PUT /v1/workspaces/{id}/retention`; the floor is 7 days (90 for the audit log, which only an owner may change); "keep forever" is an explicit choice; `artifacts <= trace <= run`. **A shortening takes effect after `ABB_RETENTION_GRACE_HOURS` (24 by default)**: until then the job enforces the longer of the old and new period, the page says when the shorter one starts, and saving the previous value cancels it; a lengthening is immediate. Audit entries of the policy itself (`retention.*`) are kept 365 days whatever the audit period is.

| Tier | Default | What it removes |
| --- | --- | --- |
| artifacts | 30 | artifact files and rows (terminal output, large payload bodies); the project's stored-bytes counter goes down by exactly their size |
| trace detail | 90 | events, spans, cost lines, findings, evaluations, run-bound jobs and the search entry; the run reads `trace_state: expired` |
| run record | 365 | the run summary and its row; the run is then a 404 |
| audit log | 365 | audit entries, in batches, each batch with a counts-only summary entry |

Fixed windows that are not configurable: finished jobs 7 days (dead letters 30), expired counters 1 hour, daily usage rows 35 days,
settled analytics markers 35 days, dead sessions 7 days, people who signed in on an invitation and never joined 30 days. A prompt or
response inside an event lives as long as the trace-detail tier (90 days by default), not 30 (documented deviation from spec section 94;
in-place scrubbing of content inside events needs UPDATE on events and is KI-192).

## The three modes

`ABB_RETENTION_MODE`, read by the retention process (the API only shows it):

* `dry_run` (default): the real job runs, measures per workspace and tier and writes a report. It deletes nothing (the e2e compares every
  table's row count, the content of every run's rows and the sha256 of every artifact file before and after).
* `enforce`: deletes. Refused (exit 2) unless `ABB_ARTIFACT_DIR` is an existing directory: the process must see the API's files.
* `off`: does nothing, not even a report.

## Turning enforcement on

1. Leave `dry_run`. After a full tick read the reports (Settings, Retention, "Last passes", or the SQL in `docs/OPERATIONS.md`). The
   counts are what `enforce` would remove now, per workspace and tier. `truncated` means a bound ended the pass; the figures are a lower
   bound. Use the preview (`GET .../retention/preview?trace_days=30`) to see a proposed policy before saving it.
2. **Take a backup bundle and perform a restore drill from it** (`docs/runbooks/backup-restore.md`). `scripts/backup-restore-e2e.sh` is
   that drill automated; run it on your own data's shape or follow the manual drill. Keep the output.
3. Set `ABB_RETENTION_MODE=enforce` and restart the retention process. Each tick removes at most `ABB_RETENTION_MAX_RUNS_PER_TICK` runs
   and `ABB_RETENTION_MAX_ROWS_PER_TICK` rows per workspace and tier and stops at the time budget; a backlog drains over ticks.
4. Watch `retention tick finished` in the log and `failed=` in its summary, then the enforced reports (`mode = 'enforce'`: what was
   actually deleted).

Run retention **before** the nightly backup, not during it. A deletion between the dump and the artifact copy makes the backup fail with
exit 7 (it never writes a bundle that lies); rerun it.

## Reading what happened

* Reports: `SELECT workspace_id, tier, mode, runs, events, artifact_bytes, row_count, truncated, cutoff_at, finished_at FROM retention_reports ORDER BY id DESC LIMIT 50`. Counts only, never content.
* Tombstones, one per run and tier, kept for ever: `SELECT run_id, tier, mode, deleted_at FROM retention_tombstones ORDER BY deleted_at DESC LIMIT 50`.
  For one run the order is `artifacts <= trace <= run` (the e2e asserts it on every run it deletes).
* The audit log: `retention.policy.update` (who changed which tier, old and new value), `retention.delete.<tier>` (counts, system actor).
* What a reader sees: the runs API reports `trace_state` (`available`, `deleting`, `expired`). Comparing or evaluating a `deleting`
  or `expired` run answers `409 TRACE_EXPIRED`; after the run-record tier the run is `404`. The Retention page and the run pages say so in
  words (ADR-161 for the analytics note).

## Kill switch

Deletion stops at the next **batch**, not the next tick:

* set `ABB_RETENTION_MODE=off` (or `dry_run`) in the `.env` file in the process's working directory (re-read before every batch; an
  unreadable value counts as `off`), or
* send SIGTERM/SIGINT to the process (the looping `run` exits 0 after the batch in hand).

A mode set only in the process environment cannot change in a running process: stop it and start it with the new mode. A run stopped
half way stays `deleting` with its progress row; the next enforcing tick finishes it. Nothing is torn. The e2e exercises both switches by
holding a row lock the job must wait on, so the signal or the file change arrives at a known point (observed in `pg_stat_activity`).

## Crash recovery

Kill the process at any point (power loss, OOM, `kill -9`): committed work stays, the run being deleted stays `deleting`, and the next
enforcing tick resumes from the progress row (`retention_run_progress`: a flag per backend). Nothing needs to be repaired by hand. The
e2e kills the real process while it waits on a lock mid-run and checks that the rerun's end state equals an uninterrupted run (same
state, row counts and tombstones, no duplicate tombstone, no run left `deleting`, no progress row left).

A failed step is retried by each tick up to `ABB_RETENTION_MAX_ATTEMPTS` (5), then waits for `python -m abb_api.retention retry-failed`
after you fix the cause. Codes: `artifact_store` (permissions or the wrong directory), `database`, `timeout` (lower
`ABB_RETENTION_BATCH_SIZE`), `internal`. A failure never blocks other runs.

## Purge a project or a workspace

Owner-run, never from the API: `python -m abb_api.retention purge-project --workspace W --project P` and `purge-workspace --workspace W`.
Dry run by default; `--confirm <exact slug>` deletes. Permanent, from every backend; the project's API keys answer the uniform 401 from the
moment it starts. Procedure, output and exit codes: `docs/OPERATIONS.md` ("Purge"). Backups still hold the data until they expire (KI-215).

## Privileges, in one paragraph

The job connects as a login in the `abb_retention` role: DELETE on the retained tables, column-level UPDATE on `runs.trace_state` and
the stored-bytes counter, no SELECT on `api_keys.secret_hash`, `server_secrets`, or the alert destination ciphertext, no UPDATE on
events. The API and worker (role `abb_runtime`) cannot UPDATE or DELETE events, or write retention reports, tombstones, progress or
purge records. The e2e asserts each over real connections, and that the API and worker processes carry no retention credential in their
environment. At start the job refuses (production) or warns (elsewhere) if its role can do more than that.

## Cost of enforcement while the system serves traffic

Measured on a developer machine in `docs/benchmarks/phase-19b-retention-e2e.md` (informational, with the load stated). The stated bound
is a statement, not an assertion: with one-row batches (the worst case for contention) ingestion latency during enforcement stayed
within the figure recorded there; the CI job asserts only that ingestion kept being accepted with no refusal or 5xx. Production tuning:
batches of `ABB_RETENTION_BATCH_SIZE` rows (default 2000) and `ABB_RETENTION_STATEMENT_TIMEOUT_SECONDS`; run the job in a quiet window if
latency matters more than catching up.

## Compose pins `ABB_ENVIRONMENT=development` for this service

`docker-compose.yml` sets `ABB_ENVIRONMENT: development` on the `retention` and `backup` services, as on every service of the local stack. In development the retention process may fall back to the owner URL when `ABB_RETENTION_DATABASE_URL` is missing, and warns instead of refusing if its role is privileged. For a deployment other people reach, run these services with `ABB_ENVIRONMENT=production` (override the variable in your own compose file or orchestrator), give the retention job its own `ABB_RETENTION_DATABASE_URL`, and keep the passwords out of the compose file; production then refuses to start without that URL and with a privileged role.

## Metrics and alerts

The full description of the modes, kill switch and reports is above; this section is the monitoring and triage view (ADR-159, ADR-160). Before enforcing, take a backup and run the restore drill ([backup and restore](backup-restore.md)), and watch `retention_dry_run_candidates` for a full tick before switching the mode.

Dashboard row: **Retention and backups**. Alerts: `AbbRetentionFailing`, `AbbRetentionStale`.

The retention process is separate from the API and worker. Its counters and gauges reach Prometheus through a textfile (`ABB_METRICS_TEXTFILE`, node_exporter), written at the end of each tick,
so they are as fresh as the last tick (hourly by default). The API and worker add `retention_mode` and `retention_seconds_since_last_pass` from the newest report in the database.

| Signal | Meaning |
| --- | --- |
| `retention_configured_mode{mode}` | The mode the API or worker was configured with (`ABB_RETENTION_MODE`); `AbbRetentionStale` stays silent when it is `off` |
| `retention_mode{mode}` | The effective mode of the newest report: `off`, `dry_run`, `enforce`. `dry_run` deletes nothing, so data is kept beyond policy by design |
| `retention_seconds_since_last_pass` | Seconds since the newest report; NaN when none exists yet |
| `retention_passes_total{mode}` | Ticks finished by mode |
| `retention_deleted_total{tier,kind}` | What enforcement removed (`runs`, `events`, `rows`, `bytes`) |
| `retention_dry_run_candidates{tier}` | What a dry run says it would delete |
| `retention_failures_total{code}` | `measure_error`, `step_failed`, `refused`, `budget_exhausted`, `tick_crashed` |

### Retention failures

`AbbRetentionFailing` names the `code`. Data is not lost by a failure: the step is retried on the next tick; it is kept longer than the policy promises.

- `step_failed`: one run or tier failed; the per-run state is in `retention_run_progress` (`SELECT tier, failure_code, attempts, count(*) FROM retention_run_progress GROUP BY 1,2,3 ORDER BY 4 DESC`)
  and the log line `retention step failed` (fields `run_id`, `tier`, `failure_code`). `artifact_store` means the files could not be removed: check the artifact directory and its permissions.
- `measure_error`: counts the errors a tick hit while measuring or processing; one count per error, the database is the suspect ([database saturation](database-saturation.md)).
- `refused`: enforcement needs an artifact store and the tick was given none, so it did nothing; check that the retention service has `ABB_ARTIFACT_DIR` and mounts the artifact volume (compose does).
- `budget_exhausted`: the tick hit its time budget (`ABB_RETENTION_TICK_BUDGET_SECONDS`); the backlog drains over ticks. Frequent exhaustion on a large backlog means the budget or the limits per tick are too small.
- `tick_crashed`: the tick raised; read the process log and `docker compose logs retention`.

### Retention is stale

`AbbRetentionStale`: no report for over 3 hours (three default ticks) while the API/worker is configured with a mode other than `off` (`retention_configured_mode`). The retention process is not running or cannot write reports. If you switched retention off on purpose, set `ABB_RETENTION_MODE=off` for the API and worker as well and restart them (the alert reads *their* configuration; an off retention process writes no report, so `retention_mode` keeps showing the last mode it ran in).
`docker compose --profile app ps retention` and its log; the database it connects to is configured separately (`ABB_RETENTION_DATABASE_URL`, role `abb_retention`). If retention is deliberately stopped,
set `ABB_RETENTION_MODE=off` so the alert stays quiet. Raise the 3 hours if you set a longer tick interval.
