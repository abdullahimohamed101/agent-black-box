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

## Consequences
Three runtime dependencies in the web app (the SDK's near-zero budget does not apply to the web). Each is widely used,
MIT-licensed and replaceable behind `lib/queries.ts`. Rejected: server-component fetching (no incremental merge story),
hand-rolled cache (re-implements dedup and invalidation).
