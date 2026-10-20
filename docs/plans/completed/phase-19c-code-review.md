# Phase 19c code review (security pass)

Branch `feature/phase-19c-operability`, 2026-10-16. Independent, adversarial review of operability (the metrics registry and `/metrics`, domain and scrape-time metrics, readiness, the database breaker and statement timeout, failed-session throttling, log schema and optional tracing, SLO assets, runbooks, `obs-e2e.sh`, supply-chain pins and lockfile audits) against the plan (D1-D9), ADR-165 and ADR-168 to ADR-173 and the AGENTS.md invariants, with a security pass. Reviewer: a Fable reviewer. Counts: **no P0, no P1**, two P2, ten P3; all fixed. (Written at close-out from the findings and dispositions the reviewer handed back; it records those, not every minor remark.)

## P2

1. **The breaker counted pool timeouts, so one low-privilege user could cause a process-wide 503 window and an unready pod.** Fixed: the breaker counts connectivity failures only; pool saturation reads `database: degraded/pool_saturated` and the instance stays ready (ADR-169 amendment, residual KI-270).
2. **A 503 or 504 mapped outside `MetricsMiddleware` was recorded as a 500.** Fixed: concrete exception handlers inside the metrics layer.

## P3 (all fixed)

The worker's metrics server has a total deadline and the rate limiter has a lock; caps on the textfile seed; exception groups render; the threshold in a document and the rule are aligned, with a test; the retention-stale alert uses the configured mode (residual KI-269); the quota alert is rate-based; the log-hygiene scan understands alias shapes; a head-lookup failure is handled; an ADR-173 note; a line in the stream-issues runbook; the Grafana unit.

## Verified correct (reviewer's list)

Five Action SHAs spot-checked against GitHub; the six image digests are multi-arch index digests; the SLO arithmetic; FastAPI's native telemetry is disabled; label cardinality is closed and capped; the readiness reason codes are fixed strings.

## Honest notes on the verification

- The obs-e2e check "worker succeeded counter equals completed queue rows" raced (the counter increments after the commit) and was changed to a bounded poll (`b30ab02`).
- A test-isolation bug (a runner-settings test read the `ABB_DATABASE_URL` that CI sets) failed CI once on #206 and was fixed in `58a6bae`; lesson recorded: emulate CI's environment (`ABB_DATABASE_URL` set, `--extra otel`) when verifying.

## Residual risks

KI-220 to KI-223, KI-225 to KI-249, KI-260 to KI-270 in `docs/KNOWN_ISSUES.md`, filed as GitHub issues in the close-out PR. Not verified here (env): the Dependabot configuration until its first scheduled run (KI-267).
