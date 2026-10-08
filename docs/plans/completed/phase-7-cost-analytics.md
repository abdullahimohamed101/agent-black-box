# Phase 7 - Cost and analytics

Status: Completed 2026-10-08 on branch `feature/phase-7-cost-analytics` (not pushed; MVP GATE reached, awaiting human review; the MVP is NOT declared accepted)
Owner: implementer agent
Branch: `feature/phase-7-cost-analytics` (from `main` cc56935; worktree `../abb-worktrees/phase-7`)
Depends on: Phase 2 (derived runs/spans, outbox), Phase 4 (web), Phase 5 (live runs)
Spec refs: §79 (cost engine), §22-23 (analytics, cost), §24 (retry cost), §61.2/61.4 (dashboard p95, Stage A), §138 (launch bar), INV-1/2/3/7
ADRs: ADR-040 (cost engine and pricing), ADR-041 (analytics store; its aggregates clause superseded), ADR-042 (retry-cost attribution), ADR-043 (daily rollups)
Migration ids used: 0040 (cost tables), 0041 (typed run/span columns, indexes), 0042 (rollups), 0043 (money columns to numeric(38,9)); chain 0009 -> 0040 -> 0041 -> 0042 -> 0043. Known issues: KI-050..055 (KI-053 accepted), KI-028 and KI-025 resolved.

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

## Design changes made during implementation (measured, see the benchmark)
- ADR-043: reading `runs`/`spans`/`cost_calculations` directly measured 1.3-8.6 s on the Stage A dataset; analytics now read five daily rollup tables
  rewritten per workspace-day by a `refresh_analytics_day` job (debounced 60 s). A variant that aggregated today live measured worse and was dropped.
  Consequences: whole-UTC-day windows, approximate percentiles (about 5%), analytics lag up to a minute, no agent filter (D5 of this plan changed).
- `cost.provider_usd` was added to the event schema (additive); D3's "retries without a span attribute nothing" stands (KI-052).
- Overrides are CLI-managed (KI-051); `GET /v1/pricing` is read-only.

## Acceptance evidence
| # | Criterion | Evidence | Status |
| --- | --- | --- | --- |
| 1 | Pricing and cost unit tests incl. historical reproducibility | `apps/api/tests/test_cost_engine.py` (21 tests: version selection by event time, glob specificity, override and source precedence, cached tokens, rounding, unpriced flag, hostile values, `test_historical_calculations_reproduce_after_prices_change`), part of api 461 passed in `scripts/quality.sh full` | VERIFIED |
| 2 | Retry attribution | `test_cost_retries.py` (5) and `test_cost_derivation.py::test_a_run_gets_priced_lines...` (`retry + initial == total`); mutation of the ancestry rule fails them | VERIFIED |
| 3 | Rebuild property (INV-2) | `test_cost_derivation.py::test_cost_lines_are_a_pure_function_of_the_events` (shuffled batches, delete + rebuild); `test_analytics_rollup.py::test_rollups_are_rebuilt_exactly_from_the_derived_tables`, `..._refresh_is_idempotent`, the CLI rebuild test | VERIFIED |
| 4 | Analytics integration tests on seeded data | `test_analytics_api.py` (12): every metric against hand-worked numbers (ingestion -> worker -> API) | VERIFIED |
| 5 | Tenant and project isolation | `test_analytics_api.py::test_a_project_key_cannot_read_or_probe_other_projects`, `..._another_workspace_is_invisible_everywhere`, `..._a_workspace_key_sees_every_project_and_can_narrow`, access tests (401/403); `test_pricing_api.py`; `test_rollup_rows_never_cross_workspaces`. Mutation checks (committed code, restored with git checkout): removing the project predicate, the workspace predicate, the project authorisation, the source precedence and the retry ancestry rule each made the targeted tests fail | VERIFIED |
| 6 | Cardinality bound | `test_grouped_results_are_bounded_by_top` (top-N + other for models and tools), `test_client_controlled_names_are_capped_per_day` (rollup cap, cost preserved); agent slugs not capped (KI-055) | VERIFIED (agents: open KI-055) |
| 7 | KI-028 resolved | `Dashboard.tsx` reads `/v1/analytics/summary`; `computeDashboard` removed; `tests/analytics.test.tsx`, `runs-list.test.tsx`; E2E dashboard test; issue #20 closed, row moved to Resolved | VERIFIED |
| 8 | Dashboard p95 < 1.5 s on Stage A, measured | `docs/benchmarks/phase-7-analytics.md` and `docs/benchmarks/phase-7-analytics-results.json` (3 repeats, warm-up, no worker): 700,000 runs / 5,948,006 spans / 2,098,880 lines; summary worst p95 about 20 ms; slowest endpoint worst p95 407 ms (first repeat after restart). Limits stated up front: seeded derived tables, tiny cardinality, no concurrent ingestion | VERIFIED under those conditions |
| 9 | Browser E2E (Playwright, axe, screenshots) | `scripts/analytics-e2e.sh`: 4 passed (dashboard figures, analytics page figures, hostile names as text with no dialog/`__pwned`, window switch + 375 px no horizontal overflow); axe serious/critical = 0; screenshots in `docs/screenshots/phase-7/`; no `dangerouslySetInnerHTML` in `apps/web/src` | VERIFIED |
| 10 | openapi, client, quality | `make openapi`, `pnpm gen:api` current; `scripts/quality.sh full` exit 0 after the review fixes: event-schema 341, sdk 135, api 499, web 283 passed; migrations up/down/up to 0043; web build; wheel build | VERIFIED |
| 11 | §138 launch bar run as a checklist | below | DONE, partly open |

## Security review note (review-change)
Every analytics statement starts from `workspace_id == tenant` (`_source`, `rollup.*_select`, `_runs_from_top`, the active-agents query); a project key is forced to
its own project and probing another is `404 PROJECT_NOT_FOUND` (`projects/access.py`, one place, tested for every endpoint); reads run in a READ ONLY transaction
with a statement timeout; the web proxy allowlist is exactly the four analytics paths (GET only; `/v1/pricing` is not exposed to the browser); trace-derived names render
only as React text nodes (tested). Findings fixed during review: `E501`/lint and a stale openapi caught by `quality.sh`; rollup cap ranked names across span kinds
(fixed with a partition); the web proxy test now pins the allowlist. Residual: KI-029 (one shared web key) unchanged; KI-055 (agent slug cardinality).

## Spec §138 MVP launch quality bar (checklist, human review pending)
| Item | Evidence | Result |
| --- | --- | --- |
| Instrumentation takes minutes, not hours | `examples/python/trace_an_agent.py` + `scripts/sdk-e2e.sh` (Phase 3, compose stack); not re-run in Phase 7 | ASSUMED from Phase 3; needs a timed fresh run |
| Live trace stable through reconnect | `scripts/stream-e2e.sh` re-run now: 5/5 passed (refresh mid-run, dropped connection, p50 6 ms / p95 25 ms accept-to-display). One earlier run timed out the latency test while the machine was loaded (load average 7); the rerun passed | VERIFIED (one flaky timeout under load) |
| Duplicate events do not appear | `test_event_store.py` duplicate/conflict tests, `stream-e2e` refresh test (no duplicate rows), web `mergeEvents` tests; all in the green quality run | VERIFIED |
| Sensitive fields can be disabled/redacted | SDK default `METADATA_ONLY`, `test_redaction.py` (payload modes, secret patterns, fail-closed callback); server-side detectors are KI-032 | VERIFIED (client-side only; KI-032 open) |
| A failed coding-agent run can be diagnosed from the UI | the coding-agent demo, shell/diff views and artifact store are Phase 6 (parallel branch, not in this tree). The generic failure path (failure trend, timeline, retry-heavy runs, run detail) is exercised | NOT VERIFIED HERE: depends on Phase 6 |
| Cost values are explainable | each model call stores source (absurd figures are clamped to 1,000,000 USD per call and counted, never silently trusted) (provider-reported / estimated / client estimate / unpriced), pricing version and components; `GET /v1/pricing`; UI "where the cost figures come from", unpriced count, unrebuilt notice. Built-in prices are illustrative only (KI-050) | PARTLY: explainable, but real vendor prices must be added (KI-050, S2) |
| Project data is tenant-isolated | tenant repository, schema-constraint, runtime-role and analytics isolation tests; mutation checks above | VERIFIED |
| SDK outage behaviour does not break the demo agent | SDK tests (`test_client.py` never-raise and offline tests, exporter retry bounds); demo agent is Phase 6 | VERIFIED for the SDK; demo agent depends on Phase 6 |
| README reproduces setup from a clean machine | no clean machine available; compose stack last verified at Phase 2 | UNVERIFIED (env) |
| CI is green | `scripts/quality.sh full` exit 0 locally; nothing pushed, so GitHub CI (including the new `analytics-e2e` job) has never run on this branch | UNVERIFIED until pushed |
| Basic load test passes the target stage | ingestion benchmark (Phase 2, about 4,000 events/s vs the Stage A peak of 1,000 events/s); analytics benchmark now (Stage A, p95 < 1.5 s). The ingestion benchmark was not re-run after Phase 7 changes (KI-054) | PARTLY: analytics VERIFIED under the stated benchmark limits (seeded derived tables, tiny cardinality, no concurrent ingestion); ingest re-run open (KI-054) |

Outcome: not every item is evidenced. Open before the MVP can be accepted: Phase 6 (coding-agent diagnosis, demo agent), KI-050 (real prices), KI-054 (ingest re-measure),
a clean-machine README run, and GitHub CI. This phase stops at the MVP gate; the human reviews.

## Independent review fixes (post-completion)
An independent review found tenancy sound and reproduced three P1 poisoning paths. Each fix has a regression test and its own commit; new guards were mutation-checked
(mutant made the targeted tests fail, restored with `git checkout <file>`).

| ID | Finding | Fix | Regression test | Mutation |
| --- | --- | --- | --- | --- |
| P1-1 | `cost.provider_usd` 5e10 x2 overflowed `numeric(20,9)` in the day's rollup (workspace-day showed $0, job retried forever); 1e11+ failed `UPDATE runs`; floats >= 1e19 were accepted, stored by JSONB as integers and then failed event reload (`EventValidationError`) | CostEngine clamps every per-call figure to 1,000,000 USD, flags the line and counts `clamped_calls`; money columns widened to `numeric(38,9)` (migration 0043); the event schema now rejects floats beyond +-2^53 like integers (the reload bug was in the contract, not the summarizer); `refresh_day` inserts in savepoints and skips/counts/logs (value-free) rows the database refuses | `test_hostile_numbers.py` (two-runs, single-huge-run, NaN/inf/negative/bool, reject-at-ingestion, poisoned-row refresh), schema parse tests | clamp, float bound: tests fail |
| P1-2 | 4,000-character model/tool name broke the btree key on rollup refresh | every rollup text key truncated to 128 chars; names capped per project/day for agent, provider, model and span/series names, and 2,000 rows per table per day, folding into `(other)` with totals preserved | `test_very_long_client_names_do_not_break_the_refresh`, `test_agents_providers_and_models_are_all_capped...`, `test_five_hundred_distinct_keys...` | truncation, cap: tests fail |
| P1-3 | `retry_call_ids` quadratic in span depth (16k deep: 86 s, 1 GB) | memoised iterative `retry_position` with cycle guard, O(n) | `test_a_very_deep_span_chain_is_linear_time` (6,000 deep, time bound), cycle test | memo removed: test fails |
| P2-4 | extreme `from`/`to` gave 500 | years bounded to 2000-2100, overflow caught: 422 `INVALID_WINDOW` | `test_extreme_windows_are_a_422_not_a_500` (6 cases x 4 endpoints) | bound removed: fails |
| P2-5 | equal-pattern overrides did not resolve to the newer | `created_at` (Python clock) in ordering and specificity | `test_the_newer_of_two_equal_overrides_wins_every_time` (100 pairs) | ordering removed: fails |
| P2-6 | rollups unbounded in agents/providers; all groups fetched into Python | caps above; `LIMIT top` in SQL with a remainder bucket for agents, models and tools; slow operations rank only the 200 busiest | cardinality tests above (500 keys: bounded rows, query < 2 s) | cap removed: fails |
| P2-7 | 1-2 h last bucket: a 10 h run reported 1.93 h | histogram range 72 h (200 buckets), overflow reports exactly 72 h; accuracy stated as one bucket, about 10% | `test_long_runs_are_not_misreported` | overflow change: fails |
| P2-8 | migration 0041 long locks | measured 3 min 5 s on 700k runs / 6M spans; runbook `docs/runbooks/analytics-migrations.md`; KI-056 (S2); not automated | n/a (documented) | n/a |
| P2-9 | stale numbers shown with no signal while the next window loads | `isPlaceholderData`: sections dimmed, "Updating..." status, `aria-busy` | `tests/analytics.test.tsx` stale-figures test | n/a |
| Bench | 801 ms explanation (cold cache) was wrong | benchmark mode no longer starts a worker; warm-up, 3 repeats, worst repeat checked; results file saved; limits up front; the earlier outlier is explained as contention and stated as not reproduced | `docs/benchmarks/phase-7-analytics.md` | n/a |
| P3 | failure_rate defined differently in docs and trend; sdk doc stale; rebuild-costs silent cap; concurrent refresh; lost-update window; active-agents vs ADR-041; retry cost label; cached tokens | trend now uses `failure_rate` + `timeout_rate` as in `rates`; sdk-python.md fixed; CLI warns at `--limit`; advisory lock per workspace-day; KI-057 (S3) for the lost-update window; active-agents documented as the one live read; UI labels retry cost "Estimated"; api-v1 states adapters must report input tokens including cached | tests above | n/a |
KI-055 raised to S2 (issue #43 relabelled). New: KI-056 (S2), KI-057 (S3).

Verification after the fixes: `scripts/quality.sh full` **exit 0** (event-schema 341, sdk 135, api 499, web 283); `scripts/analytics-e2e.sh` 4/4 passed with axe; `scripts/analytics-e2e.sh` bench mode results in `docs/benchmarks/phase-7-analytics-results.json`.
