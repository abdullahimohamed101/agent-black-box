# ADR-165: A hand-written metrics registry, the closed catalogue, and the guarded `/metrics` endpoints

Status: Accepted (Phase 19c, step 1). Implements D1, D2 and D3 of the Phase 19c plan (D1 confirmed by the user; the choices below inside it were made by the assistant under the standing instruction and are listed so they can be reversed).
Builds on ADR-152 (the shared limiter, whose counters this ADR deliberately does not use for scraping), ADR-155 (the secure-headers middleware and the body budget), INV-4 (bounded everything), INV-8 (external conventions mapped at the boundary).
Date: 2026-10-16
Phase: 19c

## Context

An operator cannot tell whether an installation is healthy without numbers: request rates and latency, ingestion acknowledgements and refusals, queue depth, and what every control from Phases 19a and 19b is doing. The spec (§116) asks for a Prometheus-style endpoint. The product must not leak tenant data through it, must not let hostile input grow its memory, and must keep a near-zero runtime dependency budget.

## Decision

### 1. A hand-written registry (`abb_api/obs/metrics.py`)

Counter, gauge and fixed-bucket histogram; about 350 lines; no new dependency. Reasons for not adopting `prometheus-client` now: the dependency budget (the API's runtime set is small and every dependency is a supply-chain surface); we need a *closed catalogue* with per-label value sets and caps, which the library does not enforce (it accepts any label value), so we would wrap it anyway; and the process model is one process per container, so its multi-process mode is not needed.

**Trigger to adopt `prometheus-client` instead** (any one): (a) we run more than one API process per container and need aggregation across them (its multiprocess mode, or an equivalent); (b) exposition-format drift: we need OpenMetrics, exemplars, native histograms or a metric type we do not have; (c) a measured correctness or performance problem in this module that the library would not have. Migrating is mechanical because call sites use only `inc`, `dec`, `set`, `observe` and the catalogue's names.

Design points:

- **Closed catalogue.** Every metric is declared once, up front, in `obs/catalogue.py`, with its type, help text and label names; `seal()` forbids later declarations. A test pins names to the Prometheus conventions (snake_case; `_total` only on counters; histograms in `_seconds` or `_bytes`; no millisecond or minute names) and forbids label *names* that denote a tenant, person, key, address or path.
- **Label discipline (the cardinality rule).** Each label is either *closed* (a fixed value set; anything else is stored as `other`) or *capped* (at most `max_values` distinct values are ever admitted; later ones become `other`). Values longer than 80 characters are truncated. Each metric also has a hard `max_series` ceiling (default 500; the HTTP ones 3000 to 5000); past it, new combinations collapse into the all-`other` series, so memory is bounded whatever a caller passes and nothing is lost, only merged. Label values are never request text: the route label is the *matched route template* (`/v1/runs/{run_id}`), the method label is a closed set (`GET HEAD POST PUT PATCH DELETE OPTIONS`, others `other`), the status label is a code from 100 to 599. A request that matches no route is `unmatched`, so a scanner walking random URLs adds one series. No tenant id, workspace or project slug, user, key id, address or raw path is ever a label; tenant detail stays in logs and the app.
- **Hot path.** One lock acquisition and a dictionary update; no I/O, no `await` inside the critical section, so the same `threading.Lock` is safe for threads and asyncio tasks. A request costs four acquisitions (in-flight up and down, one counter, one histogram); a test counts them (never wall-clock time; 19a lesson). Measured timings are informational in `docs/benchmarks/phase-19c-metrics.md`.
- **Exposition.** Prometheus text format 0.0.4: `HELP` and `TYPE` lines, escaped label values (`\\`, `\"`, `\n`) and help text, histograms as cumulative `_bucket{le=...}` with `+Inf`, `_sum`, `_count`; metrics and series sorted, so output is deterministic. Unlabelled metrics always appear (at zero). A scrape takes each metric's lock once, copies, releases, then formats: it never blocks writers for the formatting time.
- **Default buckets.** Latency (seconds): 1 ms to 30 s in roughly 2.5x steps. Bytes: 256 B to 64 MiB in 4x steps. A metric may pass its own.
- **Process-wide default and isolated registries.** `abb_api.obs.catalogue.DEFAULT` is what modules increment; `build_observability()` makes an isolated registry plus catalogue for tests, and `create_app(..., observability=...)` accepts one.
- **The catalogue declares more than step 1 increments** (the control families of D3: limiter, throttle, quotas, body cap, retention, backups, alerts, search, sessions, invitations, plus the SLI pair for ingestion acknowledgements) so later steps only increment. A literal closed set that mirrors an enum elsewhere (limiter scopes, retention tiers) is a literal in the catalogue, because `obs` must not import the modules it instruments (import cycle), and a test pins it to the enum.

### 2. HTTP instrumentation

A pure ASGI middleware (`obs/http.py`), added last so it is **outermost**: refusals from inner layers (the body budget's 503 `BUSY`, CORS preflights, a 404) are counted with their real status. It wraps `send` only to read the status and passes every message through untouched, so streaming responses (SSE) are not buffered. `http_requests_in_flight` is decremented in a `finally`, so errors, cancellation and client disconnects cannot leak it; a request that ends without a response is recorded as `499` (cancelled, client gone) or `500`. Metrics: `http_requests_total{method,route,status}`, `http_request_duration_seconds{method,route}` (duration until the response is complete, which for a stream is its lifetime), `http_requests_in_flight`.

### 3. `GET /metrics` on the API

- **Absent unless `ABB_METRICS_TOKEN` is set.** Without it the route is not registered: a plain 404, byte-for-byte the response to any unknown path (tested).
- **Bearer token**, compared in constant time: both sides are hashed with SHA-256 and compared with `hmac.compare_digest`, so neither timing nor length reveals the secret. A missing, wrong or malformed credential gets the same `401 METRICS_TOKEN_INVALID` body in the standard error envelope with `WWW-Authenticate: Bearer`. The token is a `SecretStr`, never logged or serialised, at least 24 characters (checked at startup in every environment, not only production; the refusal does not echo the value). Blank means unset (Compose passes empty strings).
- **Rate limit:** one global bucket (`ABB_METRICS_REQUESTS_PER_MINUTE`, default 120), checked **before** the token, so guessing is slow whether or not a guess is right. This uses the local in-process limiter, not the shared PostgreSQL limiter of ADR-152: a scrape must never touch the database (a metrics scrape during a database outage is the moment it matters most), and adding a shared scope would need a migration. Documented trade-off: anyone who can reach the port can spend the bucket and make the real scraper see 429 for up to a minute. The mitigation is the network: do not expose the endpoint (OPERATIONS.md).
- **Not a v1 tenant route.** It carries no tenant data, `include_in_schema=False` (so it is not in `openapi.json`, `/docs` or the generated clients), and the route-walking registry test (`tests/authz`) accounts for it in an explicit `OPERATOR_ONLY` allow-list, requiring it to be bearer-only, action-free and hidden from the schema. It carries the standard security headers and `Cache-Control: no-store`; content type `text/plain; version=0.0.4; charset=utf-8`.
- The exposition is rendered on the event loop; its size is bounded by the series ceilings (a few hundred KB at the absolute limit).

### 4. The worker's endpoint (`ABB_WORKER_METRICS_PORT`)

The worker has no web framework, so `obs/server.py` serves the same registry with `http.server` in a daemon thread: off unless the port is set (which requires `ABB_METRICS_TOKEN`; startup fails otherwise), bound to `127.0.0.1` unless `ABB_WORKER_METRICS_BIND` (an IP address) says otherwise, `GET /metrics` only (everything else 404/405), the same token check and rate bucket, a 5 s socket timeout per connection, at most 8 concurrent connections (extra ones are closed at once), the stdlib's request-line (414) and header-count (431) limits, no request body ever read, HTTP/1.0 (one request per connection), no access log (the default would log client addresses), a clean `stop()` called from the worker's shutdown path. Compose does not publish the port.

## Alternatives considered

- **`prometheus-client` now.** Rejected for the reasons above; the trigger is recorded.
- **OpenTelemetry metrics.** Rejected for scraping: heavier, and INV-8 maps OpenTelemetry at the boundary; optional tracing stays in step 4.
- **Routes labelled with the raw path, bounded by a cap.** Rejected: ids in labels are the leak this design exists to prevent, and a cap on junk would still evict real routes.
- **A shared-limiter scope for scrapes.** Rejected: needs the database and a migration; see above.
- **A separate listener port for the API's metrics.** Would remove the rate-limit trade-off and keep the endpoint off the public listener; rejected for now because it changes the deployment shape (a second port per API process, health checks, proxies). Recorded as KI-220.
- **Authenticating `/metrics` with an API key or a session.** Rejected: operator data, not tenant data; a tenant key must never open it, and a separate token rotates independently.

## Consequences

- One new setting group (`ABB_METRICS_TOKEN`, `ABB_METRICS_REQUESTS_PER_MINUTE`, `ABB_WORKER_METRICS_PORT`, `ABB_WORKER_METRICS_BIND`); no migration; no new dependency.
- Metrics are per process and reset on restart (counters restart at zero; Prometheus handles that). The API and the worker are separate processes with separate registries: HTTP series appear only on the API, job series only on the worker. Scraping several API replicas is the scraper's job.
- Step 2 instruments the domain families against the declared names; a metric nobody increments is still exposed at zero (or absent for labelled families).
- The existing request log line still records the raw path (it is a log, not a metric); D4 reviews log content.
