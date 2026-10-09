# ADR-060: Dashboard Identity via OIDC With API-Owned, Database-Backed Sessions

Status: Accepted (supersedes ADR-021)
Date: 2026-10-09
Phase: 15

## Context
ADR-021 gave the web server one shared `runs:read` key (KI-029, S1): every visitor read that workspace, and one visitor could fill the key's
stream cap for everyone. Spec §91 says users authenticate through a standard identity provider and that authorization is application-owned.
The Next.js server and the FastAPI API must trust each other without a shared secret and without the browser ever holding a key.

## Decision
- **The API owns identity and sessions; the web server is a relay.** OIDC authorization code with PKCE is implemented in `abb_api.auth`
  against a discovery document (`ABB_OIDC_ISSUER`, `ABB_OIDC_CLIENT_ID`, optional `ABB_OIDC_CLIENT_SECRET`, `ABB_WEB_ORIGIN`). The discovery
  `issuer` must equal the configured one and its endpoints must be https on the issuer's host or on a host listed in `ABB_OIDC_EXTRA_HOSTS`
  (Google keeps its token and key endpoints on another domain; the default list is empty). `http` issuers exist only when
  `ABB_ENVIRONMENT` is `development` or `test`.
- **Sessions are opaque, hashed and revocable.** The cookie is 32 random bytes; the `sessions` row stores `sha256(token)`, an absolute expiry
  (`ABB_SESSION_ABSOLUTE_HOURS`, 168) and an idle expiry (`ABB_SESSION_IDLE_HOURS`, 24, slid at most every five minutes by ordinary requests;
  the stream re-check never slides it). The cookie is `__Host-abb_session; HttpOnly; Secure; SameSite=Lax; Path=/` for an https
  `ABB_WEB_ORIGIN` and `abb_session` without `Secure` for an `http://localhost` origin in development or test (one function decides, the web
  server mirrors it). Logout is `POST /v1/auth/logout` (no GET has side effects). Expired and revoked rows are purged opportunistically at
  login (bounded). Database trouble during validation is `503`, never `401`. No stateless tokens: revocation and role changes must be immediate.
- **Login state is a database row** (`login_states`: `sha256(state)`, nonce, PKCE verifier, validated `return_to`, 10 minutes) referenced by an
  `abb_login` cookie. The callback requires the cookie to equal the `state` query value (constant time) and a live row; the row is consumed and
  the cookie cleared on every outcome. A victim following an attacker's callback URL has no matching cookie, so login CSRF fails with no
  signing secret anywhere. `return_to` must match an allowlist of the app's own routes; the callback redirects to the stored, validated path only.
- **Tokens are verified, not trusted.** RS256 and ES256 only, against the provider's JWKS (cached one hour, one refetch per minute on an
  unknown `kid`); `iss`, `aud`, `azp` (when present), `exp`, `nonce` are checked, with times compared against the application clock so tests
  control them. `email_verified` must be present and `true`; a provider that omits it cannot log in until an explicit trust setting exists
  (named non-goal, decided after the first real-provider login, AC-14). Identity is matched on `(provider, subject)` first; the verified-email
  fallback applies only to a user with no subject yet (pre-provisioned by invitation or CLI). A verified email belonging to a user linked to a
  different subject is refused (`identity_conflict`, nothing changes); re-linking after an issuer change is the audited CLI command
  `relink-user --clear-subject`, which also revokes that user's sessions.
- **The web server forwards an allowlist**: the session cookie, `X-ABB-Workspace`, `Origin`, `Last-Event-ID`, `Accept` and a JSON body of at
  most 64 KiB. `Authorization`, other cookies, `X-Forwarded-*`, `Forwarded` and `X-Request-ID` from the browser are dropped; `Set-Cookie` is
  never relayed by the generic proxy; the sign-in handlers use `redirect: "manual"` and relay `Location` and `Set-Cookie`. A cookie-authenticated
  request that is not `GET`/`HEAD` must carry an `Origin` exactly equal to `ABB_WEB_ORIGIN`, checked by the proxy and again by the API. The API
  ignores `X-Forwarded-*` and never trusts the web server's network position: there is no internal bypass.
- **Login is rate-limited where the client is visible**: per client address (10 a minute) and per instance (120 a minute) in the web handlers,
  and a global backstop in the API (`ABB_LOGIN_GLOBAL_PER_MINUTE`, 600). Next keeps a client-supplied `X-Forwarded-For`, so the address is a
  hint; the per-instance bucket and the API backstop are what actually bound abuse. Phase 19's shared limiter replaces both (KI-019).
- **Unscoped lookups are now three**: `api_keys.key_id`, `sessions.token_hash`, `login_states.state_hash` (each by an unguessable value, then
  every query carries the workspace). `GET /v1/me` is the one deliberate read of a person's memberships across workspaces.
- **Development conveniences are gated on `ABB_ENVIRONMENT in {development, test}`, never `!= production`**: an `http` issuer, the fake
  provider (`apps/api/tests/fake_oidc.py`, `scripts/fake-oidc.sh`, the compose `fake-oidc` service), `create-session` and the seeded owner
  `owner@local.test` (the last two also need `ABB_ALLOW_DEV_SESSIONS=1`). The API logs a WARNING at startup when OIDC is configured outside
  `production` or dev sessions are enabled. `ABB_ENVIRONMENT=production` is mandatory for any reachable deployment.

## Dependencies (the five questions of `docs/BUILD_PROMPT.md`)
| Dependency | Platform provides it? | Maintained? | Surface used | Local alternative clearer? | Lock-in |
| --- | --- | --- | --- | --- | --- |
| `httpx` (runtime; was dev-only) | no async HTTP client in the standard library | yes | discovery, JWKS and token requests: `trust_env=False`, `follow_redirects=False`, 5 s timeouts, logger at WARNING so query strings and bodies are never logged | `urllib` would need its own async wrapper | none |
| `pyjwt[crypto]` | no | yes | `decode` with explicit `RS256`/`ES256`, JWKS key objects | no: signature verification is not something to hand-roll | none |

Both are pinned in `apps/api/uv.lock` and covered by the API's `pip-audit` (`make audit`). Rejected: `authlib` (larger surface) and skipping
signature checks because the token arrives on the back channel (OIDC Core 3.1.3.7 permits it; weaker and harder to review).

## Alternatives
Auth.js in Next (identity and sessions outside the API that enforces them: two sources of truth, untestable from the Python suite). Stateless
signed JWT sessions (no immediate revocation, key management). A shared proxy secret (does not identify the person). GitHub OAuth first (no
ID token or verified-email contract; an adapter later). A "current workspace" stored in the session (breaks independent tabs; see ADR-061).

## Consequences
The web app can be exposed behind a login; every request is attributable to a person; revocation, removal and role changes apply to the next
request and to open streams within `ABB_STREAM_REAUTH_SECONDS`. Two unauthenticated endpoints (login, callback) exist, bounded as above until
Phase 19. A real provider needs three settings plus the web origin. Providers that do not send `email_verified` cannot log in. Changing the issuer
requires re-linking users. The first OWNER of a workspace is created by the CLI (`add-member`); everyone else joins by invitation (ADR-061).
Self-service sign-up, SSO/SAML, SCIM, MFA, email delivery of invitations and a "log out everywhere" screen are deferred (see the phase plan).
