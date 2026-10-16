# Runbook: worker lag (runs stay `processing`)

**Symptom**: `summary_state: "processing"` on runs for much longer than the debounce (about 1 s) plus a few seconds;
events are visible but summaries and spans are not.

1. Is a worker running? `docker compose --profile app ps worker` / process list. Logs should show `worker started`.
   If it is crash-looping, read its logs first (a missing `ABB_DATABASE_URL` or an unreachable database is typical).
2. Queue state (see OPERATIONS.md query). Many `pending` rows with old `available_at`: workers are down or too slow.
   `running` rows with an expired `lease_expires_at`: a worker died; another reclaims them automatically after the
   lease (60 s) and the attempt is counted; after the last allowed attempt it is dead-lettered instead (see
   dead-letters.md). A running handler extends its lease automatically, so slow is not the same as dead.
3. One huge run dominating? A single run with tens of thousands of events is recomputed in full per job (KI-016,
   `docs/benchmarks/phase-2-ingestion.md`). Look at the largest `summary.event_count` among `processing` runs. Mitigation:
   raise `ABB_SUMMARY_DEBOUNCE_SECONDS`, add workers (they serialize per run but parallelize across runs), or ask the
   agent owner to split the run.
4. Database trouble (`worker loop error`, `/readyz` failing): fix the database first; the worker resumes by itself.
5. After recovery nothing needs replaying: jobs are durable and idempotent. To force a rebuild of one run, enqueue
   a `summarize_run` job for it (or send any new event for the run).

**Do not** delete rows from `outbox_jobs` to "clear" a backlog: events are fine, but the runs would keep stale summaries
until the next event for them.

## Readiness hint

`GET /readyz` reports the same signal as a `worker` line (`degraded`/`worker_lag` when the oldest due job has waited over 60 seconds). It never fails readiness: a late worker does not make the API unable to serve. See `docs/OPERATIONS.md`, "Readiness, the database breaker and timeouts".

## Metrics and alerts

Dashboard row: **Worker and jobs**. Alerts: `AbbWorkerLagHigh` (oldest due job over 120 s for 10 minutes, ticket), `AbbWorkerLagSevere` (over 900 s, page), `AbbProcessorFailures`, `AbbScrapeDatabaseDown`.

- `worker_job_lag_seconds{type}`: age of the oldest due job that has not started. This is the signal in step 2 above without SQL. Both the API and the worker export it (it is read from the database), so take the maximum, not the sum.
- `worker_jobs_pending{type}`: due jobs waiting (saturates at 10 000).
- `worker_jobs_total{type,outcome}` and `worker_job_duration_seconds{type}`: throughput and handler time; a `retried` rise with a flat `succeeded` is a failing handler (`processor_failures_total{type}`).
- If `abb_scrape_database_up` is 0 the gauges above are cached: use the SQL in OPERATIONS ("What to watch").
- No `worker_*` series at all from the worker job: it exports metrics only when `ABB_WORKER_METRICS_PORT` is set; the API's copy of the lag gauges still works.
