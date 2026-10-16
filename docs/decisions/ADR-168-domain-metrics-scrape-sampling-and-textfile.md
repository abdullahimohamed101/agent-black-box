# ADR-168: Domain metrics: where they are incremented, database gauges sampled at scrape time, and a textfile for short-lived tools

Status: Accepted (Phase 19c, step 2). Implements D3 (the catalogue and the SLIs) of the Phase 19c plan on top of ADR-165; the choices below were made by the assistant under the standing instruction and are listed so they can be reversed.
Builds on ADR-165 (the registry and its closed catalogue), ADR-152/153/155 (the controls being measured), ADR-159/160 (retention), ADR-162 (backups and rotation), INV-4 (bounded everything).
Date: 2026-10-16
Phase: 19c

## Context

Step 1 declared the catalogue; step 2 makes it move. Four questions had real alternatives: how a deep module reaches a registry without a global that tests cannot isolate; how to report facts that live in the database (queue depth, dead letters, the retention heartbeat) without letting a scrape depend on, or load, the database; how to report on processes that exist for minutes (backup, rotation, one retention tick); and what exactly an acknowledgement SLI counts.

## Decision

### 1. Increment where the facts are known, with an injected catalogue

Modules that are built with the application take the catalogue as a constructor argument defaulting to the process-wide one (`SharedCounters`, `ActiveKeyIds`, `AuthThrottle`, `BodyBudget`, `StreamService`/`StreamLimiter`, `SearchGate`, `Worker`, `deliver_due`); the lifespan passes the application's own, so a test builds an isolated `Observability` and asserts exact deltas. Handlers run by the worker have a fixed `(job, conn)` signature, so the worker publishes its catalogue in a context variable (`jobs/context.py`, next to the job settings) and handlers read `job_metrics()`.

Three controls refuse by raising an `AppError` with a fixed code (a quota, a full search gate, an invitation-only sign-in). They are counted once, in the `AppError` handler (`obs/app_errors.py`), from the error's closed `details` vocabulary, so every present and future raise site is covered and none needs a registry handle. The batch ingestion endpoint reports quota refusals inside its 202, where no error is raised, so the ingestion router counts those itself; the single-event endpoint raises, and the handler counts it (the router passes `count_quota=False` so nothing is counted twice).

Limiter database calls and fail-opens are incremented directly in `SharedCounters`: one dictionary update next to a database round trip. Reading the existing integer counters at scrape time was considered and rejected: they are per instance and have no scope split, and the direct increment costs nothing measurable beside the call it sits next to. The body-bytes gauge, which is on the request path of every body, is a scrape-time function gauge (`Gauge.set_function`), so the accounting itself gained nothing.

### 2. What the acknowledgement SLI counts

`ingest_acks_total{outcome}` is `committed` (answered 2xx; the observer is told after `ingest_batch` returned, i.e. after the commit), `rejected` (an `AppError` below 500: the client's fault) or `error` (an `AppError` of 500 or above, or any unexpected exception, which includes a commit that failed). Accepted-event durability is `committed / (committed + error)`; `rejected` is excluded because a malformed batch is not an availability failure. The single-event endpoint answers 4xx for a refused event, so a refused single event is `rejected`, not `committed`. A cancelled request (the client went away) is counted in neither. `ingest_ack_duration_seconds` is observed exactly once per request. The observer wraps the whole handler, so refusals before the service runs (body too large, bad encoding, bad media type) are counted too; authentication failures happen in a dependency before the handler and are intentionally out of scope (they are `http_requests_total{status="401"}` and the throttle's counters). Step 1's placeholder outcomes (`success`/`failure`) and the unreachable reasons (`busy`, and a session-revocation `expired`/`member_removed`) were replaced by what the code can actually produce, so a placeholder cannot sit in the exposition forever.

### 3. Database gauges are sampled at scrape time, cached, bounded and never fatal

`obs/scrape.py`. A scrape asks a `ScrapeSampler` to refresh; it runs at most one sample per 15 seconds (a failed attempt counts, so an outage costs one attempt per window, not one per scrape) in its own task, and the scrape waits at most 1.5 seconds for it before serving the cached values. Every statement runs with `statement_timeout` 2 s and the whole sample under an 8 s deadline (pool acquisition 5 s plus connect 3 s). `abb_scrape_database_up` is 1 only when every statement of the last sample succeeded; a failed statement keeps its previous values. Gauge sets are atomic per metric (`Gauge.replace`), so a type that has no due job any more disappears instead of lingering. The queries run as the runtime role on tables it already reads; no migration, no privilege.

Plans (PostgreSQL 16, verified with `EXPLAIN ANALYZE` on 1.5 million `outbox_jobs` rows, half pending and due):

- **Due jobs by type** `SELECT job_type, count(*), min(available_at) FROM (SELECT job_type, available_at FROM outbox_jobs WHERE status = 'pending' AND available_at <= now() ORDER BY available_at LIMIT 10000) GROUP BY job_type`. With a large backlog the planner takes an `Index Scan` on `ix_outbox_claim` (the partial index on `available_at WHERE status = 'pending'`) and stops at the limit: 4.5 ms with 750 000 due jobs. With a backlog under about twice the limit it takes a bitmap scan of at most that many rows and sorts them (7 ms at 20 000). Either way the work is O(limit), not O(backlog). Consequence: depth saturates at 10 000 total and a type whose oldest job is younger than the 10 000th-oldest due job overall is not shown; the lag of the oldest type stays exact. This is the intended trade for a bounded scrape.
- **Dead letters** `SELECT count(*) FROM alert_notifications WHERE status = 'dead'` is a sequential scan: there is no index on `dead`. The table is bounded by the alert retention window (and the 2 s timeout bounds it in time). Adding a partial index would need a migration; deferred (KI-225).
- **Destinations by state** is a scan of a small table (destinations are few). **Retention heartbeat** is a backward scan of the primary key stopping at the first row.

The worker serves the same gauges: its metrics server (a thread) bridges to the worker's event loop with `run_coroutine_threadsafe`, because the engine belongs to the loop. The API and the worker therefore both report these gauges; dashboards take them from one job.

### 4. Short-lived processes write a textfile

Backup, key rotation and the retention runner write a Prometheus text file for node_exporter's textfile collector (`--metrics-textfile PATH` or `ABB_METRICS_TEXTFILE`). Alternatives rejected: a push gateway (a new component and an outbound connection from a tool that should need only the database), and reading backup age from the filesystem at scrape time (the API process does not see the backup directory and must not). The file is written to a temporary name in the same directory, fsynced, set to mode 0644 and renamed, so a reader sees the old file or the new one. It is rendered by the same registry as `/metrics`, restricted to the names the tool owns, so it can only contain catalogue metrics with closed labels: no path, URL, host or name can get in. Because each invocation is a new process, counters and gauges are seeded from the previous file (tolerantly: a damaged or hostile file is ignored line by line, and a seeded counter never decreases), so a failed backup keeps the last success timestamp and counters grow across runs. A write failure is logged by error class and never changes the tool's result. Limits: one file per tool (two tools sharing a path replace each other); the file is the tool's record, not an audit trail.

### 5. Pool wait is measured at `Pool.connect`

`postgres_pool_wait_seconds` is observed by a pool subclass whose `connect()` times `super().connect()` (`db/engine.py`). The async engines' checkouts all go through it. It measures the wait for a free connection when the pool is saturated *and*, otherwise, the cost of a new connection plus the pre-ping, so a slow database shows up even when the pool has room; it cannot separate the causes. SQLAlchemy's `checkout` event fires after the connection is obtained and so cannot time the wait. One `perf_counter` pair and one histogram observation per checkout. An engine built without it (tests use `NullPool`) records nothing.

## Consequences

- Every family is incremented by exactly one place, tested by driving the real behaviour and asserting the series moved by the expected amount; a static test fails when a declared family is used by no module.
- A scrape costs one cached, bounded query set per 15 s and cannot take the API down with the database.
- Dashboards and rules in step 5 may rely on the families, the closed label values and `abb_scrape_database_up`.
- Replaced catalogue entries (never released): `retention_batches_total`, `retention_deleted_rows_total` -> `retention_passes_total{mode}`, `retention_deleted_total{tier,kind}`; `retention_failures_total{tier}` -> `{code}`; `worker_job_lag_seconds` gained `type`; `alert_deliveries_total` gained `reason`; new families listed in docs/OPERATIONS.md.

## Alternatives considered

Bridging the 19a integer counters at scrape time (rejected, see 1). Counting quota, search and invitation refusals at each raise site (rejected: three registry handles threaded through services; one handler covers all). A `prometheus-client` collector for the database gauges (rejected with the library, ADR-165). Sampling the database on every scrape (rejected: a scrape flood would become a query flood). A materialised queue-depth row maintained by triggers (rejected: write cost on the hottest table for a gauge).
