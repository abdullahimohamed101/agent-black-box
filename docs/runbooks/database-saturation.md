# Runbook: database saturation

Spec 125 asks: connection pool, slow queries, lock waits, index bloat, write rate, analytics query load. This is the order to check them. Dashboard row: **Database and
dependencies**. Alerts: `AbbDatabaseBreakerOpen`, `AbbDatabaseBreakerTripping`, `AbbPoolWaitHigh`, `AbbScrapeDatabaseDown`, `AbbReadinessCheckFailing`, `AbbReadinessFlapping`,
`AbbLimiterDatabaseErrors`, `AbbHttpServerErrors`, `AbbIngestAckSlow`.

Everything below that runs SQL needs a role that can read `pg_stat_*` (the owner or a monitoring role); the runtime role is not meant to.

## First, decide what is down

1. `/readyz` and the **Readiness** panel (`readiness_check{check}`): is it the database, the schema, the artifact directory? A `schema: behind` is a deploy ordering problem
   ([migrations](migrations.md)), not saturation.
2. `db_breaker_state`: 0 closed, 1 half-open (probing), 2 open (requests are shed with 503 for 5 s at a time).
3. `abb_scrape_database_up`: 0 means the queue and retention gauges are cached (their alerts cannot be trusted).

## Readiness failures

`readiness_check{check="database"}` at 0 with reason `down` or `timeout` is the database; `breaker_open` is this process shedding because of earlier failures. `check="artifacts"` is the
artifact directory (missing, not writable: disk full, a mount that went read-only). `check="role"` is [security operations](security-operations.md#privileged-database-role).
Flapping (`readiness_flaps_total`) is almost always a database answering near the 2.5 s limit: treat it as saturation, not as an outage.

## Database breaker open

After 5 consecutive checkout failures within 30 s the process stops asking for connections for 5 s, then probes with one request. It is the symptom, not the cause: find why checkouts fail.

- Database down or restarting: `pg_isready`, the Postgres log, the host. The API recovers by itself within one probe after the database returns.
- Connection limit reached: `SELECT count(*), state FROM pg_stat_activity GROUP BY 2;` against `SHOW max_connections;`. API replicas times pool size, plus the worker, retention and
  stream listeners, must stay below it. Reduce replicas or pool size, or raise `max_connections` with memory in mind (or put a session-mode pooler in front; transaction-mode poolers break
  `LISTEN` for streams, see [stream issues](stream-issues.md)).
- Network: the checkout timeout is 3 s to connect; many process hosts failing at once point at the network.
- Repeated trips that each recover (`AbbDatabaseBreakerTripping`): the pool is at its limit. Look at pool wait below.

## Connection pool

`postgres_pool_wait_seconds` is the time a request waits for a connection (capped at 5 s, then the request fails). A healthy pool is below 10 ms at p99.

- p99 above 1 s (`AbbPoolWaitHigh`): connections are held too long or there are too few. Connections held by slow statements show in
  `SELECT pid, state, wait_event_type, now() - query_start AS running, left(query, 80) FROM pg_stat_activity WHERE datname = 'abb' AND state <> 'idle' ORDER BY running DESC;`
  (statement text can contain parameters: handle the output as sensitive).
- Idle in transaction for long: `state = 'idle in transaction'` with an old `xact_start` is a client holding a transaction; it also holds locks and blocks vacuum.
- Only raise pool size if the database has headroom (`max_connections`, CPU). More connections to a saturated database make it slower.

## Slow queries and statement timeouts

Every API connection starts with `statement_timeout` (30 s by default, `ABB_DB_STATEMENT_TIMEOUT_SECONDS`). A cancelled statement answers `504 DATABASE_TIMEOUT` and appears as a 5xx in
`http_requests_total` (so also in `AbbHttpServerErrors` and, for ingestion, in the error budget).

- `pg_stat_statements` (if installed): `SELECT calls, round(mean_exec_time) AS mean_ms, round(total_exec_time) AS total_ms, left(query, 100) FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;`
- Find the route first (`http_request_duration_seconds` by `route`), then the statement; do not raise the timeout until you know which statement it is (OPERATIONS, "Readiness, the database
  breaker and timeouts"). A very large ingestion batch insert is bounded by the same timeout (KI-235).
- The usual suspects in this product: summarising one huge run (KI-016), an unfiltered search, a retention batch during peak.

## Slow ingestion answers

`AbbIngestAckSlow`: more than 1% of ingestion answers took over a second. Compare `ingest_ack_duration_seconds` with `postgres_pool_wait_seconds`: if pool wait explains it, see above; if not, the commit
itself is slow (disk latency, `synchronous_commit` with a slow replica, checkpoint storms). Ingestion of one project is serialised on its project row for the length of one batch transaction
(OPERATIONS, "Project quotas"): many large batches for one project queue behind each other, which is by design.

## Lock waits

`SELECT pid, wait_event_type, wait_event, now() - xact_start AS xact_age, left(query, 80) FROM pg_stat_activity WHERE wait_event_type = 'Lock';` and
`SELECT pg_blocking_pids(pid), pid FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0;`. Known places that serialise by design: one project row per ingestion batch,
the shared limiter's counter rows, the per-run summary lock. A migration holding an `ACCESS EXCLUSIVE` lock blocks everything behind it: that is a deploy problem ([migrations](migrations.md)).

## Index bloat and vacuum

Append-heavy tables (`events`, spans) rarely bloat; the churn is in queue and counter tables (`outbox_jobs`, limiter counters) and, after retention enforcement, in the tables it deletes from.
`SELECT relname, n_dead_tup, n_live_tup, last_autovacuum FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10;` and `SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) FROM pg_stat_user_indexes ORDER BY pg_relation_size(indexrelid) DESC LIMIT 10;`.
A high dead-tuple count with an old `last_autovacuum` means autovacuum is being starved (a long transaction). `REINDEX ... CONCURRENTLY` is the rebuild that does not block writes. No metric tracks bloat yet (KI-246).

## Write rate

`telemetry_events_received_total` and `telemetry_bytes_received_total` (dashboard **Ingestion**) against the database's write capacity. A step in events per second that matches the step in
database load is demand, not a fault: apply backpressure upstream (the SDK already buffers and backs off) or add capacity. Check
the shared limiter is not being skipped: `limiter_fail_open_total` above 0 means per-project limits are only per process.

## Limiter database calls

`limiter_database_calls_total{outcome="error"}` is the shared limiter failing to reach the database; the decision is then made locally (`limiter_fail_open_total`). The limiter must
not take ingestion down, so this is the first sign of database trouble that shows before the breaker does. Treat a rising error share like any connection failure above.

## Analytics query load

Dashboards and analytics read from the same database (spec 118.4: isolate analytics from transactions at scale). Signs: slow `GET` routes in `http_request_duration_seconds` while ingestion is
fine; long `SELECT` statements in `pg_stat_activity`. Mitigation today: the per-route budgets and statement timeouts, narrower date ranges, running heavy reads on a replica
(no read-replica routing is built in). There is no separate analytics metric yet (KI-247).

## Scrape database down

`abb_scrape_database_up` is 0 when the four small queries behind the queue, dead-letter and retention gauges failed or timed out (2 s statement timeout each). Fix the database first. The gauges serve the last values meanwhile
and `worker_job_lag_seconds` and `alert_dead_letters` may be stale: look at the queue with SQL ([worker lag](worker-lag.md)).
