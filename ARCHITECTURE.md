# Architecture

## Source of Truth

The authoritative specification is `docs/architecture/agent-black-box-spec.md`
(extracted from the engineering design `.docx`). Accepted ADRs in `docs/decisions/` amend
it. This file summarizes what is *implemented now*; the spec and
`docs/IMPLEMENTATION_PLAN.md` define the full plan. Keep the status lines below honest.

## System Overview

Agent Black Box records what AI agents do. An SDK (or framework adapter) emits **canonical
events** describing runs, spans, LLM calls, tool calls, file/Git/shell activity, retries and
policy decisions. The API ingests them idempotently into PostgreSQL, derives summaries and
findings asynchronously, streams live updates over SSE, and serves a Next.js UI for
timelines, waterfalls, diffs, analytics, comparison, replay and (later) policy control.

```text
Agent / Framework -> Adapter -> SDK -> [HTTP /v1/events/batch] -> Ingestion
   -> PostgreSQL (events + outbox) -> workers (summary, cost, findings) -> derived tables
   -> Query API / SSE -> Next.js web
```

As of the current state (see `docs/PROJECT_STATE.md`) **nothing is implemented**: this
repository contains the specification, process documents and plans only.

## Major Components

### Event schema (`packages/event-schema`)
The stable spine: envelope, event families, ID rules, JSON Schema, generated TS types.
Depends on nothing else in the repo. Status: planned (Phase 1).

### API (`apps/api`) - modular monolith
Modules: `ingestion`, `runs`, `traces`, `analytics`, `evaluations`, `auth`, `policies`,
`artifacts`; each exposes a service interface and owns its repositories. Control-plane query
API and telemetry ingestion are separate routers so a slow query never sits on the SDK path.
Status: planned (Phase 0 skeleton, Phase 2 data plane).

### Workers (`apps/api` worker entrypoint)
PostgreSQL outbox + `SKIP LOCKED` leases, idempotent processors (summarizer, cost, detectors,
alert evaluator), dead-letter after bounded retries. Status: planned (Phase 2, grows later).

### Python SDK (`packages/sdk-python`)
Instrumentation API -> context manager -> builder/sanitizer -> bounded buffer -> exporter.
Never raises into the host agent. Status: planned (Phase 3).

### Web (`apps/web`)
Next.js App Router. Server-fetched page data; client-side trace viewers (timeline, waterfall,
diff, replay) with virtualization. Status: planned (Phase 0 shell, Phase 4 product).

### Integrations (`integrations/*`), Processors, Examples
Adapters translate framework callbacks to SDK calls only; processors are pluggable derived-
data producers; examples include the flagship coding-agent demo. Status: planned (Phases 6, 8-10).

## Dependency Direction

```text
apps/web  -> packages/ui, packages/shared-types   (types generated from event-schema/OpenAPI)
apps/api  -> packages/event-schema
integrations/* -> packages/sdk-python -> packages/event-schema
api modules: routers -> services -> repositories -> db   (no cross-module table access)
```

`packages/event-schema` must not depend on `apps/*`, SDKs or frameworks.

## Important Boundaries

- **Canonical model vs frameworks**: framework shapes stop at `integrations/`.
- **SDK vs host agent**: telemetry is secondary; failures are isolated and bounded.
- **Ingestion durability**: `202` means committed to PostgreSQL; derived work is async.
- **Raw events vs derived state**: events immutable; summaries/findings rebuildable.
- **Tenant boundary**: workspace ID on every record and every repository call.
- **Large payloads**: referenced via `payload_ref` -> `ArtifactStore`, not hot tables.
- **Live path**: SSE for one-way updates; WebSockets only for approvals/cancel (Phase 14).
- **Storage behind contracts**: swapping in ClickHouse/object storage must not change the SDK
  contract or event schema.

## External Systems

| System | Purpose | Phase | Status |
| --- | --- | --- | --- |
| PostgreSQL | system of record: tenancy, runs, events, outbox | 0 | planned |
| Docker Compose | reproducible local stack | 0 | planned |
| Object storage (S3-compatible) | artifacts at hosted scale | 18 | deferred (trigger) |
| Redis | cache/pub-sub/rate limits | 18 | deferred (trigger) |
| Kafka | durable event bus | 18 | deferred (trigger, spec §57.5) |
| ClickHouse | event analytics at scale | 18 | deferred (trigger, spec §57.4) |
| Kubernetes | orchestration at scale | 18 | deferred (trigger) |

## Testing Architecture

Unit; repository tests on real PostgreSQL; contract tests (SDK fixtures vs schema, adapter golden
fixtures); integration (SDK -> API -> DB -> worker -> query); browser E2E; load tests with
realistic payloads. See `docs/TESTING.md`.

## Known Architectural Risks

- Tooling gaps on the development machine (`docs/KNOWN_ISSUES.md`) block verification of
  the web, Docker and PostgreSQL paths until installed.
- Spec §15 and §64.3 disagree on event names; resolved in ADR-011 (dot-delimited).
- Scale claims require recorded load tests (spec §158); none exist yet.
