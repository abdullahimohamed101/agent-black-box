# ADR-161: Rollups after the trace tier: label what they lack, count the runs

Status: Accepted (Phase 19b, step 3). Amends ADR-160 section 1 (the KI-199 consequence).
Date: 2026-10-15
Phase: 19b

## Context

ADR-160 rebuilds a day's rollups after the trace tier removed a run's detail. The five rollup tables are aggregates of different sources:

| Rollup | Built from | After the trace tier |
| --- | --- | --- |
| `analytics_runs_daily`, `analytics_top_runs` | the run record (typed totals) | keeps the run until the run-record tier |
| `analytics_cost_daily` (by agent, provider, model, source) | `cost_calculations` | loses the run |
| `analytics_spans_daily`, the span part of `analytics_latency_daily` | `spans` | loses the run |

So for an old day the cost-by-model rows sum to less than the cost headline, and the tool and model latency histograms cover fewer calls than the run totals say. The numbers are true to their sources but a reader cannot tell why they disagree.

## Options

1. **Keep the run's detail contribution until the run-record tier.** Needs the contribution per run to survive the deletion of its source rows: a new per-run table (or a freeze of the day's detail rows, which cannot subtract a run when the record tier later removes it). New table, grants, inventory entry, purge handling, merge logic in `refresh_day`; exactly right, and large.
2. **Drop expired runs from every rollup** (also from the runs rollup). Simple and consistent, but then a run whose summary and numbers the run page still shows vanishes from the dashboard early, and D2 says the record tier owns the run's rollup contribution.
3. **Label it (chosen for now).** Everything derivable from the surviving run record keeps its value: run counts, status, cost headline and cost per day and project, retries, tool and model call counts and success rates (the typed span totals on the record), run latency. What genuinely needs the deleted rows (cost by agent/model/source, the per-name tool reliability, model and tool latency percentiles) is shown with an explicit "excludes N runs whose trace has expired".

## Decision

Option 3 now, option 1 recorded as the follow-up (KI-202).

* Migration 0103 adds `analytics_runs_daily.expired_trace_runs` (bigint, default 0), the count of runs in the group with `trace_state = 'expired'`. It is derived data: any rebuild of a day fills it, which the retention job does for every day it touches (ADR-160). Table-level grants of the retention role cover it.
* `GET /v1/analytics/cost`, `/reliability` and `/performance` return `excluded_expired_traces` (an integer, 0 when nothing is excluded), summed over the window. The summary and waste endpoints are unchanged: the summary rests on the record only; the waste report reads live and is listed in KI-202.
* The analytics pages show the line "Excludes N runs whose trace has expired" next to the figures that rest on trace detail, only when N is above zero.
* A test asserts that a day's run totals (counts, cost, retries, call and span counts) are identical before and after the trace tier ran and that the detail reports return an empty by-model list with the exclusion count.

## Consequences

* Dashboards are honest without a new table. A day's by-model chart for an old period is smaller than its headline by exactly the runs counted in the label (not by an amount the API can give: the dollars of the missing detail are not recoverable without option 1).
* A rebuild of a day that predates migration 0103 and already lost detail reads 0 until the retention job rebuilds that day again; enforcement was introduced in the same release, so no installation has such days.
