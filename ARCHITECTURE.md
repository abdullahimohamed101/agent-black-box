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

As of Phase 8 the product works end to end: the data plane below, a stdlib-only Python SDK (`packages/sdk-python`), a web UI
(`apps/web`: dashboard, runs list, run detail with a virtualized timeline) and live runs over SSE (`GET /v1/runs/{id}/stream`, woken by
Postgres `NOTIFY`; ADR-022). The data plane: the canonical event contract (`packages/event-schema`), an
ingestion API (`POST /v1/events[/batch]`, gzip, per-project rate limits, idempotent, tenant-bound), a PostgreSQL
store with tenant-keyed tables, a worker that derives run status/summary/spans from events through a transactional
outbox, and a query API (`/v1/runs`, events, spans) with cursors and project scoping. On top of that sit an artifact store for diffs and shell output (ADR-030), coding-agent telemetry with a risk classifier and secret-safe capture (ADR-031), cost and analytics rollups (`/v1/analytics/*`), and adapters for LangGraph, OpenAI, Anthropic and MCP. API reference: `docs/architecture/api-v1.md`.

## Major Components

### Event schema (`packages/event-schema`)
The stable spine: envelope, event families, ID rules, JSON Schema, generated TS types.
Depends on nothing else in the repo. Status: implemented (Phase 1): IDs, envelope models (`EventIn`/`Event`), registry of 43 event types, strict parsing with value-free errors, ordering, content-hash dedup, span derivation, generated JSON Schema and TypeScript types. Reference: `docs/architecture/events.md`.

### API (`apps/api`) - modular monolith
Routers -> services -> repositories -> db, with modules `ingestion`, `runs`, `traces`, `jobs`, `auth`, `projects`
`artifacts`, `analytics`, `authz` (one `authorize()` path, the role/scope matrix as data), `audit` and `workspaces` (members, invitations, projects)
(plus `core` and `db`); evaluations and policies arrive in later phases. Ingestion and query are separate
routers so a slow query never sits on the SDK path. Repositories take a tenant context (INV-3) and tables are keyed
`(workspace_id, id)` with composite foreign keys (ADR-002). Status: implemented (Phase 2): health, API-key auth, ingestion,
run/event/span queries, provisioning CLI, OpenAPI contract (`apps/api/openapi.json`); artifacts (Phase 6) and analytics (Phase 7) are implemented.
Phase 15 adds people: OIDC login, database-backed sessions, workspace membership with six roles, invitations, API-key management, pricing
overrides, an append-only audit log and re-authentication of open streams (ADR-060..062). Keys and people share one `Principal` and one enforcement path.

### Workers (`abb_api.worker`, same image as the API)
PostgreSQL outbox, `FOR UPDATE SKIP LOCKED` leases, retries with backoff, dead letters, one writer per run via a
row lock, debounced and coalesced `summarize_run` jobs; run state is a pure function of the run's events
(`runs/summary.py`) so it is rebuildable (ADR-012). Status: implemented (Phase 2); cost and analytics refresh jobs (Phase 7); detectors and alert jobs
arrive in later phases.

### Python SDK (`packages/sdk-python`)
Instrumentation API -> context manager -> builder/sanitizer -> bounded buffer -> exporter.
Never raises into the host agent. Status: implemented (Phase 3; `blackbox.coding` in Phase 6; SDK 0.2.0 in Phase 8).

### Web (`apps/web`)
Next.js App Router. Server-fetched page data; client-side trace viewers (timeline, waterfall,
diff, replay) with virtualization. Status: implemented (Phases 4-7, 15): dashboard, runs list, run detail with story, timeline, diff and shell panels, live updates, analytics page, sign-in, workspace switcher, settings (members, API keys, pricing, audit log).
The browser holds only a session cookie; the web server is a same-origin relay with a header allowlist (`apps/web/src/server/`), forwarding the cookie to the API, which decides every request.

### Integrations (`integrations/*`), Processors, Examples
Adapters translate framework callbacks to SDK calls only; processors are pluggable derived-
data producers; examples include the flagship coding-agent demo. Status: adapters and the coding-agent demo implemented (Phases 6, 8); processors planned (Phases 9-10).

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
- **Tenant boundary**: workspace ID on every record and every repository call. A person selects a workspace per request (`X-ABB-Workspace`); a workspace or id they cannot see is a 404.
- **Identity**: the API owns sessions and authorization; the web server never holds a credential of its own (ADR-060, ADR-061).
- **Large payloads**: referenced via `payload_ref` -> `ArtifactStore`, not hot tables.
- **Live path**: SSE for one-way updates; WebSockets only for approvals/cancel (Phase 14). SSE is built (Phase 5).
- **Ingestion vs derived state**: ingestion commits events and a job; everything else about a run is derived
  asynchronously and can lag by about a second (`summary_state` says so).
- **One clock for scheduling**: job availability, leases and backoff use the database clock only.
- **Storage behind contracts**: swapping in ClickHouse/object storage must not change the SDK
  contract or event schema.

## External Systems

| System | Purpose | Phase | Status |
| --- | --- | --- | --- |
| PostgreSQL 16 | system of record: tenancy, runs, events, outbox | 0 | running in Compose; migrations through 0047 |
| Docker Compose | reproducible local stack | 0 | implemented (`make up`) |
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

- Full recomputation of very large active runs (KI-016); incremental summarization is the planned response.
- Spec §15 and §64.3 disagree on event names; resolved in ADR-011 (dot-delimited).
- Scale claims require recorded load tests (spec §158); none exist yet.
