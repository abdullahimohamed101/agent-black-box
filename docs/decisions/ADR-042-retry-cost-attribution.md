# ADR-042: Retry Cost Is Attributed by Retry Scope Span

Status: Accepted
Date: 2026-10-08

## Context
Spec §24 wants "time/cost spent retrying" and a breakdown "initial attempt vs retries". The canonical `retry.attempted` event has `retry.attempt`, `retry.reason`, optional
`retry.of_event_id`, and sits in a span like any event. No SDK helper defines retry semantics yet, so the attribution rule must be deterministic and documented.

## Decision
- A retry event's **scope** is its `span_id` (the operation being retried). An `llm.request.completed` event is **retry cost** when, in canonical order, a `retry.attempted` event
  with scope S precedes it and the call's span is S or a descendant of S (ancestry follows `parent_span_id` from the run's derived spans; cycles and missing parents end the walk).
- A `retry.attempted` event without a `span_id` is counted in `retry_count` but attributes no cost (it names no operation). The UI says so ("n retries could not be attributed").
- Initial-attempt cost = run cost - retry cost. Both are derived inside the summarizer from the same snapshot as the cost lines, and stored per line (`is_retry`).
- Time spent retrying is not computed in this phase (needs `retry.delay_ms` semantics across SDKs); `retry_count`, `retry_rate` and retry-heavy runs are.

## Alternatives
Attribute by proximity or by a `retry.of_event_id` chain: ambiguous when events are missing or reordered, and the chain is optional; rejected as the primary rule. Mark calls with a new attribute at emission:
needs every SDK and adapter to cooperate; possible later and would simply take precedence.

## Consequences
Positive: deterministic, order-independent given canonical ordering, explainable ("calls after retry N in span S"). Negative: instrumentation that emits unscoped retries gets counts but no cost share (KI-052).

## Migration implications
`is_retry` lives in `cost_calculations`, rebuilt with the run; changing the rule bumps `SUMMARY_VERSION`.

## Revisit conditions
Phase 8 adapters define real retry emission; align this rule with their golden fixtures.
