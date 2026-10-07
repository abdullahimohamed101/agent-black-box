# ADR-012: Run State Is Derived Asynchronously Through a Transactional Outbox

Status: Accepted
Date: 2026-10-07

## Context

A run's status, timings, counters and spans can be computed from its events (INV-2) but must tolerate arbitrary
arrival order, duplicates, late events and concurrent writers. Ingestion must stay fast and must never depend on
analytics (FR-ING-008). Spec §72 prescribes a PostgreSQL outbox before any message broker.

## Decision

- **Ingestion only appends events** and, in the same transaction, enqueues a coalesced `summarize_run` job. It also
  seeds a run row on first sight so arrival order never matters.
- **A worker recomputes the whole run from its events** with a pure function (`runs/summary.py`): canonical order, status
  folded over the state machine (invalid transitions ignored), counters, and spans. The result is written to `runs` and
  `spans`. Because it is a function of the event set, replays, retries, shuffled arrival and rebuilds all converge on the
  same state (tested by comparing against a from-scratch derivation under random batching and worker timing).
- **Queue**: `outbox_jobs` with a partial unique index on `(job_type, dedupe_key) WHERE status = 'pending'` coalesces a
  burst into one job. Claiming uses `FOR UPDATE SKIP LOCKED` with a lease; a crashed worker's job is reclaimed after the
  lease; failures retry with exponential backoff and are dead-lettered after 5 attempts; completion only succeeds for the
  lease holder, in the same transaction as the handler's writes. A failed job that would collide with newer pending work
  is marked superseded. Operators use `jobs-list` / `jobs-retry`.
- **One writer per run at a time**: the handler takes `SELECT ... FOR NO KEY UPDATE` on the run row before reading events,
  so a job holding an older snapshot cannot overwrite newer state. `FOR NO KEY UPDATE`, not `FOR UPDATE`: the latter
  conflicts with the key-share lock event inserts take on their run, which would block ingestion for the duration of a
  summarization (found by test).
- **One clock for scheduling**: `available_at`, leases and backoff use the database's `now()`; host clocks are only
  injected by tests. Summaries are debounced (default 1 s) so a burst of batches costs one recomputation.
- **Events listing** follows `ordering_mode` (sequence or time) stored on the run, with keyset cursors that embed the mode;
  a mode change mid-paging yields `409 CURSOR_STALE`.
- `summary_state` on a run is `processing` while a job is pending or running, `failed` if the newest finished job was
  dead-lettered, else `current`.

## Alternatives

- Compute status and counters inside the ingestion transaction: couples ingest latency to run size and creates hot-row
  contention on `runs`; rejected.
- Incremental counters updated per event: cheaper per batch but order-sensitive and hard to rebuild; deferred until measured
  (see below).
- Kafka or Redis queue: no measured need (spec §57.5).
- Postgres `LISTEN/NOTIFY` for wake-up: possible later optimisation; polling every 0.5 s is simple and bounded.

## Consequences

Positive: simple, rebuildable, safe under concurrency, no broker. Negative: summaries lag ingestion by about the debounce
plus processing time (visible via `summary_state`); recomputation is O(events in run). Measured on a 2-vCPU VM, a single
run growing past roughly 10,000 events while still ingesting causes request-latency spikes (KI-016,
`docs/benchmarks/phase-2-ingestion.md`); incremental summarization is the planned response when that trigger is met.

## Migration implications

`summary_version` records which rules produced a summary, so a rule change is a version bump plus a re-enqueue of
affected runs. Moving to a broker later replaces the queue behind `JobQueue` and `OutboxRepository` without changing handlers.
