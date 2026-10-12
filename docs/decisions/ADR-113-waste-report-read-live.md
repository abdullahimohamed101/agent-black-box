# ADR-113: The Waste Report Reads `runs` and `cost_calculations` Live

Status: Accepted (Phase 22, step 9).
Date: 2026-10-12
Phase: 22

## Context

Analytics opens with "where the money was wasted" (plan D12): spend on runs that failed, timed out or were blocked, plus retry spend,
by agent and by model, from the cost engine's figures. The daily rollups (ADR-043) cannot answer it: `analytics_cost_daily` has no run
status, so spend on failed runs by model is not derivable from it, and the spend after a run's first error (ADR-112) lives only in
`runs.summary`.

## Decision

`GET /v1/analytics/waste` (same query parameters, role and scoping as the other analytics routes: `analytics.read`, workspace
scoped, metadata only, so every role that reads analytics sees it) reads `runs` and, for the by-model cut, `cost_calculations` joined
to `runs`, inside the store's existing read-only, time-limited transaction. Nothing new is stored, so there is nothing to rebuild
(INV-2) and the figures do not lag the rollups.

- "Wasted" never counts a dollar twice: all spend of runs with status FAILED, TIMED_OUT or BLOCKED, plus the retry spend of the
  other runs. Retry spend inside a failed run is already in its spend and is shown as `retries.in_failed_runs_usd`.
- By-agent figures come from the run rows' own cost columns, by-model figures from the cost lines; both cuts add up to the headline
  (asserted by a reconciliation test against the cost report).
- `failed.after_first_error_usd` sums `first_error.cost_after_usd` (ADR-112); failed runs summarised before `SUMMARY_VERSION` 5
  are counted in `after_first_error_unknown_runs` rather than treated as zero.
- Unpriced calls are reported, not hidden (`failed.unpriced_calls`, `by_model[].unpriced_calls`); they add $0, so the figure is a lower
  bound.
- Groups beyond `top` fold into one `{groups, wasted_usd}` bucket, so response size does not depend on how many agent or model names
  a client invents.

## Alternatives considered

- A `run_status` column on the cost rollup: needs a migration and a backfill, and until rebuilt history would silently under-report.
- Reading the rollups for by-agent and live data for by-model: two sources that disagree for up to a minute.

## Consequences

- The by-model query joins cost lines to their runs and filters on the run's `started_at` and `project_id` after the join, so it is
  bounded by the 92-day window limit and the statement timeout (`ANALYTICS_TIMEOUT`, 503, retry). If a workspace outgrows this, move the status and
  `is_retry` split into the cost rollup then.

Breakdown note: `by_agent` lists only agents whose failed runs or retries cost something (`HAVING wasted > 0`), so the sum of its `failed_runs` can be
below `failed.runs` when failed runs were all unpriced. `unrebuilt_runs` counts every run summarised below the current summary version.
