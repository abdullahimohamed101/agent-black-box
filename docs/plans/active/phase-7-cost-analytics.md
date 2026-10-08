# Phase 7 - Cost and analytics

Status: In progress
Owner: implementer agent
Branch: `feature/phase-7-cost-analytics` (from `main` cc56935; worktree `../abb-worktrees/phase-7`)
Depends on: Phase 2 (derived runs/spans, outbox), Phase 4 (web), Phase 5 (live runs)
Spec refs: §79 (cost engine), §22-23 (analytics, cost), §24 (retry cost), §61.2/61.4 (dashboard p95, Stage A), §138 (launch bar), INV-1/2/3/7
ADRs: ADR-040 (cost engine and pricing), ADR-041 (analytics store and aggregates), ADR-042 (retry-cost attribution)
Migration ids used: 0040 (chains from 0009). Known issues ids: KI-050..

## Outcome
A developer opens a project's Analytics page and sees what agents cost, where the money went (agent, model, day, retries), how reliable
they are (success, failure, timeout, retry rates; tool success) and how fast (p50/p95 run, model and tool latency), with every cost figure
traceable to its source (provider-reported, computed from a versioned price, client estimate, or unpriced). The dashboard shows
whole-window aggregates computed by the server instead of a sample of 200 runs (KI-028). Phase ends at the MVP gate (§138 checklist, human review).

## Non-goals
Budgets and enforcement (§79.4, Phase 14), invoice reconciliation (§79.3), cost by tool/tool-specific price events, evaluation-based quality
metrics (Phase 11), anomaly detectors and cost-spike findings (Phase 10), ClickHouse or any new infrastructure, per-user auth (Phase 15), run
detail page changes (Phase 6 area), quotas (KI-018). Real vendor prices: no price list can be verified offline (see KI-050).

## Current architecture (what exists)
- `runs` hold a JSONB `summary` derived by `runs/summary.py` (pure, INV-2): `estimated_cost_usd` is the sum of the client-supplied
  `cost.estimated_usd` on `llm.request.completed`; `spans` hold kind/status/duration. The worker's `summarize_run` rewrites both under a run row lock.
- No pricing, no per-call cost record, no retry attribution, no aggregate endpoint. The dashboard computes figures over the latest 200 runs.
- The read proxy allowlists `/v1/runs...` only. API keys: project-bound or workspace-wide, scope `runs:read`.

## Known issues considered
- KI-028 (S2, target Phase 7): pulled in. Acceptance 7: dashboard figures come from `GET /v1/analytics/summary`; the 200-run sample code is removed.
- KI-016 / KI-022 (S2, summarizer cost grows with run size): pulled in only as a constraint. Cost calculation runs inside the existing summarizer pass
  over events already in memory, so it adds O(llm events), not a second read; the benchmark records summarize time with cost on.
- KI-027 (S2, no project lookup): stays deferred (Phase 15). Analytics take a project id exactly as runs do (`all` = no filter); no new lookup endpoint.
- KI-029 (S1, one shared web key): stays (Phase 15). Analytics adds read paths to the same proxy allowlist; they are `runs:read` only and tenant-scoped. Noted in review.
- KI-018 (S1 unbounded cardinality): analytics group by agent slug/model/tool name, which a client controls. Every grouped result is capped (top-N + "other") so the
  response size is bounded regardless of cardinality (acceptance 6).
- KI-019 (S1, failed-auth throttling), KI-017, KI-026, KI-021, KI-024, KI-023, KI-025: unrelated, stay deferred. KI-025 (cross-project sort index) is watched by the benchmark.
- KI-032/033/034/035/030/031/013/008/009/010/014/015: unrelated.

## Decisions
- **D1 (ADR-040) Cost engine.** A pure `CostEngine` computes a `CostLine` per `llm.request.completed` event from tokens and a versioned price entry
  (§79.1 fields). Price entries are immutable data with `pricing_version`, `valid_from/valid_to`, `model_pattern` (glob), per-million prices for input,
  output, cached input and a per-request price. Workspace/project **overrides** are rows in `pricing_overrides` (append-only; newest `valid_from` wins; they beat built-in
  entries). Effective cost per call, in order: provider-reported (`cost.provider_usd`, a new optional attribute) > engine estimate (priced model) > client estimate
  (`cost.estimated_usd`) > unpriced (cost 0, flagged, never silently zero). Each `cost_calculations` row stores source, pricing version, the price components and
  both the reported and the estimated value so the UI can show estimated and provider-reported side by side. The built-in table ships illustrative example-provider
  entries only, not real vendor prices (KI-050).
- **D2 (ADR-040) Where cost is computed.** Inside `summarize_run` (same lock, same event snapshot), so costs are rebuilt exactly when summaries are (INV-2) and reproducible
  from events + pricing metadata. `SUMMARY_VERSION` 2 adds cost fields; `rebuild-costs` CLI re-enqueues runs after a price/override change.
- **D3 (ADR-042) Retry cost.** An LLM call is a retry call if a `retry.attempted` event with scope span S precedes it in canonical order and the call's span is S or a
  descendant of S (scope = `span_id` of the retry event; retries with no span are counted but not attributed). Initial-attempt cost = run cost - retry cost.
- **D4 (ADR-041) Analytics reads derived tables only** (`runs`, `spans`, `cost_calculations`), never `events` (INV-7 behind `AnalyticsStore`). Every method takes an
  `AnalyticsScope(tenant, project_id, window)`; the service forces the scope's project from a project-bound key. Aggregates are not materialized unless the
  benchmark shows a query over budget; if so a rebuildable rollup is added behind the same interface.
- **D5 API shape.** Four read endpoints under `/v1/analytics/` (`summary`, `cost`, `reliability`, `performance`) plus `GET /v1/pricing`. Window `from`/`to`
  (default last 7 days, max 92), optional `project_id`, `agent_id`. Overrides are managed by CLI (no new API-key scope; admin surface arrives with Phase 15).
- **D6 Definitions.** Finished = SUCCESS, FAILED, TIMED_OUT, BLOCKED; CANCELLED counted separately and excluded from rates. success_rate = SUCCESS/finished,
  failure_rate = (FAILED+BLOCKED)/finished, timeout_rate = TIMED_OUT/finished. retry_rate = runs with retry_count>0 / runs. Tool/LLM success = spans of kind tool/llm
  with status success / spans with a status. Runs are bucketed by `started_at` (UTC days).
- **D7 UI.** Trace-derived text (agent, model, tool names, run names) is rendered only as React text nodes; charts are small hand-written SVG/CSS bars (no charting dependency,
  no `dangerouslySetInnerHTML`). Each chart has an adjacent data table or text alternative.

## Proposed design / affected files
- `packages/event-schema`: optional attribute `cost.provider_usd` (+ regenerated schemas/types, docs). SDK: `record_usage(provider_cost_usd=)`.
- `apps/api`: `cost/` (pricing, engine, retry attribution, repository), `analytics/` (store protocol, Postgres store, service, router, schemas), migration 0040
  (`cost_calculations`, `pricing_overrides`), `runs/summary.py` (cost lines in derivation), `jobs/handlers.py`, CLI (`set-pricing-override`, `list-pricing`, `rebuild-costs`), `main.py`.
- `apps/web`: dashboard on the aggregate endpoint, `/analytics` page and components, proxy allowlist, fixtures, generated client, e2e spec.
- `scripts/`: `analytics_seed.py` (deterministic generator), `bench_analytics.py`, `analytics-e2e.sh`; `Makefile` targets; CI job.
- Docs: ADRs, `docs/architecture/api-v1.md`, `events.md`, `docs/benchmarks/phase-7-analytics.md`, KNOWN_ISSUES, DECISIONS.

## Acceptance criteria
1. Pricing/cost unit tests pass, including: version selection by `occurred_at`, glob specificity, override precedence, source precedence, rounding, cached tokens,
   unpriced flag, and **historical reproducibility** (a line computed under version V is reproduced exactly after newer versions are added; pinned recompute equals stored values).
2. Retry attribution tests (nested spans, no-span retry, retry before/after, cycles) pass; `retry_cost + initial_cost == run_cost`.
3. Rebuild property: cost lines and summary derived from events are identical under shuffled arrival/batching and after delete+rebuild (INV-2).
4. Analytics integration tests on seeded data (ingestion -> worker -> API) check every metric against independently computed expectations.
5. Tenant isolation: another workspace's data never appears (every endpoint); a project-bound key cannot read another project (`project_id` mismatch -> 404) and is
   forced to its own project when omitted; a workspace-wide key sees all projects; scope `runs:read` is required (401/403 tests).
6. Cardinality bound: a project with >1000 distinct agents/models/tools returns bounded responses (top-N + other).
7. KI-028: the dashboard shows server aggregates over the whole window; `computeDashboard` sampling removed; web tests updated.
8. Benchmark: dashboard summary p95 < 1.5 s on the Stage A dataset, MEASURED and recorded in `docs/benchmarks/phase-7-analytics.md` with the seed command and hardware.
9. Browser E2E (Playwright + axe + screenshots) on the Analytics page and the dashboard against a real API and seeded data; no `dangerouslySetInnerHTML`; hostile names render as text.
10. `make openapi`, `pnpm gen:api` current; `scripts/quality.sh full` exit 0; migration up/down/up and drift test pass.
11. Spec §138 launch bar run as a checklist with evidence in the completed plan. The MVP is not declared accepted.

## Verification plan
Unit (pricing, engine, retry), property/rebuild, repository + API integration on real Postgres, migration tests, web vitest, Playwright E2E via
`scripts/analytics-e2e.sh` (own database abb_p7, API :8150, web :3150), benchmark script, then `scripts/quality.sh full`. Mutation checks on committed cost and
scope-filter code. Independent verify and review passes (skills) before completion.

## Risks
- Cost correctness is trust-critical: every line states its source and pricing version; unpriced calls are visible, not zero-priced.
- Tenant scoping of aggregates is easy to get wrong (a missing `workspace_id` predicate leaks): one query builder applies the scope to every statement; tests assert isolation per endpoint.
- Schema change (`cost.provider_usd`) is additive/optional; conflicts possible with Phase 6 edits to the registry (resolve at merge).
- Percentiles over large windows cost time; benchmark decides whether a rollup is needed.
- Summary version bump leaves old runs on v1 cost fields until rebuilt; analytics read `cost_calculations` and fall back to `summary` only where noted (`rebuild-costs`).

## Ordered steps (one commit each)
1. Plan, ADR-040..042 and KI entries.
2. Event schema: `cost.provider_usd`; SDK `provider_cost_usd`.
3. `cost/` pure modules + unit tests (pricing, engine, retry attribution).
4. Migration 0040 + tables + derivation integration + repository + handler; rebuild tests.
5. CLI (overrides, list, rebuild-costs) + `GET /v1/pricing`.
6. Analytics store/service/router + integration and isolation tests; OpenAPI.
7. Seed generator + benchmark; decide aggregates; record.
8. Web: client, dashboard (KI-028), analytics page, fixtures, tests.
9. E2E script + Playwright spec + axe + screenshots.
10. Verify, review, harden, complete (checklist, plan moved, docs).
