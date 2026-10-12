# ADR-112: First-Error Facts in the Run Summary

Status: Accepted (Phase 22, step 2).
Date: 2026-10-12
Phase: 22

## Context

The redesigned run page (Phase 22, decision D6) opens with the failure: where the run first went wrong, how many retries that caused,
how long the run kept going afterwards and what that cost. The web client can find the first error in the events it has loaded
(`firstError()` in `apps/web/src/lib/timeline.ts`), but events are paged, a run can hold 100,000 of them, and the runs list needs the
same facts for every row without loading any events. These numbers must also agree between the list, the detail page and any export,
and they must be rebuildable from events (INV-2).

## Decision

`runs/summary.py` derives six more `summary` keys from the canonically ordered events (`SUMMARY_VERSION` 4 to 5). No column and no
migration: `runs.summary` is JSONB and these figures are not filtered, sorted or aggregated in SQL (the figures analytics aggregate stay
typed columns, ADR-043). If a list ever needs to sort by them, a typed column joins them then.

| Key | Meaning |
| --- | --- |
| `first_error_event_id` | `event_id` of the first error in canonical order, else null |
| `first_error_sequence` | that event's `sequence` (null if it has none, or there is no error) |
| `retries_after` | `retry.attempted` events later in canonical order than the first error (0 without an error) |
| `seconds_after` | seconds from the first error's `occurred_at` to `completed_at`, or to the last event while the run is not terminal; never negative; null without an error |
| `cost_after_usd` | sum of the cost lines of model calls whose `llm.request.completed` event is later than the first error, same form and rounding as `estimated_cost_usd`; null without an error |
| `cost_after_unpriced_calls` | calls among those with no price (source `unpriced`); they add nothing to `cost_after_usd`, exactly as in `unpriced_calls` for the run total |

"First error" is the rule the client already uses and is parity-tested against it with shared cases
(`packages/event-schema/tests/data/first-error-cases.json`): an event whose status is `error` or `timeout`, or whose type ends in
`.failed`; first in canonical order (the API's event order). It is the same predicate as `error_count`.

"After" is canonical order, not clock order or `sequence`: `sequence` can be absent (multi-emitter runs are in time order, ADR-070) and
clocks skew, while canonical order is what the UI shows. A model call is priced on its completed event, so a call that began before the
error and finished after it counts as after; this is stated rather than guessed from `duration_ms`.

`RunOut` also carries a typed projection, `first_error: {event_id, sequence, retries_after, seconds_after, cost_after_usd,
cost_after_unpriced_calls} | null`, built from the stored summary so the OpenAPI contract and `schema.d.ts` describe it. It is null when
the run has no error or its `summary_version` is below 5; clients that must tell those apart read `summary_version`. List and detail
carry it identically.

Visibility: these are metadata. They hold an event id, counts, seconds and USD derived from event types, statuses, timestamps and cost
lines, never from an attribute value, payload, path, command or name, so every role receives them (an authz test asserts a viewer gets
exactly what an owner gets). They are not event attributes, so they do not belong in the authz `REVIEWED_METADATA` set, which
classifies attribute keys.

## Rebuild

Stored summaries are replaced whenever a run is re-summarised. For runs summarised at version 4, `python -m abb_api.cli search-rebuild`
selects every run below the current `SUMMARY_VERSION` and queues them (a one-off; `rebuild-costs` does the same by date range). Until
then those runs return `first_error: null`, and analytics' `unrebuilt_runs` counts them. Accepted events are never edited (INV-1).

## Alternatives considered

- Compute in the browser only: wrong for the runs list and for runs larger than the loaded page; two code paths to keep equal.
- Typed columns on `runs`: needs a migration, a backfill and a second source of truth for facts nothing queries by.
- Compute on every read from events: O(events) on each list request.
- Count "after" by clock time: skew makes it wrong in exactly the multi-process runs it matters for.

## Consequences

Summary version 5 makes `search-rebuild` and the analytics `unrebuilt_runs` counter treat every v4 run as stale until rebuilt. The next
bump is 6. The web client may keep using `firstError()` over loaded events for row highlighting; the two agree by test.
