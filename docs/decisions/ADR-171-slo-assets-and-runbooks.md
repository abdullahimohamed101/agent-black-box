# ADR-171: SLO assets in the repository, the ingestion SLI excludes client refusals, and runbook consistency tests

Status: Accepted (Phase 19c, step 5). Implements D6 and D7 of the Phase 19c plan; the choices below were made by the assistant under the standing instruction and are listed so they can be reversed.
Builds on ADR-165 and ADR-168 (the catalogue and the SLI metrics), ADR-169 (readiness, breaker) and spec 116, 117, 125 and 126.
Date: 2026-10-16
Phase: 19c

## Context

The metrics exist (steps 1-4), but an operator still had to invent the queries, thresholds, dashboards and the meaning of "durably accepted" themselves, and the runbooks did not say which metric or alert to look at. Spec 117 sets an objective (99.95% of valid authenticated event batches durably accepted over 30 days) without defining the indicator.

## Decision

1. **Ship the assets in the repository** under `deploy/observability/`: a Prometheus scrape config, recording and alert rules, a Grafana dashboard and provisioning, and an optional compose profile `observability`. The rules and dashboard are tested against the catalogue, so a renamed metric breaks the build instead of a dashboard. The compose profile is a local view (loopback ports, in-memory 24-hour storage); it is not a monitoring system of record.
2. **The SLI is `committed / (committed + error)`** over `ingest_acks_total`. `rejected` (every 4xx) is excluded from the denominator: a client refusal is not a durability failure. The cost is stated in the rule file, the runbook and OPERATIONS: rate limits, quotas and validation refusals do not spend error budget, so a limit set too low refuses customers without any SLO alert. A separate ticket alert (`AbbIngestionRejectionsHigh`, over 20% of requests refused for 15 minutes) exists for exactly that. Requests that never reach the server are outside what a server metric can see.
3. **Multi-window multi-burn-rate alerts** (14.4x over 1 h and 5 min, 6x over 6 h and 30 min: page; 3x over 1 d and 2 h, 1x over 3 d and 6 h: ticket) with the budget arithmetic written in the file header. A test recomputes the thresholds from the 99.95% target.
4. **Alert thresholds are defaults with a stated reason each**, not measurements. They are expected to be tuned; tuning is editing one file.
5. **The token is read from a file** (`credentials_file`), never written in a config or an environment variable of Prometheus. In the profile it is `.local/metrics-token` (mode 0600, git-ignored, created by `make observability-token`, which prints nothing) mounted read-only; Prometheus runs as the invoking user so it can read it.
6. **`runbook_url` is a repository path with an anchor** (`docs/runbooks/x.md#heading`), checked against real files and headings. An operator who wants clickable links rewrites the prefix in Alertmanager's template.
7. **Consistency tests** (`tests/obs/test_observability_assets.py`) enforce: every metric and label in a rule or panel exists in the catalogue; every alert has severity, summary, description and a resolving runbook link; runbooks link only to what exists and mention only real metrics and alerts; the compose profile exposes loopback only. `promtool check rules` runs when the binary is present (step 6 runs it in a container).
8. **Security operations (spec 126) is documented honestly**: where only the audit log shows something, or nothing does, the runbook says so and the gap is a known issue (KI-242). No new metric was invented to look complete.

## Alternatives considered

- **Count `rejected` in the denominator.** Rejected: a customer sending invalid JSON would burn our budget, and the SLO would measure the clients.
- **A latency-based or availability-probe SLI as the durability SLI.** Not a replacement; the latency SLI is a separate ticket alert, and an outside probe is listed as a limit.
- **Publish the dashboard only to grafana.com or as a screenshot.** Rejected: untestable and drifts.
- **Persist Prometheus data in the profile.** Rejected: a named volume owned by another user conflicts with reading a 0600 token as the invoking user; the profile is for looking, not for history.
- **Per-tenant dashboards or labels.** Rejected by ADR-165; per-tenant questions are answered by logs and the audit log.

## Consequences

- Rules, dashboard and runbooks change together with the catalogue; adding a metric does not require touching them, renaming one does.
- Image tags are pinned to a version; digests are pinned with the other images in step 7.
- The assets are validated by tests and `docker compose config`; a real Prometheus and Grafana run is step 6 (`obs-e2e`).
- Open (KI to file): a backup that is never scheduled is invisible; no metrics for bloat, analytics load or the spec 126 signals; 30-day SLO panels need a Prometheus with 30 days of retention.
