# ADR-071: The Waterfall Is Derived in the Browser; Critical Path by Last-Finishing Child

Status: Accepted
Date: 2026-10-10
Phase: 9

## Context
Spec §97 requires hierarchical rows, lanes, zoom and pan, colour by kind and status, a critical path, hover, synchronized selection and "render only
visible rows, aggregate collapsed nodes". The run page already loads every event and merges live frames; `GET /v1/runs/{id}/spans` is derived, lags
the summarizer and is not streamed. Real traces are forests (no root span), contain unfinished spans and, since the API does not reject cycles
(`check_span_relationships` is not called), may contain hostile structure.

## Decision
- The waterfall derives spans in the browser with a port of `abb_event_schema.spans.derive_spans` (`apps/web/src/lib/spans.ts`). It reads the
  registry from the generated `event-types.json` (copied to `apps/web/src/lib/generated/` by `make schema`, verified by `make schema-check`) and
  is pinned by a generated golden file (`packages/event-schema/tests/data/spans-golden.json`, replayed by the Python and the TypeScript test).
  A `SpanIndex` re-derives only the spans whose event group changed when the events array changes (one O(n) id walk, the order of the live merge).
- The tree hangs the forest under a synthetic run root (start = the run's start, end = `completed_at` or `last_event_at`). Unfinished spans end at
  the run's end and are drawn open-ended with a "no end recorded" flag. A parent cycle is broken at its smallest span id, which becomes a flagged
  root; no span is dropped; depth is computed iteratively and indentation is capped at 32.
- Rows are virtualized; a collapsed subtree is one aggregate row; runs above 2,000 spans are auto-collapsed below depth 2. The time axis is a
  pure scale over zoom and pan.
- The critical path is Jaeger's last-finishing-child walk from the synthetic root's end; children outside their parent (skew) are clamped to it and
  flagged `skewed`. Lanes group the same rows by `agent_id`.
- Per-agent cost comes from the server-derived `summary.agents`. Selection is one event id shared with the timeline and the drawer. Every label,
  tooltip field and aggregate row is built from the already-withheld events through `describe`/`contentText` (a withheld value is
  `WITHHELD_TEXT`), so ADR-061's projection is inherited; the view choice lives in the URL (`?view=waterfall`).
- No new route: the authorization matrix and OpenAPI are unchanged.

## Alternatives
A `GET /v1/runs/{id}/tree` endpoint (a matrix case, a registry case, cross-workspace coverage, and still no live updates). Drawing every bar in
SVG or canvas (tens of thousands of nodes, against §97). A longest-duration critical-path heuristic (not explainable). Rejecting cycles at ingestion
(a contract change; the browser must survive stored data anyway).

## Consequences
Span derivation cost moves to the client (recorded in `docs/benchmarks/phase-9-waterfall.md`: derivation of 10,000 events about 8 ms, an incremental
500-event update about 0.3 ms, a zoom step frame-bound, at most 80 rows mounted). A mismatch between the Python and TypeScript derivations is caught by
the golden file; a hand-edited registry copy fails `make schema-check`. Very large runs show an aggregated tree until expanded. Revisit if a trace
exceeds what a browser can hold: then a server-side windowed tree endpoint, with its matrix case.
