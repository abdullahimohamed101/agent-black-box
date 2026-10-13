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
| ADR-021 | Web reads the API through a server-side proxy holding a `runs:read` key | 4 | Superseded by ADR-060 ([file](decisions/ADR-021-web-api-access-before-auth.md)) |
| ADR-022 | Live streams resume by arrival time and wake on Postgres NOTIFY | 5 | Accepted ([file](decisions/ADR-022-live-stream-resume-and-wakeup.md)) |
| ADR-030 | Artifacts behind an `ArtifactStore` contract; client-id upload; chunked reads (implements ADR-005) | 6 | Accepted ([file](decisions/ADR-030-artifact-store-and-upload-api.md)) |
| ADR-031 | Coding-agent telemetry vocabulary, command risk classes, secret-safe capture | 6 | Accepted ([file](decisions/ADR-031-coding-agent-telemetry-and-capture.md)) |
| ADR-032 | Syntax highlighting is a local tokenizer rendered as React text | 6 | Accepted ([file](decisions/ADR-032-local-syntax-highlighting.md)) |
| ADR-040 | Cost engine: versioned, reproducible, computed inside the summarizer | 7 | Accepted ([file](decisions/ADR-040-cost-engine-and-pricing.md)) |
| ADR-041 | Analytics store over derived tables; aggregates only where measured slow | 7 | Accepted ([file](decisions/ADR-041-analytics-store-and-aggregates.md)) |
| ADR-042 | Retry cost attribution by retry scope span | 7 | Accepted ([file](decisions/ADR-042-retry-cost-attribution.md)) |
| ADR-043 | Analytics read daily rollups plus a live today (measured slow without) | 7 | Accepted ([file](decisions/ADR-043-analytics-rollups.md)) |
| ADR-044 | Billing modifiers (service tier, data residency, cache writes) are optional `llm.*` attributes priced from stated vendor rules; an unknown modifier is unpriced with a reason (amends ADR-040) | 7 | Accepted ([file](decisions/ADR-044-billing-modifiers-service-tier-region-cache-writes.md)) |
| ADR-050 | Adapters are separate distributions depending only on the SDK; frameworks are optional extras | 8 | Accepted ([file](decisions/ADR-050-adapter-packaging-and-optional-dependencies.md)) |
| ADR-051 | Shared conformance suite: scenarios, structural rules, golden fixtures | 8 | Accepted ([file](decisions/ADR-051-adapter-conformance-suite.md)) |
| ADR-052 | Adapter behavior contract: never raise, run ownership, explicit parenting, no payloads by default | 8 | Accepted ([file](decisions/ADR-052-adapter-behavior-contract.md)) |
| ADR-060 | Dashboard identity via OIDC with API-owned, database-backed sessions (supersedes ADR-021) | 15 | Accepted ([file](decisions/ADR-060-dashboard-identity-oidc-sessions.md)) |
| ADR-061 | One authorization helper; keys and people share the enforcement path; the matrix is data | 15 | Accepted ([file](decisions/ADR-061-authorization-helper-and-role-matrix.md)) |
| ADR-062 | Append-only, bounded workspace audit log | 15 | Accepted ([file](decisions/ADR-062-append-only-audit-log.md)) |
| ADR-070 | Multi-agent trace semantics: one run, agent spans with their own id, explicit trace-context propagation, remote emitters omit `sequence` | 9 | Accepted ([file](decisions/ADR-070-multi-agent-trace-semantics-and-propagation.md)) |
| ADR-071 | The waterfall is derived in the browser from the loaded events; critical path by last-finishing child | 9 | Accepted ([file](decisions/ADR-071-browser-derived-waterfall.md)) |
| ADR-080 | Findings are derived, versioned, metadata-only records from a bounded detector job over the outbox | 10 | Accepted ([file](decisions/ADR-080-findings-derived-versioned-metadata-only.md)) |
| ADR-081 | Health is a set of explained dimensions with recorded thresholds, not a score | 10 | Accepted ([file](decisions/ADR-081-health-explained-dimensions.md)) |
| ADR-100 | Search is a typed filter grammar compiled server-side over a derived per-run Postgres search document (literal tsvector/tsquery, bounded reads, measured triggers) | 16 | Accepted ([file](decisions/ADR-100-search-grammar-and-derived-search-document.md)) |
| ADR-101 | Search never reveals withheld content: permission-split documents, a compile-time gate, no counts, flags or ranking (amends ADR-061) | 16 | Accepted ([file](decisions/ADR-101-search-never-an-oracle-for-withheld-content.md)) |
| ADR-090 | Evaluations are derived, versioned records with independent state, computed by the outbox worker in a budgeted thread | 11 | Accepted ([file](decisions/ADR-090-evaluations-derived-versioned-budgeted-worker.md)) |
| ADR-091 | Model judges behind a one-method provider interface; a deterministic fake is the only shipped provider | 11 | Accepted ([file](decisions/ADR-091-model-judge-interface-fake-provider.md)) |
| ADR-092 | Run comparison by normalised step alignment, computed on read, bounded by a work budget, role-aware | 11 | Accepted ([file](decisions/ADR-092-run-comparison-normalised-alignment.md)) |
| ADR-110 | Self-hosted IBM Plex (Latin-1 woff2, OFL 1.1) through `next/font/local`; no runtime font request | 21 | Accepted ([file](decisions/ADR-110-self-hosted-plex-fonts.md)) |
| ADR-112 | First-error facts (event, retries, time and model spend after it) are derived into the run summary at `SUMMARY_VERSION` 5, metadata for every role, rebuilt by re-summarising | 22 | Accepted ([file](decisions/ADR-112-first-error-summary-fields.md)) |
| ADR-111 | "Flight recorder trace" direction: semantic palette, red only for failure, glyph-plus-word status, Plex Serif for the failure line, motion language | 22 | Accepted ([file](decisions/ADR-111-flight-recorder-trace-direction.md)) |
| ADR-113 | The waste report (spend on failed runs and retries by agent and model) reads `runs` and `cost_calculations` live, metadata for every role, no new stored state | 22 | Accepted ([file](decisions/ADR-113-waste-report-read-live.md)) |
| ADR-120 | Visual replay (level 1) is a client-side projection over the events the run page already loaded: canonical order, clamped pacing, same-origin GET only, the playhead in the URL (`?t=`), no new endpoint or stored state | 12 | Accepted ([file](decisions/ADR-120-visual-replay-level-1.md)) |
| ADR-121 | Events carry the cost engine's settled per-call cost (`cost_usd`, `cost_unpriced`), additive and metadata for every role, so replay's cost so far equals the run summary on server-priced runs | 12 | Accepted ([file](decisions/ADR-121-event-settled-cost.md)) |
| ADR-130 | Redaction before persistence under INV-1 (fail closed), per-project capture modes with the SDK's meanings, artifacts stored as a note when dropped, a bounded server scanner kept in step with the SDK by a parity test (refines Phase 13 D4) | 13 | Accepted ([file](decisions/ADR-130-server-side-redaction-and-capture-modes.md)) |
| ADR-131 | Six deterministic, versioned security detectors with fixed content-free wording, `medium`/`high` severities, a separate security badge and workspace list, mutes and an allowlist per project (no allowlist = no destination findings), rebuildable with the Phase 10 job (Phase 13 D1-D3, D5) | 13 | Accepted ([file](decisions/ADR-131-security-detectors-and-findings.md)) |
| ADR-140 | Policy model: small YAML DSL (ordered rules, first match wins, default allow), pure linear-time evaluator with fixed policy-authored reasons, PyYAML event-stream reader, append-only versions per project and workspace, dry run over recorded events, cooperative guarantee, long-poll pointer (Phase 14 D1, D2) | 14 | Accepted ([file](decisions/ADR-140-policy-model-and-versioning.md)) |
