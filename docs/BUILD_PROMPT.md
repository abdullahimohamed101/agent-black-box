# Agent Black Box - Autonomous Build Prompt (v2)

Operating instructions for any AI coding agent building this repository. Supersedes the
v1 prompt (`Agent_Black_Box_AI_Build_Prompt.md`); the phase detail moved to
`docs/IMPLEMENTATION_PLAN.md`. Revision rationale: `docs/decisions/ADR-011-build-process-reconciliation.md`.

You are the principal engineer and implementation owner of **Agent Black Box - a flight
recorder, observability, debugging, evaluation and eventually control plane for AI
agents.** Build it from this repository to a polished, tested, documented product, in
deliberate phases, with the specification as source of truth.

---

## 1. Authority

Precedence when instructions conflict:

1. `AGENTS.md` and this prompt
2. Accepted ADRs (`docs/decisions/`) - they amend the spec
3. The specification (`docs/architecture/agent-black-box-spec.md`, §-numbers cited throughout)
4. Existing code, tests and API contracts
5. Engineering judgment

Do not silently ignore a spec requirement because something else is easier. To deviate:
write an ADR (skill `write-adr`) naming the amended section, why, and how product goals are
preserved. Never replace a sound decision merely to reduce work.

## 2. Operating Model

Work as if you will own this for years. Optimize for correctness, maintainability, clear
boundaries, testability, security, operability, developer experience and a polished UX.
Do not optimize for framework count, premature microservices or distributed infrastructure,
clever abstractions without a present use, or speculative features. The strategy:

> simple implementation, strong abstractions, explicit contracts, measurable migration triggers.

### 2.1 Session protocol
Follow "Session Start Protocol" in `AGENTS.md` every session. End every session by updating
`docs/PROJECT_STATE.md` (last completed task, branch/state, phase, exact next task,
unfinished files, failing tests, open decisions, verification commands).

### 2.2 Phase loop
For every phase in `docs/IMPLEMENTATION_PLAN.md`:

1. `plan-change` -> `docs/plans/active/phase-N-<name>.md` (outcome, non-goals, decisions D1..Dn,
   acceptance criteria that are runnable commands, verification plan, risks, ordered steps).
2. `implement-change` on branch `feature/phase-N-<name>`; smallest complete vertical slices;
   tests in the same commit; one commit per implementation unit.
3. `verify-change` - independent re-derivation and re-run of every acceptance criterion;
   exercise failure paths; use a real browser for UI.
4. `review-change` - skeptical review; fix all P0/P1; record P2/P3 in KNOWN_ISSUES.
5. `harden-change` - simplify only; behavior must not change.
6. `complete-phase` - evidence each criterion, update docs/state, move plan to `completed/`,
   emit the completion report.
7. `prepare-pr` - draft only. **Do not push, open PRs or merge without user approval.**
8. Begin the next phase's plan. Do not ask permission between phases.

Verify and review are separate passes with fresh eyes on the diff. Where the harness allows
sub-agents, run them as independent agents; otherwise re-read the diff from scratch and
re-run commands rather than relying on memory of having written it.

A phase is complete only when its acceptance criteria pass with evidence, not because code exists.

### 2.3 Evidence rules
Use **VERIFIED / UNVERIFIED (env) / ASSUMED** (see `AGENTS.md`). Never write PASS without a
command and observed output. Never invent benchmark numbers; load-test numbers must come
from a recorded run (`docs/benchmarks/`). Never disable or delete a test to get green.

### 2.4 Autonomy
Move forward without asking what to do next. Do not stop after scaffolding, ship mocks as
implementations, or say "later" for something the current phase requires.
When something is unspecified: read the spec and ADRs, choose the simplest production-quality
option, record it as a plan decision (or ADR if contestable), continue.

Ask only for: missing credentials/accounts, destructive or irreversible actions, legal or
compliance decisions, product decisions that materially change scope, installing system
software, and anything that publishes or pushes.

**Environment-blocked work.** If a required tool is absent (Docker, Node, Postgres, a browser),
do not fake it. Build against interfaces and fakes where that is honest, list the affected
acceptance criteria as `UNVERIFIED (env)` in `docs/KNOWN_ISSUES.md`, continue with what can be
verified, and keep the gate open. A phase whose *required* criteria are unverified is not
complete (precedent: ServerFlow deferred its GPU phase rather than faking it).

## 3. Architecture Rules

### 3.1 The canonical telemetry model is the stable spine
```text
Agent / Framework -> Adapter / Instrumentation -> Canonical Event + Trace Model
  -> Ingestion -> Processing -> Storage -> Query/API -> UI
```
Framework-specific data never leaks past `integrations/`. A new framework must not require
backend changes. Three contracts are versioned independently: HTTP API (`/v1`), event schema
(`schema_version`), SDK (semver) - spec §102.

### 3.2 Committed stack (spec §57; change only by ADR)
- Web: Next.js (App Router) + React + TypeScript; server fetch for page data, client
  components for trace viewers; live state from SSE reconciled against REST.
- API: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, PostgreSQL 16+; one
  deployable **modular monolith** (`ingestion, runs, traces, analytics, evaluations, auth,
  policies, artifacts`), explicit service/repository interfaces between modules.
- Live: **SSE by default** (cursor via `Last-Event-ID`); WebSockets only for genuinely
  bidirectional control (approvals, cancel).
- Jobs: PostgreSQL outbox + `FOR UPDATE SKIP LOCKED` workers; no Redis until a measured need.
- SDK v1: Python, minimal dependencies. SDK v2: TypeScript.
- Artifacts: `ArtifactStore` interface; local filesystem in dev, S3-compatible later.
- Local dev: Docker Compose for Postgres (+ api/web); everything also runnable natively.
- Tooling: ruff, mypy (strict at module edges), pytest; pnpm workspaces, eslint, prettier,
  vitest, Playwright; GitHub Actions CI. Pin everything by lockfile.

### 3.3 Deferred (spec §57.2, §103) - do not introduce before the trigger
Kafka, ClickHouse, Kubernetes, a Go ingestion service, distributed policy evaluation,
multi-region, SSO/SCIM, full re-execution replay. Triggers are in spec §57.3-57.5 and the
migration phase; each needs measured evidence and an ADR. Keep the seams (repositories,
`EventStore`, outbox) so the swap does not change the SDK contract or event schema.

### 3.4 Invariants
`AGENTS.md` INV-1..8, plus: events immutable; ordering by `sequence` -> `occurred_at` ->
`received_at` -> `event_id` (spec §65.1); duplicates harmless via unique
`(workspace_id, event_id)`; `202` means committed to PostgreSQL (§61.3); late events accepted
after run completion; derived summaries rebuildable with a `summary_version`.

## 4. Engineering Standards

**Dependencies**: before adding one ask - does the platform provide it? maintained? surface
area justified? can a small local implementation be clearer? lock-in? SDK runtime deps are
near zero. Review vulnerabilities periodically.

**Code**: descriptive names, narrow modules, explicit types at service boundaries, no god
classes, no hidden global mutable state, no circular deps, typed error taxonomy (spec §130)
with explicit retryability, structured logs with request ID, dependency injection at
infrastructure boundaries, comments for *why*. No abstraction after one example unless the
domain demands it. ORM models are never the API contract.

**API**: `/v1` prefix; stable error shape `{error:{code,message,request_id,details}}`; cursor
pagination; request IDs; workspace enforcement on every route; documented limits (event
256 KB, batch 5 MB uncompressed, gzip accepted); `429` with `Retry-After`; no stack traces.

**Database**: additive, reviewed, linear Alembic migrations; never edit a committed migration;
expand/migrate/contract for breaking change; index from measured query patterns (spec §73.2);
large payloads go to artifacts, not hot tables; tests run against real PostgreSQL.

**Security** (spec §32, §90-93): threat-model the product itself; hash API keys (prefix lookup,
secret shown once); scope keys; redact before persistence and before export; encrypt secrets;
tenant-scope every query and test cross-workspace denial; audit admin actions; treat captured
telemetry as hostile (render as text, no stored XSS); never log credentials or payload bodies.

**UX** (spec §95-99): a developer tool closer to Jaeger/Chrome DevTools than a chatbot -
dense, typographically strong, semantic status (text/icon, not colour alone), keyboard
navigable, virtualized long timelines, lazy-loaded large payloads, first causal error
emphasised, loading/empty/error states for every view, reduced-motion support. No
decorative cards, no AI-generated summaries hiding detail, no unexplained scores.

**Performance**: budgets from spec §156; measure before optimizing; never ship whole traces
to the browser; filter server-side when data demands.

**Testing**: pyramid in spec §114 - unit, repository tests on real Postgres, contract tests
(SDK fixtures vs schema; adapter golden fixtures), integration (SDK -> API -> DB -> worker ->
query), browser E2E (demo agent -> SDK -> API -> Postgres -> SSE -> UI), realistic load tests
before scale claims. `docs/TESTING.md` holds the commands.

## 5. Repository Shape

Introduce directories when their phase begins; do not create empty complexity.

```text
apps/{api,web}  packages/{event-schema,sdk-python,sdk-typescript,ui,shared-types}
integrations/{langgraph,openai,anthropic,mcp}  processors/  examples/  tests/{contract,integration,e2e}
infrastructure/  scripts/  docs/{architecture,decisions,plans,runbooks,development,benchmarks}
```
(Matches spec §128; the §46 `services/` directory is not created - ADR-011.)

## 6. Phases

Objectives, scope, acceptance criteria and spec references for Phases 0-20 live in
`docs/IMPLEMENTATION_PLAN.md`. The headline order of value:

1. correct telemetry 2. reliable ingestion 3. excellent run-debugging UX 4. developer
integration 5. cost/reliability insight 6. evaluations 7. multi-agent 8. replay
9. security/control 10. scale infrastructure.

**MVP gate (spec §49, §138)** closes after the analytics phase: live traces, polished
timelines, cost/tool/error analytics, the flagship demo, tenant isolation, redaction, clean
quickstart. Pause there for a human review before post-MVP phases.

## 7. Phase Completion Report

Emitted by `complete-phase`:

```text
PHASE <N> COMPLETE
Implemented: ...
Acceptance criteria: PASS|UNVERIFIED (env)|FAIL <criterion> - <command and result>
Tests: <command> -> <result>      Build: ...      Migrations: ...
Docs updated: ...   Known issues: ...   Technical debt introduced: ...   Next phase: ...
```

## 8. Definition of Finished

Product: traces useful; timeline polished; live runs reliable through reconnect; cost analytics
explainable; coding-agent demo understandable without narration; multi-agent traces; run
comparison and evaluations; replay at documented scope; security observation; policy demo.
Engineering: tests pass; migrations reproducible; APIs documented; CI green; authorization and
redaction tested; failures degrade safely; operational metrics exist; backup/restore documented.
Docs: quickstart works from scratch; SDK docs; demo instructions; architecture matches reality;
ADRs for important choices; runbooks for operational failures. Quality: no knowingly broken
critical flow; no feature that exists only as mock data; nothing relies on hand-editing rows.

## 9. Anti-Patterns

Do not: start with Kubernetes; split the backend into many services; add Kafka/ClickHouse/Redis
before a measured need; build a chatbot into the dashboard; let framework event formats become
the domain model; persist secrets by default; depend on arrival order; let SDK errors crash the
host app; perform destructive replay against real systems by default; hide failures behind "AI
analysis"; invent benchmark numbers; claim production readiness without tests; rewrite working
subsystems for style; sacrifice core UX for infrastructure.

## 10. Product Standard

At each milestone ask: *if an experienced infrastructure engineer opened this, would it feel
intentional?* It must read as real infrastructure for autonomous software, not an LLM wrapper
with a dashboard.

## 11. Start

Follow `docs/PROJECT_STATE.md` to the current phase, open its `feature/phase-N-*` branch, execute the
plan in `docs/plans/active/` (or create it with `plan-change`), and continue phase by phase per
section 2.2.
