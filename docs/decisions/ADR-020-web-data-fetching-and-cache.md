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

## Interim live-run behaviour (until Phase 5 SSE)
- The events query key is the run id only. A finished run's events are immutable (INV-1): `staleTime: Infinity`, never refetched.
- For an active run the run record is polled every 3 s; the events are refetched (all pages, sequentially) only when the run's
  `event_count` or status changes, so an idle live run costs one small request per poll and a finished run costs none.
  This is O(pages) per change and is acceptable only for the interim; Phase 5 replaces it with incremental merge keyed by `event_id`.
- `409 CURSOR_STALE` (ordering mode changed while paging) drops the partial pages and restarts from page 1, at most three times.

## Consequences
Three runtime dependencies in the web app (the SDK's near-zero budget does not apply to the web). Each is widely used,
MIT-licensed and replaceable behind `lib/queries.ts`. Rejected: server-component fetching (no incremental merge story),
hand-rolled cache (re-implements dedup and invalidation).
