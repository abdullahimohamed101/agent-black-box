# Operations

Operating Agent Black Box itself (spec §43, §104-125). Runbooks live in `docs/runbooks/`.

## Processes

| Process | Command | Notes |
| --- | --- | --- |
| API | `uvicorn abb_api.main:app_from_env --factory` | stateless; `/healthz` (liveness, no dependencies), `/readyz` (database, 2.5 s deadline) |
| Worker | `python -m abb_api.worker` | claims background jobs; any number may run; SIGTERM finishes the current batch and exits 0 |
| Migrations | `alembic upgrade head` (`make migrate`) | run once per release before the new API/worker start; compose does this in the `migrate` service |
| Provisioning | `python -m abb_api.cli ...` | workspaces, projects, API keys, dead-letter inspection, `rebuild-costs`, `rebuild-findings` |

Local stack: `make up` (migrate, api, worker, web, postgres), `make smoke` to prove it works end to end,
`make down`. Container images run as a non-root user and share one API image for api, worker and migrate.

## Configuration (environment, validated at startup)

| Variable | Default | Meaning |
| --- | --- | --- |
| `ABB_DATABASE_URL` | required | `postgresql+asyncpg://...`; startup fails naming the field if missing |
| `ABB_ENVIRONMENT`, `ABB_LOG_LEVEL`, `ABB_CORS_ORIGINS` | development, INFO, localhost:3000 | |
| `ABB_INGEST_MAX_BODY_BYTES` / `ABB_INGEST_MAX_BATCH_EVENTS` | 5 MiB / 1000 | request limits (compressed and decompressed) |
| `ABB_RATE_LIMIT_EVENTS_PER_SECOND` / `_BURST_EVENTS` | 2000 / 10000 | per project, per API process |
| `ABB_RATE_LIMIT_BYTES_PER_SECOND` / `_BURST_BYTES` | 10 MiB / 50 MiB | bursts must fit one maximal batch (checked at startup) |
| `ABB_SUMMARY_DEBOUNCE_SECONDS` | 1 | delay before a run is re-summarized after new events |
| `ABB_STREAM_MAX_TOTAL` / `_MAX_PER_KEY` | 50 / 10 | concurrent live streams per API process / per key (429 `STREAM_LIMIT` beyond) |
| `ABB_STREAM_MAX_LIFETIME_SECONDS`, `_KEEPALIVE_SECONDS`, `_FALLBACK_POLL_SECONDS`, `_WRITE_TIMEOUT_SECONDS`, `_END_QUIET_SECONDS`, `_OVERLAP_SECONDS` | 900, 15, 2, 10, 5, 30 | stream timing; see `docs/architecture/api-v1.md` and ADR-022 |
| `ABB_STREAM_MIN_POLL_SECONDS`, `_DB_CONCURRENCY`, `_WINDOW_CHECK_SECONDS`, `_PAGE_SIZE` | 0.1, 4, 2, 200 | stream cost control: poll floor, concurrent stream queries per process (the pool is shared with ingestion), late-row check cadence, replay page size |
| `ABB_FINDINGS_DELAY_SECONDS`, `_MAX_EVENTS` | 5, 50000 | delay before a run's findings are (re)computed (grows with run size, at most 60 s); events read per run |
| `ABB_FINDINGS_LOOP_MIN_REPETITIONS`, `_LOOP_MIN_REPETITIONS_SINGLE`, `_IDENTICAL_MIN_COUNT`, `_RETRY_STORM_MIN`, `_RETRY_BURST_WINDOW_SECONDS`, `_CONTEXT_GROWTH_MIN_DELTA_TOKENS`, `_CONTEXT_GROWTH_FACTOR`, `_CONTEXT_GROWTH_WARNING_TOKENS`, `_CASCADE_MIN_FAILURES`, `_CASCADE_WINDOW_CALLS`, `_COST_SPIKE_MIN_BASELINE`, `_COST_SPIKE_WINDOW_DAYS` | 3, 8, 3, 5, 60, 4000, 2.0, 100000, 3, 10, 20, 30 | detector thresholds, recorded in each finding's evidence; see `runbooks/findings.md` |
| `ABB_EVAL_TIMEOUT_SECONDS`, `_MAX_EVENTS`, `_STALE_SECONDS`, `_BATCH_DELAY_SECONDS` | 25, 50000, 3600, 5 | evaluator time budget (must be below half `ABB_WORKER_LEASE_SECONDS`), events read per evaluation, age after which a pending row reads as failed, delay of evaluate-all jobs; see `runbooks/evaluations.md` |
| `ABB_COMPARE_MAX_EVENTS`, `_MAX_STEPS`, `_MAX_EDITS` | 20000, 2000, 600 | run comparison: events read per run, steps per side, edit budget before the answer is `partial` |
| `ABB_JUDGE_PROVIDER`, `ABB_JUDGE_ALLOW_FAKE`, `ABB_JUDGE_COOLDOWN_SECONDS` | none, 0, 300 | `none` or the deterministic `fake` (development/test only unless allowed); pause after a judge reply exceeded `max_cost_usd` |
| `ABB_WORKER_POLL_INTERVAL_SECONDS`, `_BATCH_SIZE`, `_LEASE_SECONDS`, `_MAX_ATTEMPTS`, `_BACKOFF_BASE_SECONDS`, `_BACKOFF_MAX_SECONDS` | 0.5, 10, 60, 5, 5, 300 | job processing |

## What to watch

Structured JSON logs (one line per request with `request_id`, and `workspace_id`, `project_id`, `key_id` after
authentication; payloads and secrets are never logged). Messages worth alerting on: `lease lost while running`, `spans left untouched`, `job failed` with
`outcome: dead_letter`, `worker loop error`, `conflicting duplicate events ignored`, `authentication unavailable`,
`ingest unavailable`, `stream listener unavailable`, `stream failed`. Application metrics (spec §116) arrive in Phase 19; until then use the logs and these queries:

```sql
-- queue depth and oldest waiting job
SELECT status, count(*), min(available_at) FROM outbox_jobs WHERE status IN ('pending','running') GROUP BY 1;
-- live-stream listeners: one per API process; none means streams are on the 2 s fallback poll
SELECT pid, state, backend_start FROM pg_stat_activity WHERE application_name = 'abb-stream-listener';
-- jobs that need a human
SELECT id, job_type, attempt_count, left(last_error, 120) FROM outbox_jobs WHERE status = 'dead_letter';
```

## Runbooks

- [Reliability findings](runbooks/findings.md): detectors and thresholds, `rebuild-findings`, dead-lettered detector jobs, the cost-rebuild load
- [Evaluations, experiments and comparison](runbooks/evaluations.md): judge settings, budgets, stale and failed rows, the cost of evaluate-all
- [Worker lag](runbooks/worker-lag.md)
- [Dead-lettered jobs](runbooks/dead-letters.md)
- [Ingestion rejections](runbooks/ingestion-rejections.md)
- [Live streams](runbooks/stream-issues.md)

Still to write (Phases 18-19): database saturation, backup and restore drill (spec §123).
Recovery targets: RPO <= 15 min, RTO <= 4 h (spec §122) once managed PostgreSQL with point-in-time recovery is in place.
