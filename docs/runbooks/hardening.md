# Runbook: abuse resistance and hardening (Phase 19a)

For the operator of a hosted or self-hosted installation. It says what each limit, quota and throttle does, what a client and an operator see when it
triggers, how to read the counters and audit rows, how to tune each one, how to put a reverse proxy in front, and what changes when you upgrade to this
release. Decisions and reasons are in ADR-152 to ADR-158; setting names and defaults are in `docs/OPERATIONS.md` (Configuration). The behaviour below is
exercised against a real two-process stack by `make hardening-e2e` (`scripts/hardening-e2e.sh`, `docs/TESTING.md`); its measurements are in
`docs/benchmarks/phase-19a-hardening-e2e.md`.

**What this is and is not.** These are abuse rails: they keep an anonymous flood, a runaway agent or one noisy person from pushing the installation over, and
they refuse loudly. They are not a WAF, not DDoS protection and not a substitute for rate limiting at the edge (see "Reverse proxy"). All counters are in
PostgreSQL, so every API process shares them; with the database unreachable the limits fall back to per-process behaviour (fail open, by design).

## At a glance

| Layer | What it bounds | Client sees | You see | Tune |
| --- | --- | --- | --- | --- |
| Shared limiter (ADR-152) | ingestion events and bytes per project, artifacts, sign-in starts, policy/approval/alert writes, session revocations, **across API processes** | `429 RATE_LIMITED`, `Retry-After` | `rate_limit_counters` rows; log `shared limiter unavailable; failing open` when the database is the problem | `ABB_RATE_LIMIT_*`, `ABB_POLICY_*`, `ABB_ALERTS_*_PER_MINUTE`, `ABB_LOGIN_GLOBAL_PER_MINUTE`, `ABB_SESSION_WRITES_PER_MINUTE`; `ABB_LIMITS_*` for the mechanism |
| Failed-auth throttle (ADR-153) | bad API keys: per client address, per presented key id, whole installation | `429 AUTH_THROTTLED`, `Retry-After` (same body for existing and unknown keys) | one `auth.throttled` audit row per window per workspace; log `authentication throttled` with the scope names (`address`, `credential`, `global`) | `ABB_AUTH_FAIL_*`, `ABB_TRUST_PROXY` |
| Project quotas (ADR-154) | distinct agents, runs/day, events/day, artifact bytes per project | `QUOTA_EXCEEDED` naming the quota (per event inside a `202` for batches; `413` for single writes and uploads) | Settings > Quotas; `project.quota.update` audit rows; usage SQL below | per-project override (owner/admin), `ABB_QUOTA_*` defaults, `ABB_QUOTAS_ENABLED` |
| Body-bytes cap (ADR-155) | request-body bytes held in memory by one API process | `503 BUSY`, `Retry-After: 2`; `408 REQUEST_TIMEOUT`; `413` over 8 MiB | `BUSY` in the request log | `ABB_MAX_BODY_BYTES_IN_FLIGHT`, `ABB_BODY_READ_TIMEOUT_SECONDS` |
| Startup role check (ADR-155) | API/worker connected as a database role that can rewrite events | production: process exits at start with a fixed message | closed reasons in the log (`events_owner`, `schema_create`, ...), never a role name or URL | connect as `abb_runtime`; `ABB_ALLOW_PRIVILEGED_DB_ROLE=1` accepts knowingly |
| Alert ports (ADR-158) | webhook/Slack destinations on a port outside the list | `422 DESTINATION_TARGET_INVALID` (`port_not_allowed`); a delivery shows `blocked_port` | alert history `error_code` | `ABB_ALERTS_ALLOWED_PORTS` (default `443`) |
| Search fair share (ADR-158) | search permits per actor and workspace in one process | `503 SEARCH_BUSY` with `details.reason` | request log | `ABB_SEARCH_MAX_CONCURRENT` (the shares follow it) |
| Identity (ADR-156/157) | people without an invitation; sessions; invitations of demoted admins | `403 NO_ACCESS` at sign-in; revoked session `401` at once | audit: `session.revoke`, `session.revoke_others`, `invitation.revoke_inviter_lost_rights`; log `login refused` reason `not_invited` | invite the person; Settings > Sessions |

## Reading the counters

```sql
-- what is counting now (shared limiter), biggest first
SELECT scope, workspace_id, key, window_start, count, expires_at
FROM rate_limit_counters WHERE expires_at > now() ORDER BY count DESC LIMIT 20;
-- expired backlog (deleted in batches by whichever API process serves; a persistent backlog means none is running)
SELECT count(*) FILTER (WHERE expires_at < now()) AS expired, count(*) AS total FROM rate_limit_counters;
-- failed-auth pressure right now: the three scopes of ADR-153 (keys are pseudonyms, never an address or a secret)
SELECT scope, count(*) AS keys, max(count) AS busiest FROM rate_limit_counters
WHERE scope LIKE 'auth_fail_%' AND expires_at > now() GROUP BY scope;
-- quota use today per project, overrides, and who changed a limit: see "Project quotas" in OPERATIONS.md
SELECT occurred_at, actor_id, resource_id, details FROM audit_log WHERE action = 'project.quota.update' ORDER BY id DESC LIMIT 20;
-- throttle events, per workspace (one row per window at most)
SELECT occurred_at, resource_id AS key_id, details FROM audit_log WHERE action = 'auth.throttled' ORDER BY id DESC LIMIT 20;
-- sessions and invitation housekeeping
SELECT action, count(*) FROM audit_log
WHERE action LIKE 'session.%' OR action = 'invitation.revoke_inviter_lost_rights' GROUP BY 1;
SELECT count(*) FROM users u WHERE NOT EXISTS (SELECT 1 FROM workspace_members m WHERE m.user_id = u.id);  -- people without a membership
```

Counters are operational state: read them, never edit them. To unblock one budget: `DELETE FROM rate_limit_counters WHERE scope = '<scope>' AND workspace_id = '<uuid>';`
(a process that already reserved credit keeps it until its window ends, at most one window). Global scopes are stored under the nil workspace id
`00000000-0000-0000-0000-000000000000`.

## What happens when each one triggers

**`429 RATE_LIMITED` on ingestion.** A project exceeded its events/second or bytes/second (a 10 s window; burst allowed). The SDK retries with backoff and honours
`Retry-After`. Raise `ABB_RATE_LIMIT_EVENTS_PER_SECOND` / `_BYTES_PER_SECOND` (and the bursts) with the capacity to match. Across N processes the fleet admits at most the
limit and may refuse up to `N x (block - 1)` early (about 15% in the worst case, usually none); a burst straddling a window boundary can spend two windows (KI-174).

**`429 AUTH_THROTTLED`.** Someone is presenting bad API keys. Only failures count, so valid traffic does not spend a budget. Budgets per 60 s window: 30 per client address
(needs `ABB_TRUST_PROXY=1` and a proxy that sets the address), 10 per presented key id, 600 for the whole installation. A closed window refuses from memory: no key lookup, no
counter write, so a flood costs the database counters, not requests (measured: a closed-window flood of about 500 requests/s caused about 5 database transactions/s, idle level).
Keys that authenticated in the last five minutes on that process skip the gate and are never refused, and while the address or the installation window is closed **a key id that exists still reaches
authentication** (the process keeps the set of active key ids, refreshed every minute and when it creates a key), so a flood does not lock out valid keys, new keys or a restarted process. What a bad
actor can still do is close one *key id's own* window: ten bad secrets for a key id they know make that key id's valid requests (not in the process cache) get `429` for up to a window (KI-191); SDKs retry.
A key created by the CLI is admitted during a closed window after the next refresh (at most a minute). Missing or malformed keys are neither counted nor gated and
cost the database nothing (one log line per window, with a count). People's sessions, sign-in and callbacks are never behind this throttle. If a real user is being throttled: check `auth.throttled` audit rows for the key id,
confirm the key is correct and not revoked, and look for a shared NAT (everyone behind one address shares its budget). The global budget protects the database; raise it only with headroom.

**`QUOTA_EXCEEDED`.** A project hit one of its four quotas. The item is refused, nothing already stored changes, replays of accepted events are still accepted, and events of existing runs
and agents keep working (except the daily event cap, which applies to every new event). Look at Settings > Quotas, decide whether the limit or the agent is wrong, and raise the project's override
(`PUT /v1/projects/<id>/quotas`, owner or admin, audited) or the defaults. A refusal is never silent: the SDK counts it in `rejected_by_server` and warns once a minute.

**`503 BUSY`.** This process holds the maximum request-body bytes in memory. Brief and retryable. Sustained `BUSY` means one client is flooding large bodies or the cap is too low for your traffic;
raise it only with memory to match (resident memory is 4 to 6.5 times the accounted bytes plus the baseline).

**API refuses to start in production.** The log shows `Refusing to start: the database role ... can rewrite or delete stored events or change the schema` and the closed reasons. Point
`ABB_DATABASE_URL` at the `abb_runtime` role and keep the owner for migrations (`ABB_MIGRATION_DATABASE_URL`). If the database was down at boot the message is the "could not be verified" one: start the database first.

**`blocked_port` in alert history.** The destination was saved while a wider port list was in force (or the list was tightened). It fails permanently and is never retried. Move the destination to an allowed
port or add the port to `ABB_ALERTS_ALLOWED_PORTS` on both the API and the worker, restart, and re-enable.

**`SEARCH_BUSY`.** `actor_limit`: that key or person already has its searches running; `workspace_share`: the workspace holds its half of this process's permits while another workspace is searching;
`process_full`: the process is at `ABB_SEARCH_MAX_CONCURRENT`. All retryable after one second. See `docs/runbooks/search.md`.

**`403 NO_ACCESS` at sign-in.** The identity provider vouched for a person nobody invited. No row was written. Invite them (Settings > Members) or `add-member`, then they sign in again. People created by the CLI,
and everyone already linked, are unaffected.

**A demoted or removed admin's invitations.** Their open invitations were revoked in the same transaction (audit `invitation.revoke_inviter_lost_rights`); issue a new invitation from an owner or admin.

## Reverse proxy

Run the API and web behind a proxy you control; it is defence in depth, not optional polish.

* **TLS and HSTS.** Terminate TLS at the proxy, redirect http to https, and set `ABB_WEB_ORIGIN` to the public `https://` origin. That alone turns on `Secure` and `__Host-` cookies and HSTS in both services.
* **Client address.** Set `ABB_TRUST_PROXY=1` only if the proxy is the only way to reach the API and web ports, and have it **set** `X-Forwarded-For` to the address it saw (the last entry is the one that counts;
  never pass a client-supplied value through as the only entry). Otherwise leave it at `0`: the header is then ignored entirely, so nobody can pick or frame an address.
* **Limits at the edge.** Cap request bodies at 8 MiB (the API's own ceiling), set header and body read timeouts of 30 to 60 s, and add a per-address request rate and connection limit. The API's caps are per process.
* **Headers and caching.** Do not strip or overwrite the application's security headers, and **never cache HTML**: every page carries a per-request CSP nonce. Static assets under `/_next/static` may be cached.
* **Streams.** Live run streams are long-lived `text/event-stream` responses: disable response buffering for `/v1/runs/*/stream` and allow idle timeouts of several minutes.
* **Do not expose** `/docs`, `/redoc`, `/openapi.json` (off in production anyway) or the worker.

An illustrative nginx fragment (adapt, do not paste blindly):

```nginx
limit_req_zone  $binary_remote_addr zone=abb_req:10m  rate=30r/s;
limit_conn_zone $binary_remote_addr zone=abb_conn:10m;
server {
  listen 443 ssl;  # certificates omitted
  client_max_body_size 8m;  client_body_timeout 30s;  client_header_timeout 30s;
  limit_req zone=abb_req burst=60 nodelay;  limit_conn abb_conn 40;
  location /v1/ {                      # the API
    proxy_pass http://abb_api;
    proxy_set_header X-Forwarded-For $remote_addr;      # one entry: what this proxy saw
    proxy_set_header Host $host;
    proxy_http_version 1.1;  proxy_buffering off;  proxy_read_timeout 3600s;   # streams
  }
  location / {                         # the web app (HTML is not cached)
    proxy_pass http://abb_web;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header Host $host;
  }
}
```

## Upgrade notes (Phase 19a)

Deploy the API, worker and web app **together**. Migrations are additive and reversible; run them as the database owner before starting the new processes.

| Migration | Adds | Notes |
| --- | --- | --- |
| 0097 | `rate_limit_counters` | the shared limiter's counters; no foreign key to tenants; rows expire within a minute or two of their window |
| 0098 | `project_quota_overrides`, `project_usage_daily`, `project_artifact_usage` | artifact bytes are backfilled from `artifacts`; run and event counting starts at the migration, so the first day's figures exclude earlier activity |
| 0099 | `sessions.device_hint`, partial index `ix_invitations_open_email` | sessions that exist before it show "Unknown device" |

Deploy-impact items, consolidated:

1. **Database role (ADR-155).** In production the API and worker now **refuse to start** connected as a role that can rewrite events (the table owner, a superuser, a member of the owner role, or one with `CREATE` on schema `public`).
   Switch `ABB_DATABASE_URL` to `abb_runtime` (see `docker-compose.yml`) and keep the owner for migrations, or set `ABB_ALLOW_PRIVILEGED_DB_ROLE=1` knowingly (every start then warns). Start the database before the API: an unverifiable role also refuses in production.
2. **Pages render per request (ADR-155).** The web app sends a per-request CSP nonce, so no page is cacheable; make sure no proxy or CDN caches HTML. A custom page script or inline snippet injected by a proxy will be blocked.
3. **New responses on every body-reading route (ADR-155).** `503 BUSY` (retryable), `408 REQUEST_TIMEOUT`, and `413` for any body over 8 MiB. `/docs`, `/redoc` and `/openapi.json` are gone in production unless `ABB_API_DOCS_ENABLED=true`.
4. **Login cookie (ADR-156).** On https the sign-in state cookie is `__Host-abb_login` (path `/`); the old name is never read. Logins started in the ten minutes before the upgrade fail once ("Sign-in did not complete. Try again.") and succeed on the next try. http deployments see no change. Rollback has the same transient effect.
5. **Invitation-only people (ADR-157).** A person nobody invited no longer gets a `users` row at first sign-in (`403 NO_ACCESS`, plain notice). Anyone created by `seed`, `add-member` or `create-owner` and everyone already linked signs in as before. Invitations now die with their inviter's right to invite.
6. **Client address (ADR-153).** `ABB_TRUST_PROXY` now also applies to the API. Leave it `0` unless the proxy sets `X-Forwarded-For` as described above.
7. **Quotas (ADR-154).** Defaults are generous (500 agents, 50,000 runs and 5,000,000 events a day, 5 GiB of artifacts per project) but they are limits: a project that exceeded them before the upgrade starts to be refused at the next item. Check Settings > Quotas on your busiest projects and raise overrides first if needed.
8. **Alert destinations (ADR-158).** Only port 443 is allowed by default. A webhook or Slack destination saved on another port fails with `blocked_port` after the upgrade until you add the port to `ABB_ALERTS_ALLOWED_PORTS` on **both** the API and the worker.
9. **Search (ADR-158).** One actor holds at most two permits of a process, and a workspace at most half while another is searching; clients that ran many parallel searches see `SEARCH_BUSY` with a reason and should retry.

Rollback: the migrations downgrade cleanly (tests prove 0097/0098/0099 up and down); the code paths are the only reason to keep the order above (API and web together). Switches for an emergency, none needing a migration:
`ABB_QUOTAS_ENABLED=false`, `ABB_AUTH_FAIL_ENABLED=false`, `ABB_LIMITS_SHARED_ENABLED=false` (per-process limits again), `ABB_ALLOW_PRIVILEGED_DB_ROLE=1`, `ABB_API_DOCS_ENABLED=true`.

## Known limits, stated plainly

Fixed windows allow a burst across a boundary up to twice a limit (KI-174); concurrency caps for streams and approval long polls are per process (KI-017, KI-147); the failed-auth throttle does not cover session cookies (KI-176) or sign-in callbacks (KI-073);
one project's ingestion is serialised on its quota lock, so a second process stops helping one very hot project (KI-178); the body cap is per process and bounds accounted bytes, not the work after a body is read; a hostile authenticated client can fill the cap with its own legitimate large uploads (KI-182).
