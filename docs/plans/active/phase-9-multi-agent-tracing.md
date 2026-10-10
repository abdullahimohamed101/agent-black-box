# Phase 9 - Multi-agent distributed tracing and waterfall

Status: Planned and reviewed 2026-10-10; ready for implementation (independent review `phase-9-plan-review.md` at f0698e6 addressed below, see "Review dispositions"; the user's answers to the decisions below are still needed, "go with your recommendation" suffices)
Owner: implementer agent
Branch: `feature/phase-9-multi-agent-tracing` (from `main` c212058; worktree `../abb-worktrees/phase-9`)
Depends on: Phase 1 (span derivation, ordering), Phase 3 (SDK context), Phase 4/5 (run page, virtualized timeline, live merge), Phase 7 (cost lines, analytics rollups), Phase 8 (adapter contract), Phase 15 (`payload.read` projection)
Spec refs: §84 (multi-agent trace semantics), §97 (waterfall UX), §63.4-63.5 (run vs trace, span), §64.1 (`agent_id` per event), §65.1-65.3 (ordering, late events, causal display), §67.2 (context propagation), §68 (public API), §96.1 (grouping by agent), §135 (fixtures), §139 M4
ADRs: ADR-011 (dot names), ADR-013 (stdlib SDK), ADR-022 (streams), ADR-040 (cost lines carry the event's agent), ADR-043 (rollups), ADR-052 (adapter parenting), ADR-061 (content withheld from VIEWER). New: ADR-070, ADR-071 (drafted in the appendix; files are written in step 11).
Migration ids: **none**. Reserved range for this phase: 0050-0059, unused; the alembic head stays 0047 after this phase merges (see "Merge notes").
`SUMMARY_VERSION`: this phase bumps 2 -> **3**; **the next bump (Phase 16) is 4** (cross-plan item 1).
Known issues: resolves KI-063 only if its spike succeeds (D10); files KI-084..086 (claimed here; Phase 16 holds KI-080..083).
Merge order: 9 before 10 (the stable root-agent and `trace_id` rule of D4 is what makes `runs.agent_slug`, the key of Phase 10's cost-spike baseline, stable under clock skew), then 16, then 11.

## Decisions needed from the user

1. **Trace model.** One run per job: a child agent is a span (`agent.started` ... `agent.completed`) with its own `agent_id` inside the
   parent's run and trace (spec §84, §63.4), never a run of its own. Recommended: yes. (Alternative: one run per agent linked by `trace_id`; needs a
   trace-level query, splits cost and the live stream across runs, contradicts §84.)
2. **The multi-agent example.** A new pure-SDK `examples/multi-agent` (planner spawns researcher and coder in threads, the coder runs a tester in a
   subprocess, a reviewer runs as an asyncio task; fake models, no network, deterministic, with a `--skew-ms` knob for the subprocess clock).
   Recommended: yes; a LangGraph multi-agent example stays deferred with KI-061.
3. **Where the waterfall lives.** A `Timeline | Waterfall` view switch on the run page (spec §95: "timeline/waterfall" is one region), sharing the
   selection, the first-error jump and the event drawer; the choice is kept in the URL (`?view=waterfall`). Recommended: yes.
4. **Analytics attribution (reworded after review F2).** The code already splits: cost by agent (`/v1/analytics/cost.by_agent`) keys on each
   cost line's `agent_id`, which is the **event's** agent, so after this phase a child's model calls appear under the child; run counts, success
   rates and `summary.active_agents` key on the **run's** root agent (`runs.agent_slug`). Recommended: keep that split, document it in
   `api-v1.md`, test it (AC-4), and file KI-084 for per-agent run metrics when a user asks. (Alternative: attribute cost lines to the root agent;
   needs an ADR line amending ADR-040 and hides child cost from analytics.)
5. **KI-063 (adapter spans in the SDK context).** A bounded spike (step 10): if LangGraph fires a node's callbacks on the thread that runs the
   node body, the adapter binds the SDK context for that node; otherwise KI-063 stays open with the measured reason. Recommended: yes.

## Outcome

A developer traces a system of cooperating agents, in threads, asyncio tasks and other processes, with a few lines, and the run page shows
what happened both chronologically (the existing timeline, now with agent labels) and hierarchically (a waterfall with span rows nested by
parentage under one synthetic run root, one lane per agent, a zoomable and pannable time axis, the critical path highlighted, selection
synchronized with the timeline and the drawer). A child whose clock is skewed still nests under its parent. Runs with tens of thousands of
events stay responsive because only visible rows are in the DOM and collapsed subtrees are drawn as one aggregate bar. Everything a VIEWER
could not see before stays hidden.

## Non-goals

Cross-run traces (`trace_id` spanning runs), OpenTelemetry `traceparent` interoperability (ADR-007: a later adapter maps it at the boundary),
a TypeScript SDK (§69, Phase 17), processors or detectors over agent trees (Phase 10), per-agent run metrics in analytics (decision 4, KI-084),
LangGraph sub-graph identity and state diffs (KI-061), agent-to-agent message capture beyond the delegation input and result payloads, graph
layouts (DAG rendering), a server-side spans-tree endpoint (D5: the waterfall is derived in the browser from the events already loaded), new
migrations, new `/v1` routes, new runtime dependencies (the SDK stays stdlib-only; the web reuses `@tanstack/react-virtual`). Nothing here
needs Kafka, ClickHouse, Redis or Kubernetes.

## Current architecture (verified by reading the code; ASSUMED where marked)

- **Schema** (`packages/event-schema`): the envelope carries `agent_id` per event (`event.py:232`, slug `[a-z0-9][a-z0-9._-]{0,63}`, `:115`),
  `span_id`, `parent_span_id` (a parent requires a span, cannot be itself; `:266-274`). The registry already has `agent.started` (OPEN, kind
  AGENT), `agent.completed` (CLOSE), `agent.spawned` (POINT, requires `agent.child_id`), `agent.state_changed` (`registry.py:196-199`);
  `agent.child_id` is a known STRING attribute (`:52`) in the reviewed-metadata set (`apps/api/tests/authz/test_content_withheld.py:31`).
  There is no `agent.failed`: `lookup()` returns `None` for it (`:254-261`), so it is a POINT/CUSTOM event that never closes a span (the web
  fixture `failureRetryRun` uses it, `fixtures/runs.ts:115`, so its root span is unfinished to a faithful port; fixed in step 7). `spans.derive_spans`
  nests by `parent_span_id` only, never by time; a late parent, an orphan or a close-before-open is normal; `check_span_relationships` (two parents,
  cycle, span in two traces) is a separate function that **no API code calls** (`rg check_span_relationships apps/api/src` finds nothing), so a
  cycle `A.parent=B, B.parent=A` is accepted, stored and summarised. `ordering.sort_events`: by `(sequence, occurred_at, received_at, event_id)`
  when every event has a sequence, else by `(occurred_at, sequence, received_at, event_id)`; its docstring says multi-process emitters are Phase
  9's (Phase 1 plan risk R1). The golden file `tests/data/ordering-golden.json` pins the TypeScript port (`apps/web/src/lib/ordering.ts`,
  `apps/web/tests/ordering.test.ts`). `export.py:120-136` generates `schemas/1.0/event-types.json` (`span_role`, `span_kind`, required attributes
  per type); `make schema`/`schema-check` regenerate and verify it; the web has no copy of the registry today.
- **SDK** (`packages/sdk-python/src/blackbox`): `Run.__init__` creates `run_id`/`trace_id`, one `itertools.count` sequence and emits `run.started`
  unconditionally as a **point event with no `span_id`** (`client.py:290-309`); `__exit__` always calls `end()` which emits `run.completed`/
  `run.failed`/`run.cancelled` (`:383-397,447`); adapters read `run.ended` (`:311-314`). Top-level spans have `parent_span_id = None`, so a real
  trace is a **forest** (no root span); only the web fixtures wrap a run in one `agent.started` span. `Run._emit` stamps `run.agent_id` on every
  event (`client.py:740`). `Span.start()` parents to the current span of the same run when no explicit `parent` is given (`:150-152`); kind `agent`
  maps to `agent.started`/`agent.completed`/`agent.completed` (`_TYPED_SPANS`, `:53`). `build_event` takes `sequence: int` as a required parameter
  and always writes it (`events.py:155,173`): there is no omit path yet. Context is two contextvars (`context.py`); asyncio tasks inherit, threads
  need `bind()`; `os.register_at_fork` recreates buffer and exporter in a forked child (`:464-471,710-717`). ASSUMED (fork semantics, not shown by a
  test: `test_client.py:390-416` starts a new run in the child): a forked child that keeps using the parent's `Run` shares its ids and continues
  the parent's sequence counter from the fork point. `agent_slug()` (`config.py:21-25`) returns the default `"agent"` for a name with no slug
  characters, and `"agent"` is also the default root agent. There is no way to continue an existing run from another process, no agent id per
  span, and no serialized context. `Redactor.apply` drops payloads outside `PayloadMode.FULL` (`redaction.py:281-287`), so any payload that goes
  through `_emit` is gated.
- **Adapters** (`integrations/langgraph/handler.py`, ADR-052): explicit parenting by framework run id, never the SDK context (KI-063); `run_inline =
  True`; the package pins `agent-black-box>=0.2.0` (`pyproject.toml:7`). Client wrappers parent to the current SDK span.
- **API**: `runs/summary.py:derive_run` is the pure derivation (SUMMARY_VERSION 2; `test_run_summary.py:199` pins it); it sets `agent_slug =
  ordered[0].agent_id` and `trace_id = ordered[0].trace_id` (`summary.py:101,179-180`) and `started_at = min(occurred_at)` (`:97`), all of which a
  skewed or forged child event can move. `ingestion/store.py:117-152` creates an agent row (and version) for every distinct `(project, agent_id)`
  seen, seeds the run row from the earliest event of the batch (`:136-144`) and rejects an event whose run belongs to another project
  (`RUN_PROJECT_MISMATCH`, `:147-152`), so a child process must use a key of the same project. **Cost lines carry the event's agent**
  (`cost/engine.py:34,118`), the cost rollup groups by that `agent_slug` (`cost/repository.py:165`, `analytics/rollup.py:124-138`) and feeds
  `/v1/analytics/cost.by_agent` (`analytics/postgres.py:413,449-455`); the run rollups and `summary.active_agents` key on `runs.agent_slug`
  (`rollup.py:81-86`, `postgres.py:293-322`). `rollup.py:104` counts runs with `0 < summary_version < SUMMARY_VERSION` as `unrebuilt_runs`, which
  `Analytics.tsx:164-166` renders as "N runs predate cost calculation; breakdowns by model and ...". `spans` table: `parent_span_id`, `agent_slug`,
  index `ix_spans_run_parent`; the summarizer writes spans from the derivation (`runs/repository.py:147-220`). `GET /v1/runs/{id}/spans` pages
  derived spans (lags by the summarizer debounce; not live). `GET /v1/runs/{id}/events` serves canonical order with `ordering_mode`; a change of
  mode mid-paging is `409 CURSOR_STALE` and the web restarts paging at most 3 times (`queries.ts:62-104`). `RunOut.summary` is the full untyped dict
  in lists and detail (`schemas.py:36`, `service.py:70-88`), polled by the dashboard every 15 s (`queries.ts:47`). `event_out` withholds content
  attributes and `_span_out` withholds content span names for actors without `payload.read` (`runs/content.py`); stream frames use the same
  projection. 36 `/v1` operations (`test_openapi.py:55`); the route registry (`tests/authz/registry.py`) has a case per route.
- **Web** (`apps/web`): `RunDetail.tsx` loads every event (500 per page, back to back), merges live frames, filters and groups (`lib/timeline.ts`),
  renders the virtualized `Timeline.tsx` (`@tanstack/react-virtual`, 36 px rows, `role=listbox`, j/k/e keys) with selection by event id and an
  `EventDrawer`; `t0 = min(occurred_at)` (`RunDetail.tsx:183-186`). `useLiveEvents` (`live.ts:117-121`) returns only the fully merged, re-sorted
  `events` array: the per-frame batch is consumed in `onEvents` and never surfaced, and `mergeEvents` may re-sort the whole array
  (`ordering.ts:83`). No component reads `/spans`. `withheld.ts` renders a withheld attribute as `WITHHELD_TEXT` ("content withheld for your role"),
  not the API's `[withheld]` marker. Fixtures: `RunBuilder.add` stamps `sequence: this.seq` on every event and advances one clock
  (`fixtures/builder.ts:38-72`), `build()` hard-codes `ordering_mode: "sequence"` (`:132`) and derives spans with `agent_id: this.agent` and
  `parent_span_id` of the first event (`:159-166`); `tests/fixtures.test.ts:31` asserts `sequence === i + 1` for every fixture run; the fixture API
  pages events in array order; `timeline.test.ts:17` classes `agent.failed` as `run`. `RunsListPage` uses `useSearchParams` and its page wraps it in
  `<Suspense>` (`runs/page.tsx:12-14`); the run page (`runs/[runId]/page.tsx`) has no boundary. Styles live in `app/globals.css` (897 lines, a hot
  file). `@axe-core/playwright` is already a dev dependency.
- **Examples / E2E**: `examples/python/trace_an_agent.py` (used by `scripts/sdk-e2e.sh` against compose), `examples/coding-agent` (tests run by
  `scripts/quality.sh`), `examples/langgraph/agent.py` (CI `integrations-e2e`). E2E scripts own their database/API/worker/web ports (stream
  8120/3103 `abb_p5`, coding 8140/3140 `abb_p6`, analytics 8150/3150 `abb_p7`, auth 8165/3160 `abb_p15`); 8130/3104/`abb_p9` are free.
  `docs/TESTING.md:37-97` lists every E2E script and CI job; `docs/architecture/sdk-python.md:22-29` lists the SDK calls and env vars.
- **Environment**: `.venv`/`node_modules` are not installed in this worktree; nothing was executed for this plan or its review. Step 1 starts with
  `uv sync` / `pnpm install` and `scripts/quality.sh quick`. UNVERIFIED (env): the feasibility numbers of AC-3..AC-14, the 30,000-event benchmark
  harness (AC-9), the LangGraph threading measurement (D10), the stress-fixture budget (D6). ASSUMED: fork semantics above; `@tanstack/react-virtual`
  horizontal virtualization is not needed (bars are positioned by a scale, only rows are virtualized).

## Known issues considered

- **KI-063 (S3, target Phase 9)** pulled in as a bounded spike (D10, step 10): resolved if the measurement allows, otherwise re-filed with the reason.
- **KI-061 (S3, "Phase 9 or on demand")** stays deferred: sub-graph identity needs a LangGraph-specific mapping; the SDK API this phase adds
  (`run.agent`, `agent_id` on spans) is what the adapter would call. Target moves to "on demand".
- **KI-035 (S3)** the live merge rebuilds a seen-set per frame: unchanged; the span index adds one O(n) id walk per events change (D6), the same
  order as `mergeEvents`, measured on the stress fixture (AC-8) and recorded in the benchmark note.
- **KI-016 / KI-022 (S2, triggers)**: `derive_run` gains one linear pass (agents, root from `run.started`); the Phase 7 regression check is re-run on
  the 30,000-event run and the result recorded (AC-9). No cap changes.
- **KI-018 (S1, quotas)**: child agent slugs are client-controlled exactly like `agent_id` already is (`store.py:124-130` mints agent rows per
  distinct slug); this phase adds no new unbounded cardinality: `summary.agents` lists at most 50 agents (the rest fold into one `(other)` row,
  `agent_count` stays exact) and list items omit `agents` (D8).
- **KI-019 (S1)**, **KI-050 (S2)**, **KI-017, KI-021, KI-024, KI-026, KI-040, KI-041, KI-042, KI-055, KI-056, KI-057, KI-067, KI-072, KI-073**:
  unrelated, stay as listed. **KI-078 (S3)**: unchanged; `agent.child_id` and agent span names are labels by contract (the UI renders them as text).
- **New items filed at completion** (ids claimed now so the registers of the parallel phases do not collide; Phase 16 holds KI-080..083):
  **KI-084** (S3, on demand) per-agent run metrics: cost by agent is per event agent, run counts/success/`active_agents` are per root agent
  (decision 4); **KI-085** (S3, accepted/documented) clock skew across emitters: the chronological timeline, `t0`, the run's `started_at`,
  `duration_ms`, runs-list position and analytics day bucket follow the earliest `occurred_at`, which a skewed child can move; the waterfall is the
  causal view; **KI-086** (S3, with the OpenTelemetry adapter, ADR-007) W3C `traceparent` mapping of the trace context.

## Decisions (D1..D13; ADR-070 covers D1-D4 and D13, ADR-071 covers D5-D8)

- **D1 (ADR-070) One run, one trace, many agents.** A child agent is an agent span with its own `agent_id` (and optionally `agent_version`) in the
  parent's run; every event emitted inside it carries the child's `agent_id`. The run's own `agent_id` stays the root agent's. No schema or
  registry change: the envelope and the four `agent.*` types already express this. Rationale: §84 verbatim, one page per job, one live stream, one
  cost total; the existing ingestion creates agent rows for children on first sight. Analytics consequence (decision 4): cost by agent reflects
  child agents immediately (per-event agent on cost lines, ADR-040); run metrics stay per root agent; `api-v1.md` says so.
- **D2 (ADR-070) Delegation is recorded twice, by both sides.** The delegating side emits `agent.spawned` (point event; in the delegating span when
  there is one, else at run level with no `span_id`; parent agent's `agent_id`; `agent.child_id` = child slug; optional delegation input as payload)
  at the moment of delegation; the child emits `agent.started` (`parent_span_id` = the delegating span when there is one, else a root) and
  `agent.completed` (status; optional result as payload). Rationale: §84 asks for the parent that delegated, the child identity, the input, the
  result and the timing; a child that never starts is still visible as a spawn without a start (D8 lists it). Payloads are gated by `PayloadMode`
  and redacted like every other payload (INV-5, ADR-010): `input`/`set_result` go through `_emit`'s payload path, nothing new.
- **D3 (ADR-070) Context propagation.** In-process: contextvars as today (asyncio inherits; threads use `bind()`), and the current agent span is part
  of the context so nested spans inherit the agent id. Across processes: an explicit, serializable `TraceContext` (`run_id`, `trace_id`,
  `parent_span_id`, child `agent_id`), produced by `run.spawn_context(agent_id=...)` (which also emits `agent.spawned`) and consumed by
  `bb.continue_run(context)`. Wire form `abb1;run=run_<ulid>;trace=trc_<ulid>;span=spn_<ulid>;agent=<slug>` (`span` and `agent` optional, at most
  256 characters, every id validated by the contract patterns; invalid input yields `None` and a warning that never echoes the value, never an
  exception). Conventions: environment variable `BLACKBOX_TRACE_CONTEXT` (`blackbox.trace_context_from_env()`), HTTP header name
  `X-Blackbox-Trace-Context` (the SDK exports the constant; it does no HTTP on the user's behalf). Documented hazards (README, ADR): the variable
  is inherited by grandchildren that do not re-export it, so a grandchild that calls `trace_context_from_env()` parents to the grandparent's span
  (re-export per child, or `del env["BLACKBOX_TRACE_CONTEXT"]` for non-agent tools); `env={**os.environ, ...}` also hands `BLACKBOX_API_KEY` to every
  subprocess, including tools the agent runs (pass a filtered environment to tools); `multiprocessing` with the `spawn` start method (the macOS
  default) neither forks nor inherits the `Run`, so such children must use the env context. A continued run emits no `run.*` lifecycle events (the
  root owns the run) and parents every span to the context's span. Rationale: spec §67.2 ("adapters explicitly propagate required context"); our
  ids are 128-bit ULIDs, so W3C `traceparent` (64-bit parent id) cannot carry a span id losslessly; the adapter that maps it is ADR-007's concern
  (KI-086).
- **D4 (ADR-070) Ordering across emitters: remote emitters send no `sequence`; root identity comes from `run.started`.** A continued run, and a
  forked child that inherited a `Run` (`os.getpid() != run._pid`), omit `sequence` (`build_event` gains `sequence: int | None`); the run then falls
  into the contract's existing time ordering (`ordering_mode: time`), with `sequence` as the tiebreak inside one emitter. Rationale: §65.1
  ("monotonic within a run or local emitter stream"); the fallback already exists in the schema, the API cursors and the browser, so nothing new is
  invented; a per-emitter sequence space would need a schema field, a new ordering function and a new cursor mode for a total order that still
  cannot compare clocks. Consequence (documented, KI-085): under clock skew the chronological list interleaves emitters by their own clocks and the
  earliest `occurred_at` sets `started_at`/`t0`; the waterfall nests by parentage regardless (§65.3). The run's **root agent and `trace_id`** are
  taken from the first `run.started` in canonical order (a replayed or duplicated `run.started` cannot change them), falling back to the first
  event in canonical order for runs created by `POST /v1/runs` whose events never include `run.started`; so a skewed child never becomes the run's
  agent and a forged `trace_id` in a continued run never becomes the run's trace (`summary.py` fix, tested together).
- **D5 (ADR-071) The waterfall is derived in the browser from the events the page already holds.** `lib/spans.ts` is a port of
  `abb_event_schema.spans.derive_spans` pinned by a generated golden file (like ordering). The port reads the registry from the generated
  `schemas/1.0/event-types.json` (copied to `apps/web/src/lib/generated/event-types.json` by `make schema`, verified by `make schema-check`;
  UNVERIFIED (env): if the `@abb/event-schema` workspace package can be imported by the web, import the JSON from there instead of copying), so
  `span_role`/`span_kind` have one source of truth; the golden includes unregistered types (`agent.failed`), `custom.*`, a `span.started` per
  kind, a forest, an open span, a cycle and a 10,000-deep chain. No new `/v1` route, so no matrix, registry or OpenAPI change. Rationale: the page
  loads every event anyway (timeline, live merge); `/spans` lags the summarizer and is not streamed; content withholding is inherited (D8).
- **D6 (ADR-071) Incremental index and performance budget.** `SpanIndex` keeps `Map<eventId, spanId>` and `Map<spanId, events[]>`; it is fed the
  whole `events` array whenever it changes and walks ids once (O(n), the same order as `mergeEvents` today, KI-035), re-deriving only the spans whose
  group changed (new member, or a member whose canonical position moved); a vitest merges 20 batches of 500 and asserts equality with a
  from-scratch derivation. Rows are virtualized (`@tanstack/react-virtual`, as the timeline); the time axis is a pure function `scale(domain, zoom,
  pan, width)` applied to visible rows only; collapsed subtrees are one aggregate row (count, earliest start, latest end, error flag, label from
  `spanLabel` of the subtree root). Auto-collapse below depth 2 when a run has more than 2,000 spans; the user expands on demand. Indent depth is
  capped at 32 (deeper rows show the numeric level in the agent chip). Budget and gate: the 10,000-event stress fixture (about 3,900 spans) mounts at
  most 80 row elements (asserted); the script time of a zoom step (median of 5 key presses, `performance.now()` around press and the next
  `requestAnimationFrame`) and the from-scratch derivation time are **recorded** in `docs/benchmarks/phase-9-waterfall.md`, with a generous gate
  (100 ms per zoom step) rather than a tight one, because the CI runner is shared. Zoom and pan: wheel with modifier and keyboard (`+`/`-`,
  `[`/`]`, `0` = fit), with a visible control group for discoverability and a11y.
- **D7 (ADR-071) Tree, open spans, cycles and the critical path are defined for real traces.** (a) **Synthetic run root**: the tree's root is the
  run itself (start = the run's `started_at`, end = `completed_at` for a finished run, else the run's `last_event_at`), and the forest's roots
  (spans with no parent, orphans whose parent is absent, flagged `orphan`) are its children, sorted by start then span id. (b) **Unfinished spans**
  (no closer: live run, crashed child, close-before-open not yet closed) end, for layout and for the walk, at the run's end point and are drawn
  open-ended with a `data-unfinished` badge "no end recorded". (c) **Cycles**: `buildTree` detects a parent chain that returns to itself
  (iterative walk with a visited set), breaks it at the member with the smallest span id, which becomes a root flagged `cycle`, and never drops a
  span; depth is computed iteratively, never recursively. (d) **Critical path** = Jaeger's last-finishing-child walk from the synthetic root's end:
  repeatedly take the child that finishes last before the current point, add its segment, descend; when a span has no such child, the segment to
  its start is on the path; then continue in the parent from that start. Children whose timestamps fall outside their parent (skew) are clamped to
  the parent for the walk and flagged `skewed` ("clock skew" badge). Pure functions, tested on a chain, a fan-out, a forest with three top-level
  agents where the last finisher is not the first root, an open span, a cycle, a skewed child and a 10,000-deep chain (no stack growth).
  Rationale: the standard, explainable definition; §97 asks for highlighting, not a score.
- **D8 Lanes, the agent tree and `summary.agents`.** Tree view: depth-first by start time, indented, agent shown as a chip and colour; Lanes view:
  the same rows grouped by `agent_id` (lane order = first start), the delegating span of a child lane drawn as a dotted link. The agent tree panel
  (counts, cost, duration per agent) joins `summary.agents` with the derived spans; clicking an agent filters the timeline and focuses the lane.
  `summary.agents` is derived server-side (CostEngine cost per agent from the cost lines' `agent_id`, ADR-040): at most **50** entries ordered by
  first event, the rest folded into one `(other)` row; `agent_count` exact; an agent named only by `agent.spawned` (`agent.child_id`) with no events of
  its own is listed with `event_count 0` and `state: "spawned"`, so a child that never started is visible. **List items omit `agents`** (`list_runs`
  strips it; `agent_count` stays) so the runs list and the dashboard poll do not carry it; the detail endpoint has it. No OpenAPI change
  (`summary` is untyped); `api-v1.md` documents both. Every label, tooltip field and aggregate row is built from `describe()`/`contentText` (event
  type, agent, kind, status, duration, start offset, opener description), never from raw `attributes`; a withheld label is `WITHHELD_TEXT`
  (`withheld.ts`), the one wording the tests assert.
- **D9 Selection is one state.** `selected` in `RunDetail` is an event id, as today; a waterfall row selects its opening event (or its only event),
  the timeline scrolls to it, and the drawer opens on activation; the first-error jump switches to whichever view is active and selects the same
  event. The view switch is a URL search parameter (`useSearchParams`, which needs the run page wrapped in `<Suspense>` like the runs list page) so a
  link to a waterfall is shareable.
- **D10 KI-063 spike is bounded.** Measure in the LangGraph integration test whether `on_chain_start`/`on_chain_end` of a node run on the same
  thread as the node body (`threading.get_ident()`) for sync and async graphs. If yes for both: the adapter sets the SDK's current span to the node
  span in `on_chain_start` and restores it in `on_chain_end`/`on_chain_error` (token-based reset, same-thread guard), so SDK spans inside a node
  nest; conformance scenario `nested` gains a check; if the change needs SDK 0.3 APIs the pin becomes `agent-black-box>=0.3.0` (as Phase 8 did for
  `parent=`). If no: record the measurement, keep KI-063 open, retarget to "on demand". One commit either way.
- **D11 The example is pure SDK and deterministic.** `examples/multi-agent` uses fake models (fixed token counts), threads (`bind`), an asyncio
  task, and a real subprocess started with `BLACKBOX_TRACE_CONTEXT` in a filtered environment (the key stays only because the child is an agent;
  the README shows the tool case without it); `--skew-ms` offsets the subprocess's `occurred_at` by patching `blackbox.events.now_rfc3339` inside
  the child (example code only; the SDK gets no clock knob). It runs offline in tests (`mode="offline"`, events inspected) and against a real API in
  `scripts/multiagent-e2e.sh`.
- **D12 `SUMMARY_VERSION` 3, the next bump is 4, and the analytics wording changes with it.** The bump is what forces `agents` into re-summarised
  runs; `rollup.py` keeps counting older runs as `unrebuilt_runs`, so in the same commit `Analytics.tsx` says "N runs were summarised by an older
  version; per-agent figures, and cost breakdowns for runs that predate pricing, may be missing" (vitest asserts the text), because "predate cost
  calculation" would be false for every run the day after deployment. `summary.py`'s comment and the Merge notes say "3: agents and the root rule
  (Phase 9); the next bump is 4 (Phase 16)" so the Phase 16 implementer sees it on rebase (and KI-081 must say `>= 4`). A `POST /v1/cost/rebuild`
  re-derives the run, which under D4 may move a run whose first event was a skewed child to its true root agent (and so between agents in the run
  rollups and Phase 10's baseline); `api-v1.md` and ADR-070 say so. No migration, no OpenAPI change.
- **D13 (ADR-070) A continued run is a `Run` that never starts or ends the run.** `Run.__init__(..., remote: TraceContext | None = None)` takes a
  branch that reuses the context's ids, records `_pid`, sets `_root_parent`, and emits nothing; `end()` on a remote run is a no-op that counts
  `remote_run_end_ignored` and `ended` stays `False` for its lifetime (an adapter attached in the child keeps recording after the root run finished;
  late events are legal, §65.2). `__enter__` opens the agent span when an `agent_id` is known, `__exit__` closes it with the block's outcome.
  The child slug defaults to `agent_slug(name)` and, when that yields the default `"agent"` (no slug characters) or equals the parent's own slug,
  to `f"{parent_agent}-child-{n}"` (n per run), so a child never silently shares the root's identity.

## Proposed design

### SDK (`packages/sdk-python`)

```python
with bb.run("Ship feature") as run:                       # root agent: bb.config.agent_id
    with run.agent("researcher", input={"q": "oauth"}) as researcher:   # agent.spawned + agent.started
        with run.llm_call("openai", "gpt-4.1") as llm: ...                 # carries agent_id="researcher"
        researcher.set_result({"notes": 3})                               # payload of agent.completed (FULL mode only)
    ctx = run.spawn_context(agent_id="tester")            # agent.spawned now; a string for the child
    subprocess.run([...], env={**agent_env, "BLACKBOX_TRACE_CONTEXT": ctx})

# child process
with bb.continue_run(blackbox.trace_context_from_env()) as run:   # no run.started; agent span "tester" opens on enter
    with run.span("pytest", kind="shell"): ...
```

- `Span` gains `agent_id` and `agent_version` (inherited: explicit argument, else the parent span's, else the run's). `Run._emit`/`BlackBox._emit`
  take `agent_id`/`agent_version` from the span that emits (`Span.event`, `Span.start/end`) or from the current span (`Run.event`).
- `Run.agent(name, *, agent_id=None, agent_version=None, input=None, attributes=None, parent=None) -> AgentSpan` (`Span` subclass, kind
  `agent`): `start()` first emits `agent.spawned` with the delegating side's identity (`span_id` = parent span when there is one, `agent_id` =
  parent's agent, `agent.child_id` = child slug, payload `{"input": ...}` when given), then `agent.started` with the child's identity. `set_result
  (payload)` sets the closing payload. Slug default per D13.
- `propagation.py`: `TraceContext` (frozen dataclass; `serialize()`, `parse()` bounded and value-free on failure), `TRACE_CONTEXT_HEADER`,
  `trace_context_from_env()`. `Run.spawn_context(agent_id=None, *, parent=None, input=None) -> str` (emits `agent.spawned`),
  `Run.trace_context()` (no event, for in-process hand-offs that do not delegate), `BlackBox.continue_run(context, *, agent_id=None,
  agent_version=None) -> Run` (D13). Disabled mode returns an inert run as today. Every input is coerced or dropped (INV-4).
- `sequence`: omitted when `run.remote` or `os.getpid() != run._pid`; `build_event(sequence: int | None)` leaves the field out when `None`.
- Exports: `AgentSpan`, `TraceContext`, `trace_context_from_env`, `TRACE_CONTEXT_HEADER`; SDK version 0.3.0; README and
  `docs/architecture/sdk-python.md` section "Multi-agent and other processes" (API table rows, env var, header, the three hazards of D3, the
  `spawn` start method note). Contract tests validate every new event shape with `abb_event_schema` (ADR-013). The `os.fork` test ends the child
  with `os._exit` after `flush` (as `test_client.py:400-409` does) so the parent's `atexit` handlers run once.

### Schema and docs (`packages/event-schema`, `docs/architecture/events.md`)

No model or registry change. `tests/test_spans_golden.py` generates `tests/data/spans-golden.json` (cases: chain, fan-out, late parent, orphan,
close before open, open span, skewed child, two agents, remote emitter without sequence, unregistered closer `agent.failed`, `custom.*`, a
`span.started` per kind, a two-span cycle, a 10,000-deep chain, 300 random events) exactly like the ordering golden. `ordering.py` docstring:
replace the Phase 9 note with the D4 rule. `events.md`: "Agents and delegation" (the four events, who emits what, payload rules, the slug
fallback) and "Propagation" (context string, env var, header, hazards, ordering consequence, KI-085).

### API (`apps/api`)

- `runs/summary.py`: `SUMMARY_VERSION = 3` with the comment of D12; root agent and `trace_id` from the first `run.started` in canonical order
  (fallback first event); `summary["agents"]` = list (max 50, then `(other)`) of `{agent_id, parent_agent_id | null, state ("spawned" |
  "started" | "completed" | "failed"), event_count, llm_calls, tool_calls, error_count, estimated_cost_usd, first_event_at | null, last_event_at |
  null}` ordered by first event (parent from the `agent.started` opener's parent span's agent, else the `agent.spawned` emitter; spawned-only agents
  included); `summary["agent_count"]`. Cost per agent sums the CostEngine lines by `CostLine.agent_id` (Decimal, order-independent).
- `runs/service.py:list_runs`: items carry `summary` without `agents` (one dict copy per item); `get_run` keeps it.
- `Analytics.tsx` wording and vitest (D12) land in the same commit.
- Tests: `test_run_summary.py` (agents, spawned-only agent, bound at 50 with exact `agent_count`, root agent and `trace_id` under a 10 s child skew
  and under a forged child `trace_id`, duplicated `run.started`, a run without `run.started`, version 3), `test_summarizer.py` (a multi-emitter run:
  some events without `sequence` -> `ordering_mode == "time"`, spans nested by parent although the child's timestamps precede the parent's),
  `test_runs_api.py` (detail has `agents`, list items do not; `GET /spans` for a child span shows the child's `agent_id`; a VIEWER gets `agent_id` but
  no content span names, unchanged), `test_analytics_api.py` (a two-agent run: `cost.by_agent` lists both agents with their own cost;
  `summary.active_agents` and the run rollup list only the root agent).
- `docs/architecture/api-v1.md`: `summary.agents` (detail only), `agent_count`, version 3 and "next is 4", the time-ordering and `started_at` note
  for multi-emitter runs (KI-085), the analytics attribution split (decision 4), and that a rebuild may move a run between agents.

### Web (`apps/web`)

- `lib/generated/event-types.json` (from `make schema`; `schema-check` diffs it) and `lib/spans.ts`: `deriveSpans(events) -> Map<spanId, Span>`
  (port; golden-tested) plus `SpanIndex.update(events)` (D6).
- `lib/waterfall.ts` (pure, tested): `buildTree(spans, run)` (synthetic root, orphans, cycle breaking, iterative depth, D7), `treeRows(tree,
  collapsed, autoCollapseDepth)`, `laneRows(tree)`, `timeDomain(tree)`, `scale(domain, zoom, pan, width)`, `criticalPath(tree)`, `aggregate
  (subtree)`, `spanEnd(span, run)`, `skewed(span)`, `spanLabel(span, events)` (via `describe`/`contentText`), `tooltipFields(span, events)`,
  `colourClass(kind, status)`; the agent filter (`filters.agents`) in `lib/timeline.ts`.
- `components/Waterfall.tsx`: header axis with ticks and the zoom/pan control group; virtualized rows (`role=treegrid`, `aria-level` (capped),
  `aria-expanded`, `aria-selected`; keys: arrows, Enter, Escape, `e`, `+`/`-`, `[`/`]`, `0`, `c` toggles collapse); each row: indent, agent chip,
  kind glyph, label, bar (`position: absolute` from `scale`; open-ended when unfinished), duration, status text, badges (`skewed`, `unfinished`,
  `orphan`, `cycle`); hover tooltip from a single portal node fed by `tooltipFields` only (`aria-describedby`); critical path drawn as a thicker bar
  edge plus a legend entry (never colour alone).
- `components/AgentTree.tsx`: the agent hierarchy with state, counts, cost, duration (from `summary.agents` joined with the derived spans; a
  spawned-only agent shows "never started"); click filters.
- `RunDetail.tsx`: `view` from `useSearchParams` (`timeline` default); `<ViewSwitch>`; the timeline row shows an agent chip when the run has more
  than one agent; `jumpToError` works in both views. `app/w/[workspace]/projects/[project]/runs/[runId]/page.tsx` wraps `RunDetail` in
  `<Suspense>` (as the runs list page does) so `next build`'s prerender check passes.
- `app/waterfall.css` imported from `app/layout.tsx` (one line) so `globals.css` is untouched.
- Fixtures (step 7): `RunBuilder` gains per-event `sequence: null`, an `occurred_at` offset (`atMs`) and uses `extra.agent_id` for the derived
  span's `agent_id`; `build()` sorts events with `sortEvents` and sets `ordering_mode` from `orderingMode()`; `failureRetryRun` closes its root
  with `agent.completed`, `status: "error"` (and `timeline.test.ts:17` keeps `agent.failed` only as a classification case); `fixtures.test.ts:31`
  becomes "sequenced events are 1..n in canonical order". `multiAgentRun()` (planner -> researcher ∥ coder -> tester (subprocess, timestamps
  1.5 s behind, no `sequence`); reviewer; 5 agents; one tool failure in the researcher; cost per agent) added to `fixtures/index.ts`; dashboard and
  runs-list tests updated for the extra run.
- Tests: vitest for `spans.ts` (golden; `SpanIndex` 20-batch equality), `waterfall.ts` (tree, synthetic root, orphan, cycle, deep chain, lanes,
  scale, collapse, aggregate, critical path on chain/fan-out/forest/open/skew, withheld labels and tooltip fields for a VIEWER-shaped event list
  showing `WITHHELD_TEXT` and never the command), `Waterfall.test.tsx` (rows with `aria-level`, keyboard zoom changes the scale, selection calls
  back, aggregate row for a collapsed subtree, badges, DOM row count on the stress fixture), `RunDetail` view switch and selection sync,
  `Analytics.test.tsx` wording; Playwright `e2e/fixtures.spec.ts` (multi-agent fixture: 5 lanes, tester nested under coder with the skew badge,
  critical path legend, selection sync, axe clean in both views; stress fixture: <= 80 rows, timing recorded) and `e2e/multiagent.spec.ts` (real
  run, below).

### Example and E2E

- `examples/multi-agent/{README.md,agent.py,multi_agent/{planner.py,workers.py,fake_model.py},tests/test_example.py}`: `python agent.py
  [--skew-ms 1500] [--fail-tool]`; prints `run <id>`; tests run it in offline mode and assert the event shapes (agents, parents, missing
  `sequence` from the subprocess, payload gating, the planted secret in the delegation input redacted). `scripts/quality.sh` gains the example step
  (one line, like `examples/coding-agent`); `Makefile` gains `multiagent-e2e`; `docs/TESTING.md` gains the script and CI job lines.
- `scripts/multiagent-e2e.sh` (own database `abb_p9`, API on 8130, worker, built web on 3104, modeled on `stream-e2e.sh`): runs the example with
  `--skew-ms 1500`, waits for `summary_state == current`, asserts `agent_count == 5`, the tester span's `parent_span_id` is the coder's span,
  `ordering_mode == "time"`, the run's `agent_id` is the planner, then Playwright checks the waterfall (lanes, nesting, skew badge) and captures a
  screenshot to `docs/screenshots/phase-9-waterfall.png`. CI job `multiagent-e2e` added to `.github/workflows/ci.yml` in the last commit of step 9.

## Affected files

- `packages/sdk-python/src/blackbox/{client.py,context.py,events.py,__init__.py,version.py}`, new `propagation.py`; tests `test_agents.py`,
  `test_propagation.py`, `test_contract.py` (new shapes), `README.md`; `docs/architecture/sdk-python.md`.
- `packages/event-schema/src/abb_event_schema/ordering.py` (docstring), tests `test_spans_golden.py`, `tests/data/spans-golden.json`;
  `docs/architecture/events.md`; `Makefile` `schema`/`schema-check` targets (the web copy of `event-types.json`).
- `apps/api/src/abb_api/runs/{summary.py,service.py}`; tests `test_run_summary.py`, `test_summarizer.py`, `test_runs_api.py`,
  `test_analytics_api.py`; `docs/architecture/api-v1.md`; `docs/benchmarks/{phase-9-regression-check.md,phase-9-waterfall.md}`.
- `apps/web/src/lib/{spans.ts,waterfall.ts,timeline.ts}`, `lib/generated/event-types.json`, `components/{Waterfall.tsx,AgentTree.tsx,
  ViewSwitch.tsx,RunDetail.tsx,Timeline.tsx,Analytics.tsx}`, `app/waterfall.css`, `app/layout.tsx` (import), `app/w/[workspace]/projects/
  [project]/runs/[runId]/page.tsx` (Suspense), `fixtures/{builder.ts,runs.ts,index.ts}`, tests as listed (`fixtures.test.ts`, `timeline.test.ts`,
  `analytics.test.tsx`, dashboard/runs tests), `e2e/{fixtures.spec.ts,multiagent.spec.ts}`, `playwright.config.ts` (one project).
- `integrations/langgraph/src/blackbox_langgraph/handler.py`, its tests and `pyproject.toml` pin (only if D10 succeeds), `integrations/conformance`
  scenario check.
- `examples/multi-agent/*`, `scripts/multiagent-e2e.sh`, `scripts/quality.sh`, `Makefile`, `.github/workflows/ci.yml`, `docs/TESTING.md`.
- Docs: `docs/decisions/ADR-070-multi-agent-trace-semantics-and-propagation.md`, `ADR-071-browser-derived-waterfall.md`, `docs/DECISIONS.md`,
  `docs/KNOWN_ISSUES.md`, `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md`, `ARCHITECTURE.md` (status lines), `README.md` (example list).
- Untouched on purpose: `apps/api/openapi.json`, `apps/web/src/lib/api/schema.d.ts`, `apps/api/src/abb_api/main.py`, `authz/*`, `tests/authz/*`,
  `test_openapi.py`, `test_migrations.py`, `apps/api/migrations/*`, `apps/web/src/app/globals.css`, `AppShell.tsx`.

## Acceptance criteria (all command-checkable; evidence recorded here at completion)

- **AC-1 Agent spans.** `cd packages/sdk-python && uv run pytest -q tests/test_agents.py tests/test_contract.py`: `run.agent()` emits
  `agent.spawned` (parent identity, `agent.child_id`; no `span_id` when called at run level) then `agent.started`/`agent.completed` (child identity,
  `parent_span_id` = delegating span when there is one); events inside carry the child `agent_id` and `agent_version`; a nested `llm_call` inherits;
  `input`/`set_result` payloads appear only in `PayloadMode.FULL` and pass the redactor; hostile arguments (non-str names, control characters,
  1,000-char slugs, a name with no slug characters, a name equal to the root slug -> `<root>-child-<n>`) are coerced; every emitted event
  validates against `abb_event_schema`.
- **AC-2 Propagation.** `uv run pytest -q tests/test_propagation.py`: `TraceContext` round-trips; 20 malformed strings (overlong, wrong prefixes,
  NUL, nested separators, a 10 MB string) parse to `None` quickly without raising or logging the value; `continue_run` emits no `run.*`, never sets
  `ended`, counts `end()` as ignored, parents root spans to the context span, opens/closes the agent span on enter/exit with the block's outcome,
  omits `sequence`; a thread via `bind` and an asyncio task share the run and the current agent; a forked child's events omit `sequence` (`os.fork`
  test ending with `os._exit`, skipped where absent); a real `subprocess` started with `BLACKBOX_TRACE_CONTEXT` produces nested events in local
  mode (`mode="local"` file inspected). Coverage gate (94%) still met: `uv run pytest -q --cov`.
- **AC-3 Golden spans and the registry copy.** `cd packages/event-schema && uv run pytest -q tests/test_spans_golden.py` regenerates
  `spans-golden.json` with no diff; `make schema-check` passes with `apps/web/src/lib/generated/event-types.json` current (`git diff --exit-code
  packages/event-schema/tests/data apps/web/src/lib/generated`); `pnpm --filter @abb/web test -- spans` passes every golden case including the
  unregistered closer, the cycle and the deep chain, and the `SpanIndex` 20-batch equality case.
- **AC-4 Summary and analytics.** `cd apps/api && uv run pytest -q tests/test_run_summary.py tests/test_summarizer.py tests/test_runs_api.py
  tests/test_analytics_api.py`: `summary.agents` (detail only; absent from list items), spawned-only agent with `state: "spawned"`, `agent_count`
  exact with the 50 bound, version 3; root agent and `trace_id` from the first `run.started` under a 10 s child skew, a forged child `trace_id`, a
  duplicated `run.started`, and the no-`run.started` fallback; the multi-emitter run has `ordering_mode == "time"` and the child span's
  `parent_span_id` is the parent's although its timestamps precede the parent's; a two-agent run yields two `cost.by_agent` rows and one
  `active_agents` entry.
- **AC-5 No contract drift.** `git diff --exit-code apps/api/openapi.json apps/web/src/lib/api/schema.d.ts apps/api/migrations` is empty;
  `uv run pytest -q tests/test_openapi.py tests/authz tests/test_migrations.py` passes unchanged (36 operations; head 0047; no new route).
- **AC-6 Waterfall logic.** `pnpm --filter @abb/web test -- waterfall`: synthetic root, orphan roots, cycle broken at the smallest id with no span
  dropped, iterative depth on a 10,000-deep chain, lanes, scale, collapse, aggregate; critical path on the chain (whole chain), the fan-out (the
  slowest branch only), the three-root forest (the last finisher, not `roots[0]`), the open span (ends at the run end, flagged) and the skewed child
  (clamped, flagged); labels, aggregate rows and `tooltipFields` of a VIEWER-shaped event list contain `WITHHELD_TEXT` and never the command text.
- **AC-7 Waterfall UI.** `pnpm --filter @abb/web test -- Waterfall RunDetail Analytics`: rows render with `aria-level`; `+` changes the scale;
  clicking a row selects its opener event and the timeline receives the same selection; `e` selects the first error in both views;
  `?view=waterfall` is honoured; the analytics "older version" wording renders for `unrebuilt_runs > 0`.
- **AC-8 Performance.** Playwright `fixtures.spec.ts` on the stress fixture: at most 80 `[role=row]` elements mounted in the waterfall (asserted);
  the median script time of 5 zoom key presses is under 100 ms (asserted, generous) and the value, plus the vitest from-scratch derivation time for
  10,000 events and the `SpanIndex` incremental cost, are recorded in `docs/benchmarks/phase-9-waterfall.md`.
- **AC-9 Server cost.** `docs/benchmarks/phase-9-regression-check.md`: `derive_run` on the 30,000-event benchmark run before/after (the Phase 7
  harness), difference recorded; no new KI-016 regression beyond noise. UNVERIFIED (env) until the harness runs.
- **AC-10 Fixture and a11y.** `pnpm --filter @abb/web test:e2e -- fixtures.spec.ts`: multi-agent fixture shows 5 lanes, the tester row nested under
  the coder row with the skew badge, a critical path legend, axe reports no violations in both views, keyboard-only navigation reaches the drawer;
  the failure fixture's root span is finished with status error.
- **AC-11 Example offline.** `uv run --project packages/sdk-python pytest examples/multi-agent/tests -q`: 5 agents, the subprocess's events have no
  `sequence` and parent to the coder's span, `--fail-tool` yields one `tool.call.failed` and a successful run, payloads absent in the default mode,
  the planted secret absent in FULL mode.
- **AC-12 Example end to end.** `make multiagent-e2e` passes (assertions in the script; screenshot written); CI job `multiagent-e2e` green on the PR.
- **AC-13 KI-063 spike.** `cd integrations/langgraph && uv run pytest -q`: either the new nesting test passes (adapter change landed, conformance
  `nested` check extended, pin bumped if needed) or the measurement is recorded in this plan and `KNOWN_ISSUES.md` with the thread ids observed.
- **AC-14 Whole gate.** `scripts/quality.sh full` exit 0; `make stream-e2e coding-e2e auth-e2e analytics-e2e` still pass (the run page and the
  analytics wording changed; `analytics.spec.ts` asserts the new wording when `unrebuilt_runs > 0`).
- **AC-15 Docs.** ADR-070/071 exist and are indexed; `events.md`, `api-v1.md`, `sdk-python.md`, SDK README, `TESTING.md` updated; `KNOWN_ISSUES.md`
  rows KI-084..086 with GitHub issues (KI-063 moved or retargeted); `PROJECT_STATE.md` updated.

## Verification plan

Per step: the focused tests named in the step, then `scripts/quality.sh quick`. After steps 4, 6, 9 and 11: `scripts/quality.sh full`. UI steps are
exercised in a browser (Playwright on fixtures, then the real run of AC-12) with screenshots in `docs/screenshots/`. Evidence vocabulary as in
AGENTS.md; anything not run here (for example `make up`) is UNVERIFIED (env) with the reason. Mutation checks for the reviewer (each must fail a
test): drop `agent_id` inheritance in `Span`; emit `sequence` from a continued run; emit `agent.spawned` without `agent.child_id` (server
`attribute_required`); derive `parent_agent_id` from the spawned event's `agent_id` only; derive the run's agent or `trace_id` from `ordered[0]`
again; lift the 50-agent bound; parent by time instead of `parent_span_id` in `spans.ts`; treat an unregistered closer as CLOSE in the port; pick
`roots[0]` as the critical-path start; throw on a null end; drop a cycle member from the tree; skip re-deriving a span whose group changed in
`SpanIndex`; let the critical path choose the earliest-finishing child; render span names or tooltip fields from raw `attributes`.

## Risks

1. **Clock skew makes the chronological timeline and the run's `started_at` misleading** across processes (D4, KI-085). Mitigation: the waterfall
   is the causal view, the skew badge names it, `events.md`/`api-v1.md` document it; a per-emitter ordering is an ADR away.
2. **A continued run can write into any run of the same project** whoever holds the context string (it is not a credential: the key is), and the
   env variable is inherited by grandchildren and tools. Mitigation: ids are validated, the server rejects other projects' runs
   (`RUN_PROJECT_MISMATCH`), root identity ignores a forged `trace_id` (D4), the README shows the filtered-environment pattern; documented as
   "treat like a request id".
3. **Browser cost on huge runs**: one O(n) id walk per events change plus the timeline's existing per-frame merge (KI-035). Mitigation: `SpanIndex`
   re-derives changed groups only, measured on the stress fixture (AC-8); auto-collapse below depth 2 above 2,000 spans.
4. **`SUMMARY_VERSION` bump** leaves old runs at version 2 (no `agents`) and counts them as `unrebuilt_runs`. Mitigation: the UI degrades to "no
   agent breakdown", the analytics wording changes in the same commit (D12), `cost/rebuild` re-derives; no migration. Phase 16 must use 4.
5. **LangGraph threading assumption** (D10). Mitigation: measured before any adapter change; the spike cannot block the phase.
6. **Hot-file conflicts with Phases 10/11/16** ("Merge notes"). Mitigation: each touched only in the last commit of its step with additive edits.
7. **Fixture builder changes** touch every fixture and its tests. Mitigation: one commit (step 7) with the relaxed assertions and the `agent.failed`
   correction; the fixture E2E runs in the same step.
8. **Example subprocess on CI** (Python path, env). Mitigation: the example runs `sys.executable`; tests pass `PATH` as `examples/coding-agent`
   does.
9. **Hostile structure** (cycles, 10,000-deep chains, thousands of roots) reaching the browser. Mitigation: iterative walks, cycle breaking, depth cap,
   golden cases (D7).

## Security review note (what an adversarial reviewer should attack)

- **Content leakage through the new views.** Every label, lane title, aggregate row, agent-tree entry and tooltip field must come from
  `describe()`/`contentText`/`tooltipFields`, never raw `attributes`: a VIEWER-shaped event list (attributes replaced, `withheld_attributes` set)
  must never show a command, path or URL anywhere in the waterfall. Attack with `span.name` values that look like commands: they are labels by
  contract (KI-078), shown as text, never interpreted.
- **Hostile agent ids and context strings.** `agent_id` is server-validated (slug, 64 chars); `agent.child_id` is any string up to 4,096 characters
  (the registry only types it); the UI renders it as text, truncates to 64 characters for chips and never uses it as a key format or HTML.
  `TraceContext.parse` is fed 20 malformed forms and must return `None` quickly without echoing the value.
- **Hostile structure.** A two-span cycle, a self-parented chain through 10,000 spans, 5,000 roots, a close before its open, a span id reused across
  runs (the summarizer already skips those): the tree must render every span exactly once, never recurse, never throw.
- **Writing into another run.** A child with a stolen context but a key of another project is rejected per event (`RUN_PROJECT_MISMATCH`); a
  child with the same project's key can only do what that key could already do; a forged `trace_id` cannot become the run's trace (D4). Verify no
  new path lets a key name a workspace or project.
- **Unbounded growth.** `summary.agents` is capped at 50 and absent from lists; `SpanIndex` holds one entry per event and span already in memory;
  agent rows per distinct slug are KI-018 as before; the example's subprocess environment contains the context and the key only because the child
  is an agent (tools get a filtered environment).
- **Payload gating.** `input` and `set_result` payloads obey `PayloadMode` exactly like `set_payload` (never in METADATA_ONLY) and pass the
  redactor; a secret planted in the example's delegation input is redacted in storage and absent from the UI (reuse the coding-agent pattern).
- **No new credential surface.** No new route, header the API trusts, or setting. `X-Blackbox-Trace-Context` is an SDK-side name only; the API never
  reads it.
- **Logs.** The SDK logs a warning on an invalid context without echoing it; the API logs nothing new.

## Merge notes (hot files and how to rebase; merge order 9, 10, 16, 11)

- **Shared hot files this phase touches**, each only in the last commit of its step and append-only where possible: `apps/api/src/abb_api/runs/
  summary.py` (also Phase 16; step 4), `docs/architecture/api-v1.md` (all phases; step 4), `apps/web/src/components/RunDetail.tsx` (also 10, 11;
  step 6, edits confined to the view-switch block and the agent filter), `apps/web/src/fixtures/{builder,runs,index}.ts`, `apps/web/tests/
  fixtures.test.ts`, `apps/web/e2e/fixtures.spec.ts` (also 10, 16; step 7), `apps/web/playwright.config.ts` (also 11; step 9), `scripts/quality.sh`
  (9 only; step 8), `Makefile` and `.github/workflows/ci.yml` (also 10, 11; step 9, one target and one job appended), `docs/TESTING.md`, `README.md`,
  `docs/KNOWN_ISSUES.md` (KI-084..086), `docs/DECISIONS.md` (two rows), `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md`, `ARCHITECTURE.md`
  (all phases; step 11).
- **Not touched**: `apps/api/openapi.json`, `apps/web/src/lib/api/schema.d.ts`, `main.py`, the authz matrix/registry and their tests,
  `test_openapi.py`, `test_migrations.py`, `globals.css`, `AppShell.tsx`, migrations.
- **`SUMMARY_VERSION`**: Phase 9 sets 3 (step 4). **The next bump is 4**: Phase 16 must change its constant, its comment, its AC-8/AC-11 lines and
  KI-081 (`>= 4`) on rebase; Phase 10 leaves it at 2 and is unaffected. `summary.py`'s comment carries the same sentence.
- **Route counts / OpenAPI**: Phase 9 adds no operation, so `test_openapi.py` stays at 36. Merge sequence: after Phase 10, 37; after Phase 16, 37
  (its recommended decision 1; 38 with its alternative); Phase 11's "36 -> 50" rebases to 51 (or 52). Nothing for Phase 9 to change.
- **Migrations**: Phase 9 has none; the alembic head stays 0047 after it merges, so Phase 10's `0060 (down_revision "0047")` merges unchanged,
  Phase 16 then sets `0080.down_revision = "0060"` and Phase 11 sets `0070.down_revision = "0080"`, each appending its ids after the merged head
  in `test_migrations.py:REVISIONS` and re-running `scripts/quality.sh full` (upgrade head / downgrade base / upgrade head), as Phase 6 did when it
  renumbered behind Phases 7 and 8. Phase 9's reserved ids 0050-0059 stay free.
- **Why 9 merges before 10**: Phase 10's cost-spike detector keys its baseline on `runs.agent_slug` (its plan D5); D4's root-agent rule is what keeps
  that key stable under skew, and D12 notes that a rebuild may move a run between agents.
- **ADR and KI ids** do not collide: ADR 070/071 (9), 080/081 (10), 090-092 (11), 100/101 (16); KI-080..083 (16), KI-084..086 (9).

## Ordered steps (one commit each; main stays green after every step)

1. `test(event-schema): golden spans file, the web copy of the event-type registry and the multi-emitter ordering note` - `test_spans_golden.py`
   generates `spans-golden.json` (all D5/D7 cases); `make schema` copies `event-types.json` to `apps/web/src/lib/generated/` and `schema-check` diffs
   it; `ordering.py` docstring updated to D4. No behaviour change. Verify: event-schema suite; `make schema-check`; `git diff --exit-code`.
2. `feat(sdk): agent spans with their own agent id and the agent.spawned event` - `Span.agent_id/agent_version` inheritance, `AgentSpan`,
   `Run.agent`, the slug fallback (D13), `_emit` identity plumbing, contract tests, `test_agents.py`. Verify: AC-1, coverage gate.
3. `feat(sdk): trace context propagation across threads, tasks and processes` - `propagation.py`, `Run.spawn_context/trace_context`,
   `BlackBox.continue_run` with the remote `Run` branch (D13), remote/forked runs omit `sequence` (`build_event(sequence: int | None)`), README and
   `sdk-python.md` (hazards, `spawn` note), version 0.3.0. Verify: AC-2.
4. `feat(api): per-agent run summary, root identity from run.started, summary version 3 (next is 4)` - `summary.py` (agents, spawned-only agents,
   50 bound, root agent and `trace_id` rule, comment), `service.py:list_runs` strips `agents`, the multi-emitter summarizer test, the runs API and
   analytics tests, `Analytics.tsx` wording plus vitest, `api-v1.md`. Verify: AC-4, AC-5, `scripts/quality.sh full`.
5. `feat(web): derive spans and the waterfall tree, lanes and critical path in the browser` - `lib/spans.ts` (golden-tested, `SpanIndex` with the
   20-batch equality test), `lib/waterfall.ts` (D7: synthetic root, orphans, cycles, iterative depth, open spans, critical path, aggregate,
   tooltip fields), the agent filter in `lib/timeline.ts`; vitest. Verify: AC-3 (web part), AC-6.
6. `feat(web): waterfall view with lanes, zoom, critical path and synchronized selection` - `Waterfall.tsx`, `AgentTree.tsx`, `ViewSwitch`,
   `RunDetail` wiring, the run page `<Suspense>`, `waterfall.css` + layout import, timeline agent chip; component tests. Verify: AC-7,
   `scripts/quality.sh full`, browser check on existing fixtures (screenshot).
7. `feat(web): fixture builder for time-ordered multi-agent runs, the multi-agent fixture, waterfall E2E and the performance record` -
   `RunBuilder` extensions, `failureRetryRun` closer fix, relaxed `fixtures.test.ts`, `multiAgentRun`, dashboard/runs tests, `fixtures.spec.ts`
   waterfall cases, stress DOM bound and zoom timing (`docs/benchmarks/phase-9-waterfall.md`), axe. Verify: AC-8, AC-10.
8. `feat(examples): multi-agent example (threads, asyncio, subprocess, skew knob)` - `examples/multi-agent`, offline tests, `scripts/quality.sh`
   step (last file in the commit). Verify: AC-11, `scripts/quality.sh full`.
9. `test(e2e): multi-agent example against the real API and web` - `scripts/multiagent-e2e.sh`, `e2e/multiagent.spec.ts`, Playwright project,
   screenshot, `docs/TESTING.md` lines, `Makefile` target, CI job (ci.yml last). Verify: AC-12 locally; CI on the PR.
10. `feat(langgraph): place node spans in the SDK context when callbacks run on the node's thread` **or** `docs(known-issues): KI-063 measurement`
    - the D10 spike, whichever outcome (pin bump if needed). Verify: AC-13.
11. `docs(phase-9): ADR-070, ADR-071, events and SDK docs, known issues, state` - ADR files, `DECISIONS.md`, `KNOWN_ISSUES.md` (KI-084..086 with
    issues; KI-063 disposition), `PROJECT_STATE.md`, `IMPLEMENTATION_PLAN.md`, `ARCHITECTURE.md`, `README.md`, the regression benchmark note,
    evidence for AC-1..15 appended to this plan; then `complete-phase` moves the plan. Verify: AC-9, AC-14, AC-15.

## Review dispositions (independent review at f0698e6)

Counts: 30 findings and cross-plan items; **accepted 30, rejected 0, deferred 0** (F8 accepted with a different bound; F7 accepted as a generous
gate plus a recorded number). No P1 rebutted: each was re-checked against the code and confirmed (file:line in "Current architecture").

| Finding | Sev | Disposition | Where it landed |
| --- | --- | --- | --- |
| F1 tree/critical path undefined for forests, open spans, cycles, deep chains | P1 | accepted | D7 (synthetic root, open-span end, cycle rule, iterative depth, depth cap), D5 golden cases, AC-6, risk 9, security note "hostile structure", mutation checks, ADR-071 |
| F2 decision 4's attribution claim false (cost by agent is per event agent) | P1 | accepted | decision 4 reworded, D1 consequence, API test in `test_analytics_api.py`, AC-4, `api-v1.md`, KI-084, ADR-070 consequences |
| F3 `SUMMARY_VERSION` 3 collides with Phase 16; analytics wording becomes false | P1 | accepted | D12 (wording change in the same commit + vitest, "next bump is 4"), header line, Merge notes, AC-7, AC-14, risk 4, step 4 |
| F4 multi-agent fixture not expressible; `agent.failed` leaves a root span open | P1 | accepted | architecture section (builder facts), web fixtures design, step 7, AC-10, risk 7 |
| F5 `SpanIndex.merge(batch)` has no batch | P1 | accepted | D6 (`SpanIndex.update(events)`, O(n) id walk, 20-batch equality test), KI-035 line, AC-3, AC-8, mutation check |
| F6 skew moves `trace_id`, `started_at`, `t0`, day bucket | P2 | accepted | D4 (`trace_id` from `run.started`, forged-trace test), KI-085, AC-4, `api-v1.md`, risk 1, mutation check |
| F7 16 ms CI gate is flaky | P2 | accepted | D6/AC-8: DOM bound asserted, 100 ms median-of-5 gate, numbers recorded in `docs/benchmarks/phase-9-waterfall.md` |
| F8 `summary.agents` at 200 inflates list pages | P2 | accepted (bound 50 and list items omit `agents`) | D8, KI-018 line, AC-4, `service.py:list_runs` in step 4, `api-v1.md` |
| F9 TS port needs the registry | P2 | accepted | D5 (generated `event-types.json` copied under `make schema`), step 1, AC-3, Affected files |
| F10 missing hot files, KI id range | P2 | accepted | Merge notes (full list), KI-084..086 claimed in the header and "Known issues considered" |
| F11 spawned-but-never-started agent invisible | P2 | accepted | D8 (`state: "spawned"`, `event_count 0`), API section, AgentTree "never started", AC-4 |
| F12 default child slug collides with the root | P2 | accepted | D13 slug fallback, AC-1 hostile case |
| F13 tooltip and aggregate rows not bound to `contentText` | P2 | accepted | D8 (`tooltipFields`), AC-6, security note, mutation check |
| F14 step 5 split; rebuild may move a run between agents | P2 | accepted | step 5 merged into step 4; D12 and `api-v1.md`/ADR-070 rebuild note; Merge notes "why 9 before 10" |
| F15 `continue_run` must bypass `Run.__init__`/`__exit__` | P2 | accepted | D13, SDK design, AC-2 (`ended` never set), ADR-070 |
| F16 `useSearchParams` needs Suspense | P2 | accepted | D9, web design, Affected files (`runs/[runId]/page.tsx`), step 6 |
| F17 env inheritance hazards | P3 | accepted | D3 hazards, D11 filtered environment, README/`sdk-python.md`, risk 2, ADR-070 |
| F18 root-agent tie-break and no-`run.started` fallback | P3 | accepted | D4, AC-4 |
| F19 `multiprocessing` spawn start method | P3 | accepted | D3, SDK docs |
| F20 withheld wording (`[withheld]` vs `WITHHELD_TEXT`) | P3 | accepted | D8, AC-6 assert `WITHHELD_TEXT` |
| F21 `sdk-python.md`, `TESTING.md` | P3 | accepted | Affected files, steps 3 and 9, AC-15 |
| F22 extra mutation checks | P3 | accepted | Verification plan |
| F23 fork test must `os._exit` | P3 | accepted | SDK design, AC-2 |
| F24 top-level `agent.spawned` has no span | P3 | accepted | D2 wording, AC-1 |
| F25 LangGraph SDK pin | P3 | accepted | D10, AC-13 |
| Cross 1 `SUMMARY_VERSION` sequence | - | applied | header, D12, Merge notes |
| Cross 2 route counts | - | applied | Merge notes |
| Cross 3 migrations / head 0047 | - | applied | header, Merge notes |
| Cross 4 shared hot files, KI ids | - | applied | Merge notes, header |
| Cross 5 merge 9 before 10 | - | applied | header, Merge notes |
| "Unverified or wrong" statements (`build_event`, fork claim, line drift) | - | applied | architecture section corrected; fork claim marked ASSUMED |

## Appendix A - ADR-070 draft: Multi-agent trace semantics and context propagation

Status: Proposed (Phase 9). Context: spec §84 asks for delegating parent, child identity, input, result and timing overlap; §67.2 for explicit
propagation across threads, processes and sub-agents; §65.1 allows `sequence` to be per emitter; Phase 1 left multi-emitter ordering open (R1).
Decision: (1) one run and one trace per job; a child agent is an `agent.*` span with its own `agent_id` (and optional `agent_version`) whose
nested events carry that identity; the run's agent and `trace_id` are those of the first `run.started` in canonical order (fallback: the first
event), so a skewed or forged child event cannot change them. (2) Delegation is recorded by both sides: `agent.spawned` by the delegator
(`agent.child_id`, optional input payload; in the delegating span when there is one), `agent.started`/`agent.completed` by the child
(`parent_span_id` = the delegating span, optional result payload); payloads obey `PayloadMode` and the redactor. A child slug that would equal the
default or the parent's slug becomes `<parent>-child-<n>`. (3) Propagation: contextvars in-process (threads via `bind`), and an explicit serialized
`TraceContext` (`abb1;run=...;trace=...;span=...;agent=...`, env `BLACKBOX_TRACE_CONTEXT`, header name `X-Blackbox-Trace-Context`) produced by
`run.spawn_context()` and consumed by `bb.continue_run()`; a continued run is a `Run` that never emits `run.*`, is never "ended" (`end()` is a
counted no-op) and parents its spans to the context span. (4) Remote emitters (continued runs, forked children) omit `sequence`; such a run is
time-ordered by the existing contract fallback; the waterfall nests by parentage, so clock skew cannot un-nest a child. No schema change.
Alternatives: a run per agent linked by trace (splits cost, stream and page; contradicts §84); a per-emitter sequence namespace (new field, new
cursor mode, still no cross-clock total order); W3C `traceparent` as the wire form (64-bit parent id cannot carry a 128-bit span ULID; mapping
belongs to the OpenTelemetry adapter, ADR-007, KI-086). Consequences: a child process needs a key of the same project; chronological order and the
run's `started_at` across emitters follow their clocks (KI-085); the context variable is inherited by grandchildren and tools unless re-exported or
removed, and `multiprocessing`'s `spawn` start method needs the env context; analytics already attribute cost by the event's agent (ADR-040) while
run metrics stay per root agent (KI-084); a rebuild may move a run whose first event was a skewed child to its true root agent; adapters can set
`agent_id` on spans when a framework exposes sub-agent identity (KI-061). Revisit: when a trace must span runs, or when an OpenTelemetry adapter
lands.

## Appendix B - ADR-071 draft: The waterfall is derived in the browser; critical path by last-finishing child

Status: Proposed (Phase 9). Context: §97 requires hierarchical rows, lanes, zoom/pan, colour by kind/status, critical path, hover, synchronized
selection and "render only visible rows, aggregate collapsed nodes"; the run page already loads every event and merges live frames; `/spans` is a
derived, lagging, non-streamed view; real traces are forests (no root span), may contain unfinished spans and, since the API never rejects cycles,
hostile structure. Decision: the waterfall derives spans in the browser with a port of `abb_event_schema.spans.derive_spans` that reads the
generated `event-types.json` registry and is pinned by a generated golden file; a `SpanIndex` re-derives only the spans whose event group changed
when the page's events array changes (one O(n) id walk, the same order as the live merge). The tree hangs the forest under a synthetic run root
(start = run start, end = `completed_at` or `last_event_at`); unfinished spans end at the run's end and are drawn open-ended and flagged; a parent
cycle is broken at its smallest span id, which becomes a flagged root; depth is computed iteratively and indentation capped at 32. Rows are
virtualized and collapsed subtrees are one aggregate row, auto-collapsed below depth 2 above 2,000 spans; the time axis is a pure scale over zoom
and pan; the critical path is Jaeger's last-finishing-child walk from the synthetic root's end, with skewed children clamped to their parent and
flagged; lanes group the same rows by `agent_id`; per-agent cost comes from the server-derived `summary.agents` (CostEngine; at most 50 entries,
detail endpoint only, spawned-only agents included); selection is one event id shared with the timeline and the drawer; every label, tooltip field
and aggregate row is built from the already-withheld events through the timeline's describe/withheld helpers, so ADR-061's projection is inherited.
No new route, so the authorization matrix and OpenAPI are unchanged. Alternatives: a `GET /v1/runs/{id}/tree` endpoint (a matrix case, a registry
case, cross-workspace coverage, and still no live updates); SVG/canvas drawing of every bar (tens of thousands of nodes, against §97); a
critical-path heuristic by longest duration (not explainable); rejecting cycles at ingestion (a behaviour change to the contract; the browser must
survive stored data anyway). Consequences: span derivation cost moves to the client (recorded in `docs/benchmarks/phase-9-waterfall.md`); a
mismatch between the Python and TypeScript derivations is caught by the golden file; very large runs show an aggregated tree until expanded; a
hand-edited registry copy fails `make schema-check`. Revisit: if a trace exceeds what a browser can hold (then a server-side windowed tree
endpoint, with its matrix case).

## Implementation notes (steps 1-4, recorded by the implementer)

Deviations and choices where the plan was ambiguous (simplest secure option):

- **Golden spans (step 1).** `spans-golden.json` stores the 10,000-deep chain as a generator description (`generated[0]`: length, event
  pair, step) with the expected count, first and last span, not as 10,000 events (the file stays 190 KB and reviewable). The web replay
  builds the same events from it. `apps/web/.prettierignore` gains `src/lib/generated` (the registry copy is written verbatim by
  `make schema`; `make schema-check` verifies it with `cmp`).
- **Payload shapes (step 2).** `run.agent(..., input=x)` sends the payload `{"input": x}` on `agent.spawned`; `set_result(x)` sends
  `{"result": x}` on `agent.completed` (so non-dict values work and the shapes cannot collide with user keys).
- **Slug fallback.** An explicit but unusable `agent_id` falls back to the name; a name with no slug characters (including `""`), or a
  slug equal to the delegator's agent id (explicit or derived), becomes `<delegator>-child-<n>` (n per run, truncated to 64 characters).
- **Pre-existing SDK defect fixed on the way.** `check_payload` validated the payload but `BlackBox._emit` discarded its result, and
  NUL characters in payload strings or keys made the whole event invalid at the server (`string_contains_nul`). `check_payload` now
  strips NUL and `_emit` uses the checked payload; regression test in `test_contract.py`.
- **Propagation (step 3).** An invalid or missing context passed to `continue_run` returns an inert run (no events) plus a warning, never
  a new standalone run. `spawn_context` on a continued run uses the context's span as the delegating span when no span is open.
  `remote_run_end_ignored` is a new `Stats` counter. SDK 0.3.0: the five integration `uv.lock` files were refreshed (`uv lock`) because
  they record the editable SDK's version.
- **Coverage gate.** `pyproject.toml` has `fail_under = 90` (the plan said 94); measured 94.18% after this step.
- **`summary.agents` (step 4).** At most 50 entries *including* the `(other)` row (49 agents + `(other)` when there are more than 50),
  `state` of the `(other)` row is `"mixed"`. Order is first appearance in canonical order, where an `agent.spawned` naming a child counts
  as the child's first appearance (so a skewed child may sort before its parent in a time-ordered run). Agent `state`: from the agent's
  last `agent.*` span (open: `started`; closed with error/timeout: `failed`; else `completed`); an agent with events but no agent span is
  `started`, except the root agent, which follows the run's terminal status. `agent.child_id` is truncated to 64 characters when used as a
  label for a spawned-only agent.
- **Tests not added.** The VIEWER clause of the plan's `test_runs_api.py` line is the existing, unchanged behaviour covered by
  `tests/authz/test_content_withheld.py` (the plan keeps `tests/authz` untouched); `summary.agents` holds only agent ids and counts.
- **Not done in this run (steps 5-11):** web derivation and waterfall, fixtures, example, E2E, KI-063 spike, ADR files, the AC-9 regression
  benchmark (`derive_run` on 30,000 events gained one extra linear pass; not measured), `KNOWN_ISSUES.md`, `PROJECT_STATE.md`.
