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

## Consequences
Reconnects may repeat up to one overlap window of events (clients already must handle duplicates). Fan-out cost is one indexed query per
stream per wake-up; the measured trigger for Redis/NATS (many concurrent streams per run) is recorded in `docs/benchmarks/phase-5-streaming.md`.
