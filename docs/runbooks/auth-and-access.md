# Runbook: sign-in, roles and access (Phase 15)

**Scope**: people signing in to the dashboard (OIDC), workspace roles, invitations, API keys, the audit log, and recovering from lockouts.
Design: ADR-060 (identity), ADR-061 (authorization), ADR-062 (audit). API: `docs/architecture/api-v1.md`.

## 0. Before anything is reachable
`ABB_ENVIRONMENT=production` is **mandatory** for any deployment others can reach. It is what switches off the conveniences that exist only for
development and tests: an `http://` issuer, `python -m abb_api.cli create-session`, the seeded `owner@local.test`, and the fake provider. The
API logs a WARNING at startup when OIDC is configured and the environment is not `production`, and when `ABB_ALLOW_DEV_SESSIONS` is set.
Never set `ABB_ALLOW_DEV_SESSIONS` in a deployment. Remove the `fake-oidc` service from any Compose file that is not on your laptop.

## 1. Configure a real identity provider
Set on the **API** (and the worker needs none of them): `ABB_OIDC_ISSUER` (https; the discovery document is
`<issuer>/.well-known/openid-configuration`), `ABB_OIDC_CLIENT_ID`, `ABB_OIDC_CLIENT_SECRET` (if the provider issues one) and `ABB_WEB_ORIGIN`
(the browser-facing origin of the web app, e.g. `https://abb.example.com`: scheme, host, optional port, nothing else). Set the same
`ABB_WEB_ORIGIN` on the **web server** and `ABB_API_INTERNAL_URL` to the API. Register the redirect URI `<ABB_WEB_ORIGIN>/api/auth/callback`
with the provider. An https origin makes the session cookie `__Host-abb_session` (`Secure`, `HttpOnly`, `SameSite=Lax`); put TLS in front.

| Provider | Notes |
| --- | --- |
| Google | token and key endpoints are on `googleapis.com`: set `ABB_OIDC_EXTRA_HOSTS=oauth2.googleapis.com,www.googleapis.com` (check the discovery document) |
| Okta, Auth0, Keycloak | endpoints on the issuer's host; nothing extra |
| Entra ID | v2.0 issuer `https://login.microsoftonline.com/<tenant>/v2.0`; may not send `email_verified` (see below) |

The provider **must send `email_verified: true`** in the ID token. Without it the login fails closed (`401 LOGIN_FAILED`, logged
`reason=email_verified_missing` with the issuer). Trusting such a provider is a deliberate later setting (KI-069), not a workaround.
Which claims a provider sends (`email_verified`, `azp`) is recorded in the phase plan after the first real login (KI-067).

Login failures return one fixed code to the browser and log the cause. Find it: `grep '"login failed"\|"login could not' api.log`; the `reason`
field names the cause: `state_cookie_mismatch`, `state_unknown_or_expired`, `identity_conflict`, `email_verified_missing`, `email_not_verified`,
`idp_error`, a `token_*` problem (`token_signature`, `token_nonce`, `token_expired`, `token_azp`...), `unknown_kid`, a `discovery_*` problem
(`discovery_untrusted_host` means the provider needs `ABB_OIDC_EXTRA_HOSTS`), or `provider_unavailable`. Logs never contain tokens, codes, states, cookies, emails or the provider's error text.

## 2. The first owner, and everyone after
Sign-up is invite-only. Create the workspace and its first OWNER with the CLI (on a host that can reach the database as the runtime role):

```bash
python -m abb_api.cli create-workspace --name "Acme" --slug acme
python -m abb_api.cli add-member --workspace acme --email you@example.com --role OWNER
python -m abb_api.cli list-members --workspace acme
```
`add-member` refuses an existing member (role changes are the audited API `PATCH /v1/members/{user_id}`). The person then signs in with that
email; the first verified login links their identity. Everyone else: Settings, Members, "Create invitation" (OWNER/ADMIN), copy the link, send it
yourself (there is no email delivery, KI-071). The link is shown once; it works once, for seven days, and only for a signed-in person whose
verified email equals the invited one. Revoke an open invitation in the same page.

## 3. Roles
Per workspace: OWNER, ADMIN, DEVELOPER, VIEWER, SECURITY, BILLING (table in `api-v1.md`; source of truth `apps/api/src/abb_api/authz/matrix.py`).
VIEWER and BILLING never see captured content (payloads, diffs, shell output): the run page says "content hidden by your role". Only an OWNER
can create, demote or remove an OWNER. A workspace always keeps at least one OWNER (`409 LAST_OWNER`).

## 4. Recover a workspace with no OWNER
The API refuses to remove the last owner, so this happens only if the owner's account is lost (left the company, identity provider changed).
1. Add a new owner with the CLI: `python -m abb_api.cli add-member --workspace acme --email new-owner@example.com --role OWNER`.
2. If the *same email* now belongs to a different identity (issuer changed, account recreated) the login says `identity_conflict`:
   `python -m abb_api.cli relink-user --email person@example.com --clear-subject` forgets the old link, revokes that user's sessions and writes
   an audit row in every workspace they belong to. Their next verified login links the new identity.
3. The CLI writes audit rows (`actor_kind=cli`), so the recovery is visible in Settings, Audit log.

## 5. Revoke sessions
- One person, everything: `relink-user --clear-subject` also revokes their sessions; removing them from the workspace (Members, Remove) takes effect on
  their next request and ends their open live streams within `ABB_STREAM_REAUTH_SECONDS` (default 30).
- Without unlinking (rare; there is no UI, KI-068), as the database owner:
  `UPDATE sessions SET revoked_at = now() WHERE user_id = (SELECT id FROM users WHERE lower(email) = lower('person@example.com')) AND revoked_at IS NULL;`
  The next request with that cookie is `401 SESSION_INVALID`.
- A person signs out of the current session with "Sign out" (`POST /v1/auth/logout`).
- API keys: Settings, API keys, Revoke (DEVELOPERs revoke only keys they created; keys made by the CLI need an ADMIN, OWNER or SECURITY). A revoked key is
  `401` on its next request and its open streams end within the re-check interval.

## 6. Purge, retention and growth
- `login_states` and `sessions` are purged opportunistically (each login deletes up to 100 expired rows). If a deployment sees no logins for a
  long time the tables simply stop growing. To clear by hand, as the database owner:
  `DELETE FROM login_states WHERE expires_at < now();` and `DELETE FROM sessions WHERE expires_at < now() - interval '7 days' OR revoked_at < now() - interval '7 days';`
- The **audit log is append-only for the runtime role** (it cannot UPDATE or DELETE). Retention is a privileged **owner** job
  (Phase 19, KI-072). Until then, if the table must shrink, as the database owner:
  `DELETE FROM audit_log WHERE occurred_at < now() - interval '400 days';` (take a backup or export first; do it from a maintenance connection, never from
  the application). Growth is bounded by administrative actions plus at most `ABB_AUDIT_DENIALS_PER_MINUTE` (10) denial rows a minute per person.
- Per-workspace bounds (`409 LIMIT_REACHED`): 500 members, 200 active API keys, 200 open invitations, 200 projects, 1,000 pricing overrides.

## 7. Symptoms
| Symptom | Likely cause |
| --- | --- |
| "Sign-in did not complete" on `/login` | a callback failure: read the `reason` in the API log (section 1); `state_cookie_mismatch` usually means cookies were blocked or the web origin differs from `ABB_WEB_ORIGIN` |
| "Sign-in is not configured" (`503 AUTH_NOT_CONFIGURED`) | `ABB_OIDC_*` unset on the API |
| Everyone is bounced to `/login` after sign-in | the cookie name differs: `ABB_WEB_ORIGIN` must be identical (https vs http) on web and API; behind a TLS-terminating proxy the browser-facing origin is https |
| `403 CSRF_REJECTED` on every write | the browser's `Origin` is not exactly `ABB_WEB_ORIGIN` (scheme, host, port) |
| "no workspaces" after signing in | the person has no membership: invite them or `add-member` |
| `403 PERMISSION_DENIED` / "Not available for your role" | working as designed; check the role in Settings, Members |
| `404` on a workspace or run you know exists | the person is not a member of that workspace (a workspace they cannot see is a 404 by design) |
| `429` on login | the login limiter (10 a minute per client address, 120 a minute per web instance, 600 a minute across the API); wait a minute |
| A live page says "Live updates stopped: your access to this run changed" | the credential, session or membership ended while the stream was open (`STREAM_UNAUTHORIZED`); reload and sign in again |
| Streams limited per person | `429 STREAM_LIMIT` with `details.scope: user`: 10 open streams across one person's tabs |
