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
`findings` (Phase 10: detectors, finding contract, health, `GET /v1/runs/{id}/findings`) (plus `core` and `db`); `policies` and `approvals` (Phase 14, below). Ingestion and query are separate
routers so a slow query never sits on the SDK path. Repositories take a tenant context (INV-3) and tables are keyed
`(workspace_id, id)` with composite foreign keys (ADR-002). Status: implemented (Phase 2): health, API-key auth, ingestion,
run/event/span queries, provisioning CLI, OpenAPI contract (`apps/api/openapi.json`); artifacts (Phase 6) and analytics (Phase 7) are implemented.
Phase 15 adds people: OIDC login, database-backed sessions, workspace membership with six roles, invitations, API-key management, pricing
overrides, an append-only audit log and re-authentication of open streams (ADR-060..062). Keys and people share one `Principal` and one enforcement path.
Phase 16 adds `search` (a hand-written parser for the §152 grammar into a typed AST, a field catalogue as data, a compiler that takes the `Principal` and never emits a content
column for an actor without `payload.read`, the derived `run_search` document built by the summarizer, bounded reads with a per-process concurrency cap and a time budget);
the runs router carries it as `GET /v1/runs?q=` (ADR-100, ADR-101). `abb_api/content.py` holds the content classification both `runs` and `search` use.
Phase 11 adds `evaluations` (the evaluator protocol and built-ins, the judge interface with its deterministic fake, repositories, the `evaluate_run` job body), `experiments` (declared variants, the control check, a metrics read-model over `runs` and `evaluations`,
evaluate-all) and `comparison` (step extraction per projection, a table-driven normaliser, prefix/suffix plus budgeted Myers alignment, computed on read in a thread); the worker gains the `evaluate_run` handler, which writes only to `evaluations` (ADR-090..092).

Phase 14 adds the policy engine and approvals (ADR-140, ADR-141, ADR-142; **implemented, review pending**): a small YAML policy per project (and an optional workspace base) with ordered first-match evaluation, every save an immutable version (`policy_versions`, migration 0092) and every decision recorded with the policy id and version; `POST /v1/policy/decide`, which the SDK's `guard()` calls before a sensitive action; an approval state machine (`approvals`, migration 0093: REQUESTED, then APPROVED, DENIED, EXPIRED or CANCELLED) enforced by CHECK constraints and a guard trigger as well as by single-statement transitions, bound to a hash of the exact action intent and spent once by consume (the server recomputes the hash and never returns it); long-poll waits with caps; an expiry job (the outbox is the timer); server-written `policy.*` and `approval.*` events under a reserved emitter name; and the Policies and Approvals pages. **Enforcement is cooperative**: it stops an agent that asks through the SDK (`guard()`, `@guarded`, the coding recorder, the MCP adapter) and does not stop code that bypasses it. End-to-end evidence: `scripts/policy-e2e.sh`.


### Security (`apps/api/src/abb_api/security`, `findings/detectors/security.py`) - Phase 13
Redaction before persistence (ADR-130): ingestion and the artifact store pass every string through a bounded, linear-time `SecretScanner` and apply the project's capture mode
(FULL, METADATA_ONLY the default, DISABLED) before anything is stored, because accepted events are immutable (INV-1); a value that cannot be scanned is dropped and the reason recorded
(fail closed). Settings live in `project_security_settings` behind `SecuritySettingsStore` (workspace and project scoped, every change audit-logged in the same transaction; a mute or
allowlist change also queues re-analysis of the project's recent runs, ADR-131 Amendment 1). Six deterministic `sec.*` detectors run in the existing `detect_findings` job, read the full
events whatever the viewer's role and produce content-free findings (fixed wording, event links); `GET /v1/security/findings`, `/v1/security/detectors` and the settings routes
expose them (ADR-131). The web has the run page's chip and Security group, a workspace Security page and a settings page. Status: implemented (Phase 13; independent review pending);
end-to-end evidence is `make security-e2e`.

### Workers (`abb_api.worker`, same image as the API)
PostgreSQL outbox, `FOR UPDATE SKIP LOCKED` leases, retries with backoff, dead letters, one writer per run via a
row lock, debounced and coalesced `summarize_run` jobs; run state is a pure function of the run's events
(`runs/summary.py`) so it is rebuildable (ADR-012); that includes the first-error facts (event, retries, time and spend after it, ADR-112). Status: implemented (Phase 2); cost and analytics refresh jobs (Phase 7); the `detect_findings` job (Phase 10: pure detectors over a capped canonical event load, derived findings, ADR-080); the `evaluate_run` job (Phase 11: a budgeted evaluator in a thread, writes only `evaluations`, ADR-090); alert jobs
arrive in later phases.

### Python SDK (`packages/sdk-python`)
Instrumentation API -> context manager -> builder/sanitizer -> bounded buffer -> exporter.
Never raises into the host agent. Status: implemented (Phase 3; `blackbox.coding` in Phase 6; SDK 0.2.0 in Phase 8).

### Web (`apps/web`)
Next.js App Router. Server-fetched page data; client-side trace viewers (timeline, waterfall,
diff, replay) with virtualization. Status: implemented (Phases 4-7, 15): dashboard, runs list, run detail with story, timeline, waterfall (Phase 9: agent lanes, zoom and pan, critical path, derived in the browser, ADR-071), diff and shell panels, live updates, analytics page, sign-in, workspace switcher, settings (members, API keys, pricing, audit log).
The browser holds only a session cookie; the web server is a same-origin relay with a header allowlist (`apps/web/src/server/`), forwarding the cookie to the API, which decides every request.

### Integrations (`integrations/*`), Processors, Examples
Adapters translate framework callbacks to SDK calls only; processors are pluggable derived-
data producers; examples include the flagship coding-agent demo. Status: adapters, the coding-agent demo and the multi-agent example (threads, an asyncio task and a subprocess joined through a trace context, Phase 9, ADR-070) implemented (Phases 6, 8, 9); processors planned (Phase 10).

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
- **Live path**: SSE for one-way updates. Waiting for an approval is **long polling** over plain HTTP, not a WebSocket: the SDK is standard-library only and Python has no built-in WebSocket client (ADR-140 point 11, ADR-141 point 8). SSE is built (Phase 5).
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
