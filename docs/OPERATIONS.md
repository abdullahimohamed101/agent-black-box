# Operations

Operating Agent Black Box itself (spec §43, §104-125). Runbooks live in `docs/runbooks/`.

## Processes

| Process | Command | Notes |
| --- | --- | --- |
| API | `uvicorn abb_api.main:app_from_env --factory` | stateless; `/healthz` (liveness, no dependencies), `/readyz` (database, 2.5 s deadline) |
| Worker | `python -m abb_api.worker` | claims background jobs; any number may run; SIGTERM finishes the current batch and exits 0 |
| Retention | `python -m abb_api.retention run` (one tick: `python -m abb_api.cli retention run-once`) | the data-retention job (ADR-159); own credentials (`ABB_RETENTION_DATABASE_URL`), never in the API's or worker's environment; one at a time (advisory lock); SIGTERM stops after the current tick |
| Migrations | `alembic upgrade head` (`make migrate`) | run once per release before the new API/worker start; compose does this in the `migrate` service |
| Provisioning | `python -m abb_api.cli ...` | workspaces, projects, API keys, dead-letter inspection, `rebuild-costs`, `rebuild-findings` |

Upgrade note (ADR-156): with an https `ABB_WEB_ORIGIN` the sign-in cookie is renamed `__Host-abb_login` (`Path=/`). Deploy the API and the web
app together. Logins started in the previous 10 minutes fail once with "Sign-in did not complete. Try again."; sessions are untouched.

Local stack: `make up` (migrate, api, worker, web, postgres), `make smoke` to prove it works end to end,
`make down`. Container images run as a non-root user and share one API image for api, worker and migrate.

## Configuration (environment, validated at startup)

| Variable | Default | Meaning |
| --- | --- | --- |
| `ABB_DATABASE_URL` | required | `postgresql+asyncpg://...`; startup fails naming the field if missing |
| `ABB_ENVIRONMENT`, `ABB_LOG_LEVEL`, `ABB_CORS_ORIGINS` | development, INFO, localhost:3000 | |
| `ABB_INGEST_MAX_BODY_BYTES` / `ABB_INGEST_MAX_BATCH_EVENTS` | 5 MiB / 1000 | request limits (compressed and decompressed) |
| `ABB_SESSION_WRITES_PER_MINUTE` | 30 | how many times one person may end sessions (Settings, Sessions) per minute, counted across API processes (Phase 19a step 5, ADR-157). Listing is not limited |
| `ABB_QUOTAS_ENABLED` | true | per-project quotas (Phase 19a, ADR-154). `false` is an emergency switch: nothing is checked or counted (the usage figures then go stale) |
| `ABB_QUOTA_AGENTS` / `_RUNS_PER_DAY` / `_EVENTS_PER_DAY` / `_ARTIFACT_BYTES` | 500 / 50000 / 5000000 / 5368709120 (5 GiB) | the default quota of every project: distinct agents, runs created per UTC day, events accepted per UTC day, total stored artifact bytes. Owners and admins override each per project (Quotas settings page, `PUT /v1/projects/{id}/quotas`). The per-run event cap and the per-artifact size cap (`ABB_ARTIFACT_MAX_BYTES`) are separate and unchanged |
| `ABB_RETENTION_MODE` | `dry_run` | `off` (the job does nothing), `dry_run` (measures and writes reports, deletes nothing) or `enforce` (deletes, in the order and within the bounds of ADR-160; refused, exit 2, unless `ABB_ARTIFACT_DIR` is an existing directory). Read by the retention process (and shown by the API and by the Retention settings page) |
| `ABB_RETENTION_ARTIFACTS_DAYS` / `_TRACE_DAYS` / `_RUN_DAYS` / `_AUDIT_DAYS` | 30 / 90 / 365 / 365 | workspace defaults, in days after a run ended (the audit log: after the entry). 7 to 36500 or `forever`; `artifacts <= trace <= run` (the run record goes last). A workspace's owner or admin overrides each (`PUT /v1/workspaces/{id}/retention`); a bad value stops the process at startup |
| `ABB_RETENTION_DATABASE_URL` | none | the `abb_retention` role's URL; read only by the retention process. No fallback to the runtime URL. Development and test fall back to `ABB_MIGRATION_DATABASE_URL`, then `ABB_DATABASE_URL` (the owner) with a warning; staging and production refuse to run without it |
| `ABB_RETENTION_DB_PASSWORD` | none | read by `alembic upgrade` (not by the app) to give `abb_retention` a login password, like `ABB_RUNTIME_DB_PASSWORD`; without it the role cannot log in |
| `ABB_RETENTION_TICK_INTERVAL_SECONDS` / `_TICK_BUDGET_SECONDS` | 3600 / 60 | the loop interval; and the time after which a tick starts no new workspace (the one in progress finishes, bounded by its statement timeouts) |
| `ABB_RETENTION_MAX_RUNS_PER_TICK` / `_MAX_ROWS_PER_TICK` | 2000 / 100000 | per workspace and tier: runs considered, and other rows counted. When reached the report says `truncated` and the figures are a lower bound |
| `ABB_RETENTION_STATEMENT_TIMEOUT_SECONDS` / `_PREVIEW_TIMEOUT_SECONDS` / `_PREVIEW_MAX_RUNS` | 30 / 10 / 5000 | database timeouts for a tick query and the preview (`504 RETENTION_PREVIEW_TIMEOUT`), and the preview's run cap |
| `ABB_RETENTION_REPORTS_KEEP` | 50 | reports kept per workspace and tier |
| `ABB_RETENTION_WRITES_PER_MINUTE` / `_PREVIEWS_PER_MINUTE` | 20 / 10 | per workspace, shared across API processes |
| `ABB_MAX_BODY_BYTES_IN_FLIGHT` / `ABB_BODY_READ_TIMEOUT_SECONDS` | 64 MiB / 60 | most request-body bytes this API process holds at once (Phase 19a, ADR-155); a body that does not fit is refused `503 BUSY` with `Retry-After: 2` (retryable), bodies up to 64 KiB are always admitted, a body not received within the timeout is dropped `408`. Must be at least the largest body (8 MiB). Resident memory is about baseline + 4 to 6.5 x the cap under a flood of large batches (`docs/benchmarks/phase-19a-edge.md`) |
| `ABB_API_DOCS_ENABLED` | unset | `/docs`, `/redoc`, `/openapi.json`: unset follows the environment (off in `production`, on in development, test and staging); `true`/`false` overrides |
| `ABB_ALLOW_PRIVILEGED_DB_ROLE` | 0 | `1` lets the API and worker start in production although their database role can rewrite events (see "Database roles"); every start logs a warning naming why |
| `ABB_RATE_LIMIT_EVENTS_PER_SECOND` / `_BURST_EVENTS` | 2000 / 10000 | per project: each API process's local bucket, and (shared limiter, below) at most `max(rate x window, burst)` events per window across all processes |
| `ABB_RATE_LIMIT_BYTES_PER_SECOND` / `_BURST_BYTES` | 10 MiB / 50 MiB | bursts must fit one maximal batch (checked at startup) |
| `ABB_LIMITS_SHARED_ENABLED` | true | the shared limiter (Phase 19a, ADR-152): the budgets are also counted per fixed window in PostgreSQL (`rate_limit_counters`) so they hold across API processes. `false` is the old per-process behaviour |
| `ABB_LIMITS_TIMEOUT_MS`, `_BACKOFF_SECONDS` | 500, 5 | one shared-counter call is abandoned after this long (also set as the statement timeout); after a failure the shared tier is skipped for the backoff and the local limiter alone decides (fail open). One warning per 30 s, class names only |
| `ABB_LIMITS_BLOCK_DIVISOR` | 20 | units are reserved from the database in blocks of limit / divisor: fewer writes, at most `N x (block - 1)` early refusals across N processes. `1000` writes on nearly every request (the worst case the benchmark measures) |
| `ABB_LIMITS_INGEST_WINDOW_SECONDS` | 10 | window of the per-second ingestion and artifact budgets (the per-minute budgets use 60 s) |
| `ABB_AUTH_FAIL_ENABLED` | true | failed-API-key throttling (Phase 19a, ADR-153): only failures are counted, a valid request never spends a budget. A key that fails more than a budget allows in one window gets `429 AUTH_THROTTLED` with `Retry-After` until the window ends |
| `ABB_AUTH_FAIL_WINDOW_SECONDS` | 60 | the fixed window of the three failure budgets below (shared across API processes through the limiter, ADR-152; no escalation) |
| `ABB_AUTH_FAIL_PER_ADDRESS` / `_PER_CREDENTIAL` / `_GLOBAL` | 30 / 10 / 600 | failures per window per client address (needs `ABB_TRUST_PROXY`), per presented key id (existing or not), and for the whole installation. The global figure bounds the database work of a distributed flood: at most about that many key lookups per window, however many requests arrive |
| `ABB_AUTH_FAIL_NEGATIVE_SECONDS` / `_KNOWN_VALID_SECONDS` / `_CACHE_ENTRIES` | 30 / 300 / 10000 | per-process caches keyed by a digest of the whole token: an invalid token is answered from memory (no lookup) for 30 s; a token that authenticated is exempt from the throttle *gate* (never from authentication) for 300 s, which is what keeps established ingestion running during a flood. Entries are bounded. A valid key unknown to a process (new, restarted) is no longer locked out by a closed address or global window: see the next row |
| `ABB_AUTH_FAIL_ACTIVE_KEYS_REFRESH_SECONDS` / `_CAP` | 60 / 100000 | the per-process set of active key ids (ids only) that lets a real key id through a closed address or global window to full authentication, bounded by its own per-key-id budget; reloaded in the background (5 s timeout, previous set kept on failure). Above the cap an arbitrary subset is kept and one warning is logged (ADR-153) |
| `ABB_TRUST_PROXY` | 0 | `1` only when a reverse proxy you run sets `X-Forwarded-For` (its last entry is the client) and is the only way to reach the API (and the web port): the API then throttles failures per client address and the web server limits sign-in starts per address. Off, the header is ignored (it cannot evade or frame anyone); the key-id and global budgets still apply. Put your proxy's own IP rate limit in front as defence in depth: this throttle is an abuse rail, not a WAF |
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
| `ABB_ALERTS_ALLOWED_PORTS` | 443 | comma list of ports (1-65535) a webhook or Slack destination may use; an `https://` URL without a port means 443. Anything else is refused at save (`port_not_allowed`) and, for a destination saved earlier, at delivery and send-test (`blocked_port`, permanent). Extend it only for a receiver you operate (`443,8443`); a malformed list refuses to start. Email is unaffected (ADR-158) |
| `ABB_ALERTS_TEST_NETWORK_FILE` | unset | **test only** (`scripts/alerts-e2e.sh`): a JSON file that replaces the alert sender's DNS, connect address and TLS trust so local receivers can stand in for public hosts. Refused unless `ABB_ENVIRONMENT` is `development` or `test`; never set it where real alerts are sent (ADR-151 addendum) |
| `ABB_ALERTS_MAX_ATTEMPTS`, `_BACKOFF_BASE_SECONDS`, `_BACKOFF_MAX_SECONDS` | 5, 30, 1800 | delivery attempts per alert before it is `dead`; backoff is base x 2^(attempt-1), capped, +-25 % jitter |
| `ABB_ALERTS_DELIVER_BATCH` | 5 | alerts claimed per delivery tick per workspace (the retry-storm bound). A tick sends its rows one after another in one job: at most 5 x 5 s for webhook/Slack, 5 x 10 s if every row is an email that times out, and it holds a worker for that long and its rows locked (KI-163, KI-169). Raise it only with more workers |
| `ABB_ALERTS_LOCK_TIMEOUT_SECONDS` | 3 | how long an alert write (destination or rule change) waits for a row lock a delivery tick holds before it answers `409 ALERT_BUSY` (retry) |
| `ABB_ALERTS_FAILING_AFTER`, `ABB_ALERTS_PAUSE_AFTER_FAILURES` | 3, 10 | consecutive failed attempts that mark a destination `failing`, then pause it (`paused_reason: failures`, audited as `alert.destination.auto_pause`); a success resets the count, a person resumes a pause |
| `ABB_ALERTS_RATE_PER_MINUTE`, `ABB_ALERTS_DAILY_CAP` | 20, 500 | deliveries per destination per minute and per 24 h; the excess is recorded as `skipped` (`rate_limited`, `daily_cap`), not queued |
| `ABB_ALERTS_TESTS_PER_MINUTE` | 10 | "Send test" calls per workspace per minute, across API processes (shared limiter; on top of the write budget) |
| `ABB_ALERTS_RETENTION_DAYS`, `_PRUNE_INTERVAL_SECONDS`, `_PRUNE_BATCH` | 30, 21600, 2000 | finished delivery-log rows (`delivered`, `dead`, `skipped`, `failed`) older than this are deleted in batches; `pending` rows never are |
| `ABB_WORKER_POLL_INTERVAL_SECONDS`, `_BATCH_SIZE`, `_LEASE_SECONDS`, `_MAX_ATTEMPTS`, `_BACKOFF_BASE_SECONDS`, `_BACKOFF_MAX_SECONDS` | 0.5, 10, 60, 5, 5, 300 | job processing |

### Alert secrets and their key (Phase 17, ADR-150; rotation tool Phase 19b, ADR-162)

Alert destination targets and webhook signing secrets are stored as AES-256-GCM ciphertext under a key kept in the append-only `server_secrets` table (name `alerts-encryption`),
created on first use. **Back that table up with the database** (every bundle of `python -m abb_api.backup` includes it): without its row the secrets sealed under it cannot be read (the destinations then fail closed).

**Rotation** (planned, or after a suspected leak of a database read): `python -m abb_api.rotate alerts --new-key` (as the owner role: `ABB_ROTATION_DATABASE_URL`, else `ABB_MIGRATION_DATABASE_URL`, else `ABB_DATABASE_URL`; `make rotate-alert-keys ROTATE_FLAGS=--new-key`)
inserts a newer key row and re-seals every destination (target and signing secret) in resumable, bounded batches, each destination in its own transaction with its associated data (purpose, workspace, destination) preserved and the result checked to open to the same plaintext.
Then **restart the API and worker** (they cache the current key and would otherwise keep sealing under the old one) and run `python -m abb_api.rotate alerts` once more (without `--new-key`) to catch anything sealed meanwhile. The final line is
`verdict=old_keys_can_be_retired` only when no blob of any destination references a key other than the newest and none failed (exit 1 lists the failed destination ids; fix and rerun, it resumes). `--dry-run` reports what would change and writes nothing. Output is counts and destination ids, never a target or a secret.
Delivery keeps working during a rotation (old blobs open with their own key until their own commit). **Retiring the old key is a manual owner action** (the runtime role cannot delete from `server_secrets`): only after the report says zero and a bundle taken after the rotation exists,
`DELETE FROM server_secrets WHERE name = 'alerts-encryption' AND id < <newest id>;` (the foreign key from `alert_destinations.key_id` refuses while any destination still names it). Which key sealed a destination: `SELECT id, name, key_id FROM alert_destinations`.
The **event-id key** (`server_secrets`, name `event-id`) is the one key that is **not rotatable without breaking deduplication** of already-accepted server-written events; see `docs/runbooks/secrets.md` for what to do if it leaks and for how every other secret rotates.
Alerts carry no captured content; a destination's masked form is the only display of its target.

### Reading the alert delivery log and dead letters (Phase 17, ADR-151)

The owner's view is `GET /v1/alerts/notifications` (and the Alerts page, History tab): status `pending` (waiting, or between retries when `attempts > 0` and `next_attempt_at` is in the future),
`delivered`, `skipped` (with `skip_reason`: `cooldown`, `destination_paused`, `destination_deleted`, `rule_deleted`, `rate_limited`, `daily_cap`) and `dead` (every attempt used, or a failure no retry can fix).
`error_code` is one of `timeout`, `connect_failed`, `dns_failed`, `tls_failed`, `blocked_address` (the name resolved to a non-public address: fix the target), `blocked_port` (the destination's port is not in `ABB_ALERTS_ALLOWED_PORTS`: move it to an allowed port or have the operator allow it; never retried), `http_3xx` (the receiver redirects; redirects are never
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

### Database roles and the startup check (Phase 19a step 4, ADR-155, KI-031)

Run migrations as the database owner (`ABB_MIGRATION_DATABASE_URL`, alembic) and run the API and worker as the least-privilege role `abb_runtime` (`ABB_DATABASE_URL`), which can append to `events` but never update or delete them (INV-1).
At start the API and worker ask which role they are: a superuser, `BYPASSRLS`, any `UPDATE`/`DELETE`/`TRUNCATE` on `events`, membership in the owner of `events`, or `CREATE` on schema `public` counts as too privileged. In production the process then **refuses to start**
(the log names only the closed reasons: `superuser`, `bypass_rls`, `events_rewritable`, `events_owner`, `schema_create`; never a role, host or URL); it also refuses when the question cannot be answered (database down at boot), so start order matters: database first. In development, test and staging it logs one warning
(the dev stack keeps the owner on purpose). If you knowingly run privileged, set `ABB_ALLOW_PRIVILEGED_DB_ROLE=1`; every start then logs a warning with the reasons. Fix a refusal by pointing `ABB_DATABASE_URL` at the runtime role (`docker-compose.yml` shows the pattern).

### Edge: headers, docs, body cap, and running behind a reverse proxy (Phase 19a step 4, ADR-155)

* Headers are set by the applications (API: deny-all CSP, `nosniff`, frame denial, `no-store`; web: nonce CSP and the headers in ADR-155). **Do not strip or overwrite them at the proxy, and do not cache HTML**: each page carries a per-request nonce.
* **HTTPS and HSTS.** Set `ABB_WEB_ORIGIN` to the public `https://` origin (it also turns on `Secure` and `__Host-` cookies); both services then send `Strict-Transport-Security` (one year, `includeSubDomains`, no preload). Terminate TLS at the proxy and redirect http to https there. Over http no HSTS is sent.
* **Client address.** Set `ABB_TRUST_PROXY=1` only when the proxy is the only way to reach the API and web ports and it **appends** the client address as the **last** `X-Forwarded-For` entry (do not pass a client-supplied header through unchanged). Then failed-auth throttling and sign-in limits work per address (ADR-153).
* **Defence in depth at the proxy.** Limit request body size to 8 MiB (the API's own ceiling) and set read timeouts of about 30 to 60 s on request bodies and headers; add a per-IP request rate limit and connection limit. The API's caps are per process and do not replace these. Do not expose `/docs` (it is off in production by default).
* **Docs.** `ABB_API_DOCS_ENABLED=true` serves `/docs`, `/redoc`, `/openapi.json` again (for a private staging); they load Swagger UI from a CDN and carry no CSP.
* **Body cap.** Watch for `503` with code `BUSY` in the request log: sustained BUSY means the cap is too low for the traffic or a client is flooding; raise `ABB_MAX_BODY_BYTES_IN_FLIGHT` only with memory to match (about 6.5 times the cap in the worst measured case).

### Sessions, and people who sign in without an invitation (Phase 19a step 5, ADR-157)

Migration 0099 adds `sessions.device_hint` and a partial index `ix_invitations_open_email`; it is additive and reversible, and sessions that exist before it simply show "Unknown device". The web relay forwards the browser's `User-Agent` (cut to 512 characters) on the sign-in callback so the API can store a coarse label; nothing else of it is kept.

A person the identity provider vouches for but no workspace has invited gets `403 NO_ACCESS` at the callback and **no row**: the login page says "No access yet. Ask a workspace owner to invite you." In the API log this is `login refused` with `reason: not_invited` (never the address). Fix: create an invitation (Settings, Members), or `add-member` with the CLI; the person then signs in again. People made by the CLI (`seed`, `add-member`) and everyone already linked sign in as before.

A person ending a session (or all but the current one) writes `session.revoke` / `session.revoke_others` to the audit log of each workspace they belong to. Demoting or removing a person writes one `invitation.revoke_inviter_lost_rights` row per open invitation they had created. Read-only checks:
`SELECT count(*) FROM users u WHERE NOT EXISTS (SELECT 1 FROM workspace_members m WHERE m.user_id = u.id);` (people without a membership: bounded by invitations, see KI-184) and `SELECT action, count(*) FROM audit_log WHERE action LIKE 'session.%' OR action = 'invitation.revoke_inviter_lost_rights' GROUP BY 1;`.

### Data retention (Phase 19b steps 1-2, ADR-159, ADR-160)

Operator procedure (turning enforcement on, reading reports and tombstones, the kill switch, crash recovery, purge): **`docs/runbooks/retention.md`**. The whole chain is exercised on a real stack by `make retention-e2e` (`scripts/retention-e2e.sh`, CI job `retention-e2e`); the measured cost of enforcement is in `docs/benchmarks/phase-19b-retention-e2e.md`.

**State of the feature:** the default mode `dry_run` measures and reports and deletes nothing. `enforce` deletes expired data, **permanently**. There is no undelete: take a backup first, and **perform a restore drill before the first enforced tick** (see "Backups and restore" below; `enforce` should not be switched on until a restore of a recent bundle has been performed and its output kept). Read the dry-run reports for at least one full cycle before switching.

*Provisioning.* Migrations 0101 and 0102 create the role `abb_retention` (`NOLOGIN`) with exactly the grants listed in ADR-159 and ADR-160, pinned by `apps/api/tests/retention/test_retention_role.py`. Give it a login the way the runtime role gets one: set `ABB_RETENTION_DB_PASSWORD` for `alembic upgrade` (compose does, with a dev default), then point the retention process at it with `ABB_RETENTION_DATABASE_URL` (`postgresql+asyncpg://abb_retention:<password>@<host>/<db>`). Never put that URL in the API's or worker's environment. In tests and CI a login role is created `IN ROLE abb_retention`, as for `abb_runtime`. At start the process refuses (production) or warns (elsewhere) if its role is a superuser, an owner, can create in the schema or can touch the credential tables; set `ABB_ALLOW_PRIVILEGED_DB_ROLE=1` only knowingly. **Enforcement also needs the artifact files:** the retention process must see the API's artifact directory (`ABB_ARTIFACT_DIR`; compose mounts the same volume) and refuses to start in `enforce` if that directory does not exist.

*Running.* Compose (`--profile app`) runs the `retention` service in the mode `ABB_RETENTION_MODE` (default `dry_run`). Natively: `python -m abb_api.retention run` (loop) or `python -m abb_api.cli retention run-once` (one tick; prints `mode=... workspaces=N reports=N truncated=N errors=N budget_exhausted=... halted=... deleted_runs=N expired_traces=N deleted_events=N failed=N skipped_locked=N`; exit 2 when the configuration is unsafe, e.g. enforce without an artifact directory). `python -m abb_api.retention retry-failed` gives runs whose attempts ran out a fresh set (see below).

*When to flip modes (the enforce runbook).*
1. Leave `ABB_RETENTION_MODE=dry_run` (the default). Read `GET /v1/workspaces/<id>/retention/reports` (or the SQL below) after a full tick: the counts are what `enforce` would remove now, per workspace and tier. Use `GET .../retention/preview?trace_days=30` to see the effect of a proposed policy before saving it.
2. Take a backup bundle (`python -m abb_api.backup create`, below; database **and** artifacts in one consistent set), verify it, and **perform a restore drill** from it into a scratch database and directory. Enforcement removes both and cannot bring them back. Run the retention job before the backup in your schedule, not during it.
3. Set `ABB_RETENTION_MODE=enforce` and restart the retention process. The first tick removes at most `ABB_RETENTION_MAX_RUNS_PER_TICK` runs (2000) and `ABB_RETENTION_MAX_ROWS_PER_TICK` rows (100000) per workspace and tier and stops at the time budget (`ABB_RETENTION_TICK_BUDGET_SECONDS`, 60 s); a backlog drains over ticks (`truncated=true` in the report says there is more).
4. Watch `retention tick finished` in the log, the reports (`mode = 'enforce'`: counts actually deleted), and `failed=` in the tick summary.

*Kill switch.* Deletion stops **at the next batch**, not the next tick: set `ABB_RETENTION_MODE=off` (or `dry_run`) in the `.env` file the process reads (the file is re-read before every batch; **an unreadable value counts as `off`**), or send the process SIGTERM/SIGINT. A mode set only through the process environment cannot change in a running process: stop it, then start it with the new mode. A run stopped half way stays `deleting` with its progress row, and the next enforcing tick finishes it; nothing is torn.

*Reading the reports.* SQL: `SELECT workspace_id, tier, mode, runs, events, artifact_bytes, row_count, truncated, cutoff_at, finished_at FROM retention_reports ORDER BY id DESC LIMIT 50`. Per workspace and tier, counts only, never content. In `enforce` the figures are what was actually deleted in that pass, and `dry_run` reports for the same data show the same figures. Tiers are counted separately, not added: `trace` and `run` both count the runs they act on. `operational` is a workspace's finished jobs, expired counters, old usage rows and settled analytics markers; `platform` (no workspace) is dead sessions, global counters and people who never joined a workspace. A tier kept `forever` has no report. `truncated=true` means a bound or the kill switch ended the pass early. A missing report for a tier with data means its query timed out or the saved policy became invalid under the current defaults (logged as `retention measurement failed` / `retention policy invalid for a workspace`, never with content).

*The Retention settings page (Phase 19b step 3).* Settings, Retention, for every member of the workspace (`retention.read`); owners and admins also edit (`retention.write`). The top banner states the installation mode in words (the `mode` the API reports): "Retention is in dry-run mode: nothing is being deleted", "Retention is enforcing" or "Retention is off". Each tier shows its current period and whether it is the installation default or set for this workspace, with what it removes (artifacts: terminal output and large payload bodies; trace detail: events, spans, findings, evaluations and the search entry; run record: the summary, its numbers and its share of the analytics; audit log). Editing offers the default, a number of days (at least 7) or "Keep forever", checks the order rule on the page, and "Preview what this would remove" asks `GET .../retention/preview` for the **proposed** values (nothing is saved; in dry-run mode the page says nothing is deleted yet). A save that shortens any tier first asks, in words, about permanent deletion. "Last passes" lists the newest reports (time, tier, Dry run / Enforced, counts, "stopped early" when truncated). Errors: a validation message from the API, "Too many requests ... try again in N seconds" from `Retry-After`, a 404, and a preview timeout with Try again. Every save is in the audit log as `retention.policy.update`.

*What a reader sees for a run whose trace is gone.* The run page reads `runs.trace_state` first. `deleting` and `expired` show one notice ("Trace expired: the trace of this run was removed by the retention policy; the run summary and its numbers remain", amber while deleting) in place of the events, findings, waterfall, replay, context column and evaluations, with the summary figures; nothing more of the trace is requested. The runs list marks such runs "Trace expired" / "Trace being removed" (glyph and words, never red) and the row stays openable. Comparing or evaluating one answers `409 TRACE_EXPIRED`, shown as "Trace expired". A run link whose run is gone (the run-record tier) reads "This run no longer exists. It may have been removed by the retention policy, or the link may be wrong." (the API does not say which, nor does the page). After the trace tier the analytics page states "Excludes N runs whose trace has expired" beside the cost by agent/model/source, the tool success rates and the model and tool latencies (ADR-161); run counts, totals and the cost headline still include them.

*What gets deleted, in what order (per run, per tier).* Artifacts tier: files through the artifact store, then rows and the project's stored-bytes counter. Trace tier: the run is marked `deleting`, then its search entry, then events, spans, cost lines, findings, evaluations and run-bound background jobs in batches, then (one transaction) the last rows, `expired` and the tombstone, then the analytics rollups of the run's day are rebuilt. Run tier: the run record (and what cascades from it) last, then the rollups again. The runs API reports `trace_state`: `available`, `deleting` (events and spans may be partial), `expired` (only the record remains: events, spans and findings are empty; comparing or evaluating answers `409 TRACE_EXPIRED`). After the run tier the run is `404`. Each deletion leaves a tombstone: `SELECT run_id, tier, mode, deleted_at FROM retention_tombstones ORDER BY deleted_at DESC LIMIT 50`.

*Fixed windows (not configurable).* Finished background jobs 7 days (dead letters 30), expired rate-limit counters 1 hour after expiry, daily usage rows 35 days, settled analytics dirty markers 35 days, dead sessions 7 days after they expired or were revoked, and people who signed in on an invitation, never joined a workspace and have no open invitation or live session 30 days after sign-in. Audit-log entries follow the workspace's `audit` tier; each batch of the purge writes a `retention.delete.audit` entry (counts and age range) in the same transaction, and every other enforced pass writes one `retention.delete.<tier>` entry (system actor, counts only).

*When something fails.* Watch for `failed=` in the tick summary and `retention step failed` (fields: `run_id`, `tier`, `failure_code`). The state is in the table: `SELECT tier, failure_code, attempts, count(*) FROM retention_run_progress GROUP BY 1,2,3 ORDER BY 4 DESC`. Codes: `artifact_store` (the files could not be removed: check the directory and its permissions), `database`, `timeout` (a batch exceeded `ABB_RETENTION_STATEMENT_TIMEOUT_SECONDS`: lower `ABB_RETENTION_BATCH_SIZE`), `internal`. A failed run is retried by each tick up to `ABB_RETENTION_MAX_ATTEMPTS` (5); after that it waits until you fix the cause and run `retry-failed`. A failure never blocks other runs. A run that reads `deleting` for a long time has an unfinished progress row.

*A run's end* is its `completed_at`, or its last update while it has none, clamped between the server's first sight and last update of the run, so a client cannot move its own expiry. A run that is updated after it became eligible but before the job marks it is left alone.

*Known limits:* approvals keep their scrubbed arguments (KI-195); artifact files with no row are not swept (KI-196); a late event for an expired or deleted run creates a fresh run (KI-197); the audit entry of a pass is lost if the process dies in it, the tombstones are the authoritative record (KI-200).

### Purging a project or a workspace (Phase 19b step 5, ADR-163)

**This is permanent.** A purge deletes a project (or a whole workspace) from every backend: Postgres rows, artifact files, search entries and analytics rollups. Nothing brings it back except a restore of a backup taken before it, and backups keep the data until they expire (KI-215). **Take a backup bundle first (`python -m abb_api.backup create`) and know where it is.** There is no API endpoint for this and the API process cannot do it; it is run by an operator, in the retention process's environment (`ABB_RETENTION_DATABASE_URL`, and `ABB_ARTIFACT_DIR` pointing at the API's artifact directory, as for enforcement). `ABB_RETENTION_MODE` is irrelevant: a purge is an explicit action.

```
python -m abb_api.retention purge-project   --workspace <slug> --project <slug> [--dry-run] [--confirm <project-slug>]
python -m abb_api.retention purge-workspace --workspace <slug>                  [--dry-run] [--confirm <workspace-slug>]
```

1. **Dry run (the default).** Without `--confirm` nothing is changed. Example:

```
$ python -m abb_api.retention purge-project --workspace acme --project checkout
purge-project workspace=acme project=checkout
postgres, rows per table:
  agents 3
  alert_rules 2
  api_keys 4
  artifacts 1840
  events 912344
  runs 5120
  spans 40211
  ...
artifact store: files=1840 bytes=1403219811
search index: entries=5120
rollups: rows=612
dry run: nothing was deleted
to purge, run the same command with --confirm checkout (this is permanent: take a backup first)
```

   Check the numbers are what you expect for that scope and nothing else. For a workspace it also says how many people are tied to it and how many the users rule could remove.
2. **Purge.** Repeat the command with `--confirm` and the **exact** slug of what is deleted (the project slug for `purge-project`, the workspace slug for `purge-workspace`; case matters). Anything else is refused with exit 2 and nothing is touched.

```
$ python -m abb_api.retention purge-project --workspace acme --project checkout --confirm checkout
purge-project: done purge_id=7b0f...; removed (counts when the purge began):
...
```

3. **What happens first.** The project (or workspace) is marked `purging` in the same transaction as its purge record. From that moment its API keys answer the uniform `401 API_KEY_INVALID`, ingestion stops, the project and its runs read as not found (a purging workspace is a 404 for its members), and then the data is deleted: every run through the retention engine's procedure (files, search entry, events and derived rows, run record, tombstone, rollups rebuilt), then the project's rules (this resolves KI-170), approvals, policies, agents, keys, counters, and last the project row, in one transaction with the record's completion and one counts-only audit entry (`retention.purge.project`, system actor). A workspace purge does this for every project, then its destinations (with their sealed secrets), invitations, the workspace's audit log, memberships and the workspace row.
4. **The users rule.** People are platform-level. A workspace purge removes memberships and invitations and deletes a person only if they then have no membership anywhere, no live session, no open invitation elsewhere and nothing that references them; a person who is still a member of another workspace, or is signed in, is kept. The platform's sealing keys (`server_secrets`) are never touched.
5. **Interrupted or failed.** SIGTERM/SIGINT stops at the next batch (exit 5). A failed step prints a fixed code and exits 4 (`artifact_store`: check the artifact directory and permissions; `database`; `timeout`: lower `ABB_RETENTION_BATCH_SIZE`; `internal`: rows were left that the inventory says must not be, see the log line `purge refused to delete its root` for the table names). **Run the same command again**: it resumes the `running` record and never creates a second. Exit 3 `locked` means a retention tick or another purge holds that workspace; wait for it to finish. Exit 3 `not_found` is also what you get after a purge completed.
6. **Verify.** The record is `SELECT id, scope, state, requested_at, completed_at, attempts, failure_code, steps_done, counts FROM purge_tombstones ORDER BY requested_at DESC LIMIT 20` (ids, a hash of the typed slug, counts: no names or content). A running one is an unfinished purge. To check that nothing of a project is left, run the dry run again: it answers `not_found`; the test suite's scan (`tests/retention/purge_helpers.py::assert_scope_gone`) does the same for every table with a `project_id` or `workspace_id` column. A workspace's own audit log goes with it; the purge record is what remains, and the per-run tombstones of a purged project (`retention_tombstones`, ids only) stay in its workspace.

*Known limits:* backups taken earlier still hold the data (KI-215); artifact files with no database row are not found (KI-196/KI-216); other per-run read endpoints of a purging project are not filtered until their run is deleted (KI-211); non-run background jobs of a purged project are not removed (KI-214).

### Backups and restore (Phase 19b step 4, ADR-162)

`python -m abb_api.backup create|verify|restore` (and `make backup`, `make backup-verify`, `make restore`; compose service `backup`, profile `ops`) writes and reads one bundle: a `pg_dump -Fc` taken in a single snapshot, the artifact files copied after it, and a `MANIFEST.json` (schema revision, row counts, SHA-256 of every file and artifact, versions; nothing secret). Optional `--encrypt` (openssl AES-256, PBKDF2, an HMAC over the manifest; passphrase from a file, an environment variable name or a descriptor, never an argument). Bundles are created `0700`/`0600` and an existing non-empty output is refused. **A bundle holds `server_secrets` and is as sensitive as the database: encrypt it at rest.**
Restore loads only into an empty database and an empty artifact directory, never drops anything, loads the database in one transaction, upgrades an older bundle to the code's head and verifies counts and files; exit codes are fixed (3 refused, 4 integrity, 5 tool, 6 verification, 7 inconsistent backup). Full procedure, scheduling (systemd timer and cron examples), the ordering advice (retention before the backup, not during) and the restore steps: `docs/runbooks/backup-restore.md`.

**RPO = the interval between successful backups; RTO = the measured restore time** plus provisioning (the performed drill, `make backup-restore-e2e`: restore took 1.2 s for a 10 MB bundle and 2.5 s for a 132 MB one on a laptop, indicative only; `docs/benchmarks/phase-19b-restore-drill.md`, KI-219). Point-in-time recovery (WAL archiving) is the documented upgrade path when a smaller RPO is needed and is not built. **`ABB_RETENTION_MODE=enforce` requires a performed restore drill first.** Not in a bundle: the operator's `.env` and external secrets (OIDC client secret, SMTP password, role passwords, the passphrase), role logins, PostgreSQL configuration.

### Upgrading and rolling back migrations (Phase 19b step 4, ADR-162)

Every migration is `reversible` or `forward-only` in `apps/api/migrations/safety.json` (a test fails for an unclassified one). Back up, upgrade, verify; roll back reversible ones with `alembic downgrade`, and restore the pre-upgrade backup for forward-only ones: `docs/runbooks/migrations.md` (with per-migration notes on what a downgrade discards and which upgrades are lock-heavy or rewrite data).

### Project quotas (Phase 19a step 3, ADR-154)

A project at a quota has the excess refused with `QUOTA_EXCEEDED` (per event inside a `202` on `POST /v1/events/batch`; `413` on `POST /v1/events`, `POST /v1/runs` and `PUT /v1/artifacts/{id}`). Nothing the project already holds is refused or removed:
events of existing runs and agents and replays are accepted at any usage. In the product: Settings > Quotas shows each quota with use, percentage and a status in words (amber from 80 percent "Near limit", "Limit reached" when refusing); owners and admins change limits there.

Raise or lower a limit for one project (audited, applies to the next request): `PUT /v1/projects/<project id>/quotas` with `{"events_per_day": 20000000}` (`null` removes the override). The same through the Quotas page.
Raise the default for every project: set `ABB_QUOTA_*` and restart the API processes. Switch quotas off entirely: `ABB_QUOTAS_ENABLED=false` (counters stop being updated; switching back on resumes from stale figures, so the first day over-allows).

Inspect (read-only SQL; the counters are derived operational state, never edit them):

```sql
-- today's use per project (UTC day), biggest first
SELECT p.slug, u.day, u.runs_created, u.events_accepted
FROM project_usage_daily u JOIN projects p USING (workspace_id) WHERE p.id = u.project_id AND u.day = (now() AT TIME ZONE 'UTC')::date
ORDER BY u.events_accepted DESC LIMIT 20;
-- stored artifact bytes per project, and overrides
SELECT p.slug, a.bytes_stored FROM project_artifact_usage a JOIN projects p ON p.workspace_id = a.workspace_id AND p.id = a.project_id ORDER BY a.bytes_stored DESC;
SELECT p.slug, o.* FROM project_quota_overrides o JOIN projects p ON p.workspace_id = o.workspace_id AND p.id = o.project_id;
-- distinct agents per project
SELECT project_id, count(*) FROM agents GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
-- who changed a limit
SELECT occurred_at, actor_id, resource_id, details FROM audit_log WHERE action = 'project.quota.update' ORDER BY id DESC LIMIT 20;
```

The counters equal what committed: they are updated in the transaction that stores the data. If `project_artifact_usage` and the real sum ever disagree (an out-of-band delete), recompute: `UPDATE project_artifact_usage u SET bytes_stored = (SELECT coalesce(sum(size_bytes),0) FROM artifacts a WHERE a.workspace_id = u.workspace_id AND a.project_id = u.project_id);`.
The per-day rows are one per active project per day and are pruned after 35 days by the retention job (ADR-160). Ingestion of one project is serialised on its project row for the length of one batch transaction; the measured cost is in `docs/benchmarks/phase-19a-quotas.md`.
Counting began at the migration (0098): activity before it is not in the first day's figures; existing artifacts were counted from the start.

## What to watch

Structured JSON logs (one line per request with `request_id`, and `workspace_id`, `project_id`, `key_id` after
authentication; payloads and secrets are never logged). Messages worth alerting on: `lease lost while running`, `spans left untouched`, `job failed` with
`outcome: dead_letter`, `worker loop error`, `conflicting duplicate events ignored`, `authentication unavailable`,
`ingest unavailable`, `stream listener unavailable`, `stream failed`. Application metrics (spec §116) arrive in Phase 19c; until then use the logs and these queries:

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
- [Abuse resistance and hardening](runbooks/hardening.md): what each limit, quota and throttle does, what an operator sees when it triggers, reading the counters and audit rows, reverse-proxy guidance, and the Phase 19a upgrade notes (migrations 0097-0099)

- [Backup and restore](runbooks/backup-restore.md): the bundle, scheduling, verifying, the restore procedure and exit codes, the restore drill
- [Migrations](runbooks/migrations.md): the safe upgrade, rolling back each class, per-migration notes
- [Secrets](runbooks/secrets.md): every secret and how it rotates (the event-id key does not)

Still to write (Phases 18-19c): database saturation.
Recovery targets: with the backup tool above, RPO = the backup interval and RTO = the measured restore time (Phase 19b step 6). The spec §122 targets (RPO <= 15 min, RTO <= 4 h) need managed PostgreSQL with point-in-time recovery, which is not built.
