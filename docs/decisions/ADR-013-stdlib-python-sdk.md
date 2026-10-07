# ADR-013: The Python SDK Is Standard-Library Only; Contract Tests Replace Pydantic

Status: Accepted
Date: 2026-10-07

## Context

The SDK runs inside customers' agents (INV-4, spec §67): every runtime dependency is a version-conflict and supply-chain risk for
a host we do not control. `packages/event-schema` (pydantic) is the authoritative validator, and Phase 1 (R3) left open whether the SDK
imports it or builds events by hand. The exporter also needs an HTTP client and a defined reaction to `429`/`503` + `Retry-After`.

## Decision

1. **No runtime dependencies** (Python >= 3.10). The SDK builds events as plain `dict`s with a small stdlib builder, generates
   prefixed ULIDs itself (a ~40 line copy of the algorithm in `abb_event_schema.ids`), and talks HTTP with `http.client` on the exporter
   thread (explicit timeouts, gzip via `gzip`). `httpx` and `pydantic` are not imported.
2. **Drift is prevented by tests, not by sharing code.** `abb_event_schema` is a *dev* dependency of the SDK. Contract tests assert: every
   event the SDK can emit validates through `EventIn`; SDK ids match the contract's regexes and `parse_id`; the SDK's priority map equals
   `registry.lookup(...).priority` for every registered type; the SDK's limit constants equal `abb_event_schema.limits`; and SDK output
   validates against the generated JSON Schema. A schema change that breaks the SDK fails CI in the schema package's own pipeline.
3. **Validation is not the SDK's job** (the server validates; captured data is hostile either way). The builder only enforces what it
   must to avoid sending guaranteed rejects: truncates/clamps names, drops NUL characters, drops non-finite numbers, bounds attribute count and
   inline payload size (oversized payloads are dropped and counted, never sent).
4. **Exporter retry**: network errors, timeouts and `5xx` retry with exponential backoff and full jitter (base 0.5 s, cap 30 s, 5 attempts);
   `429` and `503` use `Retry-After` (seconds or HTTP-date) when present, capped at 60 s, and otherwise the same backoff. `413` splits the batch in
   half and retries each half; other `4xx` (400/401/403/404/409/415/422) are not retryable: the batch is dropped and counted (`dropped_rejected`), with one
   rate-limited warning. After the attempts are exhausted the batch is dropped and counted (`dropped_export_failed`). While a batch is backing off, the
   queue keeps accepting events up to its bound (then priority dropping applies), so the application thread never waits on the network.
   Per-event `errors` in a `202` response are counted (`rejected_by_server`) and not retried (they would fail again; duplicates/conflicts are final).
5. **Batch usage**: `POST /v1/events/batch` with `batch_id` (a fresh id per batch, reused verbatim on every retry of that batch), gzip when the
   JSON body exceeds 1 KiB, <= `batch_size` (default 100, max 1000) events and <= 4 MiB uncompressed (a safety margin under the server's 5 MiB). Retrying is
   always safe because ingestion is idempotent on `(workspace, event_id)`.

## Consequences

- Install footprint is zero; the host's own dependencies cannot conflict with ours.
- We maintain a second, small implementation of ULIDs and the envelope; the contract tests are the safety net and must stay in the gate.
- Python 3.9 and older are unsupported (the contract package already needs 3.10).
- Revisit if the SDK needs richer typed validation (it should not: that belongs to the server).
