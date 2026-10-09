# ADR-061: One Authorization Helper; Keys and People Share the Enforcement Path; the Matrix Is Data

Status: Accepted
Date: 2026-10-09
Phase: 15

## Context
Spec §91 forbids scattered role checks and asks for `authorize(actor, action, resource)`. API keys with scopes (§92) already exist and SDK keys
must never administer a workspace. Another tenant's resources must stay indistinguishable from nonexistent ones (ADR-002).

## Decision
- **One actor type.** `Principal` (`authz/principal.py`) represents both keys and people: `kind` (`api_key`|`user`), `workspace_id`,
  `project_id` (keys only), `actions`, `own_actions`, `actor_id` (`key:<id>` or `user:<id>`: safe to log, and the stream-limit key), `user_id`,
  `session_id`. Actions are dotted strings granted by role (`ROLE_GRANTS`) or scope (`SCOPE_ACTIONS`) as data in `authz/matrix.py`;
  `Own(action)` grants an action on resources the person created.
- **Routers declare, they do not inspect.** `require(action)` is a dependency that runs before any lookup; services call `authorize()` or
  `authorize_audited()` for conditions that need the loaded resource (`member.write_owner`, `Own(api_key.revoke)`). An AST test
  (`tests/authz/test_no_scattered_checks.py`) fails on `.role`/`.scopes`/`.actions` access outside `authz/`, `auth/`, `cli.py` and the
  membership code.
- **Roles.** OWNER, ADMIN, DEVELOPER, VIEWER, SECURITY, BILLING, per workspace. VIEWER and BILLING lack `payload.read`: event detail returns
  `payload: null` with `payload_withheld: true`, artifact content is `403 PERMISSION_DENIED` before any lookup, artifact metadata stays readable
  (`run.read`). BILLING reads analytics and prices and writes pricing overrides but cannot read runs. `member.write_owner` covers any change whose
  current or new role is OWNER; the last-owner rule runs under a row lock. People never obtain `event.write`/`artifact.write`; keys never obtain
  member, invitation, key-management or audit actions. `runs:read` keeps implying `payload.read` and `artifact.read`. A person may create only
  keys whose implied non-ingestion actions are a subset of their own (ingestion scopes need a project).
- **Errors by actor kind.** A key lacking an action keeps `403 INSUFFICIENT_SCOPE` with `details.required_scope` unchanged and the additive
  `required_permission`; a person gets `403 PERMISSION_DENIED`. A workspace the actor cannot see, or an id from another workspace, is `404`
  (`WORKSPACE_NOT_FOUND`, `*_NOT_FOUND`), never 403. No credential at all stays `401 API_KEY_INVALID` (SDK compatibility); an invalid or
  missing session cookie is `401 SESSION_INVALID`. If a bearer header and a cookie are both present the bearer header is the credential.
- **Workspace selection is the `X-ABB-Workspace` header** (public workspace id) sent from the page's resolved workspace, so tabs are
  independent. Missing: `400 WORKSPACE_REQUIRED`; not a member: `404`. Keys must send nothing or their own workspace. `GET /v1/me`,
  `/v1/auth/*` and `/v1/invitations/accept` need no workspace. Streams, which cannot send headers from `EventSource`, carry
  `?workspace=` that the web proxy turns into the header.
- **Invitations are one-time links bound to an email**: stored as `sha256(token)`, seven days, one open invitation per email, link
  `${ABB_WEB_ORIGIN}/invite#<token>` with the token in the fragment (never in a server or proxy log), shown once by `POST /v1/invitations`.
  Accepting needs a signed-in person whose verified email equals the invitation email at that moment; an existing member gets `409`, so
  acceptance can never change a role. Inviting as OWNER needs `member.write_owner`.
- **Every route has a case.** The role-matrix test walks `app.routes` (including HEAD/OPTIONS) and fails for any route without an entry in
  `tests/authz/registry.py`; a literal expected table in the tests mirrors the code matrix in both directions; a canary test proves nothing of
  another workspace leaks. Per-workspace bounds: 500 members, 200 active keys, 200 open invitations, 200 projects, 1,000 pricing overrides
  (`409 LIMIT_REACHED`).
- **Open streams are re-authorized** every `ABB_STREAM_REAUTH_SECONDS` (30): a revoked or expired key, a revoked session or a removed or
  demoted member ends the stream with `event: error` `STREAM_UNAUTHORIZED` (not retryable). The per-process stream cap per credential is now
  per actor, so ten streams per person across tabs and devices.

## Alternatives
Per-router role checks (what §91 forbids). Separate principal types for keys and people (two enforcement paths, twice the tests). The workspace
in the URL (breaks every route and client). Permissions stored per user in the database (flexible, not needed, harder to review). A path-less
"current workspace" in the session (breaks multi-tab).

## Consequences
Adding a permission is a one-line matrix change plus a test-table change; adding a route without an authorization case fails CI; the web renders
by the `permissions` list from `/v1/me` and never encodes the matrix; a stale page gets a clean 403 banner. Roles are per workspace, not per
project. `approval.decide`, `policy.write` and `retention.write` are reserved action names held by nobody until Phases 14 and 19.
