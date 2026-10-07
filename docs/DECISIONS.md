# Decisions

Index of Architecture Decision Records in `docs/decisions/`. Numbers ADR-001..010 are
reserved by the spec (§141) for its initial set; they are written when the decision is
implemented (file exists = written). Notable smaller choices are recorded in the active
plan's Decisions section; contestable ones become ADRs (skill `write-adr`).

| ADR | Title | Written in phase | Status |
| --- | --- | --- | --- |
| ADR-001 | Canonical event schema owned by Agent Black Box | 1 | Accepted ([file](decisions/ADR-001-canonical-event-schema.md)) |
| ADR-002 | PostgreSQL as MVP system of record (Core, structural tenancy) | 2 | Accepted ([file](decisions/ADR-002-postgresql-system-of-record.md)) |
| ADR-003 | FastAPI modular monolith for v1 | 0 | Accepted ([file](decisions/ADR-003-fastapi-modular-monolith.md)) |
| ADR-004 | SSE as default live-stream transport | 5 | Accepted by spec; file pending |
| ADR-005 | Large payloads stored as artifacts | 6 | Accepted by spec; file pending |
| ADR-006 | At-least-once ingestion with idempotent persistence | 1-2 | Accepted ([file](decisions/ADR-006-at-least-once-idempotent-ingestion.md)) |
| ADR-007 | OpenTelemetry compatibility through adapters | 1 | Accepted ([file](decisions/ADR-007-opentelemetry-via-adapters.md)) |
| ADR-008 | Kafka deferred until independent-consumer/throughput trigger | 18 | Accepted by spec; file pending |
| ADR-009 | ClickHouse deferred until analytical-scale trigger | 18 | Accepted by spec; file pending |
| ADR-010 | Client-side redaction before export | 3 | Accepted by spec; file pending |
| ADR-011 | Build-process reconciliation (prompt vs spec, ServerFlow workflow) | pre-0 | Accepted ([file](decisions/ADR-011-build-process-reconciliation.md)) |
| ADR-012 | Run state derived asynchronously through a transactional outbox | 2 | Accepted ([file](decisions/ADR-012-derived-run-state-and-outbox.md)) |
