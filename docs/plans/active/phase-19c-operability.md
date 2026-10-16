# Phase 19c: Operability (production hardening, part 3 of 3)

Status: Planned (2026-10-16). D1 was confirmed by the user in the grill ("A"); D2-D8 were decided by the assistant using its recommendations under the user's standing instruction for the overnight run (the user was asleep; every decision here is recorded so it can be reversed). Part 3 of Phase 19 (19a abuse resistance is merged; 19b data lifecycle is built and in review). This branch is stacked on `feature/phase-19b-data-lifecycle`; its PR opens against `main` after 19b merges.
Owner: api + scripts + deploy assets + docs. Branch: `feature/phase-19c-operability`.
Depends on: 19a (limiter, throttle, quotas), 19b (retention, backups), Phase 17 (alert delivery), Phase 5 (stream), job framework.
Spec refs: §116 (observability), §117 (SLOs), §118 (backpressure), §125 (runbooks), §126 (security operations), §127 (supply chain), §106; INV-8 (OpenTelemetry mapped at the boundary).

## Outcome

An operator can see whether the installation is healthy and why not: metrics for the spec's signals and for every control built in 19a/19b, readiness that checks real dependencies, optional tracing, SLO dashboards and alert rules that ship with the product, runbooks that name the metrics to look at, and a supply chain whose CI actions and base images are pinned and whose lockfiles are audited.

## Non-goals

- No metrics SaaS, no mandatory collector, no new always-on service (an optional compose profile ships Prometheus and Grafana for local use).
- No per-workspace or per-project metric labels (cardinality); tenant detail stays in logs and the app.
- No distributed tracing as a requirement: OpenTelemetry is an optional exporter, off by default.
- No Redis, no multi-node coordination.

## Decisions

- D1 (confirmed by the user) Technology: a Prometheus-style `/metrics` endpoint, structured JSON logs, and OpenTelemetry tracing only as an optional exporter (off by default). The metrics registry is a small hand-written module (counters, gauges, fixed-bucket histograms, a closed label vocabulary with a hard cardinality cap), not `prometheus-client`, to keep the dependency budget: ADR records the trigger for adopting it (multi-process aggregation need or exposition-format drift).
- D2 Exposure and safety: `/metrics` is served by the API only when `ABB_METRICS_TOKEN` is set, requires `Authorization: Bearer <token>` (constant-time compare), is excluded from the public CSP/docs logic, rate limited, never lists tenant ids, paths are templated (route patterns, not raw URLs), and the worker exposes the same registry on `ABB_WORKER_METRICS_PORT` (off by default; same token). Scrape-time database gauges (queue depth, oldest job age, retention lag, counter table size) come from one cheap bounded query cached for 15 s.
- D3 Catalogue: the §116.1 set (http requests and duration, events received/rejected/bytes, worker jobs pending and lag, processor failures, SSE connections and delivery lag, postgres pool wait, artifact upload failures) plus our controls (limiter fail-opens and database calls, failed-auth throttles and the active-key set size, quota refusals by quota, body-cap refusals and bytes in flight, retention batches/deletes/failures/dry-run counts, backup age if known, alert deliveries by status and dead letters, search busy by reason, session revocations, invitation-only refusals) and the SLIs of §117 (ingestion ack success ratio and latency histogram; accepted-event durability is the ack-after-commit counter pair; dashboard query success; live freshness; policy-decision availability).
- D4 Logs and tracing: logs keep the structured JSON shape; add the §116.2 fields where missing (`operation`, `error_type`) and prove with a planted-sentinel test that no payload, secret, token or address appears in any log line. Tracing: an optional extra (`otel`) with a span around the ingestion request through persistence and a link to the processing job, a span per job, no payload attributes, OTLP endpoint via standard env vars, tested with an in-memory exporter; absent the extra or the env, zero overhead.
- D5 Health and dependency safety: `/healthz` stays cheap liveness; `/readyz` checks the database, that the schema revision equals the code's head, that the artifact directory is writable, and reports (without failing readiness) worker heartbeat freshness; the startup role check (KI-183) is repeated on an interval and exposed as a metric and a readiness reason; an explicit timeout on every external call is enforced by a test that scans the code for unbounded `httpx`/`socket`/`smtplib` use; a default statement timeout for API database sessions with per-route overrides for budgeted reads; a small circuit breaker on database connectivity (after N consecutive pool/connect failures answer 503 DEPENDENCY_UNAVAILABLE immediately for a few seconds instead of piling up). KI-176 (failed session cookies not throttled): add shared counters with the 19a limiter for failed session lookups per address (behind a trusted proxy) and globally, failures-only, fail-open.
- D6 SLO assets: ship `deploy/observability/` with Prometheus scrape config, recording and alert rules (ingestion SLO burn-rate alerts, worker lag, dead letters, readiness flapping, limiter fail-open, retention failures, backup age), a Grafana dashboard JSON, and an optional compose profile `observability` (Prometheus + Grafana) for local use. A test fails if any metric named in the rules or dashboard is missing from the registry's catalogue.
- D7 Runbooks: complete §125 (database saturation and security operations runbooks are missing; ingestion-rejections, worker-lag, stream-issues, dead-letters exist and are updated to name the metrics and alerts), a runbook index, and a link/metric consistency test (every runbook's referenced metric exists).
- D8 Supply chain (KI-024, KI-060): pin every GitHub Action by commit SHA (tag kept in a comment) and every base image by digest; Dependabot configuration for actions, uv/pip, npm and Docker; `pip-audit`/`uv audit` for each integration lockfile in CI; a script `scripts/check-pins.sh` run in CI that fails on an unpinned action or image. This lands last because it touches every workflow line and other branches add CI jobs.
- D9 Gates: (1) `/metrics` is authenticated, disabled without a token, bounded in cardinality, and every catalogue metric moves under the right traffic (obs e2e against a real stack, API and worker); (2) readiness reflects real dependency state (database down, migrations behind, artifact dir unwritable, privileged role) and liveness stays green; (3) the breaker sheds fast during a database outage and recovers; (4) no payload, secret or address in logs (sentinel test); (5) optional tracing works with an in-memory exporter and costs nothing when off; (6) dashboards, rules and runbooks reference only existing metrics (consistency tests); (7) the pins script passes and fails on an unpinned reference; lockfile audits run; (8) metrics overhead on ingestion measured against a stated bound; no existing test weakened; clean CI; Fable review with security pass and no open P0/P1. CI scripts assert deterministic facts, not wall-clock thresholds (lesson from 19a).

## Known issues considered

Pulled in: KI-024, KI-060, KI-176, KI-183, the Redis-trigger wording note from ADR-152, KI-182 (a BUSY refusal metric gives the measurement its target asks for; the per-project share stays deferred). Stays out: KI-097 (Phase 20), KI-188 (hosting guide), KI-178.

## Acceptance criteria

The nine gates of D9, each evidenced by a command and its observed output; plus the repo standard (format, lint, mypy no args, tests, migration check where a migration is added, OpenAPI/client checks, `scripts/quality.sh full` green in CI, review with security pass and no open P0/P1).

## Verification plan

Unit tests for the registry (types, labels, cardinality cap, exposition format) and auth; integration tests per catalogue family; readiness tests with injected failures; breaker tests with a failing pool; log sentinel and call-timeout scan tests; in-memory OTel exporter tests; consistency tests for dashboards, rules and runbooks; `obs-e2e.sh` on a real stack with a CI job; pins script tests; my own gate run; then a Fable review with security pass.

## Risks

- Metrics on the hot path: the registry is lock-light and measured; the overhead bound is part of the gates.
- Cardinality explosions: a closed vocabulary and a hard cap, tested.
- A public metrics endpoint leaking internals: token-only, templated paths, no tenant ids.
- The breaker turning a blip into an outage: short windows, tested recovery, easy kill switch setting.
- Pinning breaks CI on first run: verified locally with the script, rolled in one commit, Dependabot keeps it fresh.

## Ordered steps (one commit unit each)

1. Metrics registry, HTTP instrumentation, `/metrics` with token auth, worker metrics port, the base catalogue, ADR.
2. Domain metrics for ingestion, jobs, limiter, throttle, quotas, body cap, retention, backups, alerts, stream, search; scrape-time database gauges; SLI metrics.
3. Health and dependency safety: readiness, periodic role check, statement timeout, external-call timeout scan test, database breaker, KI-176 session-cookie throttle.
4. Logs and tracing: field completeness, sentinel scrub test, optional OpenTelemetry.
5. SLO assets and runbooks: Prometheus rules, Grafana dashboard, observability compose profile, the missing runbooks, the index, consistency tests.
6. `obs-e2e.sh` with a CI job, overhead benchmark, closing evidence.
7. Supply chain: SHA pins, digests, Dependabot, lockfile audits, `check-pins.sh` and its CI step.
8. Review, fixes, push (stacked) and PR (opened against main after 19b merges).

## Progress

- Step 1 (metrics registry, HTTP instrumentation, `/metrics`, worker metrics server, base catalogue) built, 2026-10-16; not yet reviewed. ADR-165. **Registry** `abb_api/obs/metrics.py`: counter, gauge, fixed-bucket histogram; closed catalogue (`obs/catalogue.py`, sealed after declaration, 39 metrics including the control and SLI families that later steps only increment); every label closed (anything else becomes `other`) or capped, plus a per-metric series ceiling; exposition format 0.0.4, deterministic; isolated registries for tests (`build_observability`, `create_app(observability=...)`). **HTTP:** outermost ASGI middleware (`http_requests_total{method,route,status}`, `http_request_duration_seconds{method,route}`, `http_requests_in_flight`), route labels are matched templates, `unmatched` for no route, `other` for unusual methods, in-flight released on error, cancel and disconnect (a response-less end is 499/500), streaming untouched. **`GET /metrics`:** registered only when `ABB_METRICS_TOKEN` is set (24+ characters, `SecretStr`, validated in every environment; blank means unset), constant-time bearer check (SHA-256 of both sides plus `hmac.compare_digest`), one 401 body for every bad credential, global local rate bucket checked first (`ABB_METRICS_REQUESTS_PER_MINUTE`, 120), `text/plain; version=0.0.4`, `no-store`, hidden from OpenAPI, accounted for in `tests/authz` as `OPERATOR_ONLY`. **Worker:** `ABB_WORKER_METRICS_PORT` (needs the token) starts a stdlib server in a daemon thread (loopback by default via `ABB_WORKER_METRICS_BIND`, one route, same token, 5 s socket timeout, 8 connections, stdlib header limits, no access log, stopped on shutdown); Compose passes the variables and publishes nothing. Tests: `tests/obs/` (registry, catalogue consistency and parse-back, HTTP middleware, endpoint, worker server, cardinality with thousands of junk URLs and methods, canary scans of the exposition including a database-backed realistic-traffic scan, overhead by lock counts). Modified existing tests: `tests/authz/registry.py` and `test_registry_is_complete.py` (the `OPERATOR_ONLY` allow-list for `/metrics`, which exists only with a token; no existing assertion weakened). Decisions taken without the user, all in ADR-165: hand-written registry with the adoption trigger recorded; local (not shared) rate bucket so a scrape never touches the database; a token shorter than 24 characters refused in every environment; the worker needs the same token. New known issues KI-220..KI-223 (to file). Timings: `docs/benchmarks/phase-19c-metrics.md` (informational).
- Step 2 next: domain metrics (ingestion, jobs, limiter, throttle, quotas, body cap, retention, backups, alerts, stream, search), scrape-time database gauges, SLI metrics.

