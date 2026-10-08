# ADR-022: Live Streams Resume by Arrival Time and Wake on Postgres NOTIFY

Status: Accepted (implemented in Phase 5)
Date: 2026-10-07

## Context
Spec §76 asks for `GET /v1/runs/{id}/stream` over SSE with `Last-Event-ID` resume. Events arrive out of order, are idempotent by
`event_id`, and are committed in transactions that can finish in a different order than they started. Redis/Kafka fan-out is on the
spec's deferred list until a measured trigger.

## Decision
- **Resume position is arrival time, not canonical order.** The SSE `id:` is the `event_id`. A reconnect looks up that event's
  `received_at`, then re-reads everything received since `received_at - overlap` (default 30 s), ordered by `(received_at, event_id)`.
  Clients de-duplicate by `event_id`. The same overlap is re-read on every live poll; a per-connection sent-set (pruned to the window)
  stops repeats within one connection. An unknown `Last-Event-ID` means "from the start of the run".
- **Why not an insertion counter:** a sequence can be assigned to a transaction that commits after a later one, so a reader that
  remembers "highest seen" silently skips the late row. **Why not canonical order:** a late event sorts before rows already sent.
- **Known limit:** `received_at` is stamped per request before its transaction; a transaction open longer than the overlap can be
  missed by a live stream. The window is a setting; the REST list endpoint is always complete and the UI reconciles through it.
- **Wake-up:** ingestion calls `pg_notify('abb_run_events', run_id)` inside its transaction (delivered on commit; ids only: `<workspace uuid>:<run uuid>`).
  One listener connection per API process fans out in-process; each stream then queries under its own tenant context. A fallback
  poll (2 s) covers lost notifications and listener reconnects. No new infrastructure, correct with several API processes.
- **Payloads are never streamed** (same light shape as list endpoints); streams are bounded per process and per key and have a maximum lifetime.

## Cost control (added after verification and review)
The first version re-read the whole overlap window on every wake and had no index for it: with 50 viewers on a 5,000-event run the pool
was exhausted. The shipped design:
- **Index** `ix_events_run_arrival (workspace_id, run_id, received_at, event_id)` (migration 0009); a test asserts the planner uses it.
- **Incremental polls**: after the first replay a poll reads strictly after the newest (`received_at`, `event_id`) sent (an index range scan).
  Every `ABB_STREAM_WINDOW_CHECK_SECONDS` (2 s) the stream compares the row count of its overlap window (up to its position) with the number
  it sent; only a mismatch, meaning a row became visible late with an older arrival time, re-reads the window.
- **Poll floor** (`ABB_STREAM_MIN_POLL_SECONDS`, 0.1 s) so a burst of wake-ups is not a burst of queries, and a **database budget**
  (`ABB_STREAM_DB_CONCURRENCY`, 4) so streams queue instead of exhausting the pool they share with ingestion.
- **Terminal state from the database** on open, so a resume after the terminal event (outside the window) still ends with `run_end`.
- **Clock assumption**: `received_at` is stamped by the API process's clock. With several API instances, clock skew larger than the
  overlap window can make a stream miss events from the slower clock; keep instances NTP-synchronised. (Stamping with the database clock
  would remove this; not done yet.) Each ingest commit also issues a `pg_notify`, and Postgres serialises commits that carry notifications
  on a global lock, which caps ingest throughput at very high rates (measure before Phase 18).

## Consequences
Reconnects may repeat up to one overlap window of events (clients already must handle duplicates). Steady-state fan-out cost is a small index range scan per stream per poll (floor 0.1 s) plus a window count every 2 s; the measured trigger for Redis/NATS (many concurrent streams per run) is recorded in `docs/benchmarks/phase-5-streaming.md`.
