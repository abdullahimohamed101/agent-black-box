# ADR-001: Canonical Event Schema Owned by Agent Black Box

Status: Accepted
Date: 2026-10-07

## Context

SDKs, framework adapters, the API, processors and the UI all exchange agent telemetry. Once SDKs are
deployed the event contract becomes the most expensive thing to change (spec §64). External schemas
(OpenTelemetry gen_ai conventions) are still evolving and do not express agent concepts such as approvals,
retries or code diffs (§57.6).

## Decision

Agent Black Box owns a versioned canonical schema, implemented in `packages/event-schema`:

- Pydantic v2 models are the source of truth; JSON Schema and TypeScript types are generated and committed.
- **IDs** are kind-prefixed ULIDs (`evt_`, `run_`, `trc_`, `spn_`, `ws_`, `prj_`, ...): generated offline,
  time-sortable, no database sequence leakage, lossless conversion to PostgreSQL `uuid`.
- **Two models**: `EventIn` (client; tenant ids optional, must match the API key) and `Event` (stored;
  tenant ids and `received_at` required).
- **Event names** are dot-delimited (`tool.call.completed`); unknown well-formed names are accepted.
- **`agent_id` is a slug**, resolved server-side to an agent row.
- **Spans are derived** from lifecycle events (`*.started` opens, `*.completed`/`*.failed` closes) rather than
  sent as separate objects; a generic `span.*` family covers custom spans.
- **Attributes** are flat, namespaced and size-limited; registered keys are typed; unknown keys are preserved.
- **Payloads**: a small inline `payload` (<= 64 KB) plus `payload_ref` for artifacts. This amends the spec's
  envelope, which shows both forms in different sections (§15 vs §64.1), so FULL-mode content can be captured
  before the artifact store exists (Phase 6).
- Validation is strict at the wire boundary (no coercion), rejects NUL characters (PostgreSQL cannot store them),
  keeps integers within the JavaScript-safe range, and never echoes submitted values in errors.
- Versioning is `MAJOR.MINOR`; any `1.x` is accepted, unknown top-level fields are ignored.

## Alternatives

- Adopt OpenTelemetry directly as the wire format: lacks agent semantics and its gen_ai conventions are unstable.
  Rejected; mapped at the boundary instead (ADR-007).
- UUIDv7 instead of ULID: equivalent properties; Python 3.12 has no stdlib `uuid7`, and prefixed ULIDs are readable
  in logs. Storage still uses UUID columns.
- Send spans as separate records: more round trips and more partial-state cases; deriving them keeps the event the
  only unit of ingestion and idempotency.

## Consequences

Positive: one contract for every producer and consumer; strong tests (fixtures, schema/model agreement).
Negative: pydantic is a dependency of anything that uses the models directly (see Phase 3 SDK decision);
changes need the `add-event-type` checklist.

## Migration implications

None yet. Later changes follow spec §64.5; the committed JSON Schema files make drift visible in review.
