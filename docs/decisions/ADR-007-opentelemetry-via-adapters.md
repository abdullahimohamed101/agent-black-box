# ADR-007: OpenTelemetry Compatibility Through Adapters

Status: Accepted
Date: 2026-10-07

## Context

Interoperability with OpenTelemetry (spec §42, §57.6, INV-8) is valuable, but external semantic conventions
change and do not cover agent-specific concepts.

## Decision

- The canonical schema does not depend on OpenTelemetry; no OTel package is a dependency of `abb-event-schema`
  (enforced by a test that imports the contract in a clean interpreter).
- Attribute names follow the spec (`llm.*`, `tool.*`, `shell.*`), not `gen_ai.*`. A versioned mapping layer converts
  in both directions at the boundary (`trace_id`/`span_id`/`parent_span_id` map to OTel ids; attributes map to span
  attributes; point events map to span events).
- That mapper lives in its own package when built (planned `packages/otel-bridge`), outside the core pipeline.
- Event names are dot-delimited (ADR-011), which is compatible with OTel event naming.

## Alternatives

Adopting `gen_ai.*` names directly: couples every consumer to an unstable external vocabulary.

## Consequences

An OTel import/export feature costs a mapping table and tests, not a schema change.

## Migration implications

None. Mapping versions are independent of `schema_version`.
