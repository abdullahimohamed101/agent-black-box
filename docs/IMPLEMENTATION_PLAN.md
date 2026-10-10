# Implementation Plan

Roadmap for the whole product. Per-phase detail lives in `docs/plans/active/` (one
active plan at a time) and moves to `docs/plans/completed/` when done. Phases keep the
numbering of the v1 build prompt; the spec's milestones (§139) map onto them as shown.
Status values: Not started / Active / Complete / Deferred (env) / Deferred (trigger).

| Phase | Name | Spec | Milestone | Status |
| --- | --- | --- | --- | --- |
| 0 | Repository and engineering foundation | §133-134, §145 | M0 | Complete (merged, CI green) |
| 1 | Canonical telemetry contract | §15, §62-66, §141 | M0 | Complete (merged, CI green) |
| 2 | Data model, ingestion, run querying, outbox + summarizer | §60, §71-73, §78 | M0-M1 | Complete (merged, CI green) |
| 3 | Python SDK | §67-68, §17 | M1 | Complete (merged, CI green) |
| 4 | Core web product (fixtures first, then live API) | §95-100, §135 | M1 | Complete (merged, CI green) |
| 5 | Live execution streaming (SSE) | §76, §21 | M1 | Complete (merged, CI green) |
| 6 | Coding-agent observability + flagship demo | §82-83, §136, §26-27 | M2 | Complete (merged, CI green) |
| 7 | Cost and analytics | §79, §22-23, §61 | M3 | Complete (merged, CI green) |
| | **MVP gate** (spec §49, §138): human review, launch quality bar | | | Open: KI-050 (OpenAI and tiered prices); user acceptance |
| 8 | Framework integrations | §70, §18 | post-MVP | Complete (merged, CI green) |
| 9 | Multi-agent distributed tracing + waterfall | §84, §97 | M4 | Not started |
| 10 | Reliability intelligence | §80-81 | M5 | Not started |
| 11 | Evaluations, experiments, run comparison | §85-86, §98 | M6 | Not started |
| 12 | Visual replay | §87 (level 1) | | Not started |
| 13 | Security observability and redaction modes | §32, §83, §93 | M7 | Not started |
| 14 | Policy engine and human approvals | §88-89, §151 | M8 | Not started |
| 15 | Auth, workspaces, RBAC | §91-92 | | Complete (merged, CI green; plan: `docs/plans/completed/phase-15-auth-rbac.md`) |
| 16 | Search and investigation | §77, §152 | | Not started |
| 17 | Alerting and integrations | §35 | | Not started |
| 18 | Performance and scale hardening (measure first) | §57, §74, §107, §119-120 | | Deferred (trigger) |
| 19 | Production hardening | §43, §116-125 | | Not started |
| 20 | Demo, documentation, release quality | §132, §138, §144 | | Not started |

Notes on the reconciliation (ADR-011):

- Tenant scoping and API-key auth start in Phase 2 (INV-3, §92). Phase 15 adds dashboard
  users, OAuth, and RBAC on top; it must not be the first time tenancy exists.
- The outbox table and the run summarizer arrive in Phase 2 (§72, §78) so derived counters
  are rebuildable from day one; analytics (Phase 7) builds on them.
- Phase 4 builds the UI from deterministic fixtures first (§135, §145 items 10-11), then wires
  the real API, so UI work never waits on live LLM spend.
- The artifact abstraction (`ArtifactStore`, local FS) arrives in Phase 6 with diffs and
  terminal output; earlier phases carry `payload_ref` as an opaque nullable reference.
- Session-wide: each phase ends with the `complete-phase` report. Never PASS without evidence.

---

## Phase 0 - Repository and engineering foundation
**Outcome**: another engineer can clone, `cp .env.example .env`, `make setup && make dev`,
open the web UI, hit the API health endpoint, reach PostgreSQL, and run all tests.
**Scope**: pnpm + uv/pip workspace skeleton (`apps/web`, `apps/api`, `packages/event-schema`
placeholder only if used); Next.js shell; FastAPI app factory with `/healthz` `/readyz`
(DB check), request-ID middleware, structured JSON logging, typed config validated at
startup; PostgreSQL via Compose; Alembic baseline; Makefile + `scripts/quality.sh`
(quick/full); ruff, mypy, pytest; eslint, prettier, tsc, vitest; GitHub Actions CI;
`.env.example`; `.gitignore`; docs set from this directory; ADR index.
**Acceptance (all command-checkable)**: `make setup`; `make lint`, `make typecheck`,
`make test` green; `make migrate` applies baseline to an empty DB and is repeatable;
`curl /healthz` 200 and `/readyz` 200 with DB up / 503 with DB down; web shell renders and
displays API health; CI workflow runs the same targets; `docker compose up` brings the stack
up. **Env**: Node/pnpm, Docker and PostgreSQL are not installed on the current machine -
see `docs/development/setup.md`; unmet tooling leaves the corresponding criteria
`UNVERIFIED (env)` and the Phase 0 gate open.
**Do not proceed until the dev environment is reproducible.**

## Phase 1 - Canonical telemetry contract
**Outcome**: a canonical event can be built and validated with no framework involved.
**Scope** (`packages/event-schema`): ID scheme (UUIDv7/ULID, prefixes, client-generatable);
envelope per §64.1 (`schema_version, event_id, workspace_id, project_id, run_id, trace_id,
span_id, parent_span_id, agent_id, agent_version, event_type, occurred_at, sequence,
status, duration_ms, attributes, payload_ref, tags, sdk`); dot-delimited event families
for run, agent, llm, tool, file, git, shell, db, http, retry/timeout/loop/rate-limit,
policy, approval; typed enums; unknown-attribute preservation; payload-size limits
(event 256 KB); documented ordering function; JSON Schema `1.0` generated from the
Pydantic models plus TypeScript types; `docs/architecture/events.md`.
**Acceptance**: tests for valid/malformed/missing-field events, unknown attributes, schema
versions (unsupported -> `EVENT_SCHEMA_UNSUPPORTED`), parent/child relationships, duplicate
IDs, out-of-order sequences; schema artifacts regenerate with no diff; the package imports
nothing framework-specific. ADR-001, ADR-006, ADR-007 (naming) written.

## Phase 2 - Data model, ingestion, run querying
**Outcome**: a synthetic run POSTed to the public API is reconstructed by the query API with
correct ordering and span parentage; duplicates are harmless.
**Scope**: Alembic migrations for users, workspaces, workspace_members, projects, agents,
agent_versions, runs, spans, events, artifacts, evaluations, policies, project_api_keys,
outbox_jobs (spec §73.1); repositories (`RunRepository`, `EventStore`) with mandatory
workspace context; API-key creation (CLI/seed path), hashing, prefix lookup, scopes
(`events:write`, `runs:read`); `POST /v1/events`, `POST /v1/events/batch` (gzip, size limits,
per-project token-bucket limits, partial-rejection errors, `received_at`, `202` = committed);
`POST /v1/runs`, `GET /v1/runs` (cursor pagination, status/project/agent/time filters, sort),
`GET /v1/runs/{id}`, `/events`, `/spans`; run state machine (§153) validated server-side;
outbox row written in the ingest transaction; summarizer worker (`SKIP LOCKED`, leases,
dead-letter) producing `runs.summary` with `summary_version`; late-event handling; error
taxonomy and request IDs.
**Acceptance**: repository tests on real PostgreSQL for tenant scoping, `(workspace_id,
event_id)` dedup under concurrent inserts, cursor stability, out-of-order ingestion producing
correct ordering; cross-workspace read returns 404; revoked/expired/unscoped key rejected;
summaries equal a from-scratch rebuild; migrations empty->head and head-1->head.
ADR-002, ADR-003, ADR-005 written.

## Phase 3 - Python SDK
**Outcome**: a handful of lines trace an agent; the agent is never harmed by telemetry.
**Scope** (`packages/sdk-python`): `BlackBox` / `AgentTracer`-compatible entry, `run()`,
`span()`, `event()`, `llm_call()`, `@observe`; `contextvars` propagation (sync, async,
threads); sequence allocation per run; ID generation; event builder + redaction pipeline
(§67.5); payload modes FULL/METADATA_ONLY/DISABLED; bounded queue with priority classes
(§67.4); background batch exporter with gzip, timeouts, bounded exponential backoff, `429`
handling; disabled, local (JSON file) and offline modes; `flush()`/`shutdown()` + atexit;
dropped-event counters; zero required runtime deps beyond `httpx` (or stdlib).
**Acceptance**: tests for sync, async, nested spans, context propagation across tasks/threads,
retries, bounded buffering and priority dropping, backend-offline (agent continues, queue
bounded, nothing raises), flush on shutdown, redaction determinism; application-thread
overhead benchmark recorded (target <1 ms typical, §156); an example script produces a full
trace through the real API in <10 lines.

## Phase 4 - Core web product
**Outcome**: someone unfamiliar with a run understands it in ~10 seconds.
**Scope**: fixtures (§135: success, failure+retry, expensive, 10,000-event stress); app shell
(`w/[workspace]/projects/[project]/runs`), project dashboard (recent runs, success rate,
failures, cost, avg duration, running agents), runs list (filters, cursor pagination),
run detail (header summary, timeline with event-class filters, collapse grouping, virtualized
list, drawers for LLM / tool / error / generic), first-causal-error emphasis, keyboard
navigation, loading/empty/error states, responsive layout; typed API client generated from
OpenAPI; data fetching/cache strategy decision (ADR).
**Acceptance**: component tests (timeline ordering, filters, drawers, run states); Playwright
smoke on fixtures and on a real ingested run; a 10,000-event fixture scrolls without rendering
all rows; accessibility checks (focus, roles, non-colour status); screenshots captured.

## Phase 5 - Live execution streaming
**Scope**: `GET /v1/runs/{id}/stream` SSE with `Last-Event-ID` resume, heartbeat comments,
stale-connection cleanup, in-process pub/sub fed by the commit path (Postgres `LISTEN/NOTIFY`
if multiple API processes); frontend incremental merge keyed by `event_id`, reorder buffer,
"partial data" indicator, reconnect + REST reconciliation, run-completion handling, live
status line (Planning / Reading / Calling model / ...).
**Acceptance**: tests for duplicate delivery, out-of-order delivery, reconnect with missed
events, browser refresh mid-run, run completion; E2E: start the demo/example agent and watch
the trace populate with no refresh; p95 accept->display measured and recorded (target <1 s).

## Phase 6 - Coding-agent observability and flagship demo
**Scope**: event support for file.read/created/modified/deleted, git.diff/commit/branch/push,
shell.command.* with exit code, test-result semantics (§82.3), process output; `ArtifactStore`
(local FS) + upload endpoint, content hashes, diff artifacts; command risk classes R0-R4
(observe-only, §83); secret-safe terminal capture (env never captured, redaction); UI:
syntax-highlighted diffs, shell panel (cmd, cwd, duration, exit code, stdout/stderr
lazy-loaded), retry/error visualization; `examples/coding-agent` with an intentionally broken
repo (OAuth session-expiry bug), real agent loop, scripted-model mode for deterministic runs
and optional real-model mode (needs user credentials; never required).
**Acceptance**: the demo run (read -> model -> edit -> test fail -> retry -> edit -> pass) is
understandable without narration; deterministic mode used in E2E; secrets planted in demo
output are redacted in storage and UI; large outputs lazy-load.

## Phase 7 - Cost and analytics
**Scope**: versioned pricing table + `CostEngine` (§79; estimated vs provider-reported, pricing
version stored per calculation, user overrides); analytics API and `AnalyticsStore`
interface over Postgres (cost/run, /successful run, /project, /agent, /model, daily spend;
success/failure/timeout/retry rates; tool success; latency p50/p95; behaviour averages);
project analytics UI with expensive agents/models, failure trends, retry-heavy runs, slow ops;
cost-from-retries breakdown (§24); materialized aggregates only where measured slow.
**Acceptance**: pricing and cost unit tests incl. historical-version reproducibility; analytics
integration tests on seeded data; dashboard p95 <1.5 s on the Stage A dataset (measured).
**-> MVP gate**: run the §138 launch quality bar; record the result; wait for human review.

## Phase 8 - Framework integrations
LangGraph callback adapter first, then OpenAI/Anthropic client wrappers, then MCP. Each adapter
converts framework callbacks to SDK calls, ships golden canonical-event fixtures and passes a
shared conformance suite (§70). **Acceptance**: a LangGraph app is instrumented with minimal
changes and yields a useful trace; no backend change was needed.

## Phase 9 - Multi-agent distributed tracing
Trace-context propagation (inherit `trace_id`, set `parent_span_id`, child `agent_id`), agent
spawn/complete events, agent tree, lanes, waterfall with zoom/pan, critical path, synchronized
selection with timeline, virtualization. **Acceptance**: the multi-agent example is clear both
chronologically and hierarchically; skewed child timestamps still nest.

## Phase 10 - Reliability intelligence
Deterministic detectors as processors over the outbox: repeated sequence, identical request,
retry storm, context growth, cost spike, tool-failure cascade; failure-chain reconstruction;
versioned findings with evidence event IDs (§81.2); explainable health dimensions only (no
unexplained score). **Acceptance**: seeded pathological runs are detected with links to
evidence; detector fixtures; heuristic conclusions labelled "probable".

## Phase 11 - Evaluations, experiments, comparison
Evaluator protocol (§85), deterministic + metric + human evaluators, isolated LLM-judge
interface (clearly labelled), independent evaluation states, experiments with single-variable
warnings (§86), comparison with aligned steps and divergence view (§98).
**Acceptance**: two runs compared; divergence point identified; evaluator failures never change
run status.

## Phase 12 - Visual replay
Level 1 only (§87): play/pause/step/1x/2x/5x over recorded events, reduced-motion aware, no
external contact. Levels 2-3 stay deferred. **Acceptance**: a failed run replays event by event
without network calls to original systems (asserted in test).

## Phase 13 - Security observability
Detectors (secret access, `.env` reads, credential-looking output, destructive commands,
privileged DB actions, suspicious destinations, risky git); findings; server-side secret
backstop; redaction config; modes FULL / METADATA_ONLY / DISABLED verified end to end.
**Acceptance**: detection and redaction tests; a trace can be configured to store no payloads.

## Phase 14 - Policy engine and approvals
Small policy DSL (§151), deterministic ordered evaluation, ALLOW/DENY/REQUIRE_APPROVAL,
decisions record policy version, approval state machine (§154) with single-use action-bound
tokens, SDK enforcement hook with per-capability fail-open/closed, audit trail, WebSocket/
control endpoint only for the approval path. **Acceptance**: a protected demo action is blocked
or paused until a human decides; replayed approval rejected.

## Phase 15 - Auth, workspaces, RBAC
OAuth dashboard login, workspace membership, roles (OWNER, ADMIN, DEVELOPER, VIEWER, SECURITY,
BILLING), central authorization helper (actor, action, resource), sensitive-payload permissions,
key management UI, audit of admin actions. **Acceptance**: authorization failure tests per role;
explicit cross-workspace leakage tests across every endpoint (parametrized from the OpenAPI spec).
Built as: generic OIDC with API-owned sessions (ADR-060), one `authorize()` path and a role matrix kept as data (ADR-061), an append-only
audit log (ADR-062); resolves KI-029, KI-027, KI-051, KI-033. The route-walking test registry fails CI for any route without an authorization case.

## Phase 16 - Search and investigation
Filter AST + parser for the §152 grammar, compiled to storage queries; server-side; FTS for
selected text metadata; natural language (optional) compiles to the AST only.

## Phase 17 - Alerting and integrations
Alert rules evaluated by workers; webhook, email, Slack behind an extension interface.

## Phase 18 - Performance and scale hardening
Load tests with realistic payloads first. Each migration (Go ingest, Kafka, ClickHouse, Redis,
object storage, Kubernetes) requires its §57 trigger measured in `docs/benchmarks/` and an ADR.
Expect this phase to conclude "not yet" for most items.

## Phase 19 - Production hardening
Rate limits/quotas, timeouts, circuit breaking where useful, dependency health, secure headers,
secret management, backups + a *performed* restore drill, migration rollback notes, retention
jobs (§94), audit logging, app metrics (§116) and internal tracing, SLO dashboards (§117),
runbooks (§125).

## Phase 20 - Demo, docs, release quality
README with screenshots, architecture, quickstart, SDK/API/events/deployment/security/ops/
troubleshooting docs, three examples (coding, simple custom, multi-agent), deterministic seed
data (`make demo`), short demo recording, release polish checklist (no placeholder text, broken
routes, fake charts, debug logging, or secrets).
