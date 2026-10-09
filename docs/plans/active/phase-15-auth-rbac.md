# Phase 15 - Auth, workspaces and RBAC

Status: Planned 2026-10-09 (awaiting the decisions below; "go with your recommendation" is enough to start)
Owner: implementer agent
Branch: `feature/phase-15-auth-rbac` (from `main` 85d4121; worktree `../abb-worktrees/phase-15`)
Depends on: Phase 2 (API keys, tenancy), Phase 4/5 (web, read proxy, streams), Phase 6 (artifacts), Phase 7 (pricing overrides)
Spec refs: §91 (auth, RBAC, central helper), §92 (API keys), §90 (threat model: credential compromise, cross-tenant access, sensitive telemetry), §93 (data classes: payloads and diffs are Class 2), §62 INV-1/3/5/7, §32 (security layer, context only)
ADRs: ADR-002 (structural tenancy), ADR-021 (shared web key; superseded by this phase), ADR-022 (streams), ADR-030 (artifact reads), ADR-040 (pricing overrides). New: ADR-060, ADR-061, ADR-062 (drafted in the appendix).
Migration ids: 0045, 0046, 0047 (chain 0044 -> 0045 -> 0046 -> 0047).
Known issues: closes KI-029 (S1), KI-027 (S2), KI-051 (S3), KI-033 (S3).

## Decisions needed from the user

Each has a default; saying "go with your recommendation" starts the work.

1. **Identity provider.** Recommend: generic OIDC (authorization code + PKCE, discovery document), so Google, Okta, Auth0, Keycloak, Entra all work with three settings; development and CI use a local fake provider, so no account or credential is needed until the final manual check. GitHub (OAuth2 without OIDC) is a later adapter if wanted. The real provider's client id/secret are only needed by you, at the end, for one manual login.
2. **Sign-up model.** Recommend: invite-only. The first OWNER of a workspace is created by the CLI (`add-member`); everyone else joins through an invitation link created by an OWNER/ADMIN. A user who logs in with no membership sees "no workspaces" and can accept an invitation. Self-service "create a workspace on first login" is deferred (product and abuse question).
3. **What a VIEWER sees.** Recommend: metadata only. Captured content (inline payloads, event payloads via the detail endpoint, artifacts such as diffs and shell output) needs `payload.read`, which VIEWER and BILLING do not have (spec §93: Class 2 content; §91 lists `payload.read` as its own permission). The run page still works for a VIEWER: panels say "content hidden by your role".
4. **BILLING and prices.** Recommend: BILLING may read analytics and prices and **write pricing overrides** (that is what the role is for), but not read runs or payloads.

## Outcome

A person signs in to the dashboard with their organisation's identity provider, sees exactly the workspaces they belong to, switches
between them, and does only what their role allows. The web app no longer holds a shared `runs:read` key (KI-029): every browser request
carries that person's session and the API decides per request. OWNER/ADMIN manage members, invitations, API keys and pricing overrides
(KI-051) from the UI; every administrative action and every denied request by a user is in a workspace audit log. API keys keep working
unchanged for SDKs. The suite proves, for every `/v1` operation in the OpenAPI document, what each role and key kind may do and that
nothing from another workspace ever leaks; a new endpoint without such a case fails CI.

## Non-goals

SSO/SAML, SCIM, MFA, password login, email delivery of invitations (the link is shown to the inviter; Phase 17 owns outbound
integrations), self-service sign-up, billing/plans, per-project roles (roles are per workspace), API-key rotation helpers, approval
decisions and policies (`approval.decide`, `policy.write`: Phase 14), retention (`retention.write`: Phase 19), Postgres RLS (§73.4,
Phase 19 defence in depth), failed-auth throttling and a shared limiter (KI-019/KI-017: Phase 19), GitHub OAuth, session listing/"log out
everywhere" UI (only logout of the current session), user profile editing. Nothing here needs Kafka/ClickHouse/Redis/Kubernetes.

## Current architecture (what exists; verified by reading the code)

- **Auth** (`apps/api/src/abb_api/auth/`): `require_principal(scope)` is a FastAPI dependency that parses `Authorization: Bearer
  abb_live_<key_id>.<secret>`, authenticates via the one unscoped lookup (`ApiKeyLookup.find`), writes `last_used_at` at most once a
  minute, calls `set_caller(workspace_id, key_id, project_id)` for logs and checks one scope. Scopes are the four strings in
  `auth/scopes.py`, a database CHECK constraint mirrors them. Rejections are one uniform `401 API_KEY_INVALID`; missing scope is
  `403 INSUFFICIENT_SCOPE` with `details.required_scope`. Keys are created only by the CLI.
- **Principal** (`tenancy.py`): `Principal(workspace_id, project_id | None, scopes, key_id)`; `principal.tenant` is the
  `TenantContext` every repository is built with. `principal.key_id` is also the per-key stream limit key (`streaming/limits.py`,
  `StreamService.open`).
- **Project confinement** (`projects/access.py: authorise_project`): a project-bound key is confined to its project; a workspace-wide
  key may name any project of its workspace; anything else is `404 PROJECT_NOT_FOUND`. Called by analytics and pricing; `RunService._project_filter`
  duplicates the same rule (to be folded into `authorise_project` in step 4), and `RunQueries(conn, tenant, principal.project_id)` /
  `ArtifactRepository.get(id, project_id=principal.project_id)` apply the key's project to every lookup. Run/event/artifact ids of other tenants
  are 404s with identical bodies (tested in `test_runs_api.py`, `test_artifacts_api.py`, `test_stream_api.py`, `test_analytics_api.py`).
- **Tables** (`db/tables.py`, migration 0002): `users (id, email, name, created_at; unique lower(email))`, `workspace_members
  (workspace_id, user_id, role; CHECK role IN the six roles)` and `api_keys.created_by -> users.id` already exist and are **unused**
  by any code path. No sessions, invitations or audit tables. Runtime role `abb_runtime` (0007) gets full DML on new tables through
  default privileges; `events` is append-only and `test_runtime_role.py` asserts every other table is updatable (an append-only table
  must be added to that test's exception set deliberately).
- **Routes** (`test_openapi.py` pins the surface, 17 `/v1` operations): ingestion (`POST /v1/events[/batch]`, `POST /v1/runs`), reads
  (`/v1/runs...`, `/stream`, `/v1/pricing`, `/v1/analytics/*`, artifacts), `PUT /v1/artifacts/{id}`. `main.py` marks every `/v1`
  operation with the `bearerAuth` security scheme. Routers call `require_principal(scope)` directly (eight call sites); no router
  or service compares roles anywhere.
- **Web** (`apps/web`): browser code calls same-origin `GET /api/abb/<path>`; `src/server/upstream.ts` allowlists read paths by regex,
  attaches `ABB_WEB_API_KEY`, relays SSE, maps upstream 401/403 to `502 WEB_UPSTREAM_AUTH`, serves fixtures when
  `ABB_WEB_DATA_SOURCE=fixtures`. Routes are `/w/[workspace]/projects/[project]/...`; the workspace segment is a label and `all`
  means no project filter (`lib/routes.ts`, KI-027). There is no `proxy.ts`/`middleware.ts`, no session, no login page. CSP is
  `connect-src 'self'` (the browser cannot reach the API directly). `AppShell` is a client component with navigation only.
- **CLI** (`cli.py`): workspace/project/key provisioning, pricing overrides, rebuild, seed (writes `.local/dev-api-key`). Nothing is
  recorded in the database about who did what.
- **Consumers of the shared read key**: `docker-compose.yml` (web service), `.env.example`, `docs/development/setup.md`, ADR-021,
  `apps/web/playwright.config.ts` (`E2E_REAL_API_KEY`, `E2E_CODING_API_KEY` become `ABB_WEB_API_KEY` of the started web server),
  `scripts/e2e-web-real.sh`, `scripts/coding-e2e.sh`, `scripts/stream-e2e.sh`, `scripts/analytics-e2e.sh` (each creates a
  workspace-wide `runs:read` key for the web). `scripts/smoke.sh`, `sdk-e2e.sh`, `integrations-e2e.sh` use project keys only (unaffected).
- **Dependencies**: the API has no HTTP client or JWT library at runtime (`httpx` is dev-only). The web has no auth library.
- **Tests**: `api_fixtures.py` builds two tenants (`acme` with projects alpha/beta, `globex`) and nine key variants (writer, reader,
  wide, wide_reader, ingest_only, beta, revoked, expired, other). Streams are tested over a real socket (`stream_fixtures.py`).

## Known issues considered

- **KI-029 (S1)** pulled in: the web proxy forwards the visitor's session cookie instead of one key; `ABB_WEB_API_KEY` is removed
  everywhere (AC-10). Per-session stream limits replace the per-key cap for browsers.
- **KI-027 (S2)** pulled in: `GET /v1/me` (memberships with workspace slugs) and `GET /v1/projects` (id, slug, name); the web resolves
  both URL segments (AC-6). `all` stays the route form of "no project filter".
- **KI-051 (S3)** pulled in: `POST /v1/pricing/overrides` and `POST /v1/cost/rebuild` behind `pricing.write`, with a settings page (AC-7).
- **KI-033 (S3, target Phase 15)** pulled in: open streams re-check their credential and membership every `ABB_STREAM_REAUTH_SECONDS`
  (default 30) and end with `event: error` when it no longer holds, for keys and sessions (AC-9).
- **KI-018 (S1)** stays deferred (Phase 19 quotas): unrelated to identity. Phase 15 adds no client-controlled unbounded cardinality:
  members and keys per workspace are bounded by validation (max 500 members, 200 active keys, 200 open invitations per workspace, 409 over).
- **KI-019 (S1, failed-auth throttling)** stays deferred to Phase 19, noted as a risk: session validation is one indexed lookup by
  token hash (same cost profile as key lookup); the OIDC callback does one IdP round trip per attempt but only for a `state` we issued
  (unknown state is rejected before any network call). Login endpoints get a per-process token bucket reusing `InMemoryRateLimiter`
  (cheap, bounded, not a substitute for Phase 19).
- **KI-050 (S2)**: unrelated (vendor prices), stays an MVP-gate item.
- **KI-055, KI-056, KI-016, KI-022, KI-017, KI-026, KI-021, KI-024, KI-040, KI-041, KI-042 (S2)**: unrelated to this phase, stay deferred as listed.
- **KI-031, KI-023, KI-013, KI-030, KI-032, KI-035, KI-043, KI-044, KI-060..063 (S3)**: unrelated.

## Decisions (D1..D14; ADR drafts in the appendix)

- **D1 (ADR-060) The API owns identity and sessions; the web is a relay.** OIDC authorization-code + PKCE is implemented in `abb_api.auth.oidc`
  against the provider's discovery document (`ABB_OIDC_ISSUER`, `ABB_OIDC_CLIENT_ID`, optional `ABB_OIDC_CLIENT_SECRET`). The API sets and
  reads the session cookie; the Next.js server forwards the browser's cookie to the API and relays `Set-Cookie` back. No shared secret between
  web and API is needed any more: the user's session is the credential. Rationale: authorization is application-owned (§91), one enforcement
  point, the Python suite covers it against a real database, and the web stays free of business logic.
- **D2 (ADR-060) Sessions are opaque, hashed, database-backed.** `abb_session` cookie = 32 random bytes (url-safe); the `sessions` row stores
  `sha256(token)`, absolute expiry (`ABB_SESSION_ABSOLUTE_HOURS`, default 168) and idle expiry (`ABB_SESSION_IDLE_HOURS`, default 24, slid at most
  once per 5 min). Logout revokes the row. Cookie attributes: `HttpOnly; SameSite=Lax; Path=/; Secure` when `ABB_WEB_ORIGIN` is https (the
  attribute is dropped for `http://localhost` in development only). No signed/stateless tokens: revocation and role changes must be immediate.
- **D3 (ADR-060) Login state lives in the database too** (`login_states`: `sha256(state)`, nonce, PKCE verifier, `return_to`, 10 min expiry, deleted on
  use), referenced by a short-lived `abb_login` cookie carrying `state`. No server-side signing secret exists anywhere (consistent with hashed keys).
  `return_to` must be a relative path starting with `/` and not `//` (open-redirect guard, tested).
- **D4 (ADR-060) Token validation.** The ID token is verified with the provider's JWKS (`RS256`/`ES256`, `iss`, `aud`, `exp`, `nonce`, 60 s skew),
  `email_verified` must be true, and identity is matched first by `(provider, subject)`, then by verified lower-cased email to link a user who was
  pre-provisioned by invitation or CLI (the subject is then recorded; a later email change at the IdP does not create a second user). New runtime
  dependencies: `httpx` (already in the lockfile as a dev dependency; every call has a 5 s timeout) and `pyjwt[crypto]` (five questions in the ADR).
  Alternatives rejected: `authlib` (larger surface), skipping signature checks because the token arrives over the back channel (allowed by OIDC Core
  §3.1.3.7 but weaker and harder to review).
- **D5 (ADR-060) A fake OIDC provider for development and tests.** `apps/api/tests/fake_oidc.py` is a small Starlette app (discovery, `authorize`
  page that accepts any email, `token` with PKCE check, `jwks`, `userinfo`, signing with a per-process key) mounted in-process for pytest (the API's
  OIDC client takes an injectable `httpx` transport) and served by `scripts/fake-oidc.sh` on :8900 for `make dev` and the Playwright login spec.
  The API refuses an `http://` issuer unless `ABB_ENVIRONMENT` is `development` or `test`. No real IdP, account or secret is needed before the final
  manual check (AC-14, performed by the user).
- **D6 (ADR-061) One authorization helper, matrix as data.** `abb_api/authz/`: `actions.py` (the vocabulary below), `matrix.py`
  (`ROLE_ACTIONS: dict[role, frozenset[Action]]`, `SCOPE_ACTIONS: dict[scope, frozenset[Action]]`), `service.py`
  (`authorize(actor, action, resource=None)`; raises `PermissionDenied`), `dependencies.py` (`require(action)` replaces `require_principal(scope)`).
  `Principal` becomes the one actor type for keys and users: `kind` (`api_key`|`user`), `workspace_id`, `project_id` (None for users), `actions`
  (derived from scopes or role at authentication time), `actor_id` (`key:<key_id>` or `user:<usr_id>`, safe to log, the stream-limit key),
  `user_id`, `session_id`. Routers and services never look at roles or scopes; they ask for actions. A test greps the source for `role ==`/`role in`
  outside `authz/` and fails on a hit (AC-3).
- **D7 (ADR-061) Error shape by actor kind, 404 for the wrong workspace.** A key lacking an action keeps `403 INSUFFICIENT_SCOPE`
  (`details.required_scope`, plus `required_permission`), so existing clients see no change; a user lacking an action gets `403 PERMISSION_DENIED`
  (`details.required_permission`). A workspace the actor cannot see, or an id from another workspace, is always `404` (never 403), as today. Missing
  or invalid session: `401 SESSION_INVALID` (uniform for missing, unknown, expired, revoked). If both a bearer header and a cookie are present, the
  bearer header is the credential and the cookie is ignored.
- **D8 (ADR-061) Workspace selection for users is the `X-ABB-Workspace` header** (public workspace id). Users may belong to several workspaces;
  the header is sent by the browser client from the page's resolved workspace, so tabs are independent. Missing header on a workspace route by a user:
  `400 WORKSPACE_REQUIRED`; not a member: `404 WORKSPACE_NOT_FOUND`. For API keys the header must be absent or equal the key's workspace (else 404).
  `/v1/me`, `/v1/auth/*` and `/v1/invitations/accept` are the only user routes without a workspace. Rejected: a path prefix (would break every
  existing route and client) and a "current workspace" stored in the session (breaks multi-tab).
- **D9 (ADR-061) Keys and users share the enforcement path but not the vocabulary.** Sessions never obtain `event.write`/`artifact.write`
  (people do not ingest); keys never obtain any admin action (§92: SDK keys cannot administer a workspace). For compatibility `runs:read` keeps
  implying `payload.read` and `artifact.read` (the E2E scripts and SDK readers rely on it; documented in api-v1.md).
- **D10 Sensitive content needs `payload.read`.** Event detail for an actor without it returns the event with `payload: null` and a new
  additive field `payload_withheld: true`; `GET /v1/artifacts/{id}/content` returns `403 PERMISSION_DENIED`; `GET /v1/artifacts/{id}` (metadata)
  stays readable. Streams never carry payloads already.
- **D11 (ADR-062) Audit log is append-only and workspace-scoped.** `audit_log (workspace_id, id)` with `actor_kind`, `actor_id`, `action`,
  `resource_kind`, `resource_id`, `outcome` (`allowed`|`denied`), `details` (JSONB, validated, never a secret, key name/scopes/role only),
  `request_id`, `occurred_at`. Recorded: every mutating action by anyone (members, invitations, keys, projects, pricing, cost rebuild; CLI too as
  `actor_kind=cli`) and every **denial of a user actor** (keys are not audited on denial: they can flood; they are logged structurally). Reads are not
  audited. `abb_runtime` gets no UPDATE/DELETE/TRUNCATE on it (same mechanism as `events`); `test_runtime_role.py` is updated deliberately.
  Sign-in/sign-out are user-level, not workspace-level, so they go to structured logs only in this phase.
- **D12 Invitations are one-time links bound to an email.** `invitations (workspace_id, id)` with `email`, `role`, `sha256(token)`, `invited_by`,
  7-day expiry, `accepted_at/by`, `revoked_at`. The link `${ABB_WEB_ORIGIN}/invite/<token>` is shown once to the inviter (no email sending).
  Accepting requires a signed-in user whose verified email matches (case-insensitive) and creates/updates the membership. Only an OWNER may invite
  or promote to OWNER; the last OWNER cannot be removed or downgraded (`409 LAST_OWNER`).
- **D13 Web route protection is layered.** `src/proxy.ts` (Next 16's request interceptor; ASSUMED to be the `proxy.ts` convention, verify
  against the installed version, fall back to `middleware.ts`) redirects `/w/*` and `/invite/*` without an `abb_session` cookie to `/login` (cheap
  fast path, no trust). The real gate is the `w/[workspace]` server layout calling `GET /v1/me` with the forwarded cookie: 401 -> redirect to
  `/login?return_to=`, unknown slug -> 404 page. `/v1/me` returns, per membership, the effective `permissions` list, so React never encodes the
  matrix: it shows or hides by `permissions.includes("api_key.create")`, and the API remains the authority (a stale UI gets a clean 403 banner).
- **D14 Scripts and CI use sessions, not keys.** A non-production CLI command `create-session --email <e> --hours 1` (refuses when
  `ABB_ENVIRONMENT=production`) mints a session for E2E scripts and Playwright (`E2E_SESSION_TOKEN`, set via `context.addCookies`); `make seed` also
  adds an OWNER membership for `owner@local.test`. One new Playwright spec logs in through the real browser flow against the fake provider
  (`scripts/auth-e2e.sh`, CI job `auth-e2e`), so the OIDC round trip is exercised end to end without any external account.

### Action vocabulary and matrix (data in `authz/matrix.py`; the test file holds a second, literal copy)

| Action | OWNER | ADMIN | DEVELOPER | VIEWER | SECURITY | BILLING | Key scope |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `workspace.read`, `project.read`, `member.read` | x | x | x | x | x | x | any scope (project keys: own project) |
| `run.read`, `analytics.read` | x | x | x | x | x | - | `runs:read` |
| `payload.read`, `artifact.read` | x | x | x | - | x | - | `runs:read` |
| `pricing.read` | x | x | x | x | x | x | `runs:read` |
| `event.write`, `run.write` (create) | - | - | - | - | - | - | `events:write` (project key) |
| `artifact.write` | - | - | - | - | - | - | `artifacts:write` (project key) |
| `project.write` | x | x | - | - | - | - | - |
| `api_key.read` | x | x | x | - | x | - | - |
| `api_key.create` | x | x | x | - | - | - | - |
| `api_key.revoke` | x | x | own | - | x | - | - |
| `member.write`, `invite.write` | x | x | - | - | - | - | - |
| `member.write_owner` (grant/revoke OWNER) | x | - | - | - | - | - | - |
| `pricing.write` (overrides, cost rebuild) | x | x | - | - | - | x | - |
| `audit.read` | x | x | - | - | x | - | - |
| `billing.read` (placeholder, no endpoint yet) | x | x | - | - | - | x | - |

`own` = resource condition `created_by == actor.user_id`, expressed in the matrix as `Own(api_key.revoke)` and evaluated by `authorize()` from the
`resource` argument. `policy.*`, `approval.*`, `retention.*` are reserved names (Phases 14, 19), listed in `actions.py` but granted to nobody.

## Proposed design

### API

```text
auth/         keys.py, repository.py (+ SessionRepository, LoginStateRepository, UserRepository), service.py (authenticate_key, authenticate_session),
              oidc.py (discovery, authorize URL, code exchange, id-token verification; injectable transport), cookies.py, router.py
              (/v1/auth/login, /v1/auth/callback, /v1/auth/logout, /v1/me)
authz/        actions.py, matrix.py, service.py (authorize, PermissionDenied), dependencies.py (require(action), workspace header), audit.py
workspaces/   router.py (GET /v1/projects, POST /v1/projects), members (GET /v1/members, PATCH/DELETE /v1/members/{user_id}),
              invitations (POST/GET /v1/invitations, DELETE /v1/invitations/{id}, POST /v1/invitations/accept), repository.py
auth/keys router: GET /v1/api-keys, POST /v1/api-keys (returns the token once), DELETE /v1/api-keys/{key_id}
cost/router:  POST /v1/pricing/overrides, POST /v1/cost/rebuild (bounded like the CLI, enqueues summarize jobs)
audit/        router.py (GET /v1/audit, cursor paged, filters: actor_id, action, since/until), repository.py (append + page)
```

- `require(action)`: resolves the credential (bearer -> key path unchanged; else cookie -> session row -> user -> membership for the
  `X-ABB-Workspace` workspace), builds the `Principal` with `actions`, calls `set_caller(...)`, then `authorize(principal, action)`. Membership is
  read on **every request** (one indexed read on the composite primary key), so a role change or removal takes effect on the next request.
- `authorize(actor, action, resource)`: `action in actor.actions` or an `Own(...)` grant whose condition the resource satisfies; otherwise raise
  `PermissionDenied` (403 shape per D7) and, for user actors, append a `denied` audit row in its own short transaction (never inside the
  caller's).
- Streams: `StreamService.open` limits by `actor_id`; the frame loop re-validates every `ABB_STREAM_REAUTH_SECONDS` (KI-033) by re-running
  the credential check (key revoked/expired; session revoked/expired; membership still grants `run.read`) and ends the stream with `event: error`.
- OpenAPI: add `sessionCookie` (`apiKey` in cookie `abb_session`) and the `X-ABB-Workspace` header parameter; each operation lists the schemes that
  can call it (ingestion and artifact upload: bearer only; admin routes: cookie only; reads: both). `test_openapi.py` updates its surface set and the
  per-operation security assertion.
- Settings (all `ABB_*`): `oidc_issuer`, `oidc_client_id`, `oidc_client_secret` (optional), `web_origin` (required when OIDC is set; cookie `Secure`
  and `redirect_uri` derive from it), `session_absolute_hours`, `session_idle_hours`, `stream_reauth_seconds`, `login_rate_per_minute`. With OIDC
  unset, `/v1/auth/login` answers `503 AUTH_NOT_CONFIGURED`; keys keep working.
- CSRF: cookie-authenticated requests with a method other than GET/HEAD must carry `Origin` (or `Sec-Fetch-Site: same-origin`) equal to
  `ABB_WEB_ORIGIN`; otherwise `403 CSRF_REJECTED`. Bearer requests are exempt (no ambient credential). The Next proxy applies the same rule before
  forwarding (defence in depth) and forwards only the `abb_session` cookie, never the whole cookie header.
- CLI: `add-member --workspace --email --role`, `remove-member`, `create-session` (non-production), `list-members`; existing commands write audit
  rows as `actor_kind=cli`. `seed` adds the dev owner membership.

### Migrations

- **0045** `users`: add `provider`, `provider_subject` (unique pair, nullable for pre-provisioned users), `email_verified_at`, `last_login_at`;
  new `sessions` (`id`, `user_id`, `token_hash` unique, `created_at`, `last_seen_at`, `expires_at`, `idle_expires_at`, `revoked_at`) and
  `login_states` (`state_hash` pk, `nonce`, `code_verifier`, `return_to`, `expires_at`). Both are user-level, deliberately not tenant-keyed
  (documented exception like `api_keys.key_id`).
- **0046** `invitations` (`workspace_id, id` pk, composite FK to workspaces, `email`, `role` CHECK, `token_hash` unique, `invited_by`,
  `expires_at`, `accepted_at`, `accepted_by`, `revoked_at`); `workspace_members` gains `updated_at`, `invited_by`.
- **0047** `audit_log` (`workspace_id, id` pk, columns per D11, index `(workspace_id, occurred_at DESC, id)`); `REVOKE UPDATE, DELETE, TRUNCATE ON
  audit_log FROM abb_runtime`. Downgrades drop what they added. Existing rows are untouched; nothing is backfilled.

### Web

- `src/server/session.ts` (`getSession()` for server components: forwards the cookie to `/v1/me`), `src/server/upstream.ts` (forward
  `abb_session` and `X-ABB-Workspace`, allow the new paths and methods, JSON bodies up to 64 KiB, Origin check for non-GET, relay 401 as 401 so the
  client redirects, keep SSE relay), `src/app/api/auth/{login,callback,logout}/route.ts` (cookie relay incl. `Set-Cookie` on 302), `src/proxy.ts`.
- Pages: `/login`, `/invite/[token]`, `/w/[workspace]/settings/{members,api-keys,pricing,audit}`; `/w/[workspace]` lists projects; the project layout
  resolves slugs via `/v1/projects` (KI-027) and provides `{workspaceId, role, permissions, projects}` through a `WorkspaceProvider` context; the API
  client sets `X-ABB-Workspace` from it. `AppShell` gains a workspace switcher (memberships from `/v1/me`) and a user menu with logout.
- Fixture mode serves a fixture user (`OWNER` of workspace `default`) so Playwright fixture specs and UI development keep working without an API.
- No business logic in components: visibility by `permissions`, validation by the API (422 shown as field errors), roles and scopes come from
  `/v1/me` and the OpenAPI enum.

### The authorization test generator (`apps/api/tests/authz/`)

- `registry.py`: `CASES: dict[(METHOD, path_template), RouteCase]`. A `RouteCase` knows the `action` the route needs, how to build a valid
  request against the seeded fixtures (`build(ctx, tenant) -> (path, params, body, headers)`, using ids seeded in **both** tenants), the
  `transport` (`asgi` | `socket` for SSE), the success statuses, and `id_params` (which path/query parameters are tenant-owned ids).
- `test_registry_is_complete.py` (**fail-closed**): every `/v1` operation in `openapi.build_document()` must have a `RouteCase`, and every case
  must match an operation; a route without a case fails with its method and path in the message. `test_openapi.py` keeps its explicit surface set.
- `test_role_matrix.py`: parametrized over `CASES x ACTORS`, actors = six roles (sessions in `acme`), `no_membership` session, `removed_member`
  (membership deleted after login), `downgraded` (OWNER -> VIEWER after login), `expired_session`, `revoked_session`, `no_credential`, and the nine
  key variants. Expected outcome = `EXPECTED[actor][route]` from a **literal table in the test file** (not imported from `authz/matrix.py`); a
  separate test asserts the literal table equals the code's matrix, so a change must be made in both places on purpose. Allowed -> a success
  status; denied -> 403 with the right code and `required_permission`; no/invalid credential -> 401 (`API_KEY_INVALID` or `SESSION_INVALID`).
- `test_cross_workspace.py`: for every case and every `id_param`, the request is replayed with (a) the id of the identically-seeded resource in
  `globex` and (b) a random well-formed id: both must be 404 with byte-identical bodies (modulo `request_id`). Every `globex` row carries the canary
  string `CANARY-GLOBEX-7f3e` in a text field (project name, run name/agent slug, key name, member email, invitation email, override note, audit
  details, artifact name/content); **no response body or SSE frame to an `acme` actor may contain the canary**, including list routes, `/v1/me`,
  `/v1/audit` and the artifact content route. A user who is a member of both workspaces with `X-ABB-Workspace` set to `acme` must also never see
  the canary. `X-ABB-Workspace: <globex>` from an acme-only user is 404.
- Negative cases outside the matrix: CSRF (`POST` with a foreign `Origin` -> 403), `return_to` open redirect, replayed `state`, wrong `nonce`,
  unverified email, tampered ID token signature, expired login state, a session presented as a bearer token and a key presented as a cookie
  (both 401), last-owner protection, invitation reuse/expiry/wrong email, stream re-auth after revocation (socket test, test clock), the audit
  trail of a denied request, and `payload_withheld` for VIEWER.

### Affected files

- API: `auth/*` (new router, oidc, cookies, repositories), `authz/*` (new), `workspaces/*` (new module; `workspaces.py` moves into it),
  `audit/*` (new), `cost/router.py`, `runs/router.py` + `runs/service.py` (`require`, `payload_withheld`), `artifacts/router.py` + `service.py`,
  `streaming/service.py` + `router.py`, `ingestion/router.py`, `analytics/router.py`, `tenancy.py`, `core/config.py`, `main.py` (`_install_openapi`,
  routers, OIDC client on `app.state`), `cli.py`, `db/tables.py`, `migrations/versions/0045..0047`, `openapi.json`, `pyproject.toml`/`uv.lock`
  (`httpx`, `pyjwt[crypto]`), tests (`authz/`, `fake_oidc.py`, `test_auth_sessions.py`, `test_members_api.py`, `test_api_keys_api.py`,
  `test_pricing_api.py`, `test_audit_api.py`, `test_openapi.py`, `test_runtime_role.py`, `test_stream_api.py`, `test_cli.py`, `api_fixtures.py`).
- Web: `src/server/upstream.ts`, `src/server/session.ts`, `src/proxy.ts`, `src/app/api/auth/*`, `src/app/login`, `src/app/invite/[token]`,
  `src/app/w/[workspace]/{layout,page}.tsx`, `settings/*`, `src/components/{AppShell,WorkspaceSwitcher,Members,ApiKeys,PricingOverrides,AuditLog}.tsx`,
  `src/lib/api/client.ts` (header), `src/lib/api/schema.d.ts` (regenerated), `src/lib/routes.ts`, `src/fixtures/*` (fixture user), tests and
  `e2e/auth.spec.ts`, `playwright.config.ts`.
- Scripts/CI: `scripts/fake-oidc.sh`, `scripts/auth-e2e.sh`, `scripts/{e2e-web-real,coding-e2e,stream-e2e,analytics-e2e}.sh`,
  `.github/workflows/ci.yml` (`auth-e2e` job), `docker-compose.yml`, `.env.example`, `Makefile` (`auth-e2e`, `fake-oidc`).
- Docs: ADR-060/061/062 + `DECISIONS.md`, `SECURITY.md`, `architecture/api-v1.md`, `development/setup.md`, `TESTING.md`, `KNOWN_ISSUES.md`,
  `PROJECT_STATE.md`, `ARCHITECTURE.md` (auth module status), ADR-021 marked superseded, `docs/screenshots/phase-15/`.

## Acceptance criteria (each command-checkable)

- **AC-1 Fail-closed generator.** `cd apps/api && uv run pytest tests/authz/test_registry_is_complete.py -q` passes; adding a dummy
  `@router.get("/v1/probe")` without a `RouteCase` makes it fail naming `GET /v1/probe` (evidence: the failure output recorded in this plan, then reverted).
- **AC-2 Role matrix.** `uv run pytest tests/authz/test_role_matrix.py -q` passes and `--collect-only -q | tail -1` reports
  `len(CASES) x len(ACTORS)` tests (every route x every actor, no skips); `test_literal_table_matches_the_code_matrix` passes.
- **AC-3 No scattered checks.** `rg -n "require_principal\(" apps/api/src` -> no output; `rg -n "role ==|role in |\.scopes" apps/api/src --glob '!**/authz/**' --glob '!**/auth/**'` -> no output (also enforced by a test).
- **AC-4 Cross-workspace.** `uv run pytest tests/authz/test_cross_workspace.py -q` passes: every id-bearing route 404s identically for a foreign id and a random id; no response to an `acme` actor contains the canary (including SSE frames and artifact content).
- **AC-5 Sessions.** `uv run pytest tests/test_auth_sessions.py -q` passes: login/callback/logout against the in-process fake provider, state/nonce/PKCE/signature/email-verified/open-redirect negatives, idle and absolute expiry under the test clock, revocation, downgrade and removal mid-session, CSRF.
- **AC-6 Lookup (KI-027).** `curl -s -H "Cookie: abb_session=..." -H "X-ABB-Workspace: ws_..." localhost:8000/v1/projects` lists `{id, slug, name}`; the web resolves `/w/<slug>/projects/<slug>` (Playwright `auth.spec.ts`).
- **AC-7 Admin API (KI-051).** Members, invitations, API keys (token shown once in the response, never again), pricing overrides and cost rebuild work through the API and the settings pages; a created key ingests events; a revoked key is 401 on the next request. `uv run pytest tests/test_members_api.py tests/test_api_keys_api.py tests/test_pricing_api.py -q`.
- **AC-8 Audit.** Every mutating admin action (API and CLI) and every user denial produces exactly one `audit_log` row; `GET /v1/audit` pages them; `test_runtime_role.py` proves `abb_runtime` cannot UPDATE/DELETE/TRUNCATE `audit_log`; details never contain a token or secret (the log-hygiene test posts secret-looking values and scans rows).
- **AC-9 Stream re-auth (KI-033).** Socket test: a key revoked (and a session revoked, and a membership removed) while a stream is open ends the stream with `event: error` within `ABB_STREAM_REAUTH_SECONDS` of test-clock time.
- **AC-10 Shared key gone (KI-029).** `rg -n "ABB_WEB_API_KEY|WEB_UPSTREAM_AUTH" apps scripts docker-compose.yml .env.example docs --glob '!docs/decisions/ADR-021*' --glob '!docs/plans/**'` -> no output; `vitest` proxy tests prove the proxy forwards only the `abb_session` cookie and rejects non-GET without a same-origin `Origin`.
- **AC-11 Contract.** `scripts/quality.sh full` exit 0 (openapi.json current, `pnpm --filter @abb/web gen:api:check` clean, migrations 0045-0047 up/down/up, `next build`).
- **AC-12 Browser.** Playwright: `auth.spec.ts` logs in through the fake provider in a real browser, lands on `/w/local/projects/all`, switches workspace, opens settings as OWNER, is refused (clean 403 banner, no crash) as VIEWER, logs out and is redirected from `/w/...` to `/login`; axe clean on login and settings pages; screenshots in `docs/screenshots/phase-15/`. Existing `coding-e2e`, `stream-e2e`, `analytics-e2e`, `e2e-real` pass using sessions.
- **AC-13 Compatibility.** `make smoke`, `make sdk-e2e`, `make integrations-e2e` pass unchanged (API keys untouched); `make seed` on a seeded database is idempotent and adds the dev owner.
- **AC-14 Real provider (user, last).** One manual login with a real OIDC provider using the user's own client id/secret, documented as `VERIFIED` or `UNVERIFIED (env)` in this plan. Not a CI gate.
- **AC-15 Security review.** A `review-change` pass with the attack list below, findings fixed or filed as KI items.

## Verification plan

1. Per step: focused pytest/vitest, then `scripts/quality.sh quick`; `full` before each PR-bound commit.
2. Mutation checks during review (change `matrix.py` entries, drop the workspace predicate in one repository method, skip the CSRF check, skip the nonce check, skip signature verification in the fake-provider test: each must fail at least one test).
3. E2E: `make auth-e2e` (new), `make coding-e2e stream-e2e analytics-e2e e2e-real`, `make up && make smoke && make sdk-e2e` (containers; note Compose was last rebuilt at Phase 2, so this also refreshes that evidence).
4. Browser: Playwright screenshots plus one manual run through `make dev` + `scripts/fake-oidc.sh`.
5. Environment: Docker via Colima, Postgres 5433, Node 22, Chrome for Playwright are present (setup.md). AC-14 is the only item that can end `UNVERIFIED (env)` (no provider credentials); it is the user's call.

## Security review note (mandatory adversarial pass)

Scenarios the reviewer must attempt, with the expected result in parentheses:

1. Replay a `state`/`code` pair, swap the `nonce`, present an ID token signed by another key or with `aud` of another client, an unverified email, or an `iss` that differs from discovery (all 401/400, no session, no user row created).
2. `return_to=https://evil`, `//evil`, `/\evil`, `javascript:` (login refuses; callback only redirects to a relative path).
3. CSRF: `POST /v1/api-keys` with a session cookie and a foreign `Origin`, or no `Origin`, through the proxy and directly (403, no audit row says "allowed").
4. IDOR: every id-bearing route with ids from `globex` while a member of `acme` only (404, identical body); `X-ABB-Workspace` of a workspace the user left; a user in both workspaces with the header pointing at one and ids from the other (404).
5. Session fixation and theft: a session token in the URL or `Authorization` header (401), a cookie after logout (401), a cookie after the absolute lifetime with constant activity (401), cookie attributes (`HttpOnly`, `SameSite=Lax`, `Secure` in https), no `Set-Cookie` on error paths.
6. Privilege: a DEVELOPER revoking a key they did not create (403), creating a key with a scope the matrix does not list (422), an ADMIN granting OWNER (403), removing the last OWNER (409), inviting with a role outside the enum (422), accepting another person's invitation (403), accepting twice (409).
7. Keys: a key presented as a cookie, a session presented as a bearer, a key calling an admin route (403 `INSUFFICIENT_SCOPE`), a key with `X-ABB-Workspace` of another workspace (404), a project key listing `/v1/projects` (its project only).
8. Content exposure: VIEWER fetching an event detail with a payload (`payload_withheld`), artifact content (403), stream frames (no payload); the audit `details` of a key creation (no secret); `/v1/me` of a user in two workspaces (no canary from the other).
9. Streams: revoke the credential mid-stream (ends within the re-auth interval); open 11 streams with one session (10th+ `429 STREAM_LIMIT` scoped to that session, other sessions unaffected).
10. Bounds: 501st member, 201st active key, 201st open invitation (409); a 65 KiB JSON body through the proxy (413); login rate limit (429); `login_states` rows expire and are deleted on use.
11. Logs: no session token, state, code, ID token, key secret or invitation token in any log line or audit row (extend the existing log-hygiene test).
12. Fixture mode in production builds stays refused; the fake provider cannot be configured in `production`.

## Risks

1. **Scope and diff size.** Every router changes to `require(action)` and the web gains a session layer. Mitigation: step 1 is behaviour-preserving for keys and lands with the full suite green; new endpoints come one commit each; the registry forces a case per route as they appear.
2. **OIDC subtleties** (nonce/state/PKCE, signature verification, clock skew, email matching, open redirect). Mitigation: fake provider with knobs to produce each bad case; negative tests are listed as acceptance; mutation checks in review.
3. **Cookie relay through Next.js** (`Set-Cookie` on a 302 relayed by a route handler, `Secure` on plain http in development, cookie path). Mitigation: vitest tests on the auth route handlers; the Playwright login spec runs the real browser; `ABB_WEB_ORIGIN` decides the attributes in one place.
4. **Tautological matrix tests.** Mitigation: the literal expected table in the test file; the equality test makes any change a two-file, reviewed edit.
5. **CI and demo breakage while the proxy switches from key to session.** Mitigation: the web keeps the key path until step 13; step 15 switches scripts and removes the key in one commit; `make seed` and `create-session` give every script a session with no IdP.
6. **Lockout.** Last-owner rule, CLI `add-member` as the recovery path (documented in a runbook), sessions re-read membership so a fix applies at once.
7. **KI-019 exposure grows** (login endpoints are unauthenticated). Mitigation: cheap token bucket per process now; Phase 19 owns the real limiter.
8. **Next 16 `proxy.ts` convention** is ASSUMED; verify on the installed version before step 13.
9. **Audit volume on denials**: only user actors are audited on denial, and the UI never hammers a denied route (it hides what the role lacks).
10. **Runtime-role grants.** New tables get DML through default privileges (0007); the audit table must revoke in its own migration and update the role test, or the append-only guarantee silently fails.

## Ordered steps (one commit each; every step leaves `scripts/quality.sh full` green)

1. `feat(authz): action vocabulary, role/scope matrix, authorize() and require(action); routers switch from scopes to actions` - `Principal` generalised (`kind`, `actions`, `actor_id`), key behaviour unchanged, tests adjusted for `required_permission`; **plus** the `tests/authz` registry with cases for the 17 existing routes, the fail-closed completeness test, the key-actor half of the matrix and the canary cross-workspace test.
2. `feat(db): migration 0045 user identity columns, sessions and login_states; repositories` - with migration up/down tests and the tenant-exception note.
3. `feat(auth): OIDC login, callback, logout and /v1/me; sessions; fake OIDC provider` - settings, `httpx` + `pyjwt[crypto]` (ADR-060 five questions), CSRF rule, `SESSION_INVALID`, session actors added to the matrix and canary tests, `test_auth_sessions.py`.
4. `feat(workspaces): X-ABB-Workspace selection, GET/POST /v1/projects (KI-027)` - project confinement for user actors reuses `authorise_project`.
5. `feat(db): migrations 0046 invitations/membership columns and 0047 append-only audit_log; audit repository; denials and CLI actions audited` - `test_runtime_role.py` exception set updated deliberately.
6. `feat(members): members and invitations endpoints` - last-owner rule, bounds, `test_members_api.py`, registry cases.
7. `feat(keys): API key management endpoints` - token shown once, `Own(api_key.revoke)`, `test_api_keys_api.py`.
8. `feat(pricing): overrides and cost rebuild endpoints (KI-051)` - bounded like the CLI; registry cases.
9. `feat(audit): GET /v1/audit` - cursor paging, filters, `test_audit_api.py`.
10. `feat(runs,artifacts): payload.read enforcement (payload_withheld, artifact content 403)` - additive schema field, openapi/TS client regenerated.
11. `feat(streaming): re-authenticate open streams periodically (KI-033); limits keyed by actor` - socket tests with the test clock.
12. `feat(cli): add-member, remove-member, list-members, create-session (non-production); seed adds a dev owner` - `test_cli.py`.
13. `feat(web): auth routes, login and invite pages, proxy cookie relay and allowlist, route protection, workspace and project resolution, switcher` - fixture user for fixture mode; vitest for the proxy and session helper.
14. `feat(web): settings pages for members, API keys, pricing overrides and audit log` - permission-driven visibility; component tests for every state.
15. `chore(e2e): scripts and Playwright use sessions; auth-e2e with the fake provider; CI job; ABB_WEB_API_KEY removed` - compose, `.env.example`, setup docs.
16. `docs(phase-15): ADR-060/061/062, SECURITY, api-v1, TESTING, KNOWN_ISSUES (KI-029/027/051/033 resolved), PROJECT_STATE, screenshots` - then `complete-phase`.

## Appendix: ADR drafts (to be written with `write-adr` at step 16; numbers follow the 020/030/040/050 per-phase convention)

### ADR-060: Dashboard identity via OIDC with API-owned, database-backed sessions (supersedes ADR-021)

**Context.** ADR-021 gave the web server one shared `runs:read` key (KI-029, S1). Spec §91: users authenticate through a standard identity
provider; authorization is application-owned. The Next.js server and the FastAPI API must trust each other without a shared secret in the browser.

**Decision.** The API implements OIDC authorization code + PKCE against a discovery document (`ABB_OIDC_ISSUER`, `ABB_OIDC_CLIENT_ID`, optional
secret) and issues an opaque session cookie (`abb_session`, 32 random bytes, `sha256` stored, absolute 7 d / idle 24 h, revocable; `HttpOnly`,
`SameSite=Lax`, `Secure` when the web origin is https). Login state (state, nonce, PKCE verifier, return path) is a database row referenced by a
10-minute cookie, so no signing secret exists. ID tokens are verified against the provider's JWKS (`iss`, `aud`, `exp`, `nonce`); `email_verified`
is required; identity matches `(provider, subject)` first, then verified email for pre-provisioned users. The Next.js server forwards only the
`abb_session` cookie and the `X-ABB-Workspace` header to the API and relays `Set-Cookie`; cookie-authenticated non-GET requests must carry a
same-origin `Origin` (checked by proxy and API). A fake OIDC provider (`apps/api/tests/fake_oidc.py`, `scripts/fake-oidc.sh`) serves tests,
`make dev` and the Playwright login spec; `http://` issuers are refused outside development/test. Non-production CLI `create-session` mints
sessions for E2E scripts.

**Dependencies (five questions).** `httpx`: already in the lockfile (dev), maintained, small surface, stdlib `urllib` would need its own async
wrapper and timeouts; no lock-in. `pyjwt[crypto]`: JWKS/RS256/ES256 verification is not something to hand-roll; maintained; the `cryptography`
wheel is the only transitive weight; alternatives `authlib` (larger) and skipping signature checks (OIDC Core §3.1.3.7 permits it over the back
channel; rejected as weaker and harder to review).

**Alternatives.** Auth.js in Next (puts identity and sessions outside the API that enforces them; two sources of truth; harder to test with the
Python suite). Stateless signed JWT sessions (no immediate revocation; needs a key-management story). A shared proxy secret (does not identify
the user). GitHub OAuth first (no ID token or verified-email contract; can be an adapter later).

**Consequences.** The web app can be exposed with a login; every request is attributable to a person; revocation and role changes are immediate;
one more unauthenticated surface (login, callback) exists until Phase 19's limiter; a real provider is configured by the operator with three settings.

### ADR-061: One authorization helper; keys and users share the enforcement path; the matrix is data

**Context.** Spec §91 forbids scattered role checks and asks for `authorize(actor, action, resource)`. API keys with scopes already exist (§92) and
SDK keys must not administer a workspace. Another tenant's resources must stay indistinguishable from nonexistent ones (ADR-002).

**Decision.** A single `Principal` represents both keys and users (`kind`, `workspace_id`, `project_id`, `actions`, `actor_id`). Actions are
dotted strings (`run.read`, `payload.read`, `api_key.create`, ...) granted by role (`ROLE_ACTIONS`) or scope (`SCOPE_ACTIONS`) as data; `Own(action)`
expresses resource-owner conditions. Routers declare `require(action)`; services call `authorize()` for resource conditions; nothing else inspects
roles or scopes (a test enforces it). Users select a workspace with `X-ABB-Workspace`; keys are bound to theirs. Denied: 403 (`INSUFFICIENT_SCOPE`
for keys, `PERMISSION_DENIED` for users, both with `required_permission`); unseen workspace or foreign id: 404; invalid credential: 401
(`API_KEY_INVALID` / `SESSION_INVALID`). Sessions never get write scopes; keys never get admin actions; `runs:read` keeps implying `payload.read` and
`artifact.read` for compatibility. VIEWER and BILLING lack `payload.read`: event payloads are withheld (`payload_withheld`), artifact content is 403.
Every `/v1` operation must have an authorization case in `tests/authz/registry.py`; a completeness test fails otherwise, and a literal expected table
in the tests mirrors the code matrix.

**Alternatives.** Per-router role checks (what the spec forbids). Separate principal types for keys and users (two enforcement paths, twice the
tests). Workspace in the URL (breaks every existing route). Permissions stored per user in the database (flexible, but not needed and harder to
review than a table in code).

**Consequences.** Adding a permission is a one-line matrix change plus a test-table change; adding a route without an authorization case is a CI
failure; the web renders by the `permissions` list from `/v1/me` and never encodes the matrix.

### ADR-062: Append-only workspace audit log

**Context.** Spec §91/§124 expect administrative actions to be auditable; INV-1 and the `events` precedent show how to make a table immutable for the
runtime role.

**Decision.** `audit_log (workspace_id, id)` records every mutating administrative action (API, UI and CLI) and every denied request by a user
actor, with actor kind/id, action, resource, outcome, validated `details` (names, roles, scopes; never secrets), request id and time. The runtime
role has SELECT and INSERT only. Sign-in/out are user-level and go to structured logs in this phase. `GET /v1/audit` (`audit.read`) pages the log.
Reads and API-key denials are not audited (volume; they are in request logs).

**Alternatives.** Reusing `events` (telemetry, not administration; different tenancy and retention). Logs only (not queryable per workspace, not
tamper-evident). Auditing every request (volume without value).

**Consequences.** Admin actions are attributable and immutable; retention of the audit log is Phase 19's privileged job; a future "who viewed what"
requirement needs an explicit decision because reads are not recorded.
