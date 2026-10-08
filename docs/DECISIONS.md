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
| ADR-013 | Python SDK is standard-library only; contract tests replace pydantic | 3 | Accepted ([file](decisions/ADR-013-stdlib-python-sdk.md)) |
| ADR-020 | Web data fetching: TanStack Query behind a same-origin read proxy | 4 | Accepted ([file](decisions/ADR-020-web-data-fetching-and-cache.md)) |
| ADR-021 | Web reads the API through a server-side proxy holding a `runs:read` key | 4 | Accepted ([file](decisions/ADR-021-web-api-access-before-auth.md)) |
| ADR-022 | Live streams resume by arrival time and wake on Postgres NOTIFY | 5 | Accepted ([file](decisions/ADR-022-live-stream-resume-and-wakeup.md)) |
| ADR-040 | Cost engine: versioned, reproducible, computed inside the summarizer | 7 | Accepted ([file](decisions/ADR-040-cost-engine-and-pricing.md)) |
| ADR-041 | Analytics store over derived tables; aggregates only where measured slow | 7 | Accepted ([file](decisions/ADR-041-analytics-store-and-aggregates.md)) |
| ADR-042 | Retry cost attribution by retry scope span | 7 | Accepted ([file](decisions/ADR-042-retry-cost-attribution.md)) |
| ADR-043 | Analytics read daily rollups plus a live today (measured slow without) | 7 | Accepted ([file](decisions/ADR-043-analytics-rollups.md)) |
| ADR-050 | Adapters are separate distributions depending only on the SDK; frameworks are optional extras | 8 | Accepted ([file](decisions/ADR-050-adapter-packaging-and-optional-dependencies.md)) |
| ADR-051 | Shared conformance suite: scenarios, structural rules, golden fixtures | 8 | Accepted ([file](decisions/ADR-051-adapter-conformance-suite.md)) |
| ADR-052 | Adapter behavior contract: never raise, run ownership, explicit parenting, no payloads by default | 8 | Accepted ([file](decisions/ADR-052-adapter-behavior-contract.md)) |
