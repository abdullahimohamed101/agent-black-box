# ADR-152: Shared limiter on PostgreSQL counters, behind the in-memory guards

Status: Accepted (Phase 19a, step 1). Implements D1 and gate 1 and gate 7 of the Phase 19a plan.
Builds on the Phase 2 token bucket (spec §71.3) and the per-process budgets of Phases 14 and 17.
Date: 2026-10-14
Phase: 19a

## Context

Every rate limit in the API was a token bucket in process memory. With N API processes behind one database every limit was N times
its stated value (KI-017, KI-141, KI-147, KI-161), so "30 policy publishes a minute" and "2,000 events a second per project" did not
hold the moment a second process ran. A hosted deployment needs the stated numbers to be true, and the failed-auth and quota work of
the next steps needs a counter that every process shares.

## Decision

1. **PostgreSQL fixed-window counters, not Redis.** One table, `rate_limit_counters` (migration 0097), keyed by
   `(scope, workspace_id, key, window_start)` with a `count` and an `expires_at`. A reservation is one
   `INSERT ... ON CONFLICT DO UPDATE SET count = count + n ... RETURNING count`, so the row lock serialises concurrent callers and the
   returned count is exact: no lost updates (tested with 200 concurrent callers). Windows are aligned to the database clock
   (`floor(epoch(now()) / w) * w`), so processes with different clocks agree on every boundary. We already run PostgreSQL; a second
   always-on service would double the operational surface for a counter that, at the measured cost below, the database carries easily.
   The trigger for revisiting this is in "Redis trigger".
2. **Scopes are a closed enum** (`limits/scopes.py`): ingest events and bytes, artifact count and bytes, sign-in starts, policy
   publish/validate/dry-run, approval decisions per key and per workspace, alert write/preview/send-test. A tenant scope always
   carries a workspace id (INV-3) and a counter row can never be shared across workspaces; the one global scope (sign-in starts, a
   ceiling that protects the database) is stored under the nil workspace id. The table has no foreign key to `workspaces` (a counter must
   never delay deleting a tenant); it is operational state, not tenant data. The runtime role needs only what the 0007 default
   privileges already give it (SELECT, INSERT, UPDATE, DELETE); a test proves it can count and prune and cannot alter or drop the table.
3. **Two tiers, local first.** The existing in-memory bucket stays in front, with the same settings, values and 429 bodies. A request the
   local bucket refuses never touches the database. A request it admits spends *credit* that this process reserved from the shared
   counter in blocks of `k = max(1, limit // ABB_LIMITS_BLOCK_DIVISOR)` (default 20), so the database is written once per block, not once
   per request. One reservation is in flight per key and process (a burst waits for the first block). When a window is exhausted the
   process remembers it until the window ends and answers further requests from memory: a flood of refused requests costs one write
   per process per window, not one per request. Per-second budgets (ingestion, artifacts) use a 10 s window with
   `max(rate x 10, burst)` units; per-minute budgets use a 60 s window with the stated per-minute number.
4. **The bound.** For one key in one aligned window with N API processes and a reachable database:
   * the fleet admits **at most `limit`**, exactly: every unit spent was granted by the counter, a grant is clamped so the sum granted in a
     window never exceeds the limit, and unspent credit expires when its window ends (so it cannot be carried into the next one);
   * a fixed window lets a burst straddling a boundary spend two windows' budgets: **up to `2 x limit` in any interval of one window
     length** (KI-174);
   * credit held by a process that then goes quiet is unavailable to the others: **at most `N x (k - 1)` early refusals** per window (for
     the default 20 blocks and 3 processes, about 15% of the limit in the worst case, usually zero because busy processes use what they hold);
   * with the database down the local bucket alone applies: **up to N x the local limit**, the pre-19a behaviour.
   `tests/limits/test_guard.py` asserts `limit - N(k-1) <= admitted <= limit` with two instances sharing a database;
   `tests/limits/test_multi_process.py` asserts the exact stated number across two real application objects for every moved budget.
5. **Fail open to the local limiter.** A limiter that can refuse the whole API when its own store is down turns a database blip into an
   outage of the control plane's front door, and the database being down already stops everything that matters. A shared call has a
   hard deadline (`ABB_LIMITS_TIMEOUT_MS`, default 500 ms, also set as the statement timeout so the server abandons it too); on any
   failure the shared tier is skipped for `ABB_LIMITS_BACKOFF_SECONDS` (5 s) so a sick database adds one timeout, not one per request. The breaker is **per scope** (review fix): one slow statement on one hot row disables only that scope, not the login ceiling or the failed-auth counters; a full outage trips each scope once, so the first request of each scope pays one timeout per backoff
   (tested against an unreachable database and against a table held under an exclusive lock). One warning per 30 s, with the error class
   name and a running count, never a key or a driver message. This is a deliberate availability choice for INV-4 style behaviour; an
   operator who wants closed behaviour has the edge proxy.
6. **Table growth is bounded.** Rows expire at the end of their window plus a 60 s grace. Whichever API process is serving deletes expired
   rows in batches of 1,000 (served by `ix_rate_limit_counters_expires`), at most once per 30 s, and again after one second while a batch
   comes back full; a failed prune is ignored. The delete runs **in a single in-flight background task per process** (review fix), never inside the request that happened to trigger it, so a slow delete cannot delay a caller; every process still prunes opportunistically, so cleanup depends on no single one. The table therefore holds the live (scope, key) pairs of the
   last window or two. No scheduled job: the writers are the cleaners, an idle system has nothing to clean, and the job framework's
   per-workspace jobs fit a global table badly. The worker is not involved.
7. **What stays local, and why.** Concurrency caps (live streams, approval long polls) count open connections of this process, not uses
   per window; their per-process value is also what bounds the process's own sockets. A shared version needs a lease table with
   heartbeats and is a different design (KI-017, KI-147 stay open, narrowed). The denial-audit cap bounds a log write per person per
   minute and stays in memory (KI-173). Search's concurrency permits are step 6.

## Redis trigger

We move the counters off PostgreSQL if, measured with `scripts/bench_limiter.py` on the target hardware at default settings, **any** of:

* the shared tier adds more than **5 ms** to the p95 of a 100-event ingestion ack against `ABB_LIMITS_SHARED_ENABLED=false` (the spec
  budget is p95 150 ms), or more than **10 ms** with a write on every request;
* sustained counter writes exceed **500 a second** on the primary. The write rate is bounded by construction at about
  `2.1 x P x N` a second for P projects at their limit across N processes (20 block reservations plus the refusal write per 10 s window), so this fires
  at roughly 240 saturated project-process pairs, long after the ingestion path itself needs scaling out;
* `rate_limit_counters` holds more than **100,000 live rows** or autovacuum cannot keep its dead tuples under 20% of the table.

The measured values are in `docs/benchmarks/phase-19a-limiter.md`.

## Consequences

* Positive: the stated limits hold across processes; one counter service for the failed-auth and quota work of steps 2 and 3; no new
  always-on service; the local guard keeps refusals and most admissions off the database.
* Negative: the hot path gains a database write every `k` units and a pooled connection for a moment; fixed windows allow a
  boundary burst (KI-174); a limiter outage degrades silently to per-process limits (a log line, no alert yet; metrics are 19c).
* Neutral: a request the local bucket admitted but the shared tier refused has already spent its local tokens (the local bucket may
  briefly be emptier than a pure local run would leave it).

## Alternatives considered

* **Redis (or any external store).** Lower latency per increment, but a new always-on dependency, a new failure mode, backups and
  credentials. Not justified until the trigger above fires.
* **Sliding-window log or token bucket in the database.** Exact smoothing but a row per request or a read-modify-write per request;
  the fixed window with blocks is one write per `k` units.
* **Per-request increment without credit blocks.** Simplest and exactly accurate, but one database write per request on the hottest
  path; the benchmark's `every-request` configuration measures that cost.
* **Fail closed.** Refuses valid traffic during a database blip; rejected.
