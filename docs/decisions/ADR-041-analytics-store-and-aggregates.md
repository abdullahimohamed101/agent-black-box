# ADR-041: Analytics Read Derived Tables Through an `AnalyticsStore`; Aggregates Only Where Measured Slow

Status: Accepted (the "no materialized aggregates by default" clause is superseded by ADR-043, after measurement)
Date: 2026-10-08

## Context
Spec §22-23 and §61.2 (p95 dashboard aggregate < 1.5 s) need aggregate queries; INV-7 forbids ad-hoc SQL against events from controllers; INV-3 requires workspace
context on every tenant-data operation; Phase 7 forbids new infrastructure and allows materialized aggregates only where measured slow.

## Decision
- `analytics.store.AnalyticsStore` is a `Protocol` (summary, cost, reliability, performance). `PostgresAnalyticsStore` implements it with SQLAlchemy Core.
  Controllers only call the service; the service builds an `AnalyticsScope` and the store has no method that works without one.
- `AnalyticsScope` = tenant (workspace id), optional project id, optional agent slug, half-open time window `[start, end)` on `runs.started_at`. A project-bound
  API key forces `project_id` to its own project; asking for another project is `404 PROJECT_NOT_FOUND` (the same answer as an unknown one). One query-builder helper applies
  workspace, project and window predicates to every statement.
- Reads touch only derived tables: `runs` (status, duration, summary counters), `spans` (kind, status, duration, name), `cost_calculations` (per-call cost). Never `events`.
- Grouped results are capped at top-N (default 10, max 50) plus an `other` bucket, because agent slugs, models and tool names are client-controlled (KI-018).
- **No materialized aggregates by default.** The benchmark (`docs/benchmarks/phase-7-analytics.md`) decides; if a query exceeds budget, a rollup table rebuildable from
  `runs`/`cost_calculations` is added behind the same interface (INV-2) and this ADR is amended by a superseding ADR.

## Alternatives
Query `events` directly (violates INV-7, scans the largest table); precomputed rollups up front (stale-prone complexity before any measurement); ClickHouse (spec §57.4 trigger not met).

## Consequences
Positive: one place that enforces scope; swap-able storage. Negative: percentile queries scan the window's rows; very large windows rely on the 92-day cap and the benchmark.

## Migration implications
None beyond 0040's tables; indexes for analytics are added in the same migration after EXPLAIN review.

## Revisit conditions
Dashboard p95 > 1.5 s on Stage A, or > 1 s sustained on a customer dataset: add the rollup. Stage B (spec §61.4) needs a columnar store (ADR-009 trigger).
