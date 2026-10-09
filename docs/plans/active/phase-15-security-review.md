# Independent security review of the Phase 15 implementation (auth, workspaces, RBAC)

Reviewed: branch `feature/phase-15-auth-rbac` at a9b3266 (commits 7f5203c..a9b3266 on main 85d4121), worktree
`../abb-worktrees/phase-15`. Stance: adversarial, against the implemented code and tests, not the plan's description of them.
Nothing in `apps/`, `scripts/` or `docker-compose.yml` was changed; every mutation below was reverted with `git checkout`
before the next one and the tree was clean (`git status`) when this file was written. Tests ran against the scratch database
`abb_test_p15` (the suite's own); no other database was touched.

Severity: **P0** critical, **P1** must fix before merge, **P2** should fix (or file and accept explicitly), **P3** optional.
Evidence vocabulary: **VERIFIED** (a command was run here, or the exact lines were read), **UNVERIFIED** (could not be
exercised here), **ASSUMED** (reasoned).

Summary: no P0. One P1 (an unauthenticated client can lock every person out of sign-in at two requests a second; the plan's
D15 claimed the opposite), three P2 (acceptance compares a stale email, VIEWER sees `shell.command`, a revoked invitation can
still admit under a race), eight P3. The core claims hold: 26 of 26 mutations were killed by an existing test, every P1 from
the plan review landed in code, and no cross-workspace, payload or credential leak was found by reading or probing.

---

## 1. Scope and method

- Read: `AGENTS.md`, the plan and its review dispositions, `phase-15-plan-review.md`, ADR-060/061/062, `SECURITY.md`, the
  runbook, `KNOWN_ISSUES.md` (KI-067..074).
- Read in full: `authz/*`, `auth/{login,oidc,router,service,cookies,repository,keys_router}.py`, `workspaces/*`, `audit/*`,
  `cost/router.py`, `main.py`, `core/config.py`, `cli.py` (diff), migrations 0045-0047, `db/tables.py` (diff), the diffs of
  `runs/`, `artifacts/`, `streaming/`, `analytics/`, `projects/`; web `server/{upstream,authRoutes,session,loginLimiter,config,gate}.ts`,
  `proxy.ts`, the three auth route files, `app/api/abb/[...path]/route.ts`, `lib/{invite,stream,live,workspace}.ts`,
  `lib/api/client.ts`, `components/{InviteAccept,SignOutButton,WorkspaceProvider}.tsx`, `fixtures/auth.ts`;
  `docker-compose.yml`, `.env.example`, `ci.yml`, `scripts/{fake-oidc,auth-e2e}.sh`; the test harness (`tests/authz/*`,
  `fake_oidc.py`, `auth_helpers.py`) and the Phase 15 test files.
- Ran: targeted pytest selections for every mutation (each < 10 s), the full `tests/authz` + members + keys + audit suites after
  the last revert (`136 passed in 87 s`), vitest for the proxy and auth handlers (`46 passed`), and one throwaway vitest probe of
  the login limiter (deleted afterwards).
- Not run here (UNVERIFIED): the Playwright E2E scripts, `make up` with the compose `fake-oidc` service, a real identity
  provider (AC-14). The implementer's closing evidence for AC-12 is taken as their claim, not re-verified.

## 2. Plan-review P1 items: did the fix land in code?

| Review id | Landed where (VERIFIED by reading) |
| --- | --- |
| B-1 keys must not list members | `authz/matrix.py:72-82` `SCOPE_ACTIONS` grants only `workspace.read`/`project.read` plus run/ingest actions; `test_matrix_data.py::test_no_key_scope_may_administer_a_workspace` |
| B-2 projects bounded | `workspaces/router.py:24,99-106` (`MAX_PROJECTS = 200`, under `lock_for_create`) |
| B-3 acceptance never changes a member | `workspaces/members.py:336-337` `ALREADY_MEMBER` before `add`; `test_duplicates_and_existing_members_are_conflicts` |
| C-1 email fallback only for unlinked users | `auth/login.py:201-206` and the `provider_subject IS NULL` predicate in `auth/repository.py:303`; mutation M4 killed |
| C-2 state bound to the browser cookie | `auth/login.py:131-137` constant-time compare; mutation M1 killed |
| C-3 login limiter | Partially: per-client + per-instance buckets in `loginLimiter.ts`, global backstop `auth/router.py:57-61`; the "one client cannot exhaust it" property does **not** hold (finding F1) |
| C-4 gating on `{development, test}` | `core/config.py:96-99,114-116`, `main.py:139`, `cli.py` `_create_session`/`_seed`; `test_cli_access.py` incl. the staging case |
| E-1 bounded denial audit | `authz/audit.py:24-61` token bucket per actor; mutation M25 killed |
| F-1 route walker, not OpenAPI | `tests/authz/routes.py` walks `app.routes` (incl. `_IncludedRouter`, mounts, websockets); `test_registry_is_complete.py` negative cases |

## 3. Verified correct (no finding)

Each item was read at the cited lines and, where a mutation is named, exercised (section 6).

- **Login callback.** `state` must be present, well-formed, equal to the `abb_login` cookie in constant time and name a live
  row that is consumed on use (`auth/login.py:131-146`, `auth/repository.py:494-510`); an expired row is deleted and refused
  (M21). A mismatch does not consume the attacker's row, which is right (the victim never had the cookie). IdP `error` is mapped
  to `LOGIN_FAILED` and never echoed or logged (`login.py:143-144`, `router.py:105`). `code` is bounded (2048). The login cookie
  is cleared on every callback outcome (`router.py:109-124`).
- **ID token.** Algorithm allowlist checked on the unverified header before any key lookup (`oidc.py:278-280`); `jku`/`jwk`/`x5u`
  never consulted; key chosen by `kid` and `alg` from a cached JWKS (1 h) with one refetch per minute on an unknown `kid`
  (`oidc.py:235-271`, test `test_a_key_rotation_is_followed_but_probing_unknown_keys_is_bounded`); `iss` and `aud` pinned in
  `jwt.decode`, `azp` must equal the client id when present and is required for multi-audience tokens (`oidc.py:304-309`,
  M11); `exp`/`nbf`/`iat` against the injected clock with 60 s skew; `nonce` constant-time (M2); signature (M3); the HS256
  confusion case is a test knob and refused. `email_verified` must be present and literally `true` (`oidc.py:334-341`, M10).
- **Identity linking.** `(provider, subject)` first; email adopts only `provider_subject IS NULL` users, enforced both in the
  rule and in the `UPDATE ... WHERE` so a race cannot re-bind (`login.py:183-207`, `repository.py:293-306`, M4). A conflicting
  email creates and changes nothing (`counts()` assertions in the test).
- **Discovery.** `issuer` must equal the setting exactly; endpoints must be https (http only in dev/test with an http issuer),
  on the issuer's host:port or an explicit extra host, no userinfo/fragment (`oidc.py:154-198`, M16). `S256` required when
  the document lists methods. Responses capped at 1 MB; `trust_env=False`, `follow_redirects=False`, 5 s timeout (`oidc.py:111-150`).
- **Sessions.** 32 random bytes, only SHA-256 stored, looked up by hash (`service.py:84-126`); absolute and idle expiry with
  sliding at most every 5 min and never past `expires_at` (`repository.py:405-413`); DB trouble is 503 via `_guarded`
  (`dependencies.py:97-108`); bearer header wins over a cookie and a session in a bearer header or a key in a cookie
  authenticates nothing (M19 plus the matrix actors `session_as_bearer`, `key_as_cookie`). Logout is POST, origin-checked,
  idempotent, clears with identical attributes (`router.py:142-173`). Cookie attributes derive from one function
  (`cookies.py`): `__Host-` + `Secure` for https origins; `Settings` refuses an http origin outside localhost in dev/test
  (`config.py:104-107`). Purges are bounded (`PURGE_BATCH = 100`).
- **Workspace selection.** `X-ABB-Workspace` required for people (400), unknown/foreign/non-member is 404, membership is read
  on every request (`dependencies.py:150-170`, M6c); keys may only name their own workspace (M18). `/v1/me` is the single
  cross-workspace read and returns memberships only (`test_me_of_a_member_of_both_workspaces_carries_no_project_data`).
- **CSRF.** Exact-origin rule on every cookie-authenticated non-safe method in the API (`dependencies.py:73-81`, M9) and again
  in the proxy for POST/PATCH/DELETE (`upstream.ts:118-124`, vitest incl. `null`, look-alike origins); `Sec-Fetch-Site` unused.
  `SameSite=Lax` is safe because no GET has side effects beyond creating a login state.
- **Web relay.** Allowlist of forwarded headers is exactly cookie (ours only, shape-checked), `x-abb-workspace` (regex),
  `last-event-id` (regex), `accept`, and `content-type`/`origin` on writes (`upstream.ts:132-150`); `Authorization`, other
  cookies, `X-Forwarded-*`, `X-Request-ID` never pass (M7). Responses relay only `x-request-id` and `retry-after`, never
  `Set-Cookie` (M14); `redirect: "manual"` everywhere; bodies capped at 64 KiB while streaming; `/v1/auth/*` is unreachable
  through the generic proxy (not in `ROUTES`); the auth handlers relay only cookies named `abb_login`/session
  (`authRoutes.ts:33-39`) and validate `Location` (absolute http(s) without credentials for the IdP; app-path allowlist for the
  callback). Path segments are joined only after matching `[A-Za-z0-9_-]{1,64}` segments, so no traversal or query smuggling.
  `?workspace=` is converted to the header and stripped from the upstream query for every path.
- **Authorization.** One `authorize()` (`authz/service.py`), `require(action)` as a dependency before any body or lookup
  (`dependencies.py:185-206`); `Own(api_key.revoke)` evaluated against the loaded, tenant-scoped key (`keys_router.py:198-213`,
  M29); the subset-of-creator rule (M24) and `PROJECT_REQUIRED` for ingestion scopes; `member.write_owner` on any change whose
  current or new role is OWNER (`authz/members.py`, M23); last-owner rule under `FOR UPDATE` on the owner rows in a fixed lock
  order (`repository.py:102-117`, M5, concurrency test with 8 rounds); 404-not-403 for foreign and random ids with
  byte-identical bodies on every id-bearing route and for the admin routes via `probe_actors` (M6a, M6b); the canary, id-set
  and magnitude scans cover every route for 13 acme actors including a dual member.
- **Payload exposure.** Event detail withholds the body for actors without `payload.read` and says so (`runs/service.py:251-256`,
  M13); lists never carry payloads; artifact content demands `payload.read` before any lookup (403 for existing, missing and
  foreign ids alike); artifact metadata needs `run.read`; BILLING has no `run.read` at all; stream frames carry `has_payload`
  only (pre-existing). Audit `details` cannot carry secret-named keys, emails or long values (`audit/repository.py:30-56`;
  tests assert `"@" not in details`).
- **Streams.** Re-check every `stream_reauth_seconds` by identity (key row / session row / fresh `role_of`) without sliding
  the session (`service.py:129-159`, `streaming/service.py:97-101,130-142`, M12); per-person cap across sessions; a database
  error in the re-check propagates to the generic `except Exception` and ends the stream with `STREAM_UNAVAILABLE`
  (`streaming/service.py:132-137`, read only: see F7).
- **Environment gating.** Every convenience checks `environment in ("development", "test")`, never `!= production`
  (`config.py:96,116`, `main.py:139`); `create-session` and the seeded owner also need `ABB_ALLOW_DEV_SESSIONS=1`
  (`dev_sessions_enabled`); the API cannot even start with an `http://` issuer outside dev/test (`config.py:98-99`), which is
  what makes the compose `fake-oidc` service useless anywhere else; `make seed` passes the flag for its own run only; startup
  warnings exist for both. The compose default stays `development` with the mandatory-production comment, as planned.
- **Audit log.** `REVOKE UPDATE, DELETE, TRUNCATE` after `CREATE TABLE` in 0047; `APPEND_ONLY_TABLES` exported and imported by
  `test_runtime_role.py`; CHECKs on actor kind, outcome, JSON object, size and text lengths; writes in their own transaction
  after the request's, failures logged and swallowed (`record_audit`); denials of people bounded per actor (M25); keys logged
  only; CLI rows carry `cli:<os user>` and user public ids, never emails.
- **Logs.** `set_caller` carries `actor_id`/`session_id[:8]` only; the OIDC client logs `iss`/`kid`/`reason`/`status`; `httpx`
  and `httpcore` loggers at WARNING; the hygiene test renders every record's `__dict__` and asserts no state, nonce, challenge,
  code, email, session token or `id_token`. The 422 handler emits `loc/msg/type` only (`core/errors.py:125-135`).
- **Migrations.** 0045-0047 are additive, nullable, backfill-free; `uq_users_identity` allows many NULL pairs with a CHECK that
  both halves are set together; `invitations` is `(workspace_id, id)` with lower-cased email CHECK and the partial unique
  index; downgrades drop only what they added. INV-3 holds: every repository method that touches tenant data carries the
  workspace; the unscoped lookups are the four documented ones.
- **Tests are not tautological.** The literal tables (`test_role_matrix.py`, `test_matrix_data.py`) are hand-written and
  compared both ways; a missing cell fails (`row()` length check); the walker has negative tests for hidden routes, missing
  `require`, mounts and websockets; the canary lives in project names, agent/model/tool names, artifact name and content,
  override notes and audit details, not in the workspace name; magnitudes catch numeric leaks. Every mutation in section 6
  was caught by a test that names the behaviour.

## 4. Findings

### P1

**F1. One unauthenticated client can lock everyone out of sign-in (login-limiter keying).** `apps/web/src/server/loginLimiter.ts:68-77`
(`clientKey` trusts the first `X-Forwarded-For` address), `:44-62` (a refusal by the per-client bucket is returned before the
per-instance bucket is consulted; a *new* client always creates a bucket), `apps/web/node_modules/next/dist/server/base-server.js:612`
(`req.headers['x-forwarded-for'] ??= socket.remoteAddress`: a client-supplied value is **kept**). VERIFIED by a throwaway vitest
probe: a client rotating `X-Forwarded-For` values is first refused after ~130 requests (10 per-client tokens are free, then the
120/min per-instance bucket is drained), after which an honest client with its own address is refused too. At 2 requests a second
the attacker keeps it drained indefinitely; `GET /api/auth/login` needs no credential and costs the attacker nothing. The API's
global backstop (`ABB_LOGIN_GLOBAL_PER_MINUTE = 600`, `auth/router.py:57-61`, one key `"auth"` for everyone) has the same
property for a client that reaches the API directly or through several web instances. Existing sessions are unaffected; new
sign-ins and every callback are refused for every person (`/login?error=rate_limited`).
The plan's D15 and the review disposition for C-3 state the contrary ("a global bucket ... that one client cannot exhaust on its
own"); ADR-060 and KI-073 (S3) describe the limitation honestly but understate the effect ("use the whole login budget" is a
complete sign-in outage).
Suggested fix (cheap, this phase): treat a client-supplied `X-Forwarded-For` as untrusted unless `ABB_TRUSTED_PROXY=1` (or a
CIDR list) says the deployment sits behind a proxy that overwrites it; without that, key the per-client bucket on nothing the
client controls (Next does not expose the socket address to route handlers, so in practice: drop the per-client bucket and make
the per-instance bucket a *per-address-independent* high ceiling such as 1,200/min, or keep the per-client bucket and make the
per-instance bucket log-and-continue instead of refuse). Whichever is chosen, the unauthenticated endpoints must not share one
refusal pool that any client can drain at the per-client rate; document the proxy requirement in the runbook. If the user prefers
to accept KI-073 as filed, re-grade it S2 and add the concrete number (2 rps) to the register and remove the contrary sentence
from D15 and ADR-060.

### P2

**F2. Invitation acceptance compares a stale email, not the verified email "at acceptance time".** `apps/api/src/abb_api/auth/login.py:191-194`
returns a user matched by subject without updating `users.email`/`email_verified_at`; `workspaces/members.py:328-335` compares
`context.user.email` (the stored value from the *first* login) with the invitation. D12, ADR-061 and the runbook say the
verified email must equal the invited one at acceptance. VERIFIED: `test_the_subject_decides_so_an_email_change_at_the_provider_changes_nothing`
asserts `/v1/me` still shows the old email after the provider reports a new one, which is exactly the stale value acceptance
trusts. Scenario: Alice (`alice@corp`, subject s1) leaves; the IdP reassigns `alice@corp` to Bob (subject s2) and renames Alice's
account. An admin invites `alice@corp` meaning Bob. Bob's login is refused (`identity_conflict`: his verified email belongs to a
user with another subject), while Alice, still able to sign in with s1, holds a stored `alice@corp` that is no longer hers and can
accept Bob's invitation. Suggested fix: on every login matched by subject, refresh `email` and `email_verified_at` from the token
when `email_verified` is true (if the new email collides with another user under `uq_users_email_lower`, refuse with
`identity_conflict` and change nothing); then acceptance really compares the current verified email. Add the scenario to
`test_members_api.py`.

**F3. VIEWER reads `shell.command`, `file.path` and span names built from them.** `packages/event-schema/src/abb_event_schema/registry.py:76,98`
(`shell.command` and `file.path` are STRING attributes), `spans.py:44` (span names derive from `shell.command`); event lists,
event detail and spans are `run.read` routes (`runs/router.py`) and a VIEWER holds `run.read`. `SECURITY.md:3` lists "shell
output, file names, database query text and tool arguments" as the stored assets, and decision 3 promised "metadata only" for
VIEWER without saying that attributes count as metadata. A shell command line routinely contains tokens, hostnames and paths
(`curl -H "Authorization: Bearer ..."`). VERIFIED by reading; no test asserts either way. This is a product decision, not a
code defect, but it is security-relevant and undocumented. Suggested fix: decide and write it down. Either (a) treat
`shell.command` (and any future statement/argument attribute) as content: strip it from events and spans for actors without
`payload.read` the way `payload` is, with a test; or (b) state in the plan's decision 3, SECURITY.md and KI-070's neighbour that
attributes, including shell command lines and file paths, are visible to every role with `run.read`.

**F4. A revoked invitation can still admit a member under a race.** `apps/api/src/abb_api/workspaces/members.py:340-341`:
`members.add(...)` runs, then `invitations.mark_accepted(...)` whose `False` return (its `UPDATE` carries `revoked_at IS NULL`,
`repository.py:293-306`) is ignored, so when an OWNER's `DELETE /v1/invitations/{id}` commits between `invitations.get` (`:316`, a
plain read) and `mark_accepted`, the transaction commits a new membership for an invitation that ends up revoked and not
accepted. `revoke_invitation` does not take `lock_workspace`, so the acceptor's lock does not serialise it. VERIFIED by reading;
the window is milliseconds and needs the invitee to race the admin, hence P2 rather than P1. Suggested fix: `if not await
invitations.mark_accepted(...): raise invitation_not_found()` (the `begin()` block rolls the `add` back), or lock the invitation
row (`FOR UPDATE`) in `get` before the checks.

### P3

**F5. `abb_login` has no `__Host-` prefix.** `auth/cookies.py:9-12` (`Path=/api/auth` makes the prefix impossible). The
state/cookie binding is only as strong as the cookie's integrity: a sibling subdomain (or any cookie-tossing position) can
pre-set `abb_login=<attacker state>` on the victim, then send them the attacker's callback URL, and the victim is signed in as the
attacker (login CSRF / session fixation). Defence in depth: `__Host-abb_login` with `Path=/` on https origins (one more tiny
cookie per request). ASSUMED attack path (needs an attacker-controlled sibling origin).

**F6. Any account at the identity provider creates a `users` row and sessions.** `auth/login.py:195-200` creates a user for
every verified email the IdP vouches for; with a public IdP (Google) that is everyone. Bounded only by the login rate (F1's 600/min
backstop) and the bounded purges. Not a vulnerability under invite-only (no membership is granted), but unbounded cardinality
contrary to the plan's "no new unbounded cardinality" claim. Consider creating the user row only when an open invitation or a
pre-provisioned user exists for that email, or document the growth and a purge job.

**F7. The stream re-check's failure path is untested.** `streaming/service.py:130-142` ends the stream with `STREAM_UNAVAILABLE`
when `credential_still_grants` raises (read; fail closed). `tests/test_stream_reauth.py` has no case for a database error during
the check (grep for `unavailable`/`_error_frame`: none). Add one (inject a failing engine or close the pool) so the fail-closed
claim is VERIFIED rather than read.

**F8. `ProjectRepository.lock_for_create` uses `FOR UPDATE` on the workspace row.** `projects/repository.py:71-77`, unlike
`workspaces/repository.py:17-27` (`FOR NO KEY UPDATE`, whose docstring explains why). `FOR UPDATE` conflicts with the `KEY SHARE`
locks that every foreign-key check into `workspaces` takes, so a project creation briefly blocks concurrent inserts into
`workspace_members`, `invitations`, `api_keys`, `audit_log` and vice versa. Availability nit; use `key_share=True` (or call the
shared `lock_workspace`).

**F9. Open invitations outlive their inviter's rights.** An ADMIN's invitation (at most ADMIN, since OWNER needs
`member.write_owner`) stays valid for seven days after that ADMIN is demoted or removed; nothing revokes it. Acceptable if
documented (the inviter's rights were checked at creation, `members.py:251`); otherwise revoke a member's open invitations on
removal, with an audit row.

**F10. The fake provider has no gate of its own.** `tests/fake_oidc.py:767-781` serves on any host/port regardless of
`ABB_ENVIRONMENT`; the compose service publishes it on `127.0.0.1:8900` and inside the API container's namespace, so other
containers on the compose network reach it. The API refusing an `http://` issuer outside dev/test is what makes it useless
elsewhere (VERIFIED: `config.py:98-99`). Belt and braces: have `main()` refuse to start when `ABB_ENVIRONMENT` is `production`
or `staging`, and keep the "never expose" comment.

**F11. `clean_details` can exceed the 8 KiB CHECK.** `audit/repository.py:26-56` allows 20 keys of 20-item lists of 200-char
strings (~80 KiB) while 0047 enforces `pg_column_size(details) < 8192`; such a row is silently dropped (`record_audit` swallows
the error). Only programmer-controlled values reach `details` today, so no attacker lever; tighten the bound (e.g. 2 KiB total)
so a future caller cannot lose audit rows silently.

**F12. Plan/ADR text contradicted by code on two points** (documentation): D15/ADR-060's "cannot be exhausted by one client"
(F1) and D12/ADR-061's "verified email ... at acceptance time" (F2). Fix the text with the code.

## 5. Attack list walk-through (plan section "Security review note")

| # | Item | Result |
| --- | --- | --- |
| 1 | replay, cookie-less/different cookie callback, nonce, foreign key, aud/azp, email_verified, iss, discovery issuer/endpoints | all refused, no user/session row: tests exist and M1-M3, M10, M11, M16, M21 killed (VERIFIED) |
| 2 | verified email for a user linked to another subject | 401, row unchanged (M4 killed) |
| 3 | `return_to` outside the allowlist | 422 at login; callback redirects only to the stored string (`RETURN_TO` regex plus dot-segment check, `login.py:34-78`); web re-validates (`config.ts:52-57`) |
| 4 | CSRF with session cookie and foreign/missing/`null` Origin, through proxy and directly; GET logout | 403 both layers (M9, vitest); `GET /v1/auth/logout` 405 (route is POST only) |
| 5 | IDOR with globex ids from acme, header of a left workspace, dual member, accept with other workspace header | 404 identical bodies (M6a-c); `removed_member` actor 404; dual actor scoped; accept header 404 (test) |
| 6 | session in URL/bearer, after logout, after absolute lifetime with open stream, cookie attributes, no Set-Cookie on error or via proxy | all per tests and reading; the open-stream absolute-expiry case is `test_a_revoked_or_expired_session_ends_its_stream` |
| 7 | DEVELOPER revoking others'/CLI keys, scope outside enum, ADMIN touching OWNER, last owner incl. concurrent, accept as member/other email/after email change/twice, duplicate invite | all refused (M5, M23, M24, M29) **except** "after an IdP email change", which is F2 |
| 8 | key as cookie, session as bearer, key on admin routes, key with foreign header, project key listing projects | 401/403 `INSUFFICIENT_SCOPE`/404/own project only (matrix actors, M18, M19) |
| 9 | VIEWER payload, artifact content for existing/missing/foreign, stream frames, audit details of key/invitation, `/v1/me` of a dual member | withheld (M13), 403/403/403, no payload, no secret or email, no project canary; but see F3 for `shell.command` |
| 10 | revoke mid-stream, 11 streams across two sessions | ends within the interval (M12); `429 STREAM_LIMIT` scope `user` (test) |
| 11 | bounds, 65 KiB body, login limits, backstop, 1,000 denials, abandoned logins | 409s, 413, per-client 429 (but F1), global backstop test, bounded rows (M25), purge on insert (read) |
| 12 | logs | hygiene test over every record field (read; it covers state, nonce, challenge, code, email, session, `id_token`) |
| 13 | dev conveniences in production and staging, flag required, startup warning | tests `test_create_session_is_gated_on_environment_and_flag`, `test_seed_does_not_add_the_dev_owner_in_staging`, `test_unsafe_sign_in_configuration_is_refused_at_startup` (read) |
| 14 | `X-Forwarded-*`, `Forwarded`, `X-Request-ID`, `Authorization`, second cookie through the proxy; forged `Sec-Fetch-Site` | never forwarded (M7, vitest); `Sec-Fetch-Site` unread anywhere (grep) |

## 6. Mutation checks

Each row: the code change (applied with a literal replacement, run with `-x`, reverted with `git checkout -- <file>`),
the test expected to catch it, and the result. "KILLED" = the named test failed with the mutation and passes without it.

| # | Mutation (file) | Expected failing test | Result |
| --- | --- | --- | --- |
| M1 | drop `state == abb_login` compare (`auth/login.py`) | `test_a_callback_without_the_browsers_login_cookie_is_refused` | KILLED |
| M2 | skip nonce check (`auth/oidc.py`) | `test_every_bad_id_token...[bad_nonce]` | KILLED |
| M3 | `verify_signature: False` (`auth/oidc.py`) | `...[bad_signature]` | KILLED |
| M4 | email fallback re-binds linked users: rule and `IS NULL` predicate (`auth/login.py`, `auth/repository.py`) | `test_a_verified_email_cannot_take_over_a_user_linked_to_another_subject` | KILLED |
| M5 | remove `FOR UPDATE` from `lock_owners` (`workspaces/repository.py`) | `test_two_owners_removing_each_other_at_once_leave_one` | KILLED |
| M6a | drop workspace predicate in `AuditRepository.page` | `test_no_response_to_an_acme_caller_contains_globex_data[owner]` (canary in audit) | KILLED |
| M6b | drop workspace predicate in `ApiKeyRepository.get` | `test_foreign_ids_and_random_ids_are_indistinguishable[/v1/api-keys/{key_id}]` | KILLED |
| M6c | drop workspace predicate in `MembershipRepository.role_of` | `test_a_user_must_name_a_workspace_and_must_belong_to_it` | KILLED |
| M7 | forward browser `Authorization` upstream (`upstream.ts`) | proxy-session "never puts an Authorization header" + allowlist test | KILLED |
| M8 | give VIEWER `payload.read`/`artifact.read` (`authz/matrix.py`) | `test_the_actor_gets_the_expected_outcome_on_every_route[viewer]` | KILLED |
| M9 | skip the exact-origin rule (`authz/dependencies.py`) | `test_logout_by_a_foreign_origin_is_a_csrf_rejection[None]` (first of several) | KILLED |
| M10 | skip `email_verified` (`auth/login.py`) | `...[email_verified-False]` | KILLED |
| M11 | drop the `azp` check (`auth/oidc.py`) | `...[authorized_party-another-client]` | KILLED |
| M12 | session re-check always true (`auth/service.py`) | `test_removal_and_loss_of_run_read_end_the_stream_but_a_lesser_role_does_not` | KILLED |
| M13 | never withhold payloads (`runs/service.py`) | `test_a_viewer_gets_metadata_and_is_told_the_payload_is_withheld` | KILLED |
| M14 | relay upstream `Set-Cookie` (`upstream.ts`) | proxy-session "never relays Set-Cookie" | KILLED |
| M15 | drop the email check on accept (`workspaces/members.py`) | `test_only_the_invited_verified_email_can_accept` | KILLED |
| M16 | accept discovery endpoints on any host (`auth/oidc.py`) | `test_discovery_endpoints_must_live_on_the_issuers_host` | KILLED |
| M18 | keys may name another workspace (`authz/dependencies.py`) | `test_an_api_key_may_only_name_its_own_workspace` | KILLED |
| M19 | cookie used even with an `Authorization` header (`authz/dependencies.py`) | `test_the_bearer_header_wins_over_a_cookie` | KILLED |
| M21 | expired login state accepted (`auth/repository.py`) | `test_an_expired_login_is_refused` | KILLED |
| M23 | OWNER involvement never needs `member.write_owner` (`authz/members.py`) | `test_an_admin_cannot_create_demote_or_remove_an_owner` | KILLED |
| M24 | subset-of-creator rule returns empty (`authz/service.py`) | `test_a_key_is_never_stronger_than_its_creator` | KILLED |
| M25 | denial audit without the bucket (`authz/audit.py`) | `test_a_flood_of_denials_writes_a_bounded_number_of_rows` | KILLED |
| M29 | skip `authorize_audited` on key revoke (`auth/keys_router.py`) | `test_a_developer_revokes_only_keys_they_made` | KILLED |

Surviving mutations: none. After the last revert: `git status` clean; `pytest tests/authz tests/test_members_api.py
tests/test_api_keys_api.py tests/test_audit.py`: `136 passed in 87.11s`; vitest proxy/auth/gate: `46 passed`.

Not mutated (would only re-confirm a gap already found by reading): the stream re-check error path (F7) and the ignored
`mark_accepted` result (F4) have no test to kill them.

## 7. Disposition requested

- Fix F1 (or explicitly accept it with the corrected wording in D15/ADR-060/KI-073) and F2-F4 before merge; F2 and F4 are small.
- File F5-F11 as KI items or fix inline; F12 is a documentation fix alongside F1/F2.
- AC-15 can then be marked PASS with this file as the evidence.
