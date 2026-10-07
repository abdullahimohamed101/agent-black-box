# Operations

Operating Agent Black Box itself (spec §43, §104-125). Runbooks live in `docs/runbooks/`.

## Processes

| Process | Command | Notes |
| --- | --- | --- |
| API | `uvicorn abb_api.main:app_from_env --factory` | stateless; `/healthz` (liveness, no dependencies), `/readyz` (database, 2.5 s deadline) |
| Worker | `python -m abb_api.worker` | claims background jobs; any number may run; SIGTERM finishes the current batch and exits 0 |
| Migrations | `alembic upgrade head` (`make migrate`) | run once per release before the new API/worker start; compose does this in the `migrate` service |
| Provisioning | `python -m abb_api.cli ...` | workspaces, projects, API keys, dead-letter inspection |

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
| `ABB_WORKER_POLL_INTERVAL_SECONDS`, `_BATCH_SIZE`, `_LEASE_SECONDS`, `_MAX_ATTEMPTS`, `_BACKOFF_BASE_SECONDS`, `_BACKOFF_MAX_SECONDS` | 0.5, 10, 60, 5, 5, 300 | job processing |

## What to watch

Structured JSON logs (one line per request with `request_id`, and `workspace_id`, `project_id`, `key_id` after
authentication; payloads and secrets are never logged). Messages worth alerting on: `lease lost while running`, `spans left untouched`, `job failed` with
`outcome: dead_letter`, `worker loop error`, `conflicting duplicate events ignored`, `authentication unavailable`,
`ingest unavailable`. Application metrics (spec §116) arrive in Phase 19; until then use the logs and these queries:

```sql
-- queue depth and oldest waiting job
SELECT status, count(*), min(available_at) FROM outbox_jobs WHERE status IN ('pending','running') GROUP BY 1;
-- jobs that need a human
SELECT id, job_type, attempt_count, left(last_error, 120) FROM outbox_jobs WHERE status = 'dead_letter';
```

## Runbooks

- [Worker lag](runbooks/worker-lag.md)
- [Dead-lettered jobs](runbooks/dead-letters.md)
- [Ingestion rejections](runbooks/ingestion-rejections.md)

Still to write (Phases 18-19): database saturation, live-stream lag, backup and restore drill (spec §123).
Recovery targets: RPO <= 15 min, RTO <= 4 h (spec §122) once managed PostgreSQL with point-in-time recovery is in place.
