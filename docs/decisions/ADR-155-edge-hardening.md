# ADR-155: Edge hardening: body-bytes cap, secure headers and CSP, docs per environment, startup role check

Status: Accepted (Phase 19a, step 4). Implements D4 (edge part) and gate 4 of the Phase 19a plan. Resolves KI-026, KI-023 and KI-031.
Date: 2026-10-14
Phase: 19a

## Context

Four edge gaps were open. Request bodies of up to 5 MiB are buffered whole, with no limit on how many are in memory at once (KI-026, S2). The API and the web
app sent only `nosniff` and a referrer policy, and the web CSP allowed `'unsafe-inline'` scripts and styles. `/docs`, `/redoc` and `/openapi.json` were served in every environment
(KI-023). Nothing checked at startup that the API and worker were connected as the least-privilege database role rather than the owner, so a misconfigured deployment silently lost the INV-1
database protection (KI-031). The plan settled the answers with the user (D4); this ADR records the mechanics and the trade-offs.

## Decision

### 1. A per-process cap on request-body bytes held in memory (KI-026)

`abb_api.core.body_budget` is a pure ASGI middleware (innermost, so a refusal still gets CORS headers and a request ID). `ABB_MAX_BODY_BYTES_IN_FLIGHT` (default 64 MiB; it must be at least
the largest single body, checked at startup) is the most body bytes this process holds at once.

* **Only bytes the handler actually pulled from the socket are counted.** A request refused before its body is read (a bad key, a rate limit, a quota) costs nothing, and an anonymous
  client cannot reserve budget by declaring a large `Content-Length` and never sending it. This is the reason it is not a reserve-by-`Content-Length` scheme: that would let 13 anonymous slow
  connections starve ingestion.
* **Before the body is read:** a request whose `Content-Length` (capped at the largest allowed body) would not fit in what is left is answered `503 BUSY` with `Retry-After: 2` and its body is
  never read. **While it is read:** chunked bodies and requests admitted together are counted chunk by chunk; the chunk that would pass the cap is refused with the same `503 BUSY` (raised
  into the normal error handler, so the envelope is the one every route uses: category `DEPENDENCY`, `retryable: true`).
* **Release cannot leak.** The middleware releases in a `finally` around the application call, so a client disconnect (the server's `http.disconnect`), a cancelled task and an exception all return the
  bytes (tested for each, with the accounted bytes back at zero). Release is at the end of the request, not after the read, because the buffered bytes live as long as the handler.
* **Bounded in time.** A body not fully received within `ABB_BODY_READ_TIMEOUT_SECONDS` (60) is dropped with `408 REQUEST_TIMEOUT` (retryable), so a client trickling bytes cannot hold budget for ever.
* **Beyond routes that call `read_body`:** the middleware sees every request with a body, including the JSON writes that FastAPI parses itself, and applies the global ceiling
  (`max(ABB_INGEST_MAX_BODY_BYTES, ABB_ARTIFACT_MAX_BYTES)`, 8 MiB): a body that passes it is `413 PAYLOAD_TOO_LARGE` whatever the route. Before this, those routes had no body limit at all.
* **Per-request caps stay:** 5 MiB for ingestion (compressed and decompressed), 8 MiB for artifacts, 64 KiB for approvals; the middleware sits above them.
* **Fairness.** First come, first served. A body of at most 64 KiB is always admitted (it is still counted), so sign-in, approval decisions and other small writes are not starved by a flood of large batches;
  the memory bound is therefore the cap plus 64 KiB for each concurrent request, which the server's own concurrency bounds. There is no per-project share: per-project fairness is the rate limiter's (ADR-152) and the quotas' (ADR-154) job.
  One consequence worth saying plainly: a flood of authenticated large bodies makes other authenticated large bodies get `503 BUSY` until it passes. The SDK retries `503` with backoff, so well-behaved clients recover.
* **What the cap bounds.** It bounds *accounted* body bytes. The process holds more than that per body while it is being handled (the bytearray read, its `bytes` copy, the decompressed copy), so resident memory is a small
  multiple (measured 4 to 6.5 times) of the cap plus the process's baseline; `docs/benchmarks/phase-19a-edge.md` measures it (peak resident set size with and without the cap). Choose the cap with that multiple in mind.

### 2. Secure headers

**API** (every response, one place, `RequestContextMiddleware`): `X-Content-Type-Options: nosniff`, `Cache-Control: no-store` (already set on every response, so authenticated, session, key-bearing and error responses are all covered; the audit found no route that needs a different rule),
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, and
`Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'` (a JSON API renders nothing, so it may load nothing). The development/staging `/docs` and `/redoc` pages (Swagger UI from a CDN with an inline script)
are the only responses without the CSP; they still carry the other headers. `Strict-Transport-Security: max-age=31536000; includeSubDomains` only when the deployment says it is served over HTTPS, using the signal that already exists: `ABB_WEB_ORIGIN` is an
`https://` origin (the same test that turns on the `Secure` cookie and the `__Host-` prefix, `auth.cookies.is_secure`). No second setting. No `preload`.

**Web** (Next 16, `src/proxy.ts`, policy in `src/server/csp.ts`):

| Directive | Value | Protects against |
| --- | --- | --- |
| `default-src` | `'self'` | any resource type nobody listed loads only from our origin |
| `script-src` | `'self' 'nonce-<per request>' 'strict-dynamic'` | injected script: nothing runs without this response's nonce, no `'unsafe-inline'`, no `'unsafe-eval'` (development adds `'unsafe-eval'` only, for React's stack rebuilding). `'self'` is the fallback for a browser without `strict-dynamic` |
| `style-src` | `'self' 'nonce-<per request>'` | injected `<style>`: stylesheets are our own files (and nonce'd Next-generated elements) |
| `style-src-attr` | `'unsafe-inline'` | **the one weakening, see below** |
| `font-src` | `'self'` | no third-party font request: the typefaces are vendored (ADR-110) |
| `connect-src` | `'self'` | the app talks only to its own origin (the `/api/abb` relay, the `/api/auth` routes and the live-stream `EventSource`); exfiltration through `fetch`, XHR, WebSocket or SSE to elsewhere is refused |
| `img-src` | `'self'` | nothing loads an image from elsewhere (there are none today, the policy keeps it that way); no `data:` |
| `frame-ancestors` | `'none'` (+ `X-Frame-Options: DENY`) | clickjacking |
| `object-src` | `'none'` | plugins |
| `base-uri` | `'self'` | an injected `<base>` retargeting relative URLs |
| `form-action` | `'self'` | a form (or an injected one) posting credentials elsewhere |
| `frame-src`, `worker-src`, `media-src` | `'none'` | features the app does not use |
| `upgrade-insecure-requests` | only over https | mixed content |

Also on every response: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` (stricter than `strict-origin-when-cross-origin`; invitation tokens live in the URL fragment, which is never sent),
`Permissions-Policy` denying camera, microphone, geolocation, payment, usb, serial, bluetooth and the interest-cohort API, `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Resource-Policy: same-origin` (the app opens no popups and
shares no resource cross-origin), and `Strict-Transport-Security` only when `ABB_WEB_ORIGIN` is https. Routes under `/api` (relay, sign-in, health) get the deny-everything policy
(`default-src 'none'; frame-ancestors 'none'; ...`). The static headers are also set in `next.config.ts` so Next's own assets carry them.

**The nonce approach.** Next renders inline bootstrap scripts (the flight data and runtime), so a strict policy needs a nonce. `src/proxy.ts` makes 128 random bits per request, puts the policy on the *request* headers (Next reads
the `nonce-` value from there and stamps its own scripts and style elements) and on the response. Consequences, all accepted: every page is rendered per request (`dynamic = "force-dynamic"` in the root layout; the
pages are client-driven so there was nothing useful to prerender), pages are not CDN-cacheable, and a reverse proxy must not cache HTML. The nonce is never reused (tested). `'strict-dynamic'` lets the nonce'd bootstrap load Next's chunks without
listing hashes or hosts.

**The one weakening we accept: `style-src-attr 'unsafe-inline'`.** Server-rendered `style="..."` attributes (a computed bar width, a virtual-list padding, a replay marker offset: `style={{ width: ... }}` in a dozen components) are blocked
by a CSP without it, and the page renders unstyled until hydration. CSS attributes cannot run script; the residual risk is CSS-based UI redress and data-attribute exfiltration through CSS selectors on injected markup, and an attacker who can inject markup
but not script is already stopped from the worse outcomes by `script-src`. It is scoped to attributes: `style-src` (elements) has no `'unsafe-inline'`. The alternative (rewriting those components to set sizes through a CSSOM `ref` or a CSS custom property
written after hydration) was weighed and rejected for this phase: it touches rendering code for a low-risk directive. If it is revisited the directive can simply be deleted and the spec in `e2e/csp.spec.ts` tells which pages break. Recorded as KI-181.

**Verified in a real browser.** `apps/web/e2e/csp.spec.ts` derives the route list from `src/app` (every directory with a `page.tsx`, plus a missing page), opens each at 1440 and 375 px with a `securitypolicyviolation` listener
installed before page scripts, and requires: zero violations, zero console errors or page errors, every inline script carrying the nonce, fonts loaded, the stylesheet applied, axe clean (serious and critical), and the headers above on the document response. A negative control proves
the policy is enforced (an injected inline handler does not run and is reported). Real sign-in, the relay and the live stream were exercised through the existing real-API chains (`scripts/auth-e2e.sh` and `scripts/stream-e2e.sh`).

### 3. `/docs`, `/redoc`, `/openapi.json` per environment (KI-023)

Off when `ABB_ENVIRONMENT=production`; on in development, test and staging. `ABB_API_DOCS_ENABLED` (unset follows the environment; `true`/`false` overrides either way). Staging keeps its current behaviour unless the setting says otherwise.
`python -m abb_api.openapi` and `pnpm gen:api` call `app.openapi()` directly, never the route, so the committed `openapi.json` and the client generator are unaffected (tested: the schema builds with the routes off).

### 4. Startup check of the database role (KI-031)

`abb_api.db.role_check` asks the connected role one question and the API and worker lifespans act on it. **Too privileged** is any of: superuser; `BYPASSRLS`; `UPDATE`, `DELETE` or `TRUNCATE` on `events` (this is what breaks INV-1, and it catches a table
owner, a member of the owner role and an explicit grant alike); membership in the role that owns `events`; or `CREATE` on the `public` schema (can alter the structure). The reasons are a closed set (`superuser`, `bypass_rls`,
`events_rewritable`, `events_owner`, `schema_create`).

* **Production:** refuse to start with a fixed message (`PrivilegedDatabaseRole`; the worker exits with it, the API fails its lifespan). The message and the logs contain only the closed reasons: never a role name, host, database or connection string.
  If the question cannot be answered (the database is unreachable), production also refuses, with its own fixed message: a privileged process must not slip through because the database was down at boot. The cost: the API does not start while the database is down in production
  (it could not serve anyway; an orchestrator retries).
* **Development, test, staging:** one warning per start (the dev stack deliberately keeps the owner for convenience, `.env.example`).
* **Override:** `ABB_ALLOW_PRIVILEGED_DB_ROLE=1` accepts the situation knowingly; every start then logs a warning naming the reasons accepted (the audit line).
* **Only the serving processes check.** Migrations run as the owner through alembic (`ABB_MIGRATION_DATABASE_URL`), which never goes through these lifespans. The CLI is not checked (KI-183): its commands that matter run by operators (create-key, grant, seed) do so with
  whichever role the operator chose, and refusing to run an admin tool would not protect anything the operator cannot already do; the CLI's production guard against dev-only commands is separate.

## Alternatives considered

* Reserve-by-`Content-Length` (rejected, above: anonymous slowloris). A semaphore around the handler (rejected: it would also serialise requests that read nothing). `uvicorn --limit-concurrency` (not byte-aware, and a refusal there is a bare 503 outside our envelope).
* A hash-based CSP (`experimental.sri`) instead of nonces: keeps static rendering, but is not available with the Turbopack build and hashes the framework's inline scripts only; the nonce approach is the documented stable one.
* Keeping `'unsafe-inline'` for styles entirely: rejected; `style-src-attr` is a strictly smaller allowance.
* HSTS from a new setting: rejected, `ABB_WEB_ORIGIN` already decides every other https behaviour.
* Checking the role in readiness instead of at startup: a readiness warning does not stop a privileged process from taking traffic.

## Consequences

* New settings: `ABB_MAX_BODY_BYTES_IN_FLIGHT`, `ABB_BODY_READ_TIMEOUT_SECONDS`, `ABB_API_DOCS_ENABLED`, `ABB_ALLOW_PRIVILEGED_DB_ROLE`. No migration.
* New response codes on every body-reading route: `503 BUSY` (retryable) and `408 REQUEST_TIMEOUT`; `413` for any body over the global ceiling. They use the existing error envelope; the OpenAPI document is unchanged.
* A production API refuses to start when its database role is the owner: deployments that did this must switch to `abb_runtime` (docs/OPERATIONS.md, "Database roles") or set the override.
* Every web page is rendered per request.
* Limits of the body cap: per process (not shared across processes); it does not bound the work after a body is read; it does not replace a reverse proxy's body limit and read timeouts (OPERATIONS.md says to set both). A hostile authenticated client can still fill the cap with
  its own legitimate large uploads for as long as its rate limit allows, which sheds other large uploads with 503 (KI-182).
