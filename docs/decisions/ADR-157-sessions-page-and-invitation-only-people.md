# ADR-157: Sessions page, no person without an invitation, and invitations that die with their inviter's rights

Status: Accepted (Phase 19a, step 5). Implements D4 (identity part) and gate 5 of the Phase 19a plan, except the cookie work, which is the proposal in ADR-156. Resolves KI-068, KI-076 and KI-077 (the GitHub issues close with the close-out PR).
Date: 2026-10-14
Phase: 19a

## Context

Three small identity gaps were open: a person could not see or end their other sessions (KI-068); any account the identity provider vouched for got a `users` row at first sign-in whether or not anyone had invited it, so the table was bounded only by the sign-in rate (KI-076); and an invitation stayed valid for its seven days after the inviting admin was demoted or removed (KI-077). The decisions below are the ones a later contributor would otherwise question.

## Decision

### 1. Sessions (KI-068)

* **Routes** (people only, any role, no workspace needed; `SESSION_ONLY` in the authz registry, like `/v1/me`): `GET /v1/auth/sessions`, `DELETE /v1/auth/sessions/{id}`, `POST /v1/auth/sessions/revoke-others`. An API key, a session presented as a bearer token and a request with no credential all get the uniform `401 SESSION_INVALID` (the same as `/v1/me`; the plan said 403, but 401 is what the shared dependency already answers and a key holds no session to be forbidden). Writes carry the usual exact-`Origin` CSRF rule.
* **The handle** is the session row's own UUID, which is not a credential: sessions are found by the SHA-256 of the cookie value, and the row id is never accepted by anything but this page (the stream re-check reads it from the server-side principal). No new id kind was added to the shared event-schema package for it. The list never carries the cookie value, its hash or anything derived from either (tested by scanning the response).
* **One 404 for everything not yours.** A session of another person, an unknown id, an ended session and a malformed or non-canonical id (`UPPERCASE`, no dashes) are the same `404 SESSION_NOT_FOUND` with an identical body, so ids of other people's sessions cannot be probed.
* **The session in use is not revocable here** (`409 CURRENT_SESSION`; signing out is `POST /v1/auth/logout`), and `revoke-others` keeps it. This keeps "end my current session" in one place and makes the page unable to lock a person out by one mistaken click.
* **Revocation is immediate** because every request re-reads the session row (`authenticate_session`) and open streams re-check it every `ABB_STREAM_REAUTH_SECONDS` (`credential_still_grants`); there is no session cache. Tested for both (`tests/test_session_management.py`, `tests/test_stream_reauth.py`).
* **"Last used" is accurate to about five minutes** (`SESSION_SLIDE_RESOLUTION`): the row is written at most once per window so reads do not write. The page says so in the API description.
* **Device hint.** Sign-in stores a label such as "Firefox on Linux" in `sessions.device_hint` (migration 0099, nullable, length-checked to 64). The web relay forwards the browser's `User-Agent`, cut to 512 characters; the API reads at most 512 characters and reduces it to a name from two closed vocabularies (five browsers, six systems). **The header is never stored or shown**, so the hint is one of a few dozen fixed strings and can carry no markup, control character or identifier. Sessions made before the migration, and `create-session` ones, show "Unknown device".
* **The list is bounded** (100 working sessions, newest use first); `revoke-others` ends every one regardless of the list.
* **Audit** (fixed actions `session.revoke`, `session.revoke_others`; details are the count or empty, `resource_id` the session id). The audit log is per workspace and a session belongs to none, so the row is written into **each workspace the person belongs to** (at most 20), where their owners and admins can see it; a person with no workspace leaves a log line only. This is the same convention the CLI uses for `user.relink`. A no-op `revoke-others` (nothing to end) writes nothing.
* **Rate limit:** a new closed scope `session_write` on the shared limiter (ADR-152), global, keyed by the person, `ABB_SESSION_WRITES_PER_MINUTE` (30). Listing is not limited (it is a read, cheap, and the sign-in gate already protects the cookie).

### 2. No person until an invitation exists (KI-076)

* **The rule.** At the callback, a verified identity is resolved in the same order as before: by `(issuer, subject)`; then, if the email belongs to a user with no subject yet (made by the CLI: `seed`, `add-member`, `create-owner`; or cleared by `relink-user`), by linking to that user. **New:** if neither finds a user, a `users` row is created only while some workspace holds an **open** invitation (unaccepted, unrevoked, unexpired) for the verified, lower-cased email. Otherwise the callback writes nothing and answers `403 NO_ACCESS` ("No access yet. Ask a workspace owner to invite you.").
* **Why at the callback and not at acceptance.** Accepting an invitation needs a signed-in person, and a session needs a `users` row (foreign key). So the row has to exist before acceptance for the link flow to work at all; the person signs in, then opens the link. The cardinality bound is the invitations themselves: at most 200 open per workspace, each created by an owner or admin, none by an anonymous caller. Residual: a person invited and then revoked or expired before they sign in leaves no row, but one who signed in and never accepted leaves a row with no membership; purging those belongs with the audit/session retention work (KI-072).
* **No existence oracle, no stored state.** The same status, code, message and cookies for every un-invited case (no invitation, revoked, expired, accepted, a similar address), tested to be identical. The web app sends the person to `/login?error=no_access`, which shows the fixed sentence and never the address. The refusal reads one boolean from a partial index (`ix_invitations_open_email`, migration 0099) and logs only the reason `not_invited` and the issuer. It reveals nothing the provider did not already tell the person, and an attacker cannot enumerate invitations: they would have to control a verified mailbox at the provider.
* **Unchanged:** people who already exist sign in as before, with or without invitations: a user found by subject, a CLI-made user with no identity yet (the bootstrap owner, the compose demo's `owner@local.test`, anyone added with `add-member`), a user whose link `relink-user` cleared, and a user with no workspace. Their tests are in `tests/test_auth_sessions.py`.
* **Existing tests changed.** Several tests in `tests/test_auth_sessions.py` signed in strangers and expected a person to be created. They now first create an open invitation (helper `open_invitation`), which is the new precondition; their assertions are unchanged.

### 3. Invitations end with the inviter's right to invite (KI-077)

* **Definition.** An inviter "loses the right" when, in the same workspace, their role no longer holds `invite.write` (read from the role matrix, not a hard-coded list: today OWNER and ADMIN keep it; DEVELOPER, VIEWER, SECURITY and BILLING do not), or they are removed.
* **What is revoked.** Their **unexpired, unaccepted, unrevoked** invitations **in that workspace**, in the same transaction as the role change or removal (inside the same members lock the change already takes), so no moment exists where the links still work. A refused change (last owner, no permission) revokes nothing; a change to the same role revokes nothing; a change that keeps the right (ADMIN to OWNER, OWNER to ADMIN) revokes nothing. Other people's invitations, other workspaces' invitations and accepted ones are untouched: a person who already joined stays, and the remedy for an unwanted member is to remove them.
* **Audit.** One row per revoked invitation, action `invitation.revoke_inviter_lost_rights`, `resource_id` the invitation id, details `{inviter, reason}` (`inviter_role_changed` or `inviter_removed`), actor the person who made the change. As everywhere in the audit design (D11), the rows are written after the commit and a failed write is logged, never turning the change into an error.
* **The operator's `remove-member` command applies the same rule** in its own transaction, with `cli` audit rows. (The CLI has no role-change command.)
* **The token of a revoked invitation** is refused exactly like an unknown token (`404 INVITATION_NOT_FOUND`), because acceptance already treats a revoked invitation as absent.

## Consequences

* A hosted installation's `users` table grows only by invitations. The cost is one extra index read on a first sign-in.
* The sessions page gives the owner of a lost device a self-service remedy; the runbook's SQL stays as the administrator path.
* The cookie binding of the sign-in state is deliberately not in this ADR: see ADR-156 (Proposed).
