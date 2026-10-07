# Runbook: worker lag (runs stay `processing`)

**Symptom**: `summary_state: "processing"` on runs for much longer than the debounce (about 1 s) plus a few seconds;
events are visible but summaries and spans are not.

1. Is a worker running? `docker compose --profile app ps worker` / process list. Logs should show `worker started`.
   If it is crash-looping, read its logs first (a missing `ABB_DATABASE_URL` or an unreachable database is typical).
2. Queue state (see OPERATIONS.md query). Many `pending` rows with old `available_at`: workers are down or too slow.
   `running` rows with an expired `lease_expires_at`: a worker died; another reclaims them automatically after the
   lease (60 s) and the attempt is counted.
3. One huge run dominating? A single run with tens of thousands of events is recomputed in full per job (KI-016,
   `docs/benchmarks/phase-2-ingestion.md`). Look at the largest `summary.event_count` among `processing` runs. Mitigation:
   raise `ABB_SUMMARY_DEBOUNCE_SECONDS`, add workers (they serialize per run but parallelize across runs), or ask the
   agent owner to split the run.
4. Database trouble (`worker loop error`, `/readyz` failing): fix the database first; the worker resumes by itself.
5. After recovery nothing needs replaying: jobs are durable and idempotent. To force a rebuild of one run, enqueue
   a `summarize_run` job for it (or send any new event for the run).

**Do not** delete rows from `outbox_jobs` to "clear" a backlog: events are fine, but the runs would keep stale summaries
until the next event for them.
