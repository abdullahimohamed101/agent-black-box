# ADR-006: At-Least-Once Ingestion with Idempotent Persistence

Status: Accepted (contract defined in Phase 1; storage behaviour implemented in Phase 2)
Date: 2026-10-07

## Context

Exactly-once delivery is expensive and unnecessary for telemetry if consumers are idempotent (spec §66).
SDKs retry on timeouts, so the server will see duplicates, sometimes out of order, sometimes after a run ended.

## Decision

- Delivery is at-least-once; persistence is idempotent on `(workspace_id, event_id)`.
- The first accepted write wins and accepted events are never updated (INV-1).
- `dedup.content_hash` distinguishes a harmless retry (same id, same content: counted as a duplicate, success)
  from a conflict (same id, different content: original kept, conflict counted and logged, not an error to the client).
- Ordering is never taken from arrival (`ordering.sort_events`); late events are accepted after run completion.
- `202` means the batch is committed to PostgreSQL (spec §61.3).

## Alternatives

- Reject same-id/different-content with an error: would make SDK retries after a partial failure look like client bugs.
- Last write wins: violates immutability and lets a retry rewrite history.

## Consequences

Positive: SDK retry logic is simple; ingestion is safe under concurrency (a unique index is the arbiter).
Negative: conflicting duplicates are silently dropped from the client's view; they must be observable via a counter.

## Migration implications

If Kafka is adopted later (spec §57.5) the same key and hash apply at the consumer.
