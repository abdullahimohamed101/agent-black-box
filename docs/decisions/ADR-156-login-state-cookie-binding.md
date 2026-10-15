# ADR-156: Binding the sign-in state to the browser with a `__Host-` cookie (login CSRF from a sibling origin)

Status: Proposed (Phase 19a, step 5). A design for review. Nothing in this ADR is implemented: cookie behaviour is unchanged until the user approves it. Addresses KI-075 (S3).
Date: 2026-10-14
Phase: 19a

## Context

Sign-in is an OpenID Connect authorization-code flow with PKCE (ADR-060). The API starts it and finishes it; the web app relays both calls and the browser's cookies.

### The flow today, and where each secret lives

| Value | Made by | Lives in | Used for |
| --- | --- | --- | --- |
| `state` (256 random bits) | API, `LoginService.start` | the authorize URL (so the provider echoes it back); the `abb_login` cookie (the same string); the database as `SHA-256(state)`, the key of `login_states` | tie the callback to a started login |
| `nonce` (256 bits) | API | `login_states.nonce`; inside the ID token | tie the ID token to this login |
| PKCE `code_verifier` | API | `login_states.code_verifier`; only its S256 challenge goes to the provider | tie the code exchange to this login |
| `return_to` | the caller of `/v1/auth/login`, validated against a closed pattern | `login_states.return_to` | where to send the browser afterwards |
| the cookie | `GET /v1/auth/login` `Set-Cookie`, relayed by `apps/web/src/server/authRoutes.ts` | the browser: `abb_login=<state>; Path=/api/auth; HttpOnly; SameSite=Lax; Max-Age=600` (+ `Secure` on https) | **the only thing that ties the callback to the browser that started it** |

At the callback, `LoginService.finish` requires the URL `state` to equal the cookie's value (constant-time compare), then consumes the `login_states` row (single use, 10 minutes), exchanges the code with the stored verifier, checks the ID token against the stored nonce, finds or links the person, and creates a session. The web relay (`callbackRoute`) refuses to call the API when the browser has no login cookie, forwards only that cookie, and relays only `Set-Cookie` lines named `abb_login` or the session cookie.

The session cookie is already `__Host-abb_session` on https (host-only, `Secure`, `Path=/`, no `Domain`; `auth/cookies.py`). The login cookie cannot be: the `__Host-` prefix requires `Path=/`, and this cookie is scoped to `Path=/api/auth` so that it is not sent to the rest of the app.

### The threat (login CSRF by cookie planting)

The attacker needs one capability: to set cookies for the victim's browser on a name the app reads. That is possible from any sibling subdomain the attacker controls or can script (`evil.example.com` against `app.example.com`), from a network position on a plain-http page of the registrable domain, or from a subdomain takeover. A `Domain=example.com` cookie is sent to `app.example.com`; the browser sorts equal-length paths by age, so an older planted cookie is listed **before** the cookie the app itself sets later, and the app reads the first one (`cookieValue`).

1. The attacker starts a sign-in at the real app and signs in at the provider as themselves. They now hold a callback URL `…/api/auth/callback?code=C&state=S`, and a `login_states` row for `S` holds the verifier and nonce that match `C`. They do **not** open the URL.
2. From the sibling origin they plant `abb_login=S; Domain=example.com; Path=/api/auth` in the victim's browser, then send the victim the callback URL (a link, an image, a redirect).
3. The victim's browser requests the callback with the planted cookie. State equals cookie, the row exists, the code, verifier and nonce all belong to the attacker's own login, so every check passes. The victim's browser receives a **session for the attacker's account**.

Consequences: the victim works inside the attacker's person and workspaces. What they do there (create API keys and see them once, paste a policy, view runs, invite colleagues) is visible to the attacker. For a product that shows what agents did, the risky variants are the victim creating a key or uploading capture into an attacker-owned workspace. The attack gives the attacker no access to the victim's own account or data. It needs a cookie-setting foothold on a related origin plus a victim click, so it is S3 (security review F5), and it only exists where the app shares a registrable domain with something the attacker controls.

Nonce and PKCE do not help: they bind the code to the attacker's own server-side row, which is exactly what the attacker wants. The cookie is the only evidence from the victim's side, so its integrity is the whole defence.

## Options

### A. Move the login cookie to `__Host-abb_login`, `Path=/`, on https (recommended)

Same value, same single cookie, same checks. On an https web origin the name becomes `__Host-abb_login` and the attributes `Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600`, exactly as the session cookie already is. Browsers then refuse to store the cookie unless it came from a secure response of this host, with no `Domain`. A sibling cannot set it (a `Domain` cookie with this name is rejected), cannot overwrite it, and a planted cookie of the old name is simply not read.
On http (development, the fake provider, local compose) the name and path stay as they are; the session cookie already follows the same rule (`abb_session` on http), and the docs already say the prefix protections need https.

* Trade-offs: the cookie is now sent to every path of the web host for its 10-minute life (instead of `/api/auth` only). It is `HttpOnly`, the web proxy forwards nothing but the session cookie, and the API never sees it except on the callback, so the exposure is a few hundred extra header bytes for at most 10 minutes. The `/api/auth/callback` route is unchanged (the provider still redirects there; `SameSite=Lax` still sends the cookie on that top-level GET).
* Change surface: a pure function `login_cookie_name(settings)` next to `session_cookie_name`, the path becomes `/` when secure, and the same name function in the web app (`config.ts`, `authRoutes.ts`). No schema change, no new setting, no new dependency.

### B. Keep `abb_login` and add a second binding cookie `__Host-abb_bind`

At login start the API also issues a random `b` in a `__Host-` cookie and stores `SHA-256(b)` in `login_states`; the callback requires the state cookie **and** `b`. Same guarantee as A (the attacker cannot plant `b`) with two cookies, an extra column and migration, and a still-forgeable `abb_login` that no longer matters. It only buys one thing: the old cookie name could stay for `Path=/api/auth` consumers, and there are none. More moving parts for no additional protection. Rejected.

### C. Server-side state with no cookie (a client page holds the binding)

Point the provider's redirect at a small client page that compares a value in `sessionStorage` (written by a client sign-in start page) and then posts the code, state and secret to the API. `sessionStorage` is per origin and per tab, so a sibling cannot write it. Rejected: it needs JavaScript on the sign-in path, loses the flow when the sign-in is continued in another tab, and **changes the registered redirect URI**, which every deployment would have to re-register at its identity provider. The cost is far out of proportion to an S3 finding.

### D. Refuse a callback that carries the login cookie twice (complement, not an alternative)

A planted `Domain` cookie coexists with the host-only cookie the app sets (they are different cookies), so the `Cookie` header then holds `abb_login` twice. The web relay can see that and refuse (`login_failed`) instead of picking the first one. It is a few lines, it closes the common sibling case **for http and development origins where the prefix cannot be used**, and it turns the attack into a failed sign-in for as long as the cookie is planted (a nuisance, not an account confusion). On its own it is not enough: it does not stop an attacker who plants the only copy (the victim never started a login), and the browser cannot enforce it the way it enforces `__Host-`. Recommended as defence in depth next to A, not instead of it.

## Recommendation

Adopt **A, plus D** as a small defence-in-depth check. Do not adopt B or C.

### Precise change list (for the follow-up implementation, after review)

API (`apps/api`):
1. `auth/cookies.py`: `login_cookie_name(settings)` returns `__Host-abb_login` when `is_secure(settings)` else `abb_login`; `login_cookie_path(settings)` returns `/` when secure else `/api/auth`. Remove the module constants `LOGIN_COOKIE` and `LOGIN_COOKIE_PATH` (callers use the functions).
2. `auth/router.py`: `login` sets the cookie with the new name and path; `callback` reads `request.cookies.get(login_cookie_name(settings))` and clears it with the **same** name and path (a mismatch would not clear it; the existing comment says so).
3. No change to `login.py`, `login_states`, the OIDC client or any migration.

Web (`apps/web`):
4. `server/config.ts`: `loginCookieName()` with the same rule as `sessionCookieName()`; keep `LOGIN_COOKIE` only if something still needs the legacy name.
5. `server/authRoutes.ts`: `ISSUED_COOKIES` allows `loginCookieName()`; the callback reads and forwards that name.
6. (D) In `callbackRoute`, parse the raw `Cookie` header and refuse with `login_failed` if the login cookie name appears more than once. Needs a small `cookieCount(header, name)` next to `cookieValue`.

Docs: SECURITY.md (the login-cookie sentence and the https/prefix note), OPERATIONS.md (a line in the upgrade notes, see Rollout), KNOWN_ISSUES (KI-075 to Resolved), api-v1.md if it names the cookie.

### Test plan

* API, with an https `web_origin` (the existing `test_the_secure_variants_use_the_host_prefix_and_secure_cookies` pattern): the login `Set-Cookie` is exactly `__Host-abb_login=…; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=600` with no `Domain`; the callback clears it with the same attributes on every outcome; **the attack itself**: start a login as the attacker, then present the victim's callback with only a planted *legacy* `abb_login=S` (and, separately, with a planted cookie of the prefixed name sent beside none) and assert `LOGIN_FAILED` 400, no session cookie, no `users` or `sessions` row, and that the attacker's `login_states` row was **not** consumed by the refused attempt (so the attacker's own later use is unaffected, which keeps the behaviour of today's refusal path). On http the old name and path are unchanged (regression test).
* Web unit (`auth-routes.test.ts`): https origin uses the prefixed name in the allowlist, the callback guard and the forwarded header; http keeps `abb_login`; a `Set-Cookie` of the other name is not relayed; (D) two copies of the cookie in the header are refused without calling the API, one copy is accepted.
* A browser check of the claim the design rests on: in Playwright against the https variant below, `document.cookie = "__Host-abb_login=x; Domain=localhost; Path=/"` and a `Path=/api/auth` write of the same name are both not stored.
* End to end over https: a variant of `scripts/auth-e2e.sh` that starts the built web server with a throwaway self-signed certificate (`openssl`, `next start --experimental-https` with key and certificate files), `ABB_WEB_ORIGIN=https://localhost:3161`, the fake provider's redirect URI on that origin, and `ignoreHTTPSErrors` in Playwright; it signs in, asserts the `__Host-abb_login` cookie is `Secure; Path=/` while the flow runs and gone after, and that a planted `abb_login` copy cannot complete a sign-in. The existing http script keeps covering the unprefixed path.
* Authz/registry: unchanged (no route changes).

### Compatibility

* **Web relay and proxy:** only the cookie name and path change; the relay already filters by an allowlist of names, which moves with the function. The provider's redirect URI, `return_to` validation, the single-use database row and the 10-minute lifetime are untouched.
* **Fake OIDC e2e and the compose demo:** both run on http origins, so they keep `abb_login` and `Path=/api/auth` and need no change; their tests are the regression for the http path.
* **API-only clients:** there are none; the API is reached through the relay.

### Rollout and in-flight logins

* **Deploy the API and the web app together** (they already ship as one release). With the API ahead of the web app, the old relay would drop the new cookie name and sign-in would fail for the minutes of skew; with the web app ahead, the new relay would not read the old cookie. Neither state loses data.
* **Logins in flight at the upgrade** (started with the old cookie, at most 10 minutes earlier) fail at their callback with the usual "Sign-in did not complete. Try again." and succeed on the next try. The unused `login_states` rows expire on their own and are pruned by the next login start. There is **no fallback to the old cookie name**: a fallback would keep the planting attack alive for as long as it exists. The stale `abb_login` in a browser expires in 10 minutes and is never read on https.
* **Sessions are not touched.** Existing sessions and the session cookie are unchanged. **Rollback** has the same transient effect in the other direction.
* http deployments see no change at all.

### Residual risk after the change

* Plain http origins stay exposed to cookie planting (documented, development only; production refuses http issuers and requires an https web origin for the `Secure`/`__Host-` behaviour already).
* An attacker who can inject a `Set-Cookie` from the app's own origin (XSS, response splitting) is out of scope; the strict CSP of ADR-155 is the control for that.
* A sibling can still deny sign-in by planting an old-name cookie only on http (D turns that into a refusal rather than a confusion); on https it cannot affect the prefixed cookie at all.

## Consequences

* One cookie, one rule, shared with the session cookie: `is_secure` decides the prefix, `Secure` and `Path` for both.
* The login cookie is visible to all paths of the web host for its lifetime. Accepted, see A.
* The fix is small (about forty lines plus tests) and needs no migration, which is why it is proposed rather than the heavier B or C.

## Decision

Pending review. Recommended: A with D.
