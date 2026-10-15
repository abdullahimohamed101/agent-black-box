# Operations

Operating Agent Black Box itself (spec §43, §104-125). Runbooks live in `docs/runbooks/`.

## Processes

| Process | Command | Notes |
| --- | --- | --- |
| API | `uvicorn abb_api.main:app_from_env --factory` | stateless; `/healthz` (liveness, no dependencies), `/readyz` (database, 2.5 s deadline) |
| Worker | `python -m abb_api.worker` | claims background jobs; any number may run; SIGTERM finishes the current batch and exits 0 |
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
The per-day rows are one per active project per day and are not pruned yet (retention, Phase 19b). Ingestion of one project is serialised on its project row for the length of one batch transaction; the measured cost is in `docs/benchmarks/phase-19a-quotas.md`.
Counting began at the migration (0098): activity before it is not in the first day's figures; existing artifacts were counted from the start.

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
- [Abuse resistance and hardening](runbooks/hardening.md): what each limit, quota and throttle does, what an operator sees when it triggers, reading the counters and audit rows, reverse-proxy guidance, and the Phase 19a upgrade notes (migrations 0097-0099)

Still to write (Phases 18-19): database saturation, backup and restore drill (spec §123).
Recovery targets: RPO <= 15 min, RTO <= 4 h (spec §122) once managed PostgreSQL with point-in-time recovery is in place.
