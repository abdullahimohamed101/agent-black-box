# Security

Agent Black Box stores source code, prompts, shell output, file names, database query text and
tool arguments. Treat it as a high-value target and all captured telemetry as hostile input.
Source: spec §32, §38, §90-93, §126; threat model to be expanded as phases land.

## Assets and boundaries
Customer prompts/responses, diffs, terminal output, API keys, tool arguments, policy config,
approval authority, trace history, user identity. Boundaries: customer agent process | internet |
ingestion edge | control plane | database/object storage | human approvers.

## Requirements (enforced by tests where stated)
- API keys (implemented, Phase 2): `abb_live_<key_id>.<secret>`; `key_id` is public and indexed, `secret` is 32 random
  bytes shown once; only `sha256(secret)` is stored and compared in constant time (a dummy comparison runs for unknown
  keys). Scopes `events:write`, `runs:read`, `artifacts:write`, `policy:check` (database CHECK constraint); optional expiry;
  revocation; `last_used_at` written at most once a minute. A project-bound key reads and writes its project only; a
  workspace-wide key (no project) can read across projects but cannot ingest. Unknown, malformed, wrong-secret, revoked and
  expired keys all produce the same 401 (`API_KEY_INVALID`); a valid key missing a scope gets 403 with the required scope.
  Keys are issued by the CLI (`python -m abb_api.cli create-key`) or, since Phase 15, by `POST /v1/api-keys` for people holding
  `api_key.create` (the token is returned once with `Cache-Control: no-store`, only its hash is stored, a person can mint only keys
  whose non-ingestion actions they hold themselves, ingestion keys need a project); the secret is never logged.
  `make seed` writes a dev key to the gitignored, owner-only `.local/dev-api-key` and refuses to run when
  `ABB_ENVIRONMENT=production`.
- Tenancy (implemented, Phase 2): every tenant table is keyed `(workspace_id, id)` with composite foreign keys, so a row
  cannot reference another tenant's parent (database-enforced, tested); repositories are built with a tenant and no method
  takes a workspace argument; other tenants' and other projects' resources are indistinguishable from nonexistent (404, same
  body). Cross-workspace and cross-project tests cover every read route. Optional Postgres RLS remains a later defence in depth (§73.4).
- Redaction: SDK-side before export, server-side backstop, payload modes FULL/METADATA_ONLY/DISABLED. (Phases 3, 13)
- Never log secrets or payload bodies (implemented): request logs carry method, path, status, duration, request id and the
  safe identifiers `workspace_id`, `project_id`, `key_id`; a test posts secret-looking keys and payloads and asserts none
  appear in any log record. The reason a key was rejected is logged server-side only.
- Request hardening (implemented): body size limits are enforced while streaming for the compressed and the decompressed
  bytes (a 100 MB gzip bomb is rejected without being expanded, tested by peak memory); only gzip/identity encodings and
  `application/json` are accepted; validation errors never echo submitted values; NUL characters and lone surrogates are rejected
  at the boundary (PostgreSQL cannot store them); unhandled errors return a generic 500 without internals.
- Response headers (implemented): every response carries `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`; full secure-header policy is Phase 19.
- Denial of service (partly implemented): per-project token buckets (per process), bounded batches, bounded cursors and page
  sizes, bounded metadata; a shared limiter and quotas are Phase 19.
- Live streams (implemented, Phase 5): the stream endpoint uses the same authentication, `runs:read` scope and tenant/project visibility as
  the other reads (another tenant's or project's run is a 404, tested over a real socket). Payloads are never streamed, only `has_payload`.
  The `NOTIFY` that wakes streams carries ids only and is never trusted for content: streams re-read rows under their own tenant context.
  Resources are bounded per process (50 streams, 10 per key, 15 min lifetime, 10 s write timeout) so one visitor cannot hold the pool
  (a stream borrows a connection per poll, never while idle). Since Phase 15 an open stream re-checks its credential every
  `ABB_STREAM_REAUTH_SECONDS` (30): a revoked or expired key, a revoked session or a removed or demoted member ends it with `STREAM_UNAUTHORIZED`
  (KI-033, tested over a real socket and in the browser); the cap of 10 is per person (`user:<id>`) or key, not per web server.
- Rendering: payloads displayed as text; sanitize any markup; no `dangerouslySetInnerHTML` on trace data.
- Approvals bound to the exact action hash, single use, atomic consume. (Phase 14)
- Rate limits, size limits, bounded queues against telemetry flooding. (Phases 2, 19)
- Dependencies: lockfiles, scheduled vulnerability scans, minimal SDK deps.
- Database roles (KI-020, INV-1): migrations run as the database owner; the API, worker and CLI run as `abb_runtime`
  (migration `0007`), which has SELECT/INSERT on `events` and no UPDATE, DELETE, TRUNCATE or DDL anywhere. Enforced by
  `apps/api/tests/test_runtime_role.py`, and the whole API test suite runs as a role inheriting it. The deletion back door is closed too (migration 0008): `events` references `runs` with RESTRICT and the role cannot DELETE runs, agents, projects or workspaces, so events cannot vanish as a cascade. Retention (Phase 19) must be a privileged owner job. Caveats: a role with CREATEROLE/superuser or the owner can still alter privileges; `ALTER ROLE ... PASSWORD` is sent by `alembic upgrade` (set `log_statement` below `ddl` or keep migrations out of statement logs); Alembic only sets the password on an upgrade that ends at head. A superuser or the owner can still rewrite
  history: protect those credentials, and use `ABB_MIGRATION_DATABASE_URL` for the owner connection in deployments. Set
  `ABB_RUNTIME_DB_PASSWORD` for `alembic upgrade` to give the role a login; without it the role cannot log in.
- Sign-in and sessions (implemented, Phase 15, ADR-060): OIDC code + PKCE against a configured issuer; opaque 32-byte session cookie
  (`__Host-abb_session`, `HttpOnly`, `Secure`, `SameSite=Lax` on https), only `sha256` stored, absolute 7 d / idle 24 h, revoked at once by
  logout, member removal (the next request re-reads membership), or `relink-user`. State, nonce, PKCE verifier and a validated `return_to` live in
  a database row bound to an `abb_login` cookie (login CSRF fails); ID tokens are verified (RS256/ES256, `iss`, `aud`, `azp`, `exp`, `nonce`,
  `email_verified` required); identity links on `(provider, subject)` and a verified email may adopt only a user with no subject yet. Tested in
  `tests/test_auth_sessions.py`. Cookie-authenticated writes need an `Origin` equal to `ABB_WEB_ORIGIN` (proxy and API); the API never reads
  `X-Forwarded-*`. The web proxy forwards an allowlist of headers and never `Authorization`, other cookies or `Set-Cookie`.
- Authorization (implemented, Phase 15, ADR-061): one `authorize()` path for keys and people, the role/scope matrix as data with a literal
  second copy in the tests (both directions), every route in a registry the tests walk (a route without a case fails CI), an AST test against
  scattered role checks, canary tests that nothing of another workspace leaks, `404` (never `403`) for a workspace or id the actor cannot see.
  VIEWER and BILLING never receive captured content (`payload.read`; event payloads are withheld, artifact content is `403`).
  Captured content includes shell command text and file paths (review F3, ADR-061). **A VIEWER can see**: that a run, a model call, a tool
  call, a shell command, a file edit or a git step happened; times, durations, statuses, exit codes, risk classes and categories, token
  counts, costs, model and tool names, line counts, languages, content hashes, run names, project and agent names, test counts and the
  analytics built from them. **A VIEWER cannot see**: inline payloads, artifact content (diffs, stdout, stderr), `shell.command`,
  `shell.cwd`, `file.path`, `git.repo`, `git.branch`, `git.push_target`, `http.url`, `test.failing`, the same-class attributes sent under
  an unregistered name (last segment `command`, `path`, `url` ...), and the names of shell, file and git spans: those values are replaced by
  `[withheld]` on the server (events, spans, streams), so the page cannot leak what the API did not send. Free text an integration puts in
  other fields (`span.name` of a custom span, `tool.operation`, `policy.reason`, `retry.reason`, `run.name`, tags, run `metadata`) is not
  classified as content; tracked as KI-078.
- Members are visible to every role of a workspace (`member.read` is in every role, including BILLING and VIEWER: names, emails and roles of the
  people one works with). If that is too open for an organisation, remove `member.read` from a role in `authz/matrix.py` and the literal table
  (one line in two places); tracked as KI-070.
- Audit (implemented, Phase 15, ADR-062): every mutating administrative action and a bounded number of denials by people go to an append-only
  `audit_log` the runtime role cannot UPDATE or DELETE; `details` never hold secrets, tokens or emails (validated, tested); readable by OWNER, ADMIN
  and SECURITY at `GET /v1/audit`. Purging it is a privileged owner job (`docs/runbooks/auth-and-access.md`).
- Unscoped lookups (deliberate, each by an unguessable value, then every query is tenant-scoped): `api_keys.key_id`, `sessions.token_hash`,
  `login_states.state_hash`, `invitations.token_hash` (at acceptance, before the workspace is known). `GET /v1/me` reads a person's memberships
  across workspaces; it is the only cross-workspace read.
- Bounds on what a member can create: 500 members, 200 active keys, 200 open invitations, 200 projects, 1,000 pricing overrides per workspace
  (`409 LIMIT_REACHED`); denial audit rows 10 a minute per person; web request bodies 64 KiB.
- Development conveniences exist only when `ABB_ENVIRONMENT` is `development` or `test`: an `http` issuer, the fake OIDC provider, `create-session`
  and the seeded `owner@local.test` (the last two also need `ABB_ALLOW_DEV_SESSIONS=1`), fixture mode refuses production unless
  `ABB_WEB_ALLOW_FIXTURES=1`. `ABB_ENVIRONMENT=production` is mandatory for any reachable deployment; the API warns at startup when OIDC is
  configured outside it.
- Local/demo credentials are generated, documented as dev-only, never real. `owner@local.test` and the E2E people (`*@auth.test`) are fake
  addresses that only the fake provider can authenticate.

## Known gaps (tracked in KNOWN_ISSUES.md)

Login and key-management endpoints are throttled only by the per-client/per-instance web limits and the API's global backstop; a real shared limiter,
quotas and failed-authentication throttling are Phase 19 (KI-017, KI-018, KI-019). The identity provider integration was exercised against the fake
provider only; a real provider login is an open acceptance check (KI-067). Session listing and "log out everywhere" are not in the UI (SQL/CLI
recovery is in the runbook, KI-068). Providers that omit `email_verified` cannot sign in (KI-069). Rate limits are per process. The Python SDK
redacts on the client (Phase 3: key rules, best-effort secret patterns, callback, payload modes; default drops payloads);
server-side redaction arrives in Phase 13, so **other clients are responsible for not sending secrets**, and inline payloads are stored as received.

## Process
Security-sensitive changes require a `review-change` pass and a note in the phase plan.
Incidents: severity scale and flow in spec §124; cross-tenant exposure is SEV-1.
