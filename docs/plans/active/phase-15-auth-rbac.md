# Phase 15 - Auth, workspaces and RBAC

Status: Planned and reviewed 2026-10-09; ready for implementation (user decisions 1-4 confirmed 2026-10-09; independent review `phase-15-plan-review.md` at 8cc8502 addressed below, see "Review dispositions")
Owner: implementer agent
Branch: `feature/phase-15-auth-rbac` (from `main` 85d4121; worktree `../abb-worktrees/phase-15`)
Depends on: Phase 2 (API keys, tenancy), Phase 4/5 (web, read proxy, streams), Phase 6 (artifacts), Phase 7 (pricing overrides)
Spec refs: §91 (auth, RBAC, central helper), §92 (API keys), §90 (threat model: credential compromise, cross-tenant access, sensitive telemetry), §93 (data classes: payloads and diffs are Class 2), §62 INV-1/3/5/7, §32 (security layer, context only)
ADRs: ADR-002 (structural tenancy), ADR-021 (shared web key; superseded by this phase), ADR-022 (streams), ADR-030 (artifact reads), ADR-040 (pricing overrides). New: ADR-060, ADR-061, ADR-062 (drafted in the appendix).
Migration ids: 0045, 0046, 0047 (chain 0044 -> 0045 -> 0046 -> 0047).
Known issues: closes KI-029 (S1), KI-027 (S2), KI-051 (S3), KI-033 (S3).

## Decisions confirmed by the user (2026-10-09, "go with your recommendation")

1. **Identity provider.** Generic OIDC (authorization code + PKCE, discovery document), so Google, Okta, Auth0, Keycloak, Entra all work with three settings; development and CI use a local fake provider, so no account or credential is needed until the final manual check. GitHub (OAuth2 without OIDC) is a later adapter if wanted. The real provider's client id/secret are only needed by the user, at the end, for one manual login.
2. **Sign-up model.** Invite-only. The first OWNER of a workspace is created by the CLI (`add-member`); everyone else joins through an invitation link created by an OWNER/ADMIN. A user who logs in with no membership sees "no workspaces" and can accept an invitation. Self-service "create a workspace on first login" is deferred.
3. **What a VIEWER sees.** Metadata only. Captured content (inline payloads, event payloads via the detail endpoint, artifacts such as diffs and shell output) needs `payload.read`, which VIEWER and BILLING do not have (spec §93: Class 2 content; §91 lists `payload.read` as its own permission). The run page still works for a VIEWER: panels say "content hidden by your role".
4. **BILLING and prices.** BILLING may read analytics and prices and write pricing overrides, but not read runs or payloads.

## Outcome

A person signs in to the dashboard with their organisation's identity provider, sees exactly the workspaces they belong to, switches
between them, and does only what their role allows. The web app no longer holds a shared `runs:read` key (KI-029): every browser request
carries that person's session and the API decides per request. OWNER/ADMIN manage members, invitations, API keys and pricing overrides
(KI-051) from the UI; every administrative action and (bounded) every denied request by a user is in a workspace audit log. API keys keep
working unchanged for SDKs. The suite proves, for every route the application serves, what each role and key kind may do and that nothing
from another workspace ever leaks; a new route without such a case fails CI.

## Non-goals

SSO/SAML, SCIM, MFA, password login, email delivery of invitations (the link is shown to the inviter; Phase 17 owns outbound
integrations), self-service sign-up, billing/plans, per-project roles (roles are per workspace), API-key rotation helpers, approval
decisions and policies (`approval.decide`, `policy.write`: Phase 14), retention (`retention.write`: Phase 19), Postgres RLS (§73.4,
Phase 19 defence in depth), a shared rate limiter (KI-017: Phase 19), GitHub OAuth, session listing/"log out everywhere" UI (only
logout of the current session; a CLI/SQL recovery path is in the runbook), user profile editing, trusting an IdP that does not emit
`email_verified` (a later `ABB_OIDC_TRUST_EMAIL` setting, not this phase), multiple issuers per deployment. Nothing here needs
Kafka/ClickHouse/Redis/Kubernetes.

## Current architecture (what exists; verified by reading the code)

- **Auth** (`apps/api/src/abb_api/auth/`): `require_principal(scope)` is a FastAPI dependency that parses `Authorization: Bearer
  abb_live_<key_id>.<secret>`, authenticates via the one unscoped lookup (`ApiKeyLookup.find`), writes `last_used_at` at most once a
  minute, calls `set_caller(workspace_id, key_id, project_id)` for logs and checks one scope. Scopes are the four strings in
  `auth/scopes.py`, a database CHECK constraint mirrors them. Rejections are one uniform `401 API_KEY_INVALID`; missing scope is
  `403 INSUFFICIENT_SCOPE` with `details.required_scope`; database trouble is `503 DEPENDENCY_UNAVAILABLE`. Keys are created only by the CLI.
- **Principal** (`tenancy.py`): `Principal(workspace_id, project_id | None, scopes, key_id)`; `principal.tenant` is the
  `TenantContext` every repository is built with. `principal.key_id` is also the per-key stream limit key (`streaming/limits.py`,
  `StreamService.open`). `cli.py` and `tenancy.py` legitimately mention scopes (`--scopes`, `has_scope`).
- **Project confinement** (`projects/access.py: authorise_project`): a project-bound key is confined to its project; a workspace-wide
  key may name any project of its workspace; anything else is `404 PROJECT_NOT_FOUND`. Called by analytics and pricing; `RunService._project_filter`
  duplicates the same rule (folded into `authorise_project` in step 4), and `RunQueries(conn, tenant, principal.project_id)` /
  `ArtifactRepository.get(id, project_id=principal.project_id)` apply the key's project to every lookup. Run/event/artifact ids of other tenants
  are 404s with identical bodies (tested in `test_runs_api.py`, `test_artifacts_api.py`, `test_stream_api.py`; `test_analytics_api.py` covers
  foreign project ids only, analytics carry no run ids as input).
- **Tables** (`db/tables.py`, migration 0002): `users (id, email, name, created_at; unique lower(email))`, `workspace_members
  (workspace_id, user_id, role; CHECK role IN the six roles)` and `api_keys.created_by -> users.id` already exist and are **unused**
  by any code path. No sessions, invitations or audit tables. Runtime role `abb_runtime` (0007) gets full DML on new tables through
  default privileges at creation; `events` is append-only and `test_runtime_role.py` asserts every other table is updatable.
- **Routes**: 17 `/v1` operations, pinned by `test_openapi.py`; `main.py` marks every `/v1` operation with the `bearerAuth` scheme; no
  route uses `include_in_schema=False`, no mounts, no websockets. Routers call `require_principal(scope)` at eight call sites; nothing
  compares roles anywhere. `CORSMiddleware` allows `GET` from `ABB_CORS_ORIGINS` without credentials.
- **Logging** (`core/logging.py`): `JsonFormatter` emits every `extra=` field and the `set_caller` identifiers on every line; the
  log-hygiene test pattern is `test_ingestion_api.py::test_logs_contain_neither_credentials_nor_event_content`.
- **Streams** (`streaming/service.py`): the frame loop times with `loop.time()`, not the injected `Clock`.
- **Web** (`apps/web`): browser code calls same-origin `GET /api/abb/<path>`; `src/server/upstream.ts` allowlists read paths by regex,
  attaches `ABB_WEB_API_KEY`, forwards no browser headers except `Last-Event-ID`, relays SSE, maps upstream 401/403 to `502 WEB_UPSTREAM_AUTH`,
  serves fixtures when `ABB_WEB_DATA_SOURCE=fixtures`; the route handler exports only `GET`. Routes are `/w/[workspace]/projects/[project]/...`;
  the workspace segment is a label and `all` means no project filter (`lib/routes.ts`, KI-027). There is no `proxy.ts`/`middleware.ts`, no
  session, no login page. CSP is `connect-src 'self'`; `Referrer-Policy: no-referrer`. `app/api/health/route.ts` calls the public `/readyz`.
- **Environment**: `ABB_ENVIRONMENT` defaults to `development` (`core/config.py`); `docker-compose.yml` and `.env.example` set it to
  `development` explicitly.
- **CLI** (`cli.py`): workspace/project/key provisioning, pricing overrides, rebuild, seed (writes `.local/dev-api-key`); records nothing.
- **Consumers of the shared read key**: `docker-compose.yml`, `.env.example`, `docs/development/setup.md`, `docs/runbooks/stream-issues.md`,
  ADR-021, `apps/web/playwright.config.ts` (`E2E_REAL_API_KEY`, `E2E_CODING_API_KEY` become `ABB_WEB_API_KEY` of the started web server),
  `apps/web/tests/upstream.test.ts`, `apps/web/tests/upstream-stream.test.ts`, `scripts/{e2e-web-real,coding-e2e,stream-e2e,analytics-e2e}.sh`.
  `scripts/smoke.sh`, `sdk-e2e.sh`, `integrations-e2e.sh` use project keys only (unaffected). `scripts/quality.sh full` does not run the E2E
  scripts; CI runs them as separate jobs.
- **Dependencies**: the API has no HTTP client or JWT library at runtime (`httpx` is dev-only). Nothing in `packages/sdk-python` or
  `integrations/` reads `details.required_scope`. The web has no auth library.
- **Tests**: `api_fixtures.py` builds two tenants (`acme` with projects alpha/beta, `globex`) and nine key variants.
- **UNVERIFIED (env)**: `node_modules` and `.venv` are not installed in this worktree, so the Next 16 `proxy.ts` file name and Starlette's
  implicit `HEAD` on `GET` routes could not be checked here. Both are verified at the start of step 1 (`pnpm install`, `uv sync`) and the result
  recorded in this plan.

## Known issues considered

- **KI-029 (S1)** pulled in: the web proxy forwards the visitor's session cookie instead of one key; `ABB_WEB_API_KEY` is removed
  everywhere (AC-10). Per-user stream limits replace the per-key cap for browsers.
- **KI-027 (S2)** pulled in: `GET /v1/me` (memberships with workspace slugs) and `GET /v1/projects` (id, slug, name); the web resolves
  both URL segments (AC-6). `all` stays the route form of "no project filter".
- **KI-051 (S3)** pulled in: `POST /v1/pricing/overrides` and `POST /v1/cost/rebuild` behind `pricing.write`, with a settings page (AC-7).
- **KI-033 (S3, target Phase 15)** pulled in: open streams re-check their credential and membership every `ABB_STREAM_REAUTH_SECONDS`
  (default 30) and end with `event: error` when it no longer holds, for keys and sessions (AC-9).
- **KI-018 (S1)** stays deferred (Phase 19 quotas). Phase 15 adds no client-controlled unbounded cardinality: per workspace at most 500
  members, 200 active keys, 200 open invitations and 200 projects (`409 LIMIT_REACHED`); `login_states` and `sessions` are purged
  opportunistically (D3, D2); denial audit rows are rate-bounded (D11).
- **KI-019 (S1, failed-auth throttling)** stays deferred to Phase 19 for the general case. Phase 15 defines the login limiter it needs (D15):
  per-client in the Next auth route handlers, a global backstop in the API; the API ignores `X-Forwarded-For`.
- **KI-050 (S2)**: unrelated (vendor prices), stays an MVP-gate item.
- **KI-055, KI-056, KI-016, KI-022, KI-017, KI-026, KI-021, KI-024, KI-040, KI-041, KI-042 (S2)**: unrelated, stay deferred as listed.
- **KI-031, KI-023, KI-013, KI-030, KI-032, KI-035, KI-043, KI-044, KI-060..063 (S3)**: unrelated.

## Decisions (D1..D17; ADR drafts in the appendix)

- **D1 (ADR-060) The API owns identity and sessions; the web is a relay.** OIDC authorization-code + PKCE is implemented in `abb_api.auth.oidc`
  against the provider's discovery document (`ABB_OIDC_ISSUER`, `ABB_OIDC_CLIENT_ID`, optional `ABB_OIDC_CLIENT_SECRET`). The API sets and
  reads the session cookie; the Next.js server forwards the browser's cookie to the API and relays `Set-Cookie` back. No shared secret between
  web and API exists: the user's session is the credential, and the API never trusts the web server's network position (no internal bypass; a
  reachable API port is not a hole). Rationale: authorization is application-owned (§91), one enforcement point, the Python suite covers it
  against a real database, and the web stays free of business logic.
- **D2 (ADR-060) Sessions are opaque, hashed, database-backed.** Cookie = 32 random bytes (url-safe); the `sessions` row stores `sha256(token)`,
  absolute expiry (`ABB_SESSION_ABSOLUTE_HOURS`, default 168) and idle expiry (`ABB_SESSION_IDLE_HOURS`, default 24, slid at most once per
  5 min by ordinary requests; stream re-validation does **not** slide it). Cookie name `__Host-abb_session` with `HttpOnly; Secure; SameSite=Lax;
  Path=/` when `ABB_WEB_ORIGIN` is https; `abb_session` without `Secure` only for an `http://localhost` origin in development/test (one constant
  decides). Logout is `POST /v1/auth/logout`: revokes the row, clears the cookie with the same attributes, returns 200 even if the cookie was
  already invalid. No GET route has side effects (Lax still sends the cookie on top-level cross-site GETs). Each login purges expired/revoked
  session rows of that user and up to 100 globally expired rows (bounded `DELETE ... WHERE id IN (SELECT ... LIMIT 100)`). Database trouble
  during session validation stays `503 DEPENDENCY_UNAVAILABLE`, never 401. No signed/stateless tokens: revocation and role changes must be immediate.
- **D3 (ADR-060) Login state lives in the database** (`login_states`: `sha256(state)`, nonce, PKCE verifier, validated `return_to`, 10 min expiry),
  referenced by an `abb_login` cookie (`HttpOnly; SameSite=Lax; Path=/api/auth`, `Secure` as above) carrying `state`. The callback requires
  `cookie.abb_login == query.state` (constant-time) **and** a matching unexpired row; the row is deleted and the cookie cleared on every outcome
  (login CSRF: a victim following an attacker's callback URL has no matching cookie -> 400, no session). Each insert also deletes up to 100 expired
  rows. `return_to` is validated against an allowlist of the app's own routes (`^/(w|invite)(/[A-Za-z0-9._~-]{1,64}){0,6}/?(\?[A-Za-z0-9=&._~-]{0,256})?$`;
  anything with `\`, `%`, `@`, `#` or control characters is refused; default `/`); the callback answers 302 with the stored validated string. IdP
  errors on the callback (`error`, `error_description`) map to the fixed code `LOGIN_FAILED`; the description is neither echoed nor logged verbatim.
- **D4 (ADR-060) Token and provider validation.** Discovery: the document's `issuer` must equal `ABB_OIDC_ISSUER`; `authorization_endpoint`,
  `token_endpoint`, `jwks_uri` must be `https` on the issuer's host (the fake provider is the development/test exception, D5). The ID token is
  verified with `pyjwt` and the provider's JWKS (`algorithms=["RS256", "ES256"]` explicit; `jku`/`jwk` headers ignored; `iss`, `aud` = client id,
  `azp` = client id when present, `exp`, `nonce`, 60 s skew). JWKS cached per issuer for 1 h; an unknown `kid` triggers one refetch per 60 s
  at most, then the token is refused (a wrapper around `pyjwt`'s key fetching driven by the injected `Clock`, so tests control it). `email_verified`
  must be present and true: absent -> refused and logged `email_verified_missing` with the provider name (fail closed; AC-14 records the claims the
  real provider sent). **Identity linking**: match `(provider, subject)` first; the verified-email fallback applies **only** to a user whose
  `provider_subject IS NULL` (pre-provisioned by invitation or CLI), which then records the subject. A verified email matching a user with a
  different subject is refused (`401`, logged `identity_conflict`, no row changed); re-linking after an issuer change is the audited CLI command
  `relink-user --email --clear-subject`. `provider` is the issuer URL. New runtime dependencies: `httpx` (already in the lockfile as dev; used
  with `trust_env=False`, `follow_redirects=False`, 5 s timeouts, logging at WARNING so query strings and token bodies are never logged) and
  `pyjwt[crypto]` (five questions in the ADR; both pinned by `uv.lock`, covered by the existing `pip-audit` of the API lockfile). Alternatives
  rejected: `authlib` (larger surface), skipping signature checks because the token arrives over the back channel (OIDC Core §3.1.3.7 allows it;
  weaker and harder to review).
- **D5 (ADR-060) A fake OIDC provider for development and tests.** `apps/api/tests/fake_oidc.py` is a small Starlette app (discovery,
  `authorize` page that accepts any email, `token` with PKCE check, `jwks`, `userinfo`, signing with a per-process key, knobs to emit a bad
  nonce/signature/aud/issuer/unverified email) used through an injectable `httpx` transport in pytest (never mounted in `create_app`) and
  served by `scripts/fake-oidc.sh` on :8900 for `make dev` and the Playwright login spec. An `http://` issuer is accepted only when
  `ABB_ENVIRONMENT in {development, test}`. No real IdP, account or secret is needed before the final manual check (AC-14).
- **D6 (ADR-061) One authorization helper, matrix as data.** `abb_api/authz/`: `actions.py` (vocabulary below), `matrix.py` (`ROLE_ACTIONS`,
  `SCOPE_ACTIONS`), `service.py` (`authorize(actor, action, resource=None)`; raises `PermissionDenied`), `dependencies.py` (`require(action)`
  replaces `require_principal(scope)`), `principal.py` (`Principal` moves here from `tenancy.py`). `Principal` is the one actor type for keys and
  users: `kind` (`api_key`|`user`), `workspace_id`, `project_id` (None for users), `actions` (derived from scopes or role at authentication time),
  `actor_id` (`key:<key_id>` or `user:<usr_id>`, safe to log, the stream-limit key: 10 streams per user across tabs and devices), `user_id`,
  `session_id`. Routers and services never look at roles or scopes; they ask for actions. An AST-based test (`tests/authz/test_no_scattered_checks.py`)
  fails on any attribute access to `.role`/`.scopes`/`.actions` on a principal or membership object outside `authz/`, `auth/`, `cli.py` and the
  membership repository.
- **D7 (ADR-061) Error shape by actor kind, 404 for the wrong workspace.** A key lacking an action keeps `403 INSUFFICIENT_SCOPE` with
  `details.required_scope` **unchanged** (pinned by a test) plus `required_permission`; a user lacking an action gets `403 PERMISSION_DENIED`
  (`details.required_permission`). A workspace the actor cannot see, or an id from another workspace, is always `404` (never 403). Missing or
  invalid session: `401 SESSION_INVALID` (uniform). A session token in a bearer header, or a key in the cookie, is 401. If both a bearer header
  and a cookie are present, the bearer header is the credential and the cookie is ignored. Permission checks run **before** any lookup
  (`require(action)` is a dependency), so a VIEWER gets 403 for every artifact-content id and cross-workspace probing is unchanged.
- **D8 (ADR-061) Workspace selection for users is the `X-ABB-Workspace` header** (public workspace id), sent by the browser client from the
  page's resolved workspace, so tabs are independent. Missing on a workspace route by a user: `400 WORKSPACE_REQUIRED`; not a member:
  `404 WORKSPACE_NOT_FOUND`. For API keys the header must be absent or equal the key's workspace (else 404). `/v1/me`, `/v1/auth/*` and
  `/v1/invitations/accept` are the only user routes without a workspace; accept takes the workspace from the invitation row and refuses a header
  naming a different one (404). `GET /v1/me` is the one deliberate cross-workspace read (the caller's own memberships, in `auth/`, not a tenant
  repository). Rejected: a path prefix (breaks every route and client) and a "current workspace" in the session (breaks multi-tab).
- **D9 (ADR-061) Keys and users share the enforcement path but not the vocabulary.** Sessions never obtain `event.write`/`artifact.write`
  (people do not ingest); keys never obtain any member, invitation, key-management or audit action (§92). Keys get `workspace.read` (their own
  workspace row) and `project.read` (project keys: their project only) so SDK tooling can resolve names. `runs:read` keeps implying `payload.read`
  and `artifact.read` for compatibility (documented in api-v1.md). A user may create only keys whose implied actions are a subset of their own
  (true for every role holding `api_key.create` today; the rule is enforced and tested so a future matrix change cannot mint more than the creator has).
- **D10 Sensitive content needs `payload.read`.** Event detail for an actor without it returns the event with `payload: null` and the additive
  field `payload_withheld: true`; `GET /v1/artifacts/{id}/content` is `403 PERMISSION_DENIED` before any lookup; `GET /v1/artifacts/{id}`
  (metadata) stays readable. Streams never carry payloads already.
- **D11 (ADR-062) Audit log is append-only, workspace-scoped and bounded.** `audit_log (workspace_id, id)` with `actor_kind` (`user`|`api_key`|`cli`,
  CHECK), `actor_id`, `action`, `resource_kind`, `resource_id`, `outcome` (`allowed`|`denied`, CHECK), `details` (JSONB, validated shape, never a
  secret, `CHECK (pg_column_size(details) < 8192)`), `request_id`, `occurred_at`. Recorded: every mutating action by anyone (members, invitations,
  keys, projects, pricing, cost rebuild; CLI as `actor_kind=cli`) and denials of **user** actors subject to a per-actor token bucket (an
  `InMemoryRateLimiter` keyed by `actor_id`, bounded number of keys, default 10 rows/min per actor; beyond it the denial is a log line only).
  Key denials are logged structurally, not audited. The audit insert runs in its own short transaction after the request's; a failed audit write is
  logged and never changes the response (a 403 stays a 403). Reads are not audited. `abb_runtime` gets SELECT and INSERT only (same mechanism as
  `events`); sign-in/sign-out are user-level and go to structured logs.
- **D12 Invitations are one-time links bound to an email.** `invitations (workspace_id, id)` with `email` (stored lower-cased), `role` (CHECK),
  `sha256(token)` unique, `invited_by`, 7-day expiry, `accepted_at/by`, `revoked_at`; partial unique index on `(workspace_id, email) WHERE
  accepted_at IS NULL AND revoked_at IS NULL` (`409 ALREADY_INVITED`). The link `${ABB_WEB_ORIGIN}/invite#<token>` carries the token in the
  **fragment**: it never reaches server or proxy logs; the page reads `location.hash`, clears it with `history.replaceState`, and `POST`s the token.
  Only `POST /v1/invitations` returns the link, once; `InvitationOut` has no token field. Accepting requires a signed-in user whose verified email
  equals the invitation email at acceptance time; an existing membership is refused (`409 ALREADY_MEMBER`; role changes go through
  `PATCH /v1/members`), so acceptance can never change an OWNER. Inviting as OWNER needs `member.write_owner`.
- **D13 Web route protection is layered.** `src/proxy.ts` (Next 16's request interceptor; ASSUMED name, verified at step 1, fallback
  `middleware.ts`) redirects `/w/*` and `/invite` without a session cookie to `/login` (cheap fast path, no trust). The real gate is the
  `w/[workspace]` server layout calling `GET /v1/me` with the forwarded cookie: 401 -> redirect to `/login?return_to=`, unknown slug -> 404 page.
  `/v1/me` returns, per membership, the effective `permissions` list, so React never encodes the matrix: it shows or hides by
  `permissions.includes("api_key.create")`, and the API remains the authority (a stale UI gets a clean 403 banner).
- **D14 Scripts and CI use sessions, not keys.** A CLI command `create-session --email <e> --hours 1`, available only when
  `ABB_ENVIRONMENT in {development, test}` **and** `ABB_ALLOW_DEV_SESSIONS=1`, mints a session for E2E scripts and Playwright (`E2E_SESSION_TOKEN`,
  set via `context.addCookies`); `make seed` also adds an OWNER membership for `owner@local.test` (same gate). One Playwright spec logs in through
  the real browser flow against the fake provider (`scripts/auth-e2e.sh`, CI job `auth-e2e`). The web keeps an `ABB_WEB_API_KEY` fallback
  (cookie first, then key, with a deprecation warning) from step 13 until step 15 removes it, so CI's E2E jobs stay green on every commit.
- **D15 Login rate limiting.** The API never sees client addresses (every login arrives through the Next relay) and ignores `X-Forwarded-For`/
  `Forwarded` entirely in this phase (no `ABB_TRUSTED_PROXY_CIDRS` yet). The per-client limit therefore lives in the Next route handlers
  `/api/auth/login` and `/api/auth/callback` (token bucket keyed by the client address Next sees, default 10/min, bounded map), and the API keeps a
  **global** bucket on `/v1/auth/login` + `/callback` sized above the Next limit times the expected number of web instances (default 600/min) as a
  backstop that one client cannot exhaust on its own. The proxy strips every client-supplied `X-Forwarded-*`/`Forwarded` header.
- **D16 Environment gating.** Every unsafe convenience (`create-session`, `seed`'s dev owner, `http://` issuer, fixture user, the fake provider in
  compose) is gated on `ABB_ENVIRONMENT in {"development", "test"}`, never on `!= production`; `create-session` and the dev owner additionally
  need `ABB_ALLOW_DEV_SESSIONS=1`. The API logs a WARNING at startup when OIDC is configured and the environment is not `production`. The
  runbook states that `ABB_ENVIRONMENT=production` is mandatory for any reachable deployment; `.env.example` and compose keep `development` but
  gain the comment.
- **D17 Headers between proxy and API are an allowlist.** The Next proxy forwards exactly: the session cookie (and `abb_login` on the auth
  routes), `X-ABB-Workspace`, `Origin`, `Last-Event-ID`, `Accept`, `Content-Type` for JSON bodies (<= 64 KiB, else 413); everything else is
  dropped, in particular `Authorization`, other cookies, `X-Forwarded-*`, `Forwarded`, `X-Request-ID`. Auth route handlers use
  `redirect: "manual"` and relay `Location` and every `Set-Cookie` (`headers.getSetCookie()`); the generic `/api/abb` proxy never relays
  `Set-Cookie` and never exposes `/v1/auth/*`. CSRF: cookie-authenticated requests with a method other than GET/HEAD must carry an `Origin`
  exactly equal (scheme, host, port) to `ABB_WEB_ORIGIN`; `null` or missing is `403 CSRF_REJECTED`; the proxy applies the same rule first.
  `Sec-Fetch-Site` is not relied on. CORS stays `allow_credentials=False` and `ABB_CORS_ORIGINS` is not widened.

### Action vocabulary and matrix (data in `authz/matrix.py`; the test file holds a second, literal copy)

| Action | OWNER | ADMIN | DEVELOPER | VIEWER | SECURITY | BILLING | Key scope |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `workspace.read`, `project.read` | x | x | x | x | x | x | any scope (project keys: own project; `workspace.read` = own workspace row) |
| `member.read` (emails and roles; visible to every role, noted in SECURITY.md) | x | x | x | x | x | x | - |
| `run.read`, `analytics.read` | x | x | x | x | x | - | `runs:read` |
| `payload.read`, `artifact.read` | x | x | x | - | x | - | `runs:read` |
| `pricing.read` | x | x | x | x | x | x | `runs:read` |
| `event.write`, `run.write` (create) | - | - | - | - | - | - | `events:write` (project key) |
| `artifact.write` | - | - | - | - | - | - | `artifacts:write` (project key) |
| `project.write` (bounded: 200 projects) | x | x | - | - | - | - | - |
| `api_key.read` | x | x | x | - | x | - | - |
| `api_key.create` (implied actions must be a subset of the creator's) | x | x | x | - | - | - | - |
| `api_key.revoke` | x | x | own | - | x | - | - |
| `member.write` (change/remove members whose current and new role are not OWNER) | x | x | - | - | - | - | - |
| `member.write_owner` (any change where the current **or** new role is OWNER, incl. inviting as OWNER) | x | - | - | - | - | - | - |
| `invite.read`, `invite.write` (create, list, revoke invitations) | x | x | - | - | - | - | - |
| `pricing.write` (overrides, cost rebuild) | x | x | - | - | - | x | - |
| `audit.read` | x | x | - | - | x | - | - |
| `billing.read` (placeholder, no endpoint yet) | x | x | - | - | - | x | - |

`own` = resource condition `created_by == actor.user_id` (`Own(api_key.revoke)` in the matrix, evaluated by `authorize()` from the `resource`
argument; keys made by the CLI have `created_by NULL`, so a DEVELOPER cannot revoke them, intended). Self-changes: a member may remove
themselves or lower their own role under the same rules (an OWNER demoting themselves needs another OWNER to remain). The last-owner rule
(`409 LAST_OWNER`) is checked and written in one transaction holding `SELECT ... FROM workspace_members WHERE workspace_id = ? AND role = 'OWNER'
FOR UPDATE` (concurrency test: two simultaneous removals of the only two owners leave at least one). `policy.*`, `approval.*`, `retention.*` are
reserved names (Phases 14, 19), listed in `actions.py` and granted to nobody. Data invariants tested: no `Own(...)` for scopes, no write action
for any role, no admin action for any scope.

## Proposed design

### API

```text
auth/         keys.py, repository.py (+ SessionRepository, LoginStateRepository, UserRepository), service.py (authenticate_key, authenticate_session),
              oidc.py (discovery validation, authorize URL, code exchange, id-token verification, JWKS cache; injectable transport + clock),
              cookies.py, router.py (GET /v1/auth/login, GET /v1/auth/callback, POST /v1/auth/logout, GET /v1/me), keys_router.py
              (GET /v1/api-keys, POST /v1/api-keys returns the token once, DELETE /v1/api-keys/{key_id})
authz/        actions.py, matrix.py, principal.py, service.py (authorize, PermissionDenied), dependencies.py (require(action), workspace header,
              CSRF origin rule), audit.py (bounded denial audit)
workspaces/   provisioning.py (moved from workspaces.py), router.py (GET/POST /v1/projects, GET /v1/members, PATCH/DELETE /v1/members/{user_id},
              POST/GET /v1/invitations, DELETE /v1/invitations/{id}, POST /v1/invitations/accept), repository.py (members, invitations; locks)
cost/router:  POST /v1/pricing/overrides, POST /v1/cost/rebuild (bounded like the CLI, enqueues summarize jobs)
audit/        router.py (GET /v1/audit: cursor paged, newest first, optional `since`), repository.py (append + page)
```

- `require(action)`: resolves the credential (bearer -> key path unchanged; else cookie -> session row -> user -> membership for the
  `X-ABB-Workspace` workspace), builds the `Principal` with `actions`, calls `set_caller(actor_id=..., session_id=<first 8 chars>)` (never the
  email), applies the CSRF rule for cookie actors, then `authorize(principal, action)`. Membership is read on **every request** (one indexed read on
  the composite primary key), so a role change or removal takes effect on the next request.
- `authorize(actor, action, resource)`: `action in actor.actions` or an `Own(...)` grant whose condition the resource satisfies; otherwise raise
  `PermissionDenied` and, for user actors within the bucket, append a `denied` audit row in its own short transaction.
- Streams: `StreamService.open` limits by `actor_id`; the frame loop re-validates every `ABB_STREAM_REAUTH_SECONDS` using the loop clock it already
  uses (`loop.time()`, a small setting value in tests, no injected-clock refactor) by re-running the credential check (key revoked/expired; session
  revoked/expired; membership still grants `run.read`) and ends the stream with `event: error`.
- OpenAPI: add `sessionCookie` (`apiKey` in cookie) and the `X-ABB-Workspace` header parameter; each operation lists the schemes that can call it
  (ingestion and artifact upload: bearer only; member/invitation/key/audit routes: cookie only; reads: both). `test_openapi.py` updates its surface
  set and per-operation security assertion.
- Settings (all `ABB_*`): `oidc_issuer`, `oidc_client_id`, `oidc_client_secret` (optional), `web_origin` (required when OIDC is set; cookie
  attributes and `redirect_uri` derive from it), `session_absolute_hours`, `session_idle_hours`, `stream_reauth_seconds`, `login_global_per_minute`,
  `audit_denials_per_minute`, `allow_dev_sessions`. With OIDC unset, `/v1/auth/login` answers `503 AUTH_NOT_CONFIGURED`; keys keep working.
- CLI: `add-member --workspace --email --role`, `remove-member`, `list-members`, `relink-user --email --clear-subject`, `create-session`
  (gated, D14/D16); existing mutating commands write audit rows as `actor_kind=cli`. `seed` adds the dev owner (gated).
- Logging: the OIDC client logs only `iss` and `kid` on failure, never `code`, `state`, `id_token`, `access_token`, the client secret or a
  description from the IdP; the log-hygiene test is extended to the login flow and to audit `details` (no keys named `token`, `secret`, `cookie`,
  `authorization`, `code`, `state`).

### Migrations

- **0045** `users`: add `provider`, `provider_subject` (unique pair, NULLs allowed for pre-provisioned users), `email_verified_at`,
  `last_login_at`; new `sessions` (`id`, `user_id`, `token_hash` unique, `created_at`, `last_seen_at`, `expires_at`, `idle_expires_at`,
  `revoked_at`) and `login_states` (`state_hash` pk, `nonce`, `code_verifier`, `return_to`, `expires_at`). Both are user-level, deliberately not
  tenant-keyed (documented in ADR-060 next to `api_keys.key_id` as the unscoped lookups). Changing `ABB_OIDC_ISSUER` orphans every linked subject
  (`relink-user` is the path).
- **0046** `invitations` (`workspace_id, id` pk, FK to workspaces, `email`, `role` CHECK, `token_hash` unique, `invited_by`, `accepted_by`
  (FKs to users), `expires_at`, `accepted_at`, `revoked_at`, the partial unique index of D12); `workspace_members` gains `updated_at`, `invited_by`.
- **0047** `audit_log` (`workspace_id, id` pk, columns and CHECKs per D11, index `(workspace_id, occurred_at DESC, id)`); `REVOKE UPDATE, DELETE,
  TRUNCATE ON audit_log FROM abb_runtime` **after** `CREATE TABLE` in the same migration (default privileges grant at creation); the downgrade
  drops the table only. The migration module exports `APPEND_ONLY_TABLES = ("events", "audit_log")`; `test_runtime_role.py` imports it, asserts
  those have no UPDATE/DELETE/TRUNCATE and do have SELECT+INSERT, and that every other table is writable, so a future table cannot join the
  exception set silently. All three are additive, no backfill, no lock risk; existing `api_keys.created_by` stays NULL.

### Web

- `src/server/session.ts` (`getSession()` for server components: forwards the cookie to `/v1/me`), `src/server/upstream.ts` (header allowlist
  per D17, new paths with the same strict segment regex `[A-Za-z0-9_-]{1,64}` as today: `me`, `projects`, `members/{id}`, `invitations[/{id}|/accept]`,
  `api-keys[/{id}]`, `pricing/overrides`, `cost/rebuild`, `audit`; methods GET/POST/PATCH/DELETE per path; Origin check for non-GET; relay 401 as
  401 so the client redirects; keep the SSE relay; cookie first, key fallback until step 15), `src/app/api/auth/{login,callback,logout}/route.ts`
  (manual redirects, cookie relay, per-client limiter), `src/proxy.ts`.
- Pages: `/login`, `/invite` (fragment token), `/w/[workspace]/settings/{members,api-keys,pricing,audit}`; `/w/[workspace]` lists projects; the
  project layout resolves slugs via `/v1/projects` (KI-027) and provides `{workspaceId, role, permissions, projects}` through a `WorkspaceProvider`
  context; the API client sets `X-ABB-Workspace` from it. `AppShell` gains a workspace switcher (memberships from `/v1/me`) and a user menu with a
  logout form (POST).
- Fixture mode serves a fixture user (`OWNER` of workspace `default`), gated like every fixture path (production refuses without `ABB_WEB_ALLOW_FIXTURES=1`).
- No business logic in components: visibility by `permissions`, validation by the API (422 shown as field errors), roles and scopes from `/v1/me`
  and the OpenAPI enum.

### The authorization test generator (`apps/api/tests/authz/`)

- `registry.py`: `CASES: dict[(METHOD, path_template), RouteCase]`. A `RouteCase` knows the `action` the route needs, how to build a valid
  request against the seeded fixtures (`build(ctx, tenant)`, using ids seeded in **both** tenants), the `transport` (`asgi` | `socket` for SSE),
  the success statuses, and `id_params` (which path/query parameters are tenant-owned ids).
- `test_registry_is_complete.py` (**fail-closed**): walks `app.routes` recursively (`APIRoute`, `Route`, `Mount`, `WebSocketRoute`) of
  `create_app()`; every `(method, path)` for every method in `route.methods` (including the implicit `HEAD` on GET routes) must be in `CASES` or in
  the literal `PUBLIC = {GET /healthz, GET /readyz, GET /docs, GET /openapi.json, GET /redoc, GET /docs/oauth2-redirect}`; any `Mount` or
  `WebSocketRoute` fails unless listed; `include_in_schema=False` does not exempt a route. A second check diffs `CASES` against
  `openapi.build_document()` both ways. The test also asserts `len(CASES)` equals the enumerated route count, and the final count is written into
  this plan at completion (AC-2).
- `test_role_matrix.py`: parametrized over `CASES x ACTORS`, actors = six roles (sessions in `acme`), `no_membership`, `removed_member`,
  `downgraded` (OWNER -> VIEWER after login), `expired_session`, `revoked_session`, `no_credential`, `session_as_bearer`, `key_as_cookie`, and the nine
  key variants. Expected outcome = `EXPECTED[actor][route]` from a **literal table in the test file**, every cell one of `allow|deny|401|404` (a
  missing cell fails); a separate test asserts the literal table equals the code's matrix in **both directions**. `HEAD` runs for every GET case with
  the same expectation and must carry the same `Content-Length` as the GET it mirrors; a non-preflight `OPTIONS` must be 405.
- `test_cross_workspace.py`: for every case and every `id_param`, the request is replayed with (a) the id of the identically-seeded resource in
  `globex` and (b) a random well-formed id: both must be 404 with byte-identical bodies (modulo `request_id`), for 2xx and error paths alike. Every
  `globex` row carries the canary `CANARY-GLOBEX-7f3e` in a text field (project names and slugs, run names/agent slugs, key names, member emails,
  invitation emails, override notes, audit details, artifact names and content; **not** the workspace name/slug, which a dual member legitimately
  sees in `/v1/me`); no response body, error body or SSE frame (two polls) to an `acme` actor may contain the canary. Numeric and id-only leaks:
  `globex` is seeded with distinctive magnitudes (exactly 7 runs costing 777.777 each) and acme's analytics totals must equal the acme-only
  expectation; every id in a list response (`run_id`, `user_id`, `resource_id`, key ids) must be absent from the globex seed set. A user who is a
  member of both workspaces with `X-ABB-Workspace: acme` passes the same assertions; `X-ABB-Workspace: <globex>` from an acme-only user is 404.
- Negative cases outside the matrix (`test_auth_sessions.py`, `test_members_api.py`): CSRF (foreign/missing/`null` Origin -> 403),
  `return_to` outside the allowlist, `state` in the query without or with a different cookie, replayed `state`, wrong `nonce`, unverified or
  missing `email_verified`, tampered signature, wrong `aud`/`azp`/`iss`, discovery `issuer` mismatch, unknown `kid` after one refetch, identity
  conflict (C-1), expired login state, DB down during session validation (503), logout idempotence, last-owner protection incl. the concurrency
  test, self-changes, invitation reuse/expiry/wrong email/already-member/already-invited, stream re-auth after revocation (socket test, small
  `stream_reauth_seconds`), the audit trail of a denied request and its rate bound, `payload_withheld` for VIEWER, key creation beyond the
  creator's actions (422), the 201st project/key/invitation and 501st member (409).

### Affected files

- API: `auth/*` (routers, oidc, cookies, repositories), `authz/*` (new), `workspaces/*` (new module; `workspaces.py` moves into it), `audit/*`
  (new), `cost/router.py`, `runs/router.py` + `runs/service.py` (`require`, `payload_withheld`, `_project_filter` folded), `artifacts/router.py` +
  `service.py`, `streaming/service.py` + `router.py` + `limits.py`, `ingestion/router.py`, `analytics/router.py`, `tenancy.py`, `core/config.py`,
  `core/logging.py` (httpx logger level), `main.py` (`_install_openapi`, routers, OIDC client and limiters on `app.state`, startup warning),
  `cli.py`, `db/tables.py`, `migrations/versions/0045..0047`, `openapi.json`, `pyproject.toml`/`uv.lock` (`httpx`, `pyjwt[crypto]`), tests
  (`authz/`, `fake_oidc.py`, `test_auth_sessions.py`, `test_members_api.py`, `test_api_keys_api.py`, `test_pricing_api.py`, `test_audit_api.py`,
  `test_openapi.py`, `test_runtime_role.py`, `test_stream_api.py`, `test_cli.py`, `api_fixtures.py`, log-hygiene extensions).
- Web: `src/server/upstream.ts`, `src/server/session.ts`, `src/server/loginLimiter.ts`, `src/proxy.ts`, `src/app/api/auth/*`, `src/app/login`,
  `src/app/invite`, `src/app/w/[workspace]/{layout,page}.tsx`, `settings/*`, `src/components/{AppShell,WorkspaceSwitcher,Members,ApiKeys,
  PricingOverrides,AuditLog}.tsx`, `src/lib/api/client.ts` (header), `src/lib/api/schema.d.ts` (regenerated), `src/lib/routes.ts`, `src/fixtures/*`
  (fixture user), tests (`upstream*.test.ts` rewritten, auth route handler tests) and `e2e/auth.spec.ts`, `playwright.config.ts`.
- Scripts/CI: `scripts/fake-oidc.sh`, `scripts/auth-e2e.sh`, `scripts/{e2e-web-real,coding-e2e,stream-e2e,analytics-e2e}.sh`,
  `.github/workflows/ci.yml` (`auth-e2e` job), `docker-compose.yml` (`ABB_OIDC_*`, `ABB_WEB_ORIGIN`, `ABB_ENVIRONMENT` comment, optional
  `fake-oidc` service under the `app` profile, dev only), `.env.example`, `Makefile` (`auth-e2e`, `fake-oidc`).
- Docs: ADR-060/061/062 + `DECISIONS.md`, `SECURITY.md`, `architecture/api-v1.md`, `development/setup.md`, `TESTING.md`, `KNOWN_ISSUES.md`,
  `PROJECT_STATE.md`, `ARCHITECTURE.md`, `runbooks/stream-issues.md` (key references), new `runbooks/auth-and-access.md`, ADR-021 marked superseded,
  `docs/screenshots/phase-15/`.

## Acceptance criteria (each command-checkable)

- **AC-1 Fail-closed generator.** `cd apps/api && uv run pytest tests/authz/test_registry_is_complete.py -q` passes; adding a dummy
  `@router.get("/v1/probe", include_in_schema=False)` without a `RouteCase` makes it fail naming `GET /v1/probe` (evidence: the failure output
  recorded in this plan, then reverted).
- **AC-2 Role matrix.** `uv run pytest tests/authz/test_role_matrix.py -q` passes with no skips; the test asserts the case count equals the
  enumerated route count and the plan records the final numbers (`N routes x 22 actors = M tests`); `test_literal_table_matches_the_code_matrix`
  (both directions) and the data-invariant tests pass.
- **AC-3 No scattered checks.** `rg -n "require_principal\(" apps/api/src` -> no output; `uv run pytest tests/authz/test_no_scattered_checks.py -q`
  (AST scan for `.role`/`.scopes`/`.actions` attribute access outside `authz/`, `auth/`, `cli.py`, `workspaces/repository.py`) passes.
- **AC-4 Cross-workspace.** `uv run pytest tests/authz/test_cross_workspace.py -q` passes: identical 404s for foreign and random ids on every
  id-bearing route (2xx and error paths), no canary in any body or SSE frame, analytics magnitudes and id sets equal the acme-only expectation.
- **AC-5 Sessions.** `uv run pytest tests/test_auth_sessions.py -q` passes with every negative case listed above (state/cookie binding, identity
  conflict, discovery and token validation, open redirect, CSRF, expiry, revocation, downgrade, removal, DB-down 503, logout idempotence).
- **AC-6 Lookup (KI-027).** `curl -s -H "Cookie: abb_session=..." -H "X-ABB-Workspace: ws_..." localhost:8000/v1/projects` lists `{id, slug, name}`;
  the web resolves `/w/<slug>/projects/<slug>` (Playwright `auth.spec.ts`).
- **AC-7 Admin API (KI-051).** Members, invitations (link returned once, `InvitationOut` without token), API keys (token shown once), pricing
  overrides and cost rebuild work through the API and the settings pages; a created key ingests events; a revoked key is 401 on the next request;
  the bounds (500/200/200/200) return 409. `uv run pytest tests/test_members_api.py tests/test_api_keys_api.py tests/test_pricing_api.py -q`.
- **AC-8 Audit.** Every mutating admin action (API and CLI) produces exactly one `audit_log` row; a single user denial produces one row and a
  flood produces at most `audit_denials_per_minute` rows plus log lines; `GET /v1/audit` pages them; `test_runtime_role.py` proves `abb_runtime`
  can INSERT/SELECT but not UPDATE/DELETE/TRUNCATE `audit_log`; the log-hygiene test proves no token, secret, code, state or cookie reaches logs or
  `details`. Checkable after step 9.
- **AC-9 Stream re-auth (KI-033).** Socket test: a key revoked (and a session revoked, and a membership removed) while a stream is open ends the
  stream with `event: error` within `stream_reauth_seconds`; 11 streams from one user are `429 STREAM_LIMIT` scoped `user`, another user is unaffected.
- **AC-10 Shared key gone (KI-029).** `rg -n "ABB_WEB_API_KEY|WEB_UPSTREAM_AUTH" apps scripts docker-compose.yml .env.example docs --glob
  '!docs/decisions/ADR-021*' --glob '!docs/plans/**'` -> no output (this covers `docs/runbooks/stream-issues.md`, `apps/web/tests/upstream*.test.ts`,
  `playwright.config.ts`, the four E2E scripts); vitest proves the proxy forwards exactly the D17 allowlist, strips `Authorization`/`X-Forwarded-*`,
  rejects non-GET without a same-origin `Origin`, and that the auth handlers relay `Location` and `Set-Cookie` without following redirects.
- **AC-11 Contract.** `scripts/quality.sh full` exit 0 (openapi.json current, `pnpm --filter @abb/web gen:api:check` clean, migrations 0045-0047
  up/down/up, `next build`); `make audit` clean with the two new runtime dependencies.
- **AC-12 Browser.** Playwright: `auth.spec.ts` logs in through the fake provider in a real browser, lands on `/w/local/projects/all`, switches
  workspace, opens settings as OWNER, is refused (clean 403 banner, no crash) as VIEWER, logs out and is redirected from `/w/...` to `/login`; axe
  clean on login and settings pages; screenshots in `docs/screenshots/phase-15/`. Existing `coding-e2e`, `stream-e2e`, `analytics-e2e`, `e2e-real`
  pass using sessions.
- **AC-13 Compatibility.** `make smoke`, `make sdk-e2e`, `make integrations-e2e` pass unchanged (API keys untouched; `INSUFFICIENT_SCOPE` details
  pinned by a test); `make seed` on a seeded database is idempotent and adds the dev owner only under the D16 gate; `make up` with the `app` profile
  gives the compose demo a login path through the dev-only fake provider.
- **AC-14 Real provider (user, last).** One manual login with a real OIDC provider using the user's own client id/secret, recording which claims
  the provider sent (`email_verified`, `azp`), documented as `VERIFIED` or `UNVERIFIED (env)` in this plan. Not a CI gate.
- **AC-15 Security review.** A `review-change` pass with the attack list below, findings fixed or filed as KI items.

## Verification plan

1. Per step: focused pytest/vitest, then `scripts/quality.sh quick`; `full` before each commit; the E2E scripts whenever the proxy or scripts change
   (steps 13-15), since `quality.sh` does not run them.
2. Mutation checks during review: change `matrix.py` entries; drop the workspace predicate in one repository method; skip the CSRF check; skip the
   nonce check; skip signature verification; remove the `cookie == query.state` comparison; allow email linking when `provider_subject` is set;
   forward `Authorization` in the proxy; drop the last-owner `FOR UPDATE` (caught by the concurrency test). Each must fail at least one test.
3. E2E: `make auth-e2e` (new), `make coding-e2e stream-e2e analytics-e2e e2e-real`, `make up && make smoke && make sdk-e2e` (containers; Compose
   was last rebuilt at Phase 2, so this also refreshes that evidence).
4. Browser: Playwright screenshots plus one manual run through `make dev` + `scripts/fake-oidc.sh`.
5. Environment: Docker via Colima, Postgres 5433, Node 22, Chrome for Playwright are present (setup.md). Step 1 starts with `pnpm install` and `uv sync`
   to settle the two UNVERIFIED (env) items. AC-14 is the only item that can end `UNVERIFIED (env)` (no provider credentials); it is the user's call.

## Security review note (mandatory adversarial pass)

Scenarios the reviewer must attempt, with the expected result in parentheses:

1. Replay a `state`/`code` pair, call the callback with a valid `state` but no or a different `abb_login` cookie, swap the `nonce`, present an ID
   token signed by another key, with `aud`/`azp` of another client, with an unverified or missing `email_verified`, or with an `iss` that differs
   from discovery; a discovery document whose `issuer` or endpoints differ from the configured issuer (all 400/401, no session, no user row created
   or changed).
2. A verified token for an email that belongs to a user already linked to a different subject (401 `identity_conflict`, row unchanged).
3. `return_to=https://evil`, `//evil`, `/\evil`, `/%2F%2Fevil`, `/w/x%0d%0a`, `javascript:` (refused at login; callback only redirects to the stored validated path).
4. CSRF: `POST /v1/api-keys` with a session cookie and a foreign, missing or `null` `Origin`, through the proxy and directly (403, no "allowed" audit row); `GET` to logout (405).
5. IDOR: every id-bearing route with ids from `globex` while a member of `acme` only (404, identical body); `X-ABB-Workspace` of a workspace the user left; a dual member with the header on one workspace and ids from the other (404); accept an invitation with a header naming another workspace (404).
6. Session fixation and theft: a session token in the URL or `Authorization` header (401), a cookie after logout (401), after the absolute lifetime with constant activity and an open stream (401), cookie attributes (`__Host-`, `HttpOnly`, `SameSite=Lax`, `Secure` in https), no `Set-Cookie` on error paths, no `Set-Cookie` relayed by the generic proxy.
7. Privilege: a DEVELOPER revoking a key they did not create (403) or a CLI-made key (403), creating a key with a scope outside the enum (422), an ADMIN granting OWNER, demoting or removing an OWNER, or inviting as OWNER (403), removing the last OWNER incl. two concurrent removals (409), accepting an invitation as an existing member (409), accepting another person's invitation or after an IdP email change (403), accepting twice (409), a duplicate open invitation for one email (409).
8. Keys: a key presented as a cookie, a session presented as a bearer (401), a key calling `GET /v1/members`, `/v1/invitations`, `/v1/api-keys`, `/v1/audit` (403 `INSUFFICIENT_SCOPE`), a key with `X-ABB-Workspace` of another workspace (404), a project key listing `/v1/projects` (its project only).
9. Content exposure: VIEWER fetching an event detail with a payload (`payload_withheld`), artifact content for an existing, missing and foreign id (403, 403, 403), stream frames (no payload); the audit `details` of a key creation and an invitation (no secret, no token); `/v1/me` of a dual member (no project canary).
10. Streams: revoke the credential mid-stream (ends within the re-auth interval); 11 streams from one user across two sessions (`429 STREAM_LIMIT` scope `user`, other users unaffected).
11. Bounds and floods: 501st member, 201st active key, 201st open invitation, 201st project (409); a 65 KiB JSON body through the proxy (413); 11 logins/min from one client through Next (429) while another client still logs in; the API global backstop; 1,000 denied requests by one VIEWER (at most `audit_denials_per_minute` audit rows per minute, 403 every time, never 500); `login_states` after 1,000 abandoned logins (bounded by purge).
12. Logs: no session token, state, code, ID token, access token, client secret, key secret, invitation token, email or IdP error description in any API log line, Next access log (fragment token) or audit row.
13. Fixture mode and dev conveniences: `create-session`, the dev owner, an `http://` issuer and the fixture user all refused when `ABB_ENVIRONMENT=production` **and** when it is `staging`; `create-session` refused in `development` without `ABB_ALLOW_DEV_SESSIONS=1`; the startup WARNING appears when OIDC is set outside production.
14. Headers: `X-Forwarded-For`, `Forwarded`, `X-Request-ID`, `Authorization` and a second cookie sent by the browser never reach the API through the proxy (vitest); `Sec-Fetch-Site` forged by curl changes nothing.

## Risks

1. **Scope and diff size.** Every router changes to `require(action)` and the web gains a session layer. Mitigation: step 1 is behaviour-preserving for keys (`required_scope` pinned) and lands with the full suite green; new endpoints come one commit each; the registry forces a case per route as they appear.
2. **OIDC subtleties** (state/cookie binding, nonce, PKCE, signature, JWKS rotation, skew, email matching, open redirect). Mitigation: fake provider with knobs for every bad case; negatives are acceptance; mutation checks in review.
3. **Cookie relay through Next.js** (Node `fetch` follows redirects by default; `Set-Cookie` on a 302; `__Host-` needs https; Next 16 `proxy.ts` name is ASSUMED). Mitigation: `redirect: "manual"` with vitest on the handlers; the Playwright login spec runs the real browser; attributes decided in one place from `ABB_WEB_ORIGIN`; verified at step 1.
4. **Tautological matrix tests.** Mitigation: the literal expected table with every cell required; two-way equality test; data-invariant tests.
5. **CI and demo breakage while the proxy switches from key to session.** Mitigation: step 13 keeps the key fallback, step 15 switches scripts and removes it in one commit, E2E scripts run on both; `make seed` and `create-session` give every script a session with no IdP.
6. **Lockout.** Last-owner rule under a lock, CLI `add-member`/`relink-user` as recovery (runbook), sessions re-read membership so a fix applies at once.
7. **Login endpoints are unauthenticated** (KI-019). Mitigation: D15 (per-client limit in Next, global backstop in the API, no spoofable address); Phase 19 owns the real limiter.
8. **Audit growth.** Denials are rate-bounded per actor and never block the response; retention is Phase 19's owner-role job (runbook).
9. **Runtime-role grants.** The audit table revokes in its own migration after creation and the role test imports the append-only set.
10. **Provider claim variance** (`email_verified` absent on Entra/some Okta setups): logins fail closed with a logged reason; the setting to trust such a provider is a named non-goal, decided after AC-14's findings.

## Ordered steps (one commit each; every step leaves `scripts/quality.sh full` and the CI E2E jobs green)

1. `feat(authz): action vocabulary, role/scope matrix, authorize() and require(action); routers switch from scopes to actions` - `Principal` moves to
   `authz/principal.py` and gains `kind`, `actions`, `actor_id`; key behaviour unchanged, `INSUFFICIENT_SCOPE.details.required_scope` pinned by a test;
   **plus** the `tests/authz` registry with cases for the 17 existing routes, the route-walking completeness test (incl. HEAD/OPTIONS), the key-actor
   half of the literal matrix, the AST no-scattered-checks test and the canary/magnitude cross-workspace test. Starts by recording the two
   UNVERIFIED (env) items (`proxy.ts`, implicit HEAD).
2. `feat(db): migration 0045 user identity columns, sessions and login_states; repositories` - with migration up/down tests and the unscoped-lookup note.
3. `feat(auth): OIDC login, callback, logout and /v1/me; sessions; fake OIDC provider` - settings, `httpx` + `pyjwt[crypto]` (ADR-060 five questions,
   `trust_env=False`), discovery/JWKS/token validation per D4, state/cookie binding and `return_to` allowlist per D3, CSRF rule per D17, global login
   backstop per D15, environment gating and startup warning per D16, `SESSION_INVALID`, session actors added to the matrix and canary tests, log
   hygiene extended to the login flow (`set_caller` without email, httpx logger at WARNING), `test_auth_sessions.py`.
4. `feat(workspaces): X-ABB-Workspace selection, GET/POST /v1/projects (KI-027), project bound` - `RunService._project_filter` folded into `authorise_project`.
5. `feat(db): migrations 0046 invitations/membership columns and 0047 append-only audit_log; audit repository; bounded denial audit; CLI actions audited`
   - `APPEND_ONLY_TABLES` exported from the migration and imported by `test_runtime_role.py`.
6. `feat(members): members and invitations endpoints` - last-owner rule under `FOR UPDATE` with a concurrency test, `member.write_owner` semantics,
   self-changes, bounds, `ALREADY_MEMBER`/`ALREADY_INVITED`, fragment link, `test_members_api.py`, registry cases.
7. `feat(keys): API key management endpoints` - token shown once, `Own(api_key.revoke)`, subset-of-creator rule, `test_api_keys_api.py`.
8. `feat(pricing): overrides and cost rebuild endpoints (KI-051)` - bounded like the CLI; registry cases.
9. `feat(audit): GET /v1/audit` - cursor paging newest first, optional `since`, `test_audit_api.py`.
10. `feat(runs,artifacts): payload.read enforcement (payload_withheld, artifact content 403 before lookup)` - additive schema field, openapi/TS client regenerated.
11. `feat(streaming): re-authenticate open streams periodically (KI-033); limits keyed by actor (per user)` - socket tests with a small `stream_reauth_seconds`.
12. `feat(cli): add-member, remove-member, list-members, relink-user, create-session (gated); seed adds a gated dev owner` - `test_cli.py`.
13. `feat(web): auth routes, login and invite pages, proxy header allowlist and cookie relay with ABB_WEB_API_KEY fallback, route protection, workspace and
    project resolution, switcher` - manual redirects, per-client login limiter, fixture user; vitest for the proxy, the auth handlers and the session helper;
    existing E2E scripts still pass on the key fallback (run them).
14. `feat(web): settings pages for members, API keys, pricing overrides and audit log` - permission-driven visibility; component tests for every state.
15. `chore(e2e): scripts and Playwright use sessions; auth-e2e with the fake provider; CI job; ABB_WEB_API_KEY fallback removed` - compose (`ABB_OIDC_*`,
    `ABB_WEB_ORIGIN`, dev-only `fake-oidc` service), `.env.example`, `upstream*.test.ts`, `stream-issues.md`, setup docs; all E2E jobs run in this commit.
16. `docs(phase-15): ADR-060/061/062, SECURITY (members visible to all roles, unscoped lookups), api-v1, TESTING, runbooks/auth-and-access.md (purge
    login_states/sessions, recover a workspace with no OWNER, revoke all sessions of a user, audit purge is an owner job, ABB_ENVIRONMENT=production is
    mandatory), KNOWN_ISSUES (KI-029/027/051/033 resolved), PROJECT_STATE, screenshots` - then `complete-phase`.

## Review dispositions

Review: `docs/plans/active/phase-15-plan-review.md` (commit 8cc8502). Every finding was re-checked against the code; all load-bearing facts the
reviewer cited (`core/config.py` default, `core/logging.py` extras, `streaming/service.py` `loop.time()`, `cli.py`/`tenancy.py` grep hits, no
`node_modules`/`.venv` in the worktree) were confirmed. Accepted 56, partially accepted 1 (H-2), noted without change 1 (H-3), rejected 0, deferred 0.

| Id | Sev | Disposition | Where it landed |
| --- | --- | --- | --- |
| A-1 | P3 | accepted | AC-3 is an AST test excluding `cli.py`/`tenancy.py`; `Principal` moves to `authz/principal.py` (D6) |
| A-2 | P3 | accepted | AC-10 names the files; step 15 rewrites them |
| A-3 | P3 | accepted | Current architecture: analytics covers foreign project ids only |
| A-4 | - | accepted | Marked UNVERIFIED (env) in Current architecture; settled at the start of step 1; HEAD in the registry test |
| B-1 | P1 | accepted | Matrix: keys get `workspace.read`/`project.read` only; `member.read`, `invite.*`, `api_key.*`, `audit.read` are session-only (D9) |
| B-2 | P1 | accepted | Projects bounded at 200 (`409 LIMIT_REACHED`), listed with the other bounds (Known issues, matrix, AC-7, step 4) |
| B-3 | P1 | accepted (option a) | D12: acceptance by an existing member is `409 ALREADY_MEMBER`; inviting as OWNER needs `member.write_owner` |
| B-4 | P2 | accepted | Matrix notes: `FOR UPDATE` on the owner rows, concurrency test; mutation check; step 6 |
| B-5 | P2 | accepted | Matrix: `member.write_owner` = any change where current or new role is OWNER; self-change rules stated |
| B-6 | P2 | accepted | `invite.read`/`invite.write` in the matrix; `InvitationOut` has no token; AC-7 |
| B-7 | P2 | accepted | D12: token in the URL fragment, `history.replaceState`; review item 12 covers the Next access log |
| B-8 | P2 | accepted | D8/D12: accept takes the workspace from the invitation, refuses a differing header; email compared at acceptance time |
| B-9 | P3 | accepted | D7/D10: `require(payload.read)` before any lookup; review item 9 |
| B-10 | P3 | accepted | Matrix row and step 16 (SECURITY.md note) |
| B-11 | P3 | accepted | D9: subset-of-creator rule, tested (step 7) |
| B-12 | P3 | accepted | Per user (`user:<id>`, 10 streams across sessions); AC-9 and review item 10 aligned |
| B-13 | P3 | accepted | Canary marks project names/slugs, never the workspace; stated in the generator section |
| C-1 | P1 | accepted | D4: email fallback only when `provider_subject IS NULL`; `identity_conflict`; `relink-user` CLI; AC-5, review item 2 |
| C-2 | P1 | accepted | D3: `cookie.abb_login == query.state` constant-time, cookie `Path=/api/auth`, cleared on every outcome; AC-5, review item 1 |
| C-3 | P1 | accepted | D15: per-client limit in the Next auth handlers, global API backstop, `X-Forwarded-*` ignored and stripped (D17) |
| C-4 | P1 | accepted | D16: gate on `{development, test}`, `ABB_ALLOW_DEV_SESSIONS=1`, startup warning, runbook; review item 13 |
| C-5 | P2 | accepted | D4: JWKS cache 1 h, one refetch per 60 s on unknown `kid`, explicit algorithms, `jku`/`jwk` ignored, clock-driven wrapper |
| C-6 | P2 | accepted | D4: discovery `issuer` equality, https endpoints on the issuer host, `aud` and `azp` pinned |
| C-7 | P2 | accepted | D3: route allowlist regex, refused characters; review item 3 |
| C-8 | P2 | accepted | D4: absent claim refused and logged; `ABB_OIDC_TRUST_EMAIL` is a named non-goal; AC-14 records claims |
| C-9 | P2 | accepted | D2 (`__Host-`, `POST` logout, idempotent, same attributes), D17 (exact Origin, `null` rejected, `Sec-Fetch-Site` not relied on) |
| C-10 | P2 | accepted | D2/D3: bounded purges of `login_states` and `sessions` on login; review item 11 |
| C-11 | P3 | accepted | D2: streams do not slide the idle timer |
| C-12 | P3 | accepted | D2 and AC-5: DB down stays 503 |
| C-13 | P3 | accepted | D3: `LOGIN_FAILED`, description neither echoed nor logged |
| D-1 | P2 | accepted | D17 and step 13: `redirect: "manual"`, relay `Location` and `getSetCookie()`, vitest; risk 3 |
| D-2 | P2 | accepted | D17 header allowlist; AC-10; review item 14 |
| D-3 | P2 | accepted | Web section: strict segment regex per new path; `/v1/auth/*` only via the dedicated handlers, generic proxy never relays `Set-Cookie` |
| D-4 | P3 | accepted | D1 and ADR-060: no trust in the web server's network position |
| D-5 | P3 | accepted | D17: `allow_credentials=False`, `ABB_CORS_ORIGINS` unchanged |
| E-1 | P1 | accepted (option c) | D11: per-actor token bucket on denial audits, log beyond it, audit failure never changes the response; AC-8; (b) not chosen because coalescing needs UPDATE on an append-only table |
| E-2 | P2 | accepted | Migrations 0045: `provider` is the issuer URL; issuer change orphans subjects; `relink-user` |
| E-3 | P2 | accepted | D12/0046: lower-cased email, role CHECK, partial unique index on open invitations |
| E-4 | P2 | accepted | D11/0047: CHECKs on `actor_kind`/`outcome`, `pg_column_size(details) < 8192`; log-hygiene key-name scan (AC-8) |
| E-5 | P2 | accepted | 0047 exports `APPEND_ONLY_TABLES`; `test_runtime_role.py` imports it and asserts SELECT+INSERT remain |
| E-6 | P2 | accepted | Migrations: REVOKE after CREATE in 0047, downgrade drops only; `created_by NULL` for existing keys documented in the matrix notes |
| E-7 | P3 | accepted | D8 (`/v1/me` is the one deliberate cross-workspace read) and ADR-060 (unscoped lookups listed) |
| F-1 | P1 | accepted | Generator: walks `app.routes` incl. mounts/websockets/`include_in_schema=False`/HEAD with a literal `PUBLIC` set; OpenAPI diff is the second check; AC-1 uses `include_in_schema=False` for the probe; fake provider never in `create_app` (D5) |
| F-2 | P2 | accepted | Generator: HEAD with equal `Content-Length`, non-preflight OPTIONS 405 |
| F-3 | P2 | accepted | Generator: distinctive magnitudes (7 runs x 777.777), id-set assertions, error bodies and two SSE polls scanned |
| F-4 | P2 | accepted | Generator: every cell required, two-way equality, data invariants (no `Own` for scopes, no writes for roles, no admin for scopes) |
| F-5 | P2 | accepted | AC-2: the test asserts the count; final numbers recorded in the plan at completion |
| F-6 | P3 | accepted | Streams keep `loop.time()` with a small test setting (API section, step 11) |
| F-7 | P3 | accepted | AC-8 reworded (one row per single action, bounded under a flood) |
| F-8 | P3 | accepted | Verification plan item 2 lists the four extra mutants |
| G-1 | P2 | accepted | D14 and steps 13/15: key fallback kept in step 13, removed with the script switch in step 15; E2E scripts run on both; risk 5 reworded |
| G-2 | P2 | accepted | D7 and step 1: `required_scope` unchanged and pinned by a test |
| G-3 | P2 | accepted | Step 3 and the Logging paragraph: `set_caller` without email, OIDC client logs `iss`/`kid` only, httpx at WARNING, hygiene test extended |
| G-4 | P2 | accepted | Step 15 and AC-13: compose gets `ABB_OIDC_*`, `ABB_WEB_ORIGIN`, the environment comment and a dev-only `fake-oidc` service |
| G-5 | P3 | accepted | AC-8 marked "checkable after step 9" |
| G-6 | P3 | accepted | Step 16: `runbooks/auth-and-access.md` with the listed procedures |
| H-1 | P2 | accepted | D4: `trust_env=False`, `follow_redirects=False`, lockfile pins, existing `pip-audit`; AC-11 includes `make audit` |
| H-2 | P3 | partially accepted | `GET /v1/audit` keeps only cursor paging and `since` (actor/action filters dropped); `POST /v1/cost/rebuild` is kept because without it an override never applies to past runs from the UI, which is what KI-051 asks for |
| H-3 | P3 | noted | No change: naming stays `member.write` (plan vocabulary), rotation and RLS remain non-goals as the review agrees |

## Appendix: ADR drafts (to be written with `write-adr` at step 16; numbers follow the 020/030/040/050 per-phase convention)

### ADR-060: Dashboard identity via OIDC with API-owned, database-backed sessions (supersedes ADR-021)

**Context.** ADR-021 gave the web server one shared `runs:read` key (KI-029, S1). Spec §91: users authenticate through a standard identity
provider; authorization is application-owned. The Next.js server and the FastAPI API must trust each other without a shared secret in the browser.

**Decision.** The API implements OIDC authorization code + PKCE against a discovery document (`ABB_OIDC_ISSUER`, `ABB_OIDC_CLIENT_ID`, optional
secret) whose `issuer` must equal the configured one and whose endpoints must be https on the issuer's host. It issues an opaque session cookie
(`__Host-abb_session`, 32 random bytes, `sha256` stored, absolute 7 d / idle 24 h, revocable, purged opportunistically; `HttpOnly`, `SameSite=Lax`,
`Secure`). Login state (state, nonce, PKCE verifier, validated return path from a route allowlist) is a database row referenced by a 10-minute
`abb_login` cookie that the callback must match exactly, so no signing secret exists and login CSRF fails. ID tokens are verified against the
provider's JWKS (explicit `RS256`/`ES256`, cached 1 h, one refetch per minute on an unknown `kid`; `iss`, `aud`, `azp`, `exp`, `nonce`);
`email_verified` is required and present; identity matches `(provider, subject)` first and falls back to verified email **only** for users with
no subject yet (an `identity_conflict` is refused; re-linking is an audited CLI command). The Next.js server forwards only an allowlisted set of
headers (session cookie, `X-ABB-Workspace`, `Origin`, `Last-Event-ID`, `Accept`, `Content-Type`) and relays `Set-Cookie` without following
redirects; cookie-authenticated non-GET requests must carry an `Origin` equal to `ABB_WEB_ORIGIN` (checked by proxy and API); the API ignores
`X-Forwarded-*` and never trusts the web server's network position. Login rate limiting is per client in the Next handlers plus a global backstop in
the API. The unscoped lookups of the system are now `api_keys.key_id`, `sessions.token_hash` and `login_states.state_hash`; `GET /v1/me` is the one
deliberate read of a user's memberships across workspaces. A fake OIDC provider (`apps/api/tests/fake_oidc.py`, `scripts/fake-oidc.sh`) serves tests,
`make dev`, compose (dev profile) and the Playwright login spec; `http://` issuers, dev sessions (`create-session`, also needing
`ABB_ALLOW_DEV_SESSIONS=1`) and the seeded owner are available only when `ABB_ENVIRONMENT` is `development` or `test`; the API warns at startup when
OIDC is configured outside `production`.

**Dependencies (five questions, plus pinning).** `httpx`: already in the lockfile (dev), maintained, small surface, used with `trust_env=False`,
`follow_redirects=False` and 5 s timeouts; stdlib `urllib` would need its own async wrapper; no lock-in. `pyjwt[crypto]`: JWKS/RS256/ES256
verification is not something to hand-roll; maintained; the `cryptography` wheel is the only transitive weight; alternatives `authlib` (larger) and
skipping signature checks (OIDC Core §3.1.3.7 permits it over the back channel; rejected as weaker and harder to review). Both pinned in `uv.lock`
and covered by the API's `pip-audit` in `make audit`.

**Alternatives.** Auth.js in Next (identity and sessions outside the API that enforces them; two sources of truth; untestable from the Python suite).
Stateless signed JWT sessions (no immediate revocation; key management). A shared proxy secret (does not identify the user). GitHub OAuth first
(no ID token or verified-email contract; an adapter later).

**Consequences.** The web app can be exposed with a login; every request is attributable to a person; revocation and role changes are immediate;
two unauthenticated endpoints exist (login, callback) with the limits above until Phase 19's shared limiter; a real provider is configured with
three settings; providers that do not emit `email_verified` cannot log in until a later explicit trust setting; changing the issuer requires re-linking.

### ADR-061: One authorization helper; keys and users share the enforcement path; the matrix is data

**Context.** Spec §91 forbids scattered role checks and asks for `authorize(actor, action, resource)`. API keys with scopes already exist (§92) and
SDK keys must not administer a workspace. Another tenant's resources must stay indistinguishable from nonexistent ones (ADR-002).

**Decision.** A single `Principal` (`authz/principal.py`) represents both keys and users (`kind`, `workspace_id`, `project_id`, `actions`,
`actor_id`). Actions are dotted strings granted by role (`ROLE_ACTIONS`) or scope (`SCOPE_ACTIONS`) as data; `Own(action)` expresses resource-owner
conditions. Routers declare `require(action)` (checked before any lookup); services call `authorize()` for resource conditions; nothing else inspects
roles or scopes (an AST test enforces it). Users select a workspace with `X-ABB-Workspace`; keys are bound to theirs. Denied: 403
(`INSUFFICIENT_SCOPE` with the unchanged `required_scope` for keys, `PERMISSION_DENIED` for users, both with `required_permission`); unseen workspace
or foreign id: 404; invalid credential: 401 (`API_KEY_INVALID` / `SESSION_INVALID`). Sessions never get write scopes; keys never get member,
invitation, key-management or audit actions and see only their own workspace row and project(s); `runs:read` keeps implying `payload.read` and
`artifact.read` for compatibility; a user may only create keys whose implied actions are a subset of their own. VIEWER and BILLING lack `payload.read`:
event payloads are withheld (`payload_withheld`), artifact content is 403. `member.write_owner` covers any membership change whose current or new role
is OWNER; the last-owner rule runs under a row lock. Every route the application serves (walked from `app.routes`, including HEAD) must have an
authorization case in `tests/authz/registry.py`; a completeness test fails otherwise, and a literal expected table in the tests mirrors the code matrix
in both directions. Per-workspace bounds: 500 members, 200 active keys, 200 open invitations, 200 projects.

**Alternatives.** Per-router role checks (what the spec forbids). Separate principal types for keys and users (two enforcement paths, twice the
tests). Workspace in the URL (breaks every existing route). Permissions stored per user in the database (flexible, not needed, harder to review).

**Consequences.** Adding a permission is a one-line matrix change plus a test-table change; adding a route without an authorization case is a CI
failure; the web renders by the `permissions` list from `/v1/me` and never encodes the matrix.

### ADR-062: Append-only, bounded workspace audit log

**Context.** Spec §91/§124 expect administrative actions to be auditable; INV-1 and the `events` precedent show how to make a table immutable for the
runtime role. An append-only table the runtime cannot purge must not be a write path any member can grow without limit.

**Decision.** `audit_log (workspace_id, id)` records every mutating administrative action (API, UI and CLI) and, bounded by a per-actor token bucket,
denied requests by user actors, with actor kind/id, action, resource, outcome, validated `details` (names, roles, scopes; never secrets; at most 8 KiB),
request id and time. The runtime role has SELECT and INSERT only (`APPEND_ONLY_TABLES` is exported by the migration and asserted by the role test).
Audit writes run in their own transaction after the request's and can never change the response. Sign-in/out are user-level and go to structured
logs in this phase. `GET /v1/audit` (`audit.read`) pages the log newest first. Reads and API-key denials are not audited (volume; request logs cover them).

**Alternatives.** Reusing `events` (telemetry, not administration; different tenancy and retention). Logs only (not queryable per workspace, not
tamper-evident). Auditing every request or every denial unbounded (volume without value; disk-fill by any member). Coalescing denial rows with a
counter (needs UPDATE on an append-only table).

**Consequences.** Admin actions are attributable and immutable; retention of the audit log is Phase 19's privileged job (runbook); a flood of denials
is visible in logs and capped in the table; a future "who viewed what" requirement needs an explicit decision because reads are not recorded.

## Implementation notes (deviations and findings while building)

- **UNVERIFIED (env) items settled (step 1).** (a) Next 16.3.8 (`apps/web/node_modules/next`) defines `PROXY_FILENAME = 'proxy'` located at
  `(src/)?proxy`: the `proxy.ts` name is VERIFIED. (b) Starlette 1.7 / FastAPI 0.142 do **not** add an implicit `HEAD` to `APIRoute` GET routes:
  `HEAD /v1/runs` answers `405` before any dependency runs (VERIFIED by a request; only the plain Starlette `Route`s `/docs`, `/redoc`,
  `/openapi.json`, `/docs/oauth2-redirect` carry `HEAD`). The generator therefore asserts that HEAD and a non-preflight OPTIONS on every GET API route are
  `405` with no data (`test_head_and_options_serve_nothing_on_any_api_route`), and the literal `PUBLIC` set lists those four docs routes with both methods.
- **FastAPI 0.142 wraps included routers** in `_IncludedRouter`/`_EffectiveRouteContext`; `app.routes` no longer holds `APIRoute` objects directly. The route
  walker (`tests/authz/routes.py`) unwraps them and treats any shape it does not recognise (Mount, websocket, unknown) as a finding. It also reads each
  route's `Depends(require(action))` (the dependency exposes `required_action`), so the registry's `action` per case is checked against the code, not just
  its existence.
- **BILLING reads analytics (matrix table vs decision 4).** The matrix table row `run.read, analytics.read` shows BILLING as `-`, but decision 4 says BILLING
  may read analytics. The two actions are separate: BILLING holds `analytics.read` (and `pricing.read/write`, `billing.read`) but not `run.read`.
  Analytics responses contain run ids of the most expensive runs (existing behaviour); that is the accepted consequence of decision 4.
- **Artifact content requires `payload.read` already in step 1** (`ContentReader`): behaviour-preserving for keys (`runs:read` implies it), and step 10
  only has to add `payload_withheld` for event detail.
- **Registry shape.** The role-matrix test is parametrized per actor (13 key-kind actors in step 1, each looping over all 17 routes) instead of one pytest
  item per cell, because a seeded two-tenant world with a socket server costs about a second per test. The cell count is the product: step 1 =
  17 routes x 13 actors = 221 cells in 13 tests; later steps add the user actors.
- **`details` of `INSUFFICIENT_SCOPE`** gained `required_permission` (additive). The existing exact-equality assertions in `test_ingestion_api.py` and
  `test_auth_service.py` were updated to include it; `required_scope` is pinned in `test_role_matrix.py`. The log field `key_id` became `actor_id`
  (`key:<key_id>`), also asserted in the ingestion log test.
- **Artifact metadata needs `run.read`, not `artifact.read`.** D10 says a VIEWER can still read `GET /v1/artifacts/{id}` (metadata) while the matrix
  withholds `artifact.read` from VIEWER. The route demands `run.read` (held by VIEWER, and by `runs:read` keys, so key behaviour is unchanged); content
  demands `payload.read`. `artifact.read` stays in the vocabulary and the matrix for the roles that hold `payload.read` (future listing/download routes).
- **Step 2.** `IdKind` gained `USER = "usr"` and `INVITATION = "inv"` in `packages/event-schema` (public ids for people; they never appear in events,
  so no JSON Schema or generated type changed). The user/session/login-state repositories live in `auth/repository.py` as the plan says. `link_identity`
  carries `provider_subject IS NULL` in its `UPDATE ... WHERE`, so the "email never re-binds a linked user" rule holds even under a race, and a
  `CHECK ((provider IS NULL) = (provider_subject IS NULL))` keeps a half identity out of the table.
- **Step 3.**
  - *Workspace selection moved up from step 4:* a session actor cannot exist without a workspace, so `X-ABB-Workspace` handling (400 `WORKSPACE_REQUIRED`,
    404 `WORKSPACE_NOT_FOUND` for non-member/malformed/foreign, key header must equal its workspace) lives in `require(action)` from this step. Step 4 adds
    the project endpoints and folds `_project_filter`.
  - *No credential at all stays `401 API_KEY_INVALID`* (SDK compatibility); `SESSION_INVALID` is returned when a cookie is the credential. An
    `Authorization` header of any kind makes the request a key request; the cookie is then ignored (bearer wins).
  - *Discovery host rule:* endpoints must be https on the issuer's host **or** a host in the new optional `ABB_OIDC_EXTRA_HOSTS` (comma-separated). A strict
    same-host rule would make Google (token/JWKS endpoints on googleapis.com) impossible, and the plan lists Google; the default stays strict.
  - *Time checks use the injected clock:* PyJWT is called with `verify_exp/nbf/iat` off and `exp`/`nbf`/`iat` are checked against `Clock` with 60 s skew,
    so tests control time (the fake provider stamps tokens from the same clock).
  - *Unverified-email handling:* `email_verified` absent -> `LOGIN_FAILED` 401 logged `email_verified_missing`; `false` or a non-boolean such as the
    string `"true"` -> refused (`email_not_verified`), fail closed.
  - *Callback failures are JSON `400/401 LOGIN_FAILED`* (the web handler turns them into a redirect to `/login?error=`); the `abb_login` cookie is cleared on
    every outcome of the callback. A state/cookie mismatch does not consume the state row.
  - *Registry:* the sign-in routes are in the literal `PUBLIC` set (unauthenticated by nature) and `GET /v1/me` in a literal `SESSION_ONLY` set whose guard
    (`requires_session`) the walker checks; `/v1/me` has its own tests. `tests/authz` now runs 17 routes x 27 actors (13 key + 14 people) = 459 cells in 27
    tests, plus a dual-workspace actor in the canary tests.
  - *Session minting for tests* goes straight to the database (`tests/auth_helpers.py`); the CLI `create-session` stays in step 12.
  - *Startup warning* is emitted by `create_app` (the OIDC client factory) when OIDC is configured and `ABB_ENVIRONMENT != production`.
  - *Not yet done in step 3 (by design, later steps):* the stream re-check and the sliding opt-out for it (step 11), audit rows for denials (step 5).

### Steps 5-8 (audit, members and invitations, API keys, pricing writes)

- **Step 5 (0046, 0047, audit).**
  - `audit_log.id` is a `BIGINT GENERATED ALWAYS AS IDENTITY` (strictly increasing, so a cursor needs no tie-break); the index is plain
    `(workspace_id, occurred_at, id)` (Postgres scans it backwards for newest first; a `DESC` expression index would make `compare_metadata`
    report drift). `details` is also CHECKed to be a JSON object, and the text columns have length CHECKs.
  - The migration module `0047_audit_log.py` exports `APPEND_ONLY_TABLES = ("events", "audit_log")`; `test_runtime_role.py` loads it by file
    path (module names starting with digits cannot be imported normally), asserts those tables have no UPDATE/DELETE/TRUNCATE but keep
    SELECT+INSERT, and that every other table stays writable.
  - Denials are audited from `require()` (first stage) and from `authorize_audited()` (checks that need the loaded resource: `member.write_owner`,
    `Own(api_key.revoke)`). The denial row's `action` is the required permission; `details` hold only the HTTP method and the route
    *template*. Key denials are log lines only. The bucket is injectable (`app.state.denial_limiter`) so the flood test has an exact bound.
  - CLI: one row per mutating command (`workspace.create`, `project.create`, `api_key.create/revoke`, `pricing_override.create`, `cost.rebuild`, and
    the same for `seed`), in the command's own transaction, `actor_id = cli:<os user>`. `refresh-analytics` (derived state only) is not audited.
  - `details` are validated by `clean_details` (flat scalars/lists, <= 20 items, values <= 200 chars, no key naming a token, secret, cookie,
    authorization, password, credential, code or state); a violation is a programming error that tests catch.
- **Step 6 (members, invitations).**
  - Logic that inspects roles lives in `workspaces/members.py` and `authz/members.py`; `test_no_scattered_checks` allows `workspaces/members.py`
    next to `workspaces/repository.py` (routes live in `members_router.py`, which stays free of role access). The OWNER-involvement rule itself is
    in `authz/members.py` (`authorize_role_change`).
  - Last-owner rule: `SELECT ... WHERE role='OWNER' ORDER BY user_id FOR UPDATE` first, then the target row; verified by mutation (removing the
    `FOR UPDATE` makes `test_two_owners_removing_each_other_at_once_leave_one` fail).
  - Self-changes follow the same rules and no more: there is no self-service "leave" for roles without `member.write`.
  - Per-workspace bounds (open invitations, members at acceptance, active keys, overrides) serialise on `lock_workspace()`
    (`FOR NO KEY UPDATE` on the workspace row, so foreign-key checks of other inserts are not blocked).
  - Invitation outcomes: unknown or revoked token `404 INVITATION_NOT_FOUND`; reused `409 INVITATION_USED`; lapsed `410 INVITATION_EXPIRED`;
    other or unverified email `403 INVITATION_EMAIL_MISMATCH`; already a member `409 ALREADY_MEMBER`; a header naming another workspace
    `404 WORKSPACE_NOT_FOUND`. Lapsed open invitations are retired (marked revoked at their expiry) whenever someone invites, which frees the
    one-open-per-email slot and the 200 bound. Accepting is audited in the invitation's workspace as `invitation.accept` by the accepting user.
  - `POST /v1/invitations/accept` is `SESSION_ONLY` in the registry (like `/v1/me`): it has its own tests instead of a role-matrix row.
  - Unchanged role (`PATCH` to the current role) answers 200 and writes no audit row.
- **Step 7 (keys).**
  - D9 as written ("implied actions subset of the creator's") would forbid every person from creating an ingestion key, because no role holds
    `event.write`/`artifact.write`. The rule therefore excludes `INGESTION_ACTIONS`: `ungrantable_actions = scope_actions(scopes) - INGESTION_ACTIONS -
    actor.actions`; the end-to-end test patches the role matrix to prove the 422 `SCOPE_NOT_ALLOWED` path, since no current role can trigger it.
  - Ingestion scopes (`events:write`, `artifacts:write`) require a `project_id` (`422 PROJECT_REQUIRED`): workspace-wide keys cannot ingest anyway.
  - `require(action, owned=True)` lets a holder of only `Own(action)` through the dependency; the handler then loads the key and calls
    `authorize_audited(..., key)`. A key that is revoked, unknown, malformed or foreign is `404 KEY_NOT_FOUND` for everyone who reaches the handler.
  - The creation response carries `Cache-Control: no-store`. The list shows keys that are not revoked (active or expired), at most 500.
- **Step 8 (pricing writes).**
  - Overrides are capped at 1000 per workspace (`409 LIMIT_REACHED`): the table is append-only, so a bound was needed (not in the plan's list).
    Prices are `0..1,000,000` with at most 9 decimals and finite; `valid_from` must carry a timezone and lie between 1970 and 2100.
  - `POST /v1/cost/rebuild` takes `project_id`, `since`, `limit` (1..10,000, default 10,000), answers `202 {matched, queued, truncated}`; repeated calls
    are idempotent while jobs are pending (dedupe key). BILLING may call it although it cannot read runs: the response carries counts only.
