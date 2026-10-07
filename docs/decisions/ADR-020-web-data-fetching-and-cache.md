# ADR-020: Web Data Fetching Uses TanStack Query Behind a Same-Origin Read Proxy

Status: Accepted
Date: 2026-10-07

## Context
The run views are interactive (filters, cursors, a drawer, a 10,000-event timeline) and Phase 5 will merge streamed events
into the same data. Options: React server components with `fetch` caching; client-side SWR-style cache; a hand-rolled hook.

## Decision
- Client components use **TanStack Query v5**: stale-while-revalidate, request de-duplication, `useInfiniteQuery` for cursor
  paging, and a cache keyed by request that Phase 5 can update incrementally (`setQueryData`, keyed by `event_id`).
- The timeline is virtualized with **@tanstack/react-virtual** (headless, ~10 kB), so DOM size is independent of event count.
- Server components only render shells; no data fetching happens during SSR, so a slow API never blocks navigation and the
  page can show loading, empty and error states explicitly.
- A typed client (`openapi-typescript` + `openapi-fetch`) is generated from the committed `openapi.json`.
- Defaults: `staleTime` 5 s for lists, 60 s for immutable data (events of a terminal run), no retry on 4xx, two retries with
  backoff otherwise; running runs refetch every 3 s until Phase 5 replaces polling with SSE.

## Live runs (Phase 5, replaces the interim polling design)
- History comes through REST as before (`useRunEvents`, 500 per page, a finished run is immutable and never refetched).
- A running run additionally opens an SSE stream (`useLiveEvents`, ADR-022) once the history is complete, resuming after the newest
  event received. Streamed events are merged into the REST list by `event_id` in canonical order (`mergeEvents`, parity-tested against the
  Python `sort_events`), once per animation frame. Duplicates and the server's resume overlap are harmless.
- While the stream is connecting, live or reconnecting the events list is **not** refetched when the run's `event_count` changes (the old
  O(pages) behaviour). The run record is still polled every 3 s for the summary figures.
- Fallbacks: if streaming is off (fixture data), unsupported, or `unavailable` after repeated failures, the old rule applies: reload the
  events when the run's `event_count` or status changes. After a gap of more than 20 s in live delivery, and when the run ends, the events
  and the run record are reloaded once (reconciliation).
- `409 CURSOR_STALE` (ordering mode changed while paging) still drops the partial pages and restarts from page 1, at most three times.

## Consequences
Three runtime dependencies in the web app (the SDK's near-zero budget does not apply to the web). Each is widely used,
MIT-licensed and replaceable behind `lib/queries.ts`. Rejected: server-component fetching (no incremental merge story),
hand-rolled cache (re-implements dedup and invalidation).
