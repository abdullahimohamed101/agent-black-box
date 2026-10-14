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
| `ABB_RATE_LIMIT_EVENTS_PER_SECOND` / `_BURST_EVENTS` | 2000 / 10000 | per project: each API process's local bucket, and (shared limiter, below) at most `max(rate x window, burst)` events per window across all processes |
| `ABB_RATE_LIMIT_BYTES_PER_SECOND` / `_BURST_BYTES` | 10 MiB / 50 MiB | bursts must fit one maximal batch (checked at startup) |
| `ABB_LIMITS_SHARED_ENABLED` | true | the shared limiter (Phase 19a, ADR-152): the budgets are also counted per fixed window in PostgreSQL (`rate_limit_counters`) so they hold across API processes. `false` is the old per-process behaviour |
| `ABB_LIMITS_TIMEOUT_MS`, `_BACKOFF_SECONDS` | 500, 5 | one shared-counter call is abandoned after this long (also set as the statement timeout); after a failure the shared tier is skipped for the backoff and the local limiter alone decides (fail open). One warning per 30 s, class names only |
| `ABB_LIMITS_BLOCK_DIVISOR` | 20 | units are reserved from the database in blocks of limit / divisor: fewer writes, at most `N x (block - 1)` early refusals across N processes. `1000` writes on nearly every request (the worst case the benchmark measures) |
| `ABB_LIMITS_INGEST_WINDOW_SECONDS` | 10 | window of the per-second ingestion and artifact budgets (the per-minute budgets use 60 s) |
| `ABB_SUMMARY_DEBOUNCE_SECONDS` | 1 | delay before a run is re-summarized after new events |
| `ABB_STREAM_MAX_TOTAL` / `_MAX_PER_KEY` | 50 / 10 | concurrent live streams per API process / per key (429 `STREAM_LIMIT` beyond) |
| `ABB_STREAM_MAX_LIFETIME_SECONDS`, `_KEEPALIVE_SECONDS`, `_FALLBACK_POLL_SECONDS`, `_WRITE_TIMEOUT_SECONDS`, `_END_QUIET_SECONDS`, `_OVERLAP_SECONDS` | 900, 15, 2, 10, 5, 30 | stream timing; see `docs/architecture/api-v1.md` and ADR-022 |
| `ABB_STREAM_MIN_POLL_SECONDS`, `_DB_CONCURRENCY`, `_WINDOW_CHECK_SECONDS`, `_PAGE_SIZE` | 0.1, 4, 2, 200 | stream cost control: poll floor, concurrent stream queries per process (the pool is shared with ingestion), late-row check cadence, replay page size |
| `ABB_SECURITY_SCAN_EVENT_MAX_SECONDS`, `_ARTIFACT_MAX_BYTES`, `_ARTIFACT_MAX_SECONDS` | 1, 4 MiB, 10 | server-side secret scan budgets (ADR-130); beyond them the value or artifact body is dropped and the reason recorded, never stored unscanned |
| `ABB_SECURITY_REANALYSE_MAX_RUNS`, `_MAX_AGE_DAYS` | 200, 30 | a mute or allowlist change re-analyses at most this many of the project's newest runs started within this many days (0 runs: off) |
| `ABB_SECURITY_REANALYSE_COOLDOWN_SECONDS` | 60 | at most one re-analysis wave per project per this many seconds; a later change is queued, due when the cooldown ends (0: no limit) |
| `ABB_FINDINGS_DELAY_SECONDS`, `_MAX_EVENTS` | 5, 50000 | delay before a run's findings are (re)computed (grows with run size, at most 60 s); events read per run |
| `ABB_FINDINGS_LOOP_MIN_REPETITIONS`, `_LOOP_MIN_REPETITIONS_SINGLE`, `_IDENTICAL_MIN_COUNT`, `_RETRY_STORM_MIN`, `_RETRY_BURST_WINDOW_SECONDS`, `_CONTEXT_GROWTH_MIN_DELTA_TOKENS`, `_CONTEXT_GROWTH_FACTOR`, `_CONTEXT_GROWTH_WARNING_TOKENS`, `_CASCADE_MIN_FAILURES`, `_CASCADE_WINDOW_CALLS`, `_COST_SPIKE_MIN_BASELINE`, `_COST_SPIKE_WINDOW_DAYS` | 3, 8, 3, 5, 60, 4000, 2.0, 100000, 3, 10, 20, 30 | detector thresholds, recorded in each finding's evidence; see `runbooks/findings.md` |
| `ABB_EVAL_TIMEOUT_SECONDS`, `_MAX_EVENTS`, `_STALE_SECONDS`, `_BATCH_DELAY_SECONDS` | 25, 50000, 3600, 5 | evaluator time budget (must be below half `ABB_WORKER_LEASE_SECONDS`), events read per evaluation, age after which a pending row reads as failed, delay of evaluate-all jobs; see `runbooks/evaluations.md` |
| `ABB_COMPARE_MAX_EVENTS`, `_MAX_STEPS`, `_MAX_EDITS` | 20000, 2000, 600 | run comparison: events read per run, steps per side, edit budget before the answer is `partial` |
| `ABB_JUDGE_PROVIDER`, `ABB_JUDGE_ALLOW_FAKE`, `ABB_JUDGE_COOLDOWN_SECONDS` | none, 0, 300 | `none` or the deterministic `fake` (development/test only unless allowed); pause after a judge reply exceeded `max_cost_usd` |
| `ABB_SMTP_HOST`, `_PORT`, `_STARTTLS`, `_USERNAME`, `_PASSWORD`, `_FROM` | unset, 587, true, unset, unset, unset | the SMTP relay for **email alert destinations** (Phase 17). Without `ABB_SMTP_HOST` an email destination cannot be created (`422 EMAIL_NOT_CONFIGURED`); the host needs `ABB_SMTP_FROM`; the username and password go together and are read from the environment only (never stored, logged or returned) |
| `ABB_ALERTS_MAX_RULES_PER_WORKSPACE`, `_MAX_DESTINATIONS_PER_WORKSPACE`, `_MAX_DESTINATIONS_PER_RULE` | 100, 50, 10 | alerting configuration limits (409 `ALERT_LIMIT_REACHED` beyond) |
| `ABB_ALERTS_WRITES_PER_MINUTE` | 30 | alert destination and rule changes per workspace per minute, across API processes (shared limiter; 429 with `Retry-After`) |
| `ABB_ALERTS_SWEEP_INTERVAL_SECONDS` | 60 | how often the scheduled alert checks (approval about to expire, daily spend over a threshold) run per workspace that has such a rule |
| `ABB_ALERTS_SKIPPED_PER_WINDOW`, `ABB_ALERTS_PREVIEW_TIMEOUT_SECONDS` | 25, 5 | at most this many `skipped` history rows per rule and destination per cooldown window (the rest are only counted in the log); time budget of a rule preview |
| `ABB_ALERTS_DELIVERY_ENABLED` | true | `false` holds delivery: notifications stay `pending` and are sent when it is switched back on and a delivery tick runs (the next alert of that workspace queues a tick that sends the whole due backlog; there is no separate flush command) |
| `ABB_ALERTS_REQUEST_TIMEOUT_SECONDS`, `ABB_ALERTS_SMTP_TIMEOUT_SECONDS` | 5, 10 | total deadline of one webhook/Slack request (resolve, connect, TLS, write, status line); total deadline of one email send (the SMTP session as a whole; each socket step also times out at this value) |
| `ABB_ALERTS_TEST_NETWORK_FILE` | unset | **test only** (`scripts/alerts-e2e.sh`): a JSON file that replaces the alert sender's DNS, connect address and TLS trust so local receivers can stand in for public hosts. Refused unless `ABB_ENVIRONMENT` is `development` or `test`; never set it where real alerts are sent (ADR-151 addendum) |
| `ABB_ALERTS_MAX_ATTEMPTS`, `_BACKOFF_BASE_SECONDS`, `_BACKOFF_MAX_SECONDS` | 5, 30, 1800 | delivery attempts per alert before it is `dead`; backoff is base x 2^(attempt-1), capped, +-25 % jitter |
| `ABB_ALERTS_DELIVER_BATCH` | 5 | alerts claimed per delivery tick per workspace (the retry-storm bound). A tick sends its rows one after another in one job: at most 5 x 5 s for webhook/Slack, 5 x 10 s if every row is an email that times out, and it holds a worker for that long and its rows locked (KI-163, KI-169). Raise it only with more workers |
| `ABB_ALERTS_LOCK_TIMEOUT_SECONDS` | 3 | how long an alert write (destination or rule change) waits for a row lock a delivery tick holds before it answers `409 ALERT_BUSY` (retry) |
| `ABB_ALERTS_FAILING_AFTER`, `ABB_ALERTS_PAUSE_AFTER_FAILURES` | 3, 10 | consecutive failed attempts that mark a destination `failing`, then pause it (`paused_reason: failures`, audited as `alert.destination.auto_pause`); a success resets the count, a person resumes a pause |
| `ABB_ALERTS_RATE_PER_MINUTE`, `ABB_ALERTS_DAILY_CAP` | 20, 500 | deliveries per destination per minute and per 24 h; the excess is recorded as `skipped` (`rate_limited`, `daily_cap`), not queued |
| `ABB_ALERTS_TESTS_PER_MINUTE` | 10 | "Send test" calls per workspace per minute, across API processes (shared limiter; on top of the write budget) |
| `ABB_ALERTS_RETENTION_DAYS`, `_PRUNE_INTERVAL_SECONDS`, `_PRUNE_BATCH` | 30, 21600, 2000 | finished delivery-log rows (`delivered`, `dead`, `skipped`, `failed`) older than this are deleted in batches; `pending` rows never are |
| `ABB_WORKER_POLL_INTERVAL_SECONDS`, `_BATCH_SIZE`, `_LEASE_SECONDS`, `_MAX_ATTEMPTS`, `_BACKOFF_BASE_SECONDS`, `_BACKOFF_MAX_SECONDS` | 0.5, 10, 60, 5, 5, 300 | job processing |

### Alert secrets and their key (Phase 17, ADR-150)

Alert destination targets and webhook signing secrets are stored as AES-256-GCM ciphertext under a key kept in the append-only `server_secrets` table (name `alerts-encryption`),
created on first use. **Back that table up with the database**: without its row the secrets sealed under it cannot be read (the destinations then fail closed). **Rotation** (planned or after a
suspected leak of a database read): as the database owner role run
`INSERT INTO server_secrets (name, secret) VALUES ('alerts-encryption', decode('<64 hex characters from openssl rand -hex 32>', 'hex'));` and restart the API and worker processes (they cache the
current key). New and re-saved destinations use the new key; existing ones keep opening with their own, so nothing needs re-encrypting; to retire the old key, replace each destination's target (`PATCH` with the `target`; the stored URL is never
returned, so the owner supplies it again) and rotate webhook signing secrets (`rotate_signing_secret`). Which key sealed a destination: `SELECT id, name, key_id FROM alert_destinations`. Alerts carry no captured content; a destination's masked form is the only display of its target.

### Reading the alert delivery log and dead letters (Phase 17, ADR-151)

The owner's view is `GET /v1/alerts/notifications` (and the Alerts page, History tab): status `pending` (waiting, or between retries when `attempts > 0` and `next_attempt_at` is in the future),
`delivered`, `skipped` (with `skip_reason`: `cooldown`, `destination_paused`, `destination_deleted`, `rule_deleted`, `rate_limited`, `daily_cap`) and `dead` (every attempt used, or a failure no retry can fix).
`error_code` is one of `timeout`, `connect_failed`, `dns_failed`, `tls_failed`, `blocked_address` (the name resolved to a non-public address: fix the target), `http_3xx` (the receiver redirects; redirects are never
followed: put the final URL in the destination), `http_4xx` (the receiver rejected it: wrong URL or revoked hook), `http_5xx`, `bad_response`, `smtp_failed`, `config_unreadable` (the sealed target cannot be opened:
the `alerts-encryption` key row is missing or the row was altered; re-save the target) and `internal_error`. Response bodies are never stored. SQL for an operator:
`SELECT status, last_error_code, count(*) FROM alert_notifications WHERE created_at > now() - interval '1 day' GROUP BY 1, 2;` and
`SELECT d.name, d.state, d.consecutive_failures, d.paused_reason FROM alert_destinations d WHERE d.state <> 'active';`. A destination paused for `failures` stays paused until a person resumes it
(`PATCH state: active`, which clears the count); alerts that arrive while it is paused are recorded as `skipped (destination_paused)`. A dead alert is not retried again: fix the target, then use "Send test".
Delivery is **at-least-once**: a receiver that sees `X-ABB-Alert-Id` twice must ignore the second. Delivery ticks and pruning are `deliver_alerts` and `alert_prune` outbox jobs (`SELECT ... FROM outbox_jobs
WHERE job_type IN ('deliver_alerts', 'alert_prune')`); `lease lost while running` for them means a tick outlived its lease (many slow receivers: lower `ABB_ALERTS_DELIVER_BATCH` or raise `ABB_WORKER_LEASE_SECONDS`; jobs claimed behind a slow tick have their leases extended while they wait, but a worker still serves its batch one job at a time, so run more than one worker where tick times are long). `ABB_ALERTS_RETENTION_DAYS` must be at least 7, the longest rule cooldown.

### The shared limiter's counters (Phase 19a, ADR-152)

Budgets that moved onto the shared limiter: ingestion events and bytes, artifact count and bytes, sign-in starts, policy publish/validate/dry-run, approval decisions (per key and per workspace) and the alert write, preview and send-test budgets. The `Scope` enum in `apps/api/src/abb_api/limits/scopes.py` is the closed list. Not moved, on purpose: live-stream and long-poll concurrency caps (per process, KI-017/KI-147), the denial-audit cap (KI-173).

Inspect (read-only; the counters are operational state, never edit them):

```sql
-- what is counting now, biggest first
SELECT scope, workspace_id, key, window_start, count, expires_at
FROM rate_limit_counters WHERE expires_at > now() ORDER BY count DESC LIMIT 20;
-- table size and backlog (expired rows are deleted in batches of 1000 by whichever API process
-- is serving; a persistent backlog means no API process is running)
SELECT count(*) FILTER (WHERE expires_at < now()) AS expired, count(*) AS total FROM rate_limit_counters;
```

Reset one budget (an operator unblocking a workspace): `DELETE FROM rate_limit_counters WHERE scope = 'policy_write' AND workspace_id = '<uuid>';` (a process that already holds reserved credit keeps it until its window ends, at most one window).

Logs: `shared limiter unavailable; failing open to the local limiter` (at most once per 30 s per process, field `error_class` only) means the budgets are per process again until the database answers; it is also the sign that the database is in trouble. The bench numbers and the Redis trigger are in `docs/benchmarks/phase-19a-limiter.md` and ADR-152.

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

- [Security observability](runbooks/security-observability.md): capture modes (the default is METADATA_ONLY; how to set FULL), scan budgets, settings changes and re-analysis, what is not scrubbed
- [Reliability findings](runbooks/findings.md): detectors and thresholds, `rebuild-findings`, dead-lettered detector jobs, the cost-rebuild load
- [Evaluations, experiments and comparison](runbooks/evaluations.md): judge settings, budgets, stale and failed rows, the cost of evaluate-all
- [Worker lag](runbooks/worker-lag.md)
- [Dead-lettered jobs](runbooks/dead-letters.md)
- [Ingestion rejections](runbooks/ingestion-rejections.md)
- [Live streams](runbooks/stream-issues.md)

Still to write (Phases 18-19): database saturation, backup and restore drill (spec §123).
Recovery targets: RPO <= 15 min, RTO <= 4 h (spec §122) once managed PostgreSQL with point-in-time recovery is in place.
