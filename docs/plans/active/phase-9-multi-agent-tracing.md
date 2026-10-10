# Phase 9 - Multi-agent distributed tracing and waterfall

Status: Planned 2026-10-10 (awaiting the user's answers to the decisions below; "go with your recommendation" suffices)
Owner: implementer agent
Branch: `feature/phase-9-multi-agent-tracing` (from `main` c212058; worktree `../abb-worktrees/phase-9`)
Depends on: Phase 1 (span derivation, ordering), Phase 3 (SDK context), Phase 4/5 (run page, virtualized timeline, live merge), Phase 8 (adapter contract), Phase 15 (`payload.read` projection)
Spec refs: §84 (multi-agent trace semantics), §97 (waterfall UX), §63.4-63.5 (run vs trace, span), §64.1 (`agent_id` per event), §65.1-65.3 (ordering, late events, causal display), §67.2 (context propagation), §68 (public API), §96.1 (grouping by agent), §135 (fixtures), §139 M4
ADRs: ADR-011 (dot names), ADR-013 (stdlib SDK), ADR-022 (streams), ADR-052 (adapter parenting), ADR-061 (content withheld from VIEWER). New: ADR-070, ADR-071 (drafted in the appendix; files are written in step 12).
Migration ids: **none**. Reserved range for this phase: 0050-0059, unused (see "Merge notes").
Known issues: resolves KI-063 only if its spike succeeds (D10); files new items (see "Known issues considered").

## Decisions needed from the user

1. **Trace model.** One run per job: a child agent is a span (`agent.started` ... `agent.completed`) with its own `agent_id` inside the
   parent's run and trace (spec §84, §63.4), never a run of its own. Recommended: yes. (Alternative: one run per agent linked by `trace_id`; needs a
   trace-level query, splits cost and the live stream across runs, contradicts §84.)
2. **The multi-agent example.** A new pure-SDK `examples/multi-agent` (planner spawns researcher and coder in threads, the coder runs a tester in a
   subprocess, a reviewer runs as an asyncio task; fake models, no network, deterministic, with a `--skew-ms` knob for the subprocess clock).
   Recommended: yes; a LangGraph multi-agent example stays deferred with KI-061.
3. **Where the waterfall lives.** A `Timeline | Waterfall` view switch on the run page (spec §95: "timeline/waterfall" is one region), sharing the
   selection, the first-error jump and the event drawer; the choice is kept in the URL (`?view=waterfall`). Recommended: yes.
4. **Analytics attribution.** Child agents' model calls stay attributed to the run's root agent in the agent rollups (ADR-043 keys by run); the
   run page shows cost per agent instead. Recommended: accept and file a KI (per-agent rollups when a user asks).
5. **KI-063 (adapter spans in the SDK context).** A bounded spike (step 11): if LangGraph fires a node's callbacks on the thread that runs the
   node body, the adapter binds the SDK context for that node; otherwise KI-063 stays open with the measured reason. Recommended: yes.

## Outcome

A developer traces a system of cooperating agents, in threads, asyncio tasks and other processes, with a few lines, and the run page shows
what happened both chronologically (the existing timeline, now with agent labels) and hierarchically (a waterfall with span rows nested by
parentage, one lane per agent, a zoomable and pannable time axis, the critical path highlighted, selection synchronized with the timeline and
the drawer). A child whose clock is skewed still nests under its parent. Runs with tens of thousands of events stay responsive because only
visible rows are in the DOM and collapsed subtrees are drawn as one aggregate bar. Everything a VIEWER could not see before stays hidden.

## Non-goals

Cross-run traces (`trace_id` spanning runs), OpenTelemetry `traceparent` interoperability (ADR-007: a later adapter maps it at the boundary),
a TypeScript SDK (§69, Phase 17), processors or detectors over agent trees (Phase 10), per-agent analytics rollups (decision 4), LangGraph
sub-graph identity and state diffs (KI-061), agent-to-agent message capture beyond the delegation input and result payloads, graph layouts
(DAG rendering), a server-side spans-tree endpoint (D5: the waterfall is derived in the browser from the events already loaded), new
migrations, new `/v1` routes, new runtime dependencies (the SDK stays stdlib-only; the web reuses `@tanstack/react-virtual`). Nothing here
needs Kafka, ClickHouse, Redis or Kubernetes.

## Current architecture (verified by reading the code; ASSUMED where marked)

- **Schema** (`packages/event-schema`): the envelope carries `agent_id` per event (`event.py:232`, slug `[a-z0-9][a-z0-9._-]{0,63}`), `span_id`,
  `parent_span_id` (a parent requires a span, cannot be itself). The registry already has `agent.started` (OPEN, kind AGENT), `agent.completed`
  (CLOSE), `agent.spawned` (POINT, requires `agent.child_id`), `agent.state_changed`; `agent.child_id` is a known STRING attribute and is in the
  reviewed-metadata set of `tests/authz/test_content_withheld.py`. There is no `agent.failed` (a failed agent is `agent.completed` with
  `status=error`; the web fixture's `agent.failed` is an unregistered type that is accepted and classed CUSTOM). `spans.derive_spans` nests by
  `parent_span_id` only, never by time; a late parent, an orphan or a close-before-open is normal; only two parents, a cycle or one span id in two
  traces are issues. `ordering.sort_events`: by `(sequence, occurred_at, received_at, event_id)` when every event has a sequence, else by
  `(occurred_at, sequence, received_at, event_id)`; its docstring says multi-process emitters are Phase 9's (Phase 1 plan risk R1). The golden file
  `tests/data/ordering-golden.json` pins the TypeScript port (`apps/web/src/lib/ordering.ts`, `apps/web/tests/ordering.test.ts`).
- **SDK** (`packages/sdk-python/src/blackbox`): `Run` creates `run_id`/`trace_id` and one `itertools.count` sequence; `Run._emit` always stamps
  `run.agent_id` on every event (`client.py:739`). `Span.start()` parents to the current span of the same run when no explicit `parent` is given;
  kind `agent` maps to `agent.started`/`agent.completed`/`agent.completed` (`_TYPED_SPANS`). Context is two contextvars (`context.py`); asyncio tasks
  inherit, threads need `bind()`; `os.register_at_fork` recreates the exporter in a forked child but the child keeps the parent's `Run` objects and
  sequence counter (`test_client.py:390`). `build_event` omits `sequence` only if asked (it is always passed today). There is no way to continue an
  existing run from another process, no agent id per span, and no serialized context.
- **Adapters** (`integrations/langgraph/handler.py`, ADR-052): explicit parenting by framework run id, never the SDK context (KI-063); `run_inline =
  True`. Client wrappers parent to the current SDK span.
- **API**: `runs/summary.py:derive_run` is the pure derivation (SUMMARY_VERSION 2; `test_run_summary.py:199` pins it); it sets `agent_slug =
  ordered[0].agent_id` (`summary.py:180`), which under time ordering is whichever event sorts first. `ingestion/store.py:117-150` creates an agent
  row (and version) for every distinct `(project, agent_id)` seen and rejects an event whose run belongs to another project
  (`RUN_PROJECT_MISMATCH`), so a child process must use a key of the same project. `spans` table: `parent_span_id`, `agent_slug`, index
  `ix_spans_run_parent`; the summarizer writes spans from the derivation (`runs/repository.py:apply_derivation`). `GET /v1/runs/{id}/spans` pages
  derived spans (lags by the summarizer debounce; not live). `GET /v1/runs/{id}/events` serves canonical order with `ordering_mode`; a change of
  mode mid-paging is `409 CURSOR_STALE` and the web restarts paging (`queries.ts:MAX_STALE_RESETS`). `event_out` withholds content attributes
  and `_span_out` withholds content span names for actors without `payload.read` (`runs/content.py`); stream frames use the same projection.
  36 `/v1` operations (`test_openapi.py`); the route registry (`tests/authz/registry.py`) has a case per route.
- **Web** (`apps/web`): `RunDetail.tsx` loads every event (500 per page, back to back), merges live frames (`live.ts`, `ordering.ts`), filters and
  groups (`lib/timeline.ts`), renders the virtualized `Timeline.tsx` (`@tanstack/react-virtual`, 36 px rows, `role=listbox`, j/k/e keys) with
  selection by event id and an `EventDrawer`. No component reads `/spans`. Fixtures (`fixtures/runs.ts`) have one `agent.started` root span per run
  and a 10,000-event stress run; `fixtures.test.ts` checks statuses and id uniqueness. Styles live in `app/globals.css` (897 lines, a hot file).
  `codingKind` widens the drawer for coding events.
- **Examples / E2E**: `examples/python/trace_an_agent.py` (used by `scripts/sdk-e2e.sh` against compose), `examples/coding-agent` (tests run by
  `scripts/quality.sh`), `examples/langgraph/agent.py` (CI `integrations-e2e`). E2E scripts (`stream-e2e.sh` etc.) start their own database, API,
  worker and built web server and drive Playwright; each is a CI job.
- **Environment**: `.venv`/`node_modules` are not installed in this worktree; nothing was executed for this plan. Step 1 starts with `uv sync` /
  `pnpm install` and `scripts/quality.sh quick`. ASSUMED: LangGraph callback threading (D10 measures it); `@tanstack/react-virtual` horizontal
  virtualization is not needed (bars are positioned by a scale, only rows are virtualized).

## Known issues considered

- **KI-063 (S3, target Phase 9)** pulled in as a bounded spike (D10, step 11): resolved if the measurement allows, otherwise re-filed with the reason.
- **KI-061 (S3, "Phase 9 or on demand")** stays deferred: sub-graph identity needs a LangGraph-specific mapping; the SDK API this phase adds
  (`run.agent`, `agent_id` on spans) is what the adapter would call. Target moves to "on demand".
- **KI-035 (S3)** the live merge rebuilds a seen-set per frame: unchanged, but the waterfall must not add a second full pass per frame; the span
  derivation is incremental per merged batch (D6) and its cost is measured on the stress fixture (AC-8).
- **KI-016 / KI-022 (S2, triggers)**: `derive_run` gains one linear pass (agents); the Phase 7 regression check is re-run on the 30,000-event run and
  the result recorded (AC-9). No cap changes.
- **KI-018 (S1, quotas)**: child agent slugs are client-controlled, exactly like `agent_id` already is; this phase adds no new unbounded
  cardinality: `summary.agents` lists at most 200 agents (the rest fold into one `(other)` row, like ADR-043's rollup bound).
- **KI-019 (S1)**, **KI-050 (S2)**, **KI-017, KI-021, KI-024, KI-026, KI-040, KI-041, KI-042, KI-055, KI-056, KI-057, KI-067, KI-072, KI-073**:
  unrelated, stay as listed. **KI-078 (S3)**: unchanged; `agent.child_id` and agent span names are labels by contract (the UI renders them as text).
- **New items filed at completion** (severity, target): per-agent analytics rollups (S3, on demand; decision 4); skewed clocks reorder the
  chronological timeline across emitters (S3, accepted: documented in `events.md`, the waterfall is the causal view); W3C `traceparent` mapping
  (S3, with the OpenTelemetry adapter, ADR-007).

## Decisions (D1..D11; ADR-070 covers D1-D4, ADR-071 covers D5-D7)

- **D1 (ADR-070) One run, one trace, many agents.** A child agent is an agent span with its own `agent_id` (and optionally `agent_version`) in the
  parent's run; every event emitted inside it carries the child's `agent_id`. The run's own `agent_id` stays the root agent's. No schema or
  registry change: the envelope and the four `agent.*` types already express this. Rationale: §84 verbatim, one page per job, one live stream, one
  cost total; the existing ingestion creates agent rows for children on first sight.
- **D2 (ADR-070) Delegation is recorded twice, by both sides.** The delegating side emits `agent.spawned` (point event in the delegating span,
  parent agent's `agent_id`, `agent.child_id` = child slug, optional delegation input as payload) at the moment of delegation; the child emits
  `agent.started` (`parent_span_id` = the delegating span) and `agent.completed` (status; optional result as payload). Rationale: §84 asks for the
  parent that delegated, the child identity, the input, the result and the timing; a child that never starts is still visible as a spawn without a
  start. Payloads are gated by `PayloadMode` and redacted like every other payload (INV-5, ADR-010).
- **D3 (ADR-070) Context propagation.** In-process: contextvars as today (asyncio inherits; threads use `bind()`), and the current agent span is part
  of the context so nested spans inherit the agent id. Across processes: an explicit, serializable `TraceContext` (`run_id`, `trace_id`,
  `parent_span_id`, child `agent_id`), produced by `run.spawn_context(agent_id=...)` (which also emits `agent.spawned`) and consumed by
  `bb.continue_run(context)`. Wire form `abb1;run=run_<ulid>;trace=trc_<ulid>;span=spn_<ulid>;agent=<slug>` (`span` and `agent` optional, at most
  256 characters, every id validated by the contract patterns; invalid input yields `None` and a warning, never an exception). Conventions:
  environment variable `BLACKBOX_TRACE_CONTEXT` (`blackbox.trace_context_from_env()`), HTTP header name `X-Blackbox-Trace-Context` (the SDK
  exports the constant; it does no HTTP on the user's behalf). A continued run emits no `run.*` lifecycle events (the root owns the run) and
  parents every span to the context's span. Rationale: spec §67.2 ("adapters explicitly propagate required context"); our ids are 128-bit ULIDs,
  so W3C `traceparent` (64-bit parent id) cannot carry a span id losslessly; the adapter that maps it is ADR-007's concern.
- **D4 (ADR-070) Ordering across emitters: remote emitters send no `sequence`.** A continued run, and a forked child that inherited a `Run`
  (`os.getpid() != run._pid`), omit `sequence`; the run then falls into the contract's existing time ordering (`ordering_mode: time`), with
  `sequence` as the tiebreak inside one emitter. Rationale: §65.1 ("monotonic within a run or local emitter stream"); the fallback already exists in
  the schema, the API cursors and the browser, so nothing new is invented; a per-emitter sequence space would need a schema field, a new
  ordering function and a new cursor mode for a total order that still cannot compare clocks. Consequence (documented, accepted): under clock
  skew the chronological list interleaves emitters by their own clocks; the waterfall nests by parentage regardless (§65.3). The root agent of a
  run is the `agent_id` of `run.started` (fallback: first event), so a skewed child never becomes the run's agent (`summary.py` fix).
- **D5 (ADR-071) The waterfall is derived in the browser from the events the page already holds.** `lib/spans.ts` is a port of
  `abb_event_schema.spans.derive_spans` pinned by a generated golden file (like ordering), applied incrementally as live batches merge. No new
  `/v1` route, so no matrix, registry or OpenAPI change. Rationale: the page loads every event anyway (timeline, live merge); `/spans` lags the
  summarizer and is not streamed; content withholding is inherited for free (D7).
- **D6 (ADR-071) Layout and performance budget.** Rows are virtualized (`@tanstack/react-virtual`, as the timeline); the time axis is a pure
  function `scale(domain, zoom, pan)` applied to visible rows only; collapsed subtrees are one aggregate row (count, earliest start, latest end,
  error flag). Auto-collapse below depth 2 when a run has more than 2,000 spans; the user expands on demand. Budget: the 10,000-event stress
  fixture (about 3,900 spans) mounts at most 80 row elements, a zoom step re-renders in under 16 ms of script time on the CI runner (measured, AC-8).
  Zoom and pan: wheel with modifier and keyboard (`+`/`-`, `[`/`]`, `0` = fit), with a visible control group for discoverability and a11y.
- **D7 (ADR-071) Critical path** = Jaeger's last-finishing-child walk: from the root's end, repeatedly take the child that finishes last before the
  current point, add its segment, descend; when a span has no such child, the segment to its start is on the path; then continue in the parent from
  that start. Spans whose timestamps fall outside their parent (skew) are clamped to the parent for the walk and flagged `skewed` for display.
  Pure, tested on a chain, a fan-out and a skewed child. Rationale: the standard, explainable definition; §97 asks for highlighting, not a score.
- **D8 Lanes and tree are two orderings of the same rows.** Tree: depth-first by start time, indented, agent shown as a label and colour; Lanes:
  rows grouped by `agent_id` (lane order = first start), the delegating span of a child lane drawn as a dotted link. The agent tree panel (counts,
  cost, duration per agent from `summary.agents`, with the hierarchy from the spans) is the navigation: clicking an agent filters the timeline and
  focuses the lane. `summary.agents` is derived server-side so cost per agent uses the CostEngine (ADR-040), not client estimates.
- **D9 Selection is one state.** `selected` in `RunDetail` is an event id, as today; a waterfall row selects its opening event (or its only event),
  the timeline scrolls to it, and the drawer opens on activation; the first-error jump switches to whichever view is active and selects the same
  event. The view switch is a URL search parameter so a link to a waterfall is shareable.
- **D10 KI-063 spike is bounded.** Measure in the LangGraph integration test whether `on_chain_start`/`on_chain_end` of a node run on the same
  thread as the node body (`threading.get_ident()`) for sync and async graphs. If yes for both: the adapter sets the SDK's current span to the node
  span in `on_chain_start` and restores it in `on_chain_end`/`on_chain_error` (token-based reset, same-thread guard), so SDK spans inside a node
  nest; conformance scenario `nested` gains a check. If no: record the measurement, keep KI-063 open, retarget to "on demand". One commit either way.
- **D11 The example is pure SDK and deterministic.** `examples/multi-agent` uses fake models (fixed token counts), threads (`bind`), an asyncio
  task, and a real subprocess started with `BLACKBOX_TRACE_CONTEXT` in its environment; `--skew-ms` offsets the subprocess's `occurred_at` by
  patching `blackbox.events.now_rfc3339` inside the child (example code only; the SDK gets no clock knob). It runs offline in tests
  (`mode="offline"`, events inspected) and against a real API in `scripts/multiagent-e2e.sh`.

## Proposed design

### SDK (`packages/sdk-python`)

```python
with bb.run("Ship feature") as run:                       # root agent: bb.config.agent_id
    with run.agent("researcher", input={"q": "oauth"}) as researcher:   # agent.spawned + agent.started
        with run.llm_call("openai", "gpt-4.1") as llm: ...                 # carries agent_id="researcher"
        researcher.set_result({"notes": 3})                               # payload of agent.completed (FULL mode only)
    ctx = run.spawn_context(agent_id="tester")            # agent.spawned now; a string for the child
    subprocess.run([...], env={**os.environ, "BLACKBOX_TRACE_CONTEXT": ctx})

# child process
with bb.continue_run(blackbox.trace_context_from_env()) as run:   # no run.started; agent span "tester" opens on enter
    with run.span("pytest", kind="shell"): ...
```

- `Span` gains `agent_id` and `agent_version` (inherited: explicit argument, else the parent span's, else the run's). `Run._emit`/`BlackBox._emit`
  take `agent_id`/`agent_version` from the span that emits (`Span.event`, `Span.start/end`) or from the current span (`Run.event`).
- `Run.agent(name, *, agent_id=None, agent_version=None, input=None, attributes=None, parent=None) -> AgentSpan` (`Span` subclass, kind
  `agent`): `start()` first emits `agent.spawned` with the delegating side's identity (`span_id` = parent span, `agent_id` = parent's agent,
  `agent.child_id` = child slug, payload `{"input": ...}` when given), then `agent.started` with the child's identity. `set_result(payload)` sets the
  closing payload. `agent_id` defaults to `agent_slug(name)`.
- `TraceContext` (frozen dataclass; `serialize()`, `parse()`), `Run.spawn_context(agent_id=None, *, parent=None, input=None) -> str`,
  `Run.trace_context()` (no event, for in-process hand-offs that do not delegate), `BlackBox.continue_run(context, *, agent_id=None,
  agent_version=None) -> Run`, `trace_context_from_env()`, `TRACE_CONTEXT_HEADER`. A continued `Run`: same `run_id`/`trace_id`, `remote=True`,
  `_root_parent = context.parent_span_id` (spans without a parent use it), `__enter__` opens the agent span when `agent_id` (from the argument or
  the context) is set, `__exit__` closes it with the block's outcome and never emits `run.*`; `end()` is a no-op that counts `remote_run_end_ignored`.
  Disabled mode returns an inert run as today. Every input is coerced or dropped (INV-4).
- `sequence`: omitted when `run.remote` or `os.getpid() != run._pid`; `build_event` keeps the field out when `None`.
- Exports: `AgentSpan`, `TraceContext`, `trace_context_from_env`, `TRACE_CONTEXT_HEADER`; SDK version 0.3.0; README section "Multi-agent and
  other processes". Contract tests validate every new event shape with `abb_event_schema` (ADR-013).

### Schema and docs (`packages/event-schema`, `docs/architecture/events.md`)

No model or registry change. `tests/test_spans_golden.py` generates `tests/data/spans-golden.json` (cases: chain, fan-out, late parent, orphan,
close before open, skewed child, two agents, remote emitter without sequence, 300 random events) exactly like the ordering golden.
`ordering.py` docstring: replace the Phase 9 note with the D4 rule. `events.md`: "Agents and delegation" (the four events, who emits what,
payload rules) and "Propagation" (context string, env var, header, ordering consequence).

### API (`apps/api`)

- `runs/summary.py`: `SUMMARY_VERSION = 3`; `agent_slug` from `run.started` (fallback first event); `summary["agents"]` = list (max 200, then
  `(other)`) of `{agent_id, parent_agent_id | null, event_count, llm_calls, tool_calls, error_count, estimated_cost_usd, first_event_at,
  last_event_at}` ordered by first event (parent from the `agent.started` opener's parent span's agent, else the `agent.spawned` emitter);
  `summary["agent_count"]`. Cost per agent sums the CostEngine lines by the event's `agent_id` (Decimal, order-independent).
- Tests: `test_run_summary.py` (agents, root agent under skew, bound at 200, version 3), `test_summarizer.py` (a multi-emitter run: some events
  without `sequence` -> `ordering_mode == "time"`, spans nested by parent although the child's timestamps precede the parent's), `test_runs_api.py`
  (the fields appear; `GET /spans` for a child span shows `agent_id` of the child; a VIEWER gets `agent_id` but no content span names, unchanged).
- `docs/architecture/api-v1.md`: `summary.agents`, `agent_count`, version 3, the time-ordering note for multi-emitter runs. Existing runs keep
  version 2 until re-summarized; the web treats a missing `agents` as "no agent breakdown" (no rebuild is required; `POST /v1/cost/rebuild`
  re-derives when wanted).

### Web (`apps/web`)

- `lib/spans.ts`: `deriveSpans(events) -> Map<spanId, Span>` (port; golden-tested) plus `SpanIndex` with `merge(batch)` for incremental updates.
- `lib/waterfall.ts` (pure, tested): `buildTree(spans)` (roots, children sorted by start then id; orphans become roots flagged `orphan`),
  `treeRows(tree, collapsed, autoCollapseDepth)`, `laneRows(tree)`, `timeDomain(spans)`, `scale(domain, zoom, pan, width)`, `criticalPath(tree)`,
  `aggregate(subtree)`, `skewed(span)`, `spanLabel(span, events)` (uses `contentText` so withheld names show `[withheld]`), `colourClass(kind, status)`.
- `components/Waterfall.tsx`: header axis with ticks and the zoom/pan control group; virtualized rows (`role=treegrid`, `aria-level`,
  `aria-expanded`, `aria-selected`; keys: arrows, Enter, Escape, `e`, `+`/`-`, `[`/`]`, `0`, `c` toggles collapse); each row: indent, agent chip, kind
  glyph, name, bar (`position: absolute` from `scale`), duration, status text; hover tooltip from a single portal node (`aria-describedby`); critical
  path drawn as a thicker bar edge plus a legend entry (never colour alone); `data-skewed` badge "clock skew".
- `components/AgentTree.tsx`: the agent hierarchy with counts, cost, duration (from `summary.agents` joined with the derived spans); click filters.
- `RunDetail.tsx`: `view` from `useSearchParams` (`timeline` default); `<ViewSwitch>`; the agent filter (`filters.agents`) added to `lib/timeline.ts`
  `Filters`; the timeline row shows an agent chip when the run has more than one agent; `jumpToError` works in both views.
- `app/waterfall.css` imported from `app/layout.tsx` (one line) so `globals.css` is untouched.
- Fixtures: `multiAgentRun()` (planner -> researcher ∥ coder -> tester (subprocess, timestamps 1.5 s behind, no `sequence`) ; reviewer; 5 agents,
  one tool failure in the researcher, cost per agent) added to `fixtures/index.ts`; `fixtures.test.ts` and the dashboard/runs tests updated for the
  extra run.
- Tests: vitest for `spans.ts` (golden), `waterfall.ts` (tree, lanes, scale, collapse, aggregate, critical path on chain/fan-out/skew, withheld
  labels), `Waterfall.test.tsx` (renders rows, keyboard zoom changes the scale, selection calls back, aggregate row for a collapsed subtree, DOM
  row count on the stress fixture), `RunDetail` view switch and selection sync; Playwright `e2e/fixtures.spec.ts` (waterfall of the multi-agent
  fixture: 5 lanes, tester nested under coder with the skew badge, critical path present, selection sync with the timeline, axe clean) and
  `e2e/multiagent.spec.ts` (real run, see below).

### Example and E2E

- `examples/multi-agent/{README.md,agent.py,multi_agent/{planner.py,workers.py,fake_model.py},tests/test_example.py}`: `python agent.py
  [--skew-ms 1500] [--fail-tool]`; prints `run <id>`; tests run it in offline mode and assert the event shapes (agents, parents, missing
  `sequence` from the subprocess, payload gating). `scripts/quality.sh` gains the example step (one line, like `examples/coding-agent`);
  `Makefile` gains `multiagent-e2e`.
- `scripts/multiagent-e2e.sh` (own database `abb_p9`, API on 8130, worker, built web on 3104, modeled on `stream-e2e.sh`): runs the example with
  `--skew-ms 1500`, waits for `summary_state == current`, asserts `agent_count == 5`, the tester span's `parent_span_id` is the coder's span,
  `ordering_mode == "time"`, then Playwright checks the waterfall (lanes, nesting, skew badge) and captures a screenshot to
  `docs/screenshots/phase-9-waterfall.png`. CI job `multiagent-e2e` added to `.github/workflows/ci.yml` in the last commit of step 10.

## Affected files

- `packages/sdk-python/src/blackbox/{client.py,context.py,events.py,__init__.py,version.py}`, new `propagation.py`; tests `test_agents.py`,
  `test_propagation.py`, `test_contract.py` (new shapes), `README.md`, `CHANGELOG` section if present.
- `packages/event-schema/src/abb_event_schema/ordering.py` (docstring), tests `test_spans_golden.py`, `tests/data/spans-golden.json`;
  `docs/architecture/events.md`.
- `apps/api/src/abb_api/runs/summary.py`; tests `test_run_summary.py`, `test_summarizer.py`, `test_runs_api.py`; `docs/architecture/api-v1.md`;
  `docs/benchmarks/phase-9-regression-check.md`.
- `apps/web/src/lib/{spans.ts,waterfall.ts,timeline.ts}`, `components/{Waterfall.tsx,AgentTree.tsx,ViewSwitch.tsx,RunDetail.tsx,Timeline.tsx}`,
  `app/waterfall.css`, `app/layout.tsx` (import), `fixtures/{runs.ts,index.ts}`, tests as listed, `e2e/{fixtures.spec.ts,multiagent.spec.ts}`,
  `playwright.config.ts` (one project).
- `integrations/langgraph/src/blackbox_langgraph/handler.py` and tests (only if D10 succeeds), `integrations/conformance` scenario check.
- `examples/multi-agent/*`, `scripts/multiagent-e2e.sh`, `scripts/quality.sh`, `Makefile`, `.github/workflows/ci.yml`.
- Docs: `docs/decisions/ADR-070-multi-agent-trace-semantics-and-propagation.md`, `ADR-071-browser-derived-waterfall.md`, `docs/DECISIONS.md`,
  `docs/KNOWN_ISSUES.md`, `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md`, `ARCHITECTURE.md` (status lines), `README.md` (example list).
- Untouched on purpose: `apps/api/openapi.json`, `apps/web/src/lib/api/schema.d.ts`, `apps/api/src/abb_api/main.py`, `authz/*`, `tests/authz/*`,
  `test_openapi.py`, `test_migrations.py`, `apps/api/migrations/*`, `apps/web/src/app/globals.css`, `AppShell.tsx`.

## Acceptance criteria (all command-checkable; evidence recorded here at completion)

- **AC-1 Agent spans.** `cd packages/sdk-python && uv run pytest -q tests/test_agents.py tests/test_contract.py`: `run.agent()` emits
  `agent.spawned` (parent identity, `agent.child_id`) then `agent.started`/`agent.completed` (child identity, `parent_span_id` = delegating span);
  events inside carry the child `agent_id` and `agent_version`; a nested `llm_call` inherits; `input`/`set_result` payloads appear only in
  `PayloadMode.FULL` and pass the redactor; hostile arguments (non-str names, control characters, 1,000-char slugs) are coerced; every emitted
  event validates against `abb_event_schema`.
- **AC-2 Propagation.** `uv run pytest -q tests/test_propagation.py`: `TraceContext` round-trips; 20 malformed strings parse to `None` without
  raising; `continue_run` emits no `run.*`, parents root spans to the context span, opens/closes the agent span on enter/exit with the block's
  outcome, omits `sequence`; a thread via `bind` and an asyncio task share the run and the current agent; a forked child's events omit
  `sequence` (`os.fork` test, skipped where absent); a real `subprocess` started with `BLACKBOX_TRACE_CONTEXT` produces nested events in local
  mode (`mode="local"` file inspected). Coverage gate (94%) still met: `uv run pytest -q --cov`.
- **AC-3 Golden spans.** `cd packages/event-schema && uv run pytest -q tests/test_spans_golden.py` regenerates `spans-golden.json` with no diff
  (`git diff --exit-code packages/event-schema/tests/data`); `pnpm --filter @abb/web test -- spans` passes every golden case.
- **AC-4 Summary.** `cd apps/api && uv run pytest -q tests/test_run_summary.py tests/test_summarizer.py tests/test_runs_api.py`: `summary.agents`,
  `agent_count`, version 3, 200-agent bound, root agent = `run.started`'s agent under a 10 s child skew; the multi-emitter run has
  `ordering_mode == "time"` and the child span's `parent_span_id` is the parent's although its timestamps precede the parent's.
- **AC-5 No contract drift.** `git diff --exit-code apps/api/openapi.json apps/web/src/lib/api/schema.d.ts apps/api/migrations` is empty;
  `uv run pytest -q tests/test_openapi.py tests/authz` passes unchanged (36 operations; no new route).
- **AC-6 Waterfall logic.** `pnpm --filter @abb/web test -- waterfall`: tree/lanes/scale/collapse/aggregate; critical path on the chain (whole
  chain), the fan-out (the slowest branch only) and the skewed child (clamped, flagged); labels of a VIEWER-shaped event list contain
  `[withheld]` and never the command text.
- **AC-7 Waterfall UI.** `pnpm --filter @abb/web test -- Waterfall RunDetail`: rows render with `aria-level`; `+` changes the scale; clicking a row
  selects its opener event and the timeline receives the same selection; `e` selects the first error in both views; `?view=waterfall` is honoured.
- **AC-8 Performance.** Playwright `fixtures.spec.ts` on the stress fixture: at most 80 `[role=row]` elements mounted in the waterfall; a zoom
  step measured with `performance.now()` around the key press and `requestAnimationFrame` completes in under 16 ms script time (number recorded);
  the vitest stress case derives spans for 10,000 events in under 50 ms (recorded).
- **AC-9 Server cost.** `docs/benchmarks/phase-9-regression-check.md`: `derive_run` on the 30,000-event benchmark run before/after (the Phase 7
  harness), difference recorded; no new KI-016 regression beyond noise.
- **AC-10 Fixture and a11y.** `pnpm --filter @abb/web test:e2e -- fixtures.spec.ts`: multi-agent fixture shows 5 lanes, the tester row nested under
  the coder row with the skew badge, a critical path legend, axe reports no violations in both views, keyboard-only navigation reaches the drawer.
- **AC-11 Example offline.** `uv run --project packages/sdk-python pytest examples/multi-agent/tests -q`: 5 agents, the subprocess's events have no
  `sequence` and parent to the coder's span, `--fail-tool` yields one `tool.call.failed` and a successful run, payloads absent in the default mode.
- **AC-12 Example end to end.** `make multiagent-e2e` passes (assertions in the script; screenshot written); CI job `multiagent-e2e` green on the PR.
- **AC-13 KI-063 spike.** `cd integrations/langgraph && uv run pytest -q`: either the new nesting test passes (adapter change landed, conformance
  `nested` check extended) or the measurement is recorded in this plan and `KNOWN_ISSUES.md` with the thread ids observed.
- **AC-14 Whole gate.** `scripts/quality.sh full` exit 0; `make stream-e2e coding-e2e auth-e2e analytics-e2e` still pass (the run page changed).
- **AC-15 Docs.** ADR-070/071 exist and are indexed; `events.md`, `api-v1.md`, SDK README updated; `KNOWN_ISSUES.md` rows for the new items with
  GitHub issues; `PROJECT_STATE.md` updated.

## Verification plan

Per step: the focused tests named in the step, then `scripts/quality.sh quick`. After steps 4, 7, 10 and 12: `scripts/quality.sh full`. UI steps are
exercised in a browser (Playwright on fixtures, then the real run of AC-12) with screenshots in `docs/screenshots/`. Evidence vocabulary as in
AGENTS.md; anything not run here (for example `make up`) is UNVERIFIED (env) with the reason. Mutation checks for the reviewer (each must fail a
test): drop `agent_id` inheritance in `Span`; emit `sequence` from a continued run; parent by time instead of `parent_span_id` in `spans.ts`; let the
critical path choose the earliest-finishing child; render span names without `contentText`; lift the 200-agent bound; derive the run's agent from
`ordered[0]` again.

## Risks

1. **Clock skew makes the chronological timeline misleading** across processes (D4). Mitigation: the waterfall is the causal view, the skew badge
   names it, `events.md` documents it; a future per-emitter ordering is an ADR away.
2. **A continued run can write into any run of the same project** whoever holds the context string (it is not a credential: the key is). Mitigation:
   ids are validated, the server rejects other projects' runs (`RUN_PROJECT_MISMATCH`), the context string carries no secret; documented as
   "treat like a request id".
3. **Browser cost on huge runs**: span derivation per live batch plus the timeline's existing per-frame merge (KI-035). Mitigation: incremental
   `SpanIndex.merge`, measured on the stress fixture (AC-8); auto-collapse below depth 2 above 2,000 spans.
4. **SUMMARY_VERSION bump** leaves old runs at version 2 (no `agents`). Mitigation: the UI degrades to "no agent breakdown"; `cost/rebuild`
   re-derives; no migration.
5. **LangGraph threading assumption** (D10). Mitigation: measured before any adapter change; the spike cannot block the phase.
6. **Hot-file conflicts with Phases 10/11/16** (quality.sh, Makefile, ci.yml, KNOWN_ISSUES, DECISIONS, PROJECT_STATE, IMPLEMENTATION_PLAN,
   README). Mitigation: each touched only in the last commit of its step with additive one-line edits ("Merge notes").
7. **Fixture additions change counts** in `fixtures.test.ts`/dashboard tests and the runs list E2E. Mitigation: update in the same commit.
8. **Example subprocess on CI** (Python path, env). Mitigation: the example runs `sys.executable`; tests pass `PATH` as `examples/coding-agent`
   does.

## Security review note (what an adversarial reviewer should attack)

- **Content leakage through the new views.** The waterfall and agent tree must derive every label through `contentText`/withheld markers: a
  VIEWER-shaped event list (attributes replaced by `[withheld]`, `withheld_attributes` set) must never show a command, path or URL in a row,
  tooltip, aggregate row, lane title or agent tree. Attack with `span.name` values that look like commands: they are labels by contract (KI-078),
  shown as text, never interpreted.
- **Hostile agent ids and context strings.** `agent.child_id` is any string up to 4,096 characters (the registry only types it); the UI renders it as
  text, truncates to 64 characters for chips and never uses it as a key format or HTML. `TraceContext.parse` is fed 20 malformed forms (overlong,
  wrong prefixes, NUL, nested separators, 10 MB string) and must return `None` quickly.
- **Writing into another run.** A child with a stolen context but a key of another project is rejected per event (`RUN_PROJECT_MISMATCH`); a
  child with the same project's key can only do what that key could already do. Verify no new path lets a key name a workspace or project.
- **Unbounded growth.** `summary.agents` is capped at 200; `SpanIndex` holds one entry per span id (bounded by the events already in memory);
  the example's subprocess env contains only the context (no key is passed on the command line; the key stays in the environment it inherits).
- **Payload gating.** `input` and `set_result` payloads must obey `PayloadMode` exactly like `set_payload` (never in METADATA_ONLY) and pass the
  redactor; secret-looking values in the example's delegation input are redacted in storage and absent from the UI (reuse the coding-agent
  planted-secret pattern).
- **No new credential surface.** No new route, header the API trusts, or setting. `X-Blackbox-Trace-Context` is an SDK-side name only; the API never
  reads it.
- **Logs.** The SDK logs a warning on an invalid context without echoing it; the API logs nothing new.

## Merge notes (hot files and how to rebase)

- Touched hot files, each in the last commit of its step, additive one-line edits: `scripts/quality.sh` (step 9, one `step` line),
  `Makefile` (step 10, one target), `.github/workflows/ci.yml` (step 10, one job appended), `README.md` (step 12, one example bullet),
  `docs/KNOWN_ISSUES.md` (step 12, rows), `docs/DECISIONS.md` (step 12, two rows), `docs/PROJECT_STATE.md` and `docs/IMPLEMENTATION_PLAN.md`
  (step 12, status lines), `ARCHITECTURE.md` (step 12, status sentence).
- Not touched: `apps/api/openapi.json`, `apps/web/src/lib/api/schema.d.ts`, `main.py`, the authz matrix/registry and their tests, `test_openapi.py`,
  `test_migrations.py`, `globals.css`, `AppShell.tsx`, migrations.
- Migrations: Phase 9 adds none, so its merge never changes the alembic chain. For the other phases (each chaining from 0047 today): whichever
  merges second edits its first migration's `down_revision` from `"0047"` to the merged head (for example `"0061"`), appends its ids after it in
  `test_migrations.py:REVISIONS` and re-runs `scripts/quality.sh full` (upgrade head / downgrade base / upgrade head), as Phase 6 did when it
  renumbered behind Phases 7 and 8. Phase 9's reserved ids 0050-0059 stay free.
- Web rebases: `RunDetail.tsx` and `lib/timeline.ts` are the likely conflict points with Phase 11 (comparison) and Phase 16 (search); keep this
  phase's edits to RunDetail confined to the view switch block and the agent filter so a rebase is a block move.

## Ordered steps (one commit each; main stays green after every step)

1. `test(event-schema): golden spans file and the multi-emitter ordering note` - `test_spans_golden.py` generates `spans-golden.json`; `ordering.py`
   docstring updated to D4. No behaviour change. Verify: event-schema suite; `git diff --exit-code tests/data`.
2. `feat(sdk): agent spans with their own agent id and the agent.spawned event` - `Span.agent_id/agent_version` inheritance, `AgentSpan`,
   `Run.agent`, `_emit` identity plumbing, contract tests, `test_agents.py`. Verify: AC-1, coverage gate.
3. `feat(sdk): trace context propagation across threads, tasks and processes` - `propagation.py` (`TraceContext`, env helper, header name),
   `Run.spawn_context/trace_context`, `BlackBox.continue_run`, remote/forked runs omit `sequence`, README, version 0.3.0. Verify: AC-2.
4. `feat(api): per-agent run summary and a stable root agent (summary version 3)` - `summary.py` agents/agent_count/root agent, tests,
   `api-v1.md`. Verify: AC-4 (summary part), `scripts/quality.sh full`, AC-5.
5. `test(api): a multi-emitter run orders by time and nests a skewed child` - summarizer and runs API tests only (the behaviour exists; the tests pin
   it). Verify: AC-4 (ordering part), AC-5.
6. `feat(web): derive spans and waterfall layout in the browser` - `lib/spans.ts` (golden-tested, `SpanIndex`), `lib/waterfall.ts`, the agent
   filter in `lib/timeline.ts`; vitest. Verify: AC-3 (web part), AC-6.
7. `feat(web): waterfall view with lanes, zoom, critical path and synchronized selection` - `Waterfall.tsx`, `AgentTree.tsx`, `ViewSwitch`,
   `RunDetail` wiring, `waterfall.css` + layout import, timeline agent chip; component tests. Verify: AC-7, `scripts/quality.sh full`, browser check
   on existing fixtures (screenshot).
8. `feat(web): multi-agent fixture, waterfall E2E and the performance check` - `multiAgentRun`, fixture tests updated, `fixtures.spec.ts` waterfall
   cases, stress DOM/zoom measurement, axe. Verify: AC-8, AC-10.
9. `feat(examples): multi-agent example (threads, asyncio, subprocess, skew knob)` - `examples/multi-agent`, offline tests, `scripts/quality.sh`
   step (last file in the commit). Verify: AC-11, `scripts/quality.sh full`.
10. `test(e2e): multi-agent example against the real API and web` - `scripts/multiagent-e2e.sh`, `e2e/multiagent.spec.ts`, Playwright project,
    screenshot, `Makefile` target, CI job (ci.yml last). Verify: AC-12 locally; CI on the PR.
11. `feat(langgraph): place node spans in the SDK context when callbacks run on the node's thread` **or** `docs(known-issues): KI-063 measurement`
    - the D10 spike, whichever outcome. Verify: AC-13.
12. `docs(phase-9): ADR-070, ADR-071, events and SDK docs, known issues, state` - ADR files, `DECISIONS.md`, `KNOWN_ISSUES.md` (new rows with
    issues), `PROJECT_STATE.md`, `IMPLEMENTATION_PLAN.md`, `ARCHITECTURE.md`, `README.md`, benchmark note, evidence for AC-1..15 appended to this
    plan; then `complete-phase` moves the plan. Verify: AC-9, AC-14, AC-15.

## Appendix A - ADR-070 draft: Multi-agent trace semantics and context propagation

Status: Proposed (Phase 9). Context: spec §84 asks for delegating parent, child identity, input, result and timing overlap; §67.2 for explicit
propagation across threads, processes and sub-agents; §65.1 allows `sequence` to be per emitter; Phase 1 left multi-emitter ordering open (R1).
Decision: (1) one run and one trace per job; a child agent is an `agent.*` span with its own `agent_id` (and optional `agent_version`) whose
nested events carry that identity; the run's agent is the root agent (the `run.started` emitter). (2) Delegation is recorded by both sides:
`agent.spawned` by the delegator (`agent.child_id`, optional input payload), `agent.started`/`agent.completed` by the child (`parent_span_id` = the
delegating span, optional result payload); payloads obey `PayloadMode` and the redactor. (3) Propagation: contextvars in-process (threads via
`bind`), and an explicit serialized `TraceContext` (`abb1;run=...;trace=...;span=...;agent=...`, env `BLACKBOX_TRACE_CONTEXT`, header name
`X-Blackbox-Trace-Context`) produced by `run.spawn_context()` and consumed by `bb.continue_run()`; a continued run never emits `run.*`. (4)
Remote emitters (continued runs, forked children) omit `sequence`; such a run is time-ordered by the existing contract fallback; the waterfall
nests by parentage, so clock skew cannot un-nest a child. No schema change. Alternatives: a run per agent linked by trace (splits cost, stream and
page; contradicts §84); a per-emitter sequence namespace (new field, new cursor mode, still no cross-clock total order); W3C `traceparent` as the
wire form (64-bit parent id cannot carry a 128-bit span ULID; mapping belongs to the OpenTelemetry adapter, ADR-007). Consequences: a child
process needs a key of the same project; chronological order across emitters follows their clocks (documented); adapters can set `agent_id` on
spans when a framework exposes sub-agent identity (KI-061). Revisit: when a trace must span runs, or when an OpenTelemetry adapter lands.

## Appendix B - ADR-071 draft: The waterfall is derived in the browser; critical path by last-finishing child

Status: Proposed (Phase 9). Context: §97 requires hierarchical rows, lanes, zoom/pan, colour by kind/status, critical path, hover, synchronized
selection and "render only visible rows, aggregate collapsed nodes"; the run page already loads every event and merges live frames; `/spans` is a
derived, lagging, non-streamed view. Decision: the waterfall derives spans in the browser with a port of `abb_event_schema.spans.derive_spans`
pinned by a generated golden file, updated incrementally per live batch; rows are virtualized and collapsed subtrees are one aggregate row,
auto-collapsed below depth 2 above 2,000 spans; the time axis is a pure scale over zoom and pan; the critical path is Jaeger's last-finishing-child
walk with skewed children clamped to their parent and flagged; lanes group the same rows by `agent_id`; per-agent cost comes from the
server-derived `summary.agents` (CostEngine) and is capped at 200 agents; selection is one event id shared with the timeline and the drawer; the
content projection of ADR-061 is inherited because labels are built from the already-withheld events. No new route, so the authorization matrix
and OpenAPI are unchanged. Alternatives: a `GET /v1/runs/{id}/tree` endpoint (a matrix case, a registry case, cross-workspace coverage, and still
no live updates); SVG/canvas drawing of every bar (tens of thousands of nodes, against §97); a critical-path heuristic by longest duration
(not explainable). Consequences: span derivation cost moves to the client (measured on the stress fixture); a mismatch between the Python and
TypeScript derivations is caught by the golden file; very large runs show an aggregated tree until expanded. Revisit: if a trace exceeds what a
browser can hold (then a server-side windowed tree endpoint, with its matrix case).
