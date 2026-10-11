# Independent review of the Phase 9 plan (multi-agent distributed tracing and waterfall)

Reviewed: `docs/plans/active/phase-9-multi-agent-tracing.md` at commit 6fb9465 on `feature/phase-9-multi-agent-tracing`
(worktree `../abb-worktrees/phase-9`, main c212058). Stance: adversarial, on paper, before any code exists. Nothing but this
file was written; no production code, test or migration was touched. Sibling plans (Phases 10, 11, 16) were read for
cross-checking only.

Severity: **P0** critical, **P1** must fix before implementation, **P2** should fix, **P3** optional.
Evidence: **VERIFIED** (the cited lines were read in this worktree), **UNVERIFIED (env)** (no `.venv`/`node_modules`/database
here; nothing was executed), **ASSUMED** (reasoned).

Summary: no P0. Five P1: the critical-path and tree definitions (D7/`buildTree`) are undefined for exactly the traces the SDK
produces (a forest, unfinished spans) and for hostile cycles; decision 4's load-bearing claim about analytics attribution is
wrong (cost-by-agent already keys on the event's `agent_id`); the `SUMMARY_VERSION` 3 bump collides with Phase 16's identical
bump and makes the analytics page tell every existing deployment that all its runs "predate cost calculation"; the
multi-agent fixture cannot be built with the current `RunBuilder` (sequence-only, `agent.failed` closer, array-order paging);
`SpanIndex.merge(batch)` has no batch to consume. Eleven P2, nine P3. The architecture section is accurate on every claim I
could check except three small ones listed under "Unverified or wrong".

---

## 1. Verified correct (claims of the "Current architecture" section checked against code)

| Plan claim | Evidence (file:line) |
| --- | --- |
| `agent_id` is a slug `[a-z0-9][a-z0-9._-]{0,63}` on every event; `parent_span_id` needs `span_id` and cannot equal it | `packages/event-schema/src/abb_event_schema/event.py:115,232,266-274` |
| Registry: `agent.started` OPEN/AGENT, `agent.completed` CLOSE, `agent.spawned` POINT requiring `agent.child_id`, `agent.state_changed`; no `agent.failed` | `registry.py:196-199`; `lookup()` returns `None` for `agent.failed` (`:254-261`), so it is POINT/CUSTOM |
| `agent.child_id` is a known STRING attribute in the reviewed-metadata set | `registry.py:52`; `apps/api/tests/authz/test_content_withheld.py:31` |
| `derive_spans` nests by `parent_span_id` only; late parent / orphan / close-before-open are normal; only two parents, a cycle, or one span id in two traces are issues | `spans.py:51-79` (derivation), `:87-121` (`check_span_relationships`), `:124-126` (orphans informational) |
| `sort_events` keys and the "Phase 9" docstring note | `ordering.py:8-9,32-48` |
| Ordering golden pins the TS port | `packages/event-schema/tests/test_ordering_golden.py:1-8`; `apps/web/src/lib/ordering.ts:3-10` |
| `Run` creates `run_id`/`trace_id` and one `itertools.count`; every event carries `run.agent_id` | `packages/sdk-python/src/blackbox/client.py:290-298,740` |
| `Span.start()` parents to the current span of the same run when no explicit parent | `client.py:150-152` |
| kind `agent` maps to `agent.started`/`agent.completed`/`agent.completed` | `client.py:53` |
| contextvars; asyncio inherits; threads need `bind()` | `context.py:1-5,32-39` |
| `register_at_fork` recreates buffer/exporter in the child | `client.py:464-471,710-717` |
| No way to continue a run from another process, no agent id per span, no serialized context | `client.py` (no such API), `context.py` (two contextvars only) |
| LangGraph adapter parents by framework run id, `run_inline = True` | `integrations/langgraph/src/blackbox_langgraph/handler.py:82,115-129` |
| `SUMMARY_VERSION` 2, pinned by `test_run_summary.py:199`; `agent_slug = ordered[0].agent_id` | `apps/api/src/abb_api/runs/summary.py:24,101,180`; `tests/test_run_summary.py:199` |
| Ingestion creates an agent row (and version) per distinct `(project, agent_id)`; rejects `RUN_PROJECT_MISMATCH` | `ingestion/store.py:117-152` |
| `spans` table has `parent_span_id`, `agent_slug`, `ix_spans_run_parent`; summarizer writes spans from the derivation | `db/tables.py:334-350`; `runs/repository.py:147-220` |
| `GET /spans` lags the summarizer; `GET /events` serves canonical order with `ordering_mode`; mode change mid-paging is `409 CURSOR_STALE`; web restarts paging at most 3 times | `runs/service.py:226-254,274-294,326-331`; `apps/web/src/lib/queries.ts:62-104` |
| `event_out` withholds content attributes; `_span_out` withholds content span names; stream frames use the same projection | `runs/service.py:47-67,91-109`; `runs/content.py`; `streaming/service.py:88,113-114` |
| 36 `/v1` operations | `apps/api/tests/test_openapi.py:55` |
| `RunDetail.tsx` pages 500 per page back to back, merges live frames, filters/groups, virtualized 36 px rows, `role=listbox`, j/k/e keys, selection by event id | `RunDetail.tsx:137-153,180-181`; `Timeline.tsx:8,66-96,104-111`; `queries.ts:83` |
| No component reads `/spans` | `rg "/spans" apps/web/src` finds only the fixture API |
| Fixtures: one `agent.started` root span per run; 10,000-event stress run; `fixtures.test.ts` checks statuses and id uniqueness | `fixtures/runs.ts:64-67,166-190`; `tests/fixtures.test.ts:17-35` |
| `globals.css` is 897 lines | `wc -l` |
| E2E scripts own their database/API/worker/web ports (stream 8120/3103 `abb_p5`, coding 8140/3140 `abb_p6`, analytics 8150/3150 `abb_p7`, auth 3160 `abb_p15`); 8130/3104/`abb_p9` are free | `scripts/*-e2e.sh:2-11`; `apps/web/playwright.config.ts` |
| `@axe-core/playwright` already a dev dependency | `apps/web/package.json:27` |
| `CostLine` carries `agent_id` so per-agent cost can be summed from lines | `apps/api/src/abb_api/cost/engine.py:34,118` |
| Phases 10/11/16 migrations all chain from 0047 (0060; 0070-0071; 0080) and Phase 9 has none | sibling plans `:9`; `apps/api/migrations/versions/` ends at 0047 |

## 2. Unverified or wrong statements in the plan

- **Wrong (minor):** "`build_event` omits `sequence` only if asked (it is always passed today)". There is no omit path: `sequence: int` is a
  required parameter and is always written (`events.py:155,173`). The plan's SDK section correctly says this must be added; the architecture
  line should say so too.
- **Overstated:** "the child keeps the parent's `Run` objects and sequence counter (`test_client.py:390`)". That test starts a *new* run in the
  child (`test_client.py:390-416`); the shared-counter claim is reasoned from `fork` semantics (ASSUMED, correct), not shown by that test.
- **Line drift:** "`client.py:739`" is `:740`; "`store.py:117-150`" is `:117-152`. Cosmetic.
- **UNVERIFIED (env):** everything that needs a database, `uv`, `pnpm` or a browser: AC-3..AC-14 feasibility, the 30,000-event benchmark harness
  (AC-9), the LangGraph threading measurement (D10), and the stress-fixture budget numbers (D6). The plan already marks these.

---

## 3. Findings

### P1

**F1. The critical path, the tree and the collapse rule are undefined for the traces this phase produces (D5/D7/`lib/waterfall.ts`).**
Plan: D7 "from the root's end ..."; `buildTree(spans)` "(roots, children sorted by start then id; orphans become roots flagged `orphan`)".
Code: an SDK run has **no root span**: `bb.run()` emits `run.started` as a point event with no `span_id` (`client.py:304-309`), and top-level
spans have `parent_span_id = None` (`client.py:150-156`); only the web fixtures wrap everything in one `agent.started` span
(`fixtures/runs.ts:64-67`). A continued run's spans parent to the context span, but the parent's own top-level spans are still roots. So
every real trace is a **forest**, and "the root's end" is undefined. Second, Jaeger's walk needs `ended_at` for every span; a live run,
a crashed child (no `agent.completed`), or a close-before-open span has `ended_at = None` (`spans.py:74`). Third, `derive_spans` never
rejects a cycle (`check_span_relationships` is a separate function the API does not call: `rg check_span_relationships apps/api/src`
finds nothing), so events `A.parent=B, B.parent=A` are accepted and stored; in `buildTree` neither is a root nor an orphan (its parent
exists), so both vanish from the tree silently, or a depth/`aria-level` computation loops. A chain 10,000 deep (one hostile batch) blows
a recursive DFS.
Failure scenario: the demo run from `examples/multi-agent` has three top-level agent spans; the critical path code picks `roots[0]`
(the planner), ignores the reviewer that finished last, and highlights the wrong branch; a VIEWER opens a run whose child crashed and the
walk throws on `ended_at === null`; a hostile client sends a two-span cycle and the waterfall renders an empty tree with "3,900 spans".
Suggested plan change: (a) define a synthetic run root (start = min start, end = run `completed_at` or "now"/`last_event_at` for a live
run) whose children are the forest's roots; (b) define the end of an unfinished span for the walk and the bar (`last_event_at` of the
run, drawn open-ended and flagged `unfinished`); (c) state the cycle rule (break the cycle at the member with the smallest span id, make
it a root flagged `cycle`), make the walks iterative, cap indent depth (e.g. 32, deeper rows show the numeric level); (d) add golden
cases for forest, open span and cycle to `spans-golden.json` and vitest, and three mutation checks ("pick `roots[0]`", "throw on null
end", "drop cycle members").

**F2. Decision 4 and ADR-070 rest on a false claim: child agents already appear in the cost-by-agent analytics.**
Plan: "Child agents' model calls stay attributed to the run's root agent in the agent rollups (ADR-043 keys by run)". Code: the cost
rollup keys on the **cost line's** `agent_slug`, which is the event's `agent_id` (`cost/engine.py:118` `agent_id=event.agent_id`;
`cost/repository.py:165` `"agent_slug": ln.agent_id`; `analytics/rollup.py:118-121` `cost_select` groups by `L.agent_slug`;
`analytics/postgres.py:413,449-452` `by_agent` comes from that). Only the *run* rollups key on `runs.agent_slug`
(`rollup.py:77-108`). After this phase an `llm.request.completed` emitted inside `run.agent("researcher")` carries
`agent_id="researcher"`, so `/v1/analytics/cost.by_agent` gains a "researcher" row while `/v1/analytics/summary.active_agents`
(`postgres.py:293-322`, from `runs.agent_slug`) does not list it, and the run-count/success rollups stay under the root agent. The two
reports disagree about who exists, and the "per-agent rollups when a user asks" KI the plan files would describe a feature half of which
already happens. There is no test for it (the analytics tests predate per-event agent ids).
Suggested plan change: rewrite decision 4 and ADR-070's consequence as what the code does (cost by agent per event `agent_id`; runs,
success rates and `active_agents` by the run's root agent), decide whether that split is wanted (recommended: yes, document it in
`api-v1.md` analytics section), add one `test_analytics_api.py` case with a two-agent run asserting both shapes, and add a
`by_agent` row to AC-4. If instead the split is unwanted, the fix is in `CostLine.agent_id` (root agent) and needs its own ADR line.

**F3. `SUMMARY_VERSION = 3` collides with Phase 16 and regresses the analytics page for every existing deployment.**
Plan: step 4 bumps to 3; "Existing runs keep version 2 until re-summarized; the web treats a missing `agents` as no breakdown". Phase 16
bumps to the same 3 (`../phase-16/.../phase-16-search-investigation.md:146,229,312`) and its KI-081 says "search covers runs summarised
at `SUMMARY_VERSION >= 3`". With the agreed merge order 9, 10, 16, 11, runs re-summarised between the two merges carry version 3 **without**
a search document; Phase 16's "rebuild semantics cover the backfill" then silently skip them. Separately, `analytics/rollup.py:104`
counts every run with `0 < summary_version < SUMMARY_VERSION` as `unrebuilt_runs`, and `Analytics.tsx:164-166` renders that number as
"N runs predate cost calculation; breakdowns by model and ..." The day after this phase deploys, every workspace's analytics page says
all of its runs predate cost calculation, which is false (their cost lines are current; only `agents` is missing). Phase 16's own "the
merge takes the higher number" note (`:413`) does not solve either problem because both plans say 3.
Suggested plan change: (a) state in the plan, the `summary.py` comment and the Merge notes: "Phase 9 is 3; the next bump (Phase 16) must
be 4, and KI-081 must say `>= 4`"; (b) decide what `unrebuilt_runs` means after this bump: either keep the count but change the
Analytics wording to "N runs were summarised by an older version (per-agent figures missing)" in the same commit, or make the rollup
compare against a separate `COST_VERSION`; add a vitest for the wording; (c) add the analytics page to AC-14's regression list
(`make analytics-e2e` is there, but the message text is not asserted today).

**F4. The multi-agent fixture cannot be expressed with the current fixture builder, and one existing fixture contradicts a faithful port.**
Plan: `multiAgentRun()` with "tester (subprocess, timestamps 1.5 s behind, no `sequence`)". Code: `RunBuilder.add` stamps
`sequence: this.seq` on every event and advances a single clock (`fixtures/builder.ts:38-72`); `build()` hard-codes
`ordering_mode: "sequence"` (`:132`) and derives spans with `parent_span_id: es[0].parent_span_id` and `agent_id: this.agent`
(`:159-166`); `tests/fixtures.test.ts:31` asserts `e.sequence === i + 1` for **every** fixture run; the fixture API pages events in array
order (so a time-ordered run must be stored sorted by time, not by construction order). None of this is in the plan's "fixtures.test.ts
and the dashboard/runs tests updated". Also, `failureRetryRun` closes its root span with the unregistered `agent.failed`
(`fixtures/runs.ts:115`); a faithful port of `derive_spans` (`lookup()` returns `None` -> POINT) leaves that span **unfinished**, so the
failed run's waterfall shows an open root bar with no status, and AC-3's golden parity is exactly what forbids the port from "fixing" it.
Suggested plan change: extend `RunBuilder` with per-event `sequence: null`, `occurred_at` offsets and `agent_id` (already in `Extra`),
sort the built events with the TS `sortEvents` and set `ordering_mode` from `orderingMode()`; relax `fixtures.test.ts:31` to "sequenced
events are 1..n in order"; change `agent.failed` to `agent.completed` with `status: "error"` in `failureRetryRun` (and `fixtures.spec.ts`
if it names the type); list these in step 8.

**F5. `SpanIndex.merge(batch)` has no batch to consume.**
Plan: D5/D6 "applied incrementally as live batches merge"; KI-035 "the span derivation is incremental per merged batch". Code:
`useLiveEvents` returns only the fully merged, re-sorted `events` array (`live.ts:117-121`); the per-frame batch is consumed inside
`onEvents` and never surfaced; `mergeEvents` may re-sort the whole array (`ordering.ts:83`), and canonical order matters to the
derivation (opener = first OPEN, closer = last CLOSE in sorted order: `spans.py:60-61`). `RunDetail` therefore has nothing to pass to
`merge(batch)` without the seen-set rebuild KI-035 describes, and the budget in AC-8 is measured on a static fixture, not on a live
run, so the claim "must not add a second full pass per frame" is unverifiable as written.
Suggested plan change: either expose the fresh events from `useLiveEvents` (return `{events, fresh}` where `fresh` is the last
batch's new ids), or keep a persistent `Map<spanId, events[]>` keyed by event id inside `SpanIndex` and feed it the whole array with an
O(n) id walk per frame (the same cost as `mergeEvents` today) and document that as the budget; add a vitest that merges 20 batches of
500 and asserts equality with a from-scratch derivation (also a mutation target: "skip re-deriving a span whose group changed").

### P2

**F6. Clock skew leaks into everything derived from `ordered[0]` and `min(occurred_at)`, not only `agent_slug`.** `summary.py:97,101,170-171,179`:
`started_at = min(occurred_at)`, `trace_id = ordered[0].trace_id`, `first_event_at`; `RunDetail.tsx:183-186` `t0 = min(occurred_at)`;
`store.py:136-144` seeds the run row from the earliest event in the batch. A child 10 s behind moves the run's `started_at`, `duration_ms`,
the runs-list position, the analytics day bucket and every timeline offset by 10 s, and a forged `trace_id` in a continued run becomes the
run's `trace_id` whenever the child sorts first. The plan fixes `agent_slug` only. Suggested: derive `trace_id` the same way as the root
agent (from `run.started`, fallback first event) in step 4, test it next to the agent test, and list the `started_at`/`t0` consequence
explicitly in the KI the plan files for skew and in `api-v1.md`.

**F7. AC-8's "under 16 ms script time on the CI runner" is a flaky gate.** A wall-clock assertion inside Playwright on a shared GitHub runner
will fail on noisy days and the plan says the number is both "measured" and an acceptance criterion. Suggested: assert the DOM bound
(<= 80 rows) and record the timing in `docs/benchmarks/phase-9-waterfall.md`; if a gate is wanted, make it generous (e.g. 100 ms) and
measured as the median of 5 presses.

**F8. `summary.agents` at 200 entries inflates every runs-list page.** `RunOut.summary` is the full dict in lists and detail
(`runs/service.py:70-88,204-206`; `schemas.py:36`). 50 runs x 200 agents x ~9 fields is on the order of a megabyte per page and is sent to
the dashboard poller every 15 s (`queries.ts:47`). Suggested: bound `agents` at 50 in the summary (fold the rest into `(other)`) and keep
`agent_count` exact, or strip `agents` from list items in `list_runs` (no OpenAPI change: `summary` is untyped) and say so in `api-v1.md`.

**F9. The TypeScript port needs the registry, and the plan does not say where it comes from.** `derive_spans` depends on `lookup()` for
`span_role` and `span_kind` (`spans.py:60-63,82-84`); the web has no copy of the registry (`rg span_role apps/web/src` finds nothing) and
imports nothing from `packages/event-schema` except a comment. A hand-written TS table is a second source of truth that drifts on the
next `add-event-type`. `export.py:120-136` already produces `event_types_document()` with `span_role`/`span_kind`. Suggested: read the
generated `schemas/1.0/event-types.json` (or copy it into `apps/web/src/lib/generated/` under `make schema-check`) and make the golden
include unregistered types, `custom.*`, `agent.failed` and a `span.started` with each kind.

**F10. Hot files missing from "Merge notes" (cross-plan).** Phase 10 edits `fixtures/{runs,api,index}.ts`, `tests/fixtures.test.ts`,
`e2e/fixtures.spec.ts`, `RunDetail.tsx`, `queries.ts`, `Makefile`, `ci.yml`, `api-v1.md` (`../phase-10/...:132-135`); Phase 11 edits
`RunDetail.tsx`, `playwright.config.ts`, `Makefile`, `ci.yml` (`:340-344`); Phase 16 edits `runs/summary.py`, `fixtures/runs.ts`,
`api-v1.md` (`:265-268,416`). Phase 9's list names only `RunDetail.tsx` and `lib/timeline.ts` for the web. Suggested: add
`fixtures/runs.ts`, `fixtures/index.ts`, `tests/fixtures.test.ts`, `e2e/fixtures.spec.ts`, `playwright.config.ts`, `runs/summary.py`,
`docs/architecture/api-v1.md` to the Merge notes with the same "last commit of the step, append-only" rule, and reserve a KI id range
(Phase 16 takes KI-080..083; Phase 9 should name KI-084..086 now so the registers do not collide).

**F11. Delegation to a child that never starts is invisible in the agent tree.** `summary.agents` is built from events' `agent_id`
(plan: API section); an `agent.spawned` with `agent.child_id = "tester"` whose child never emits has no events under "tester", so the
tree (D8, from `summary.agents` joined with spans) omits the agent the plan's D2 rationale promises to show. Suggested: include spawned
agents from `agent.child_id` with `event_count 0` and `state: "spawned"` (bounded by the same 200), and test it.

**F12. Default child slug collides with the root.** `agent_id` defaults to `agent_slug(name)`; `config.py:21-25` returns the default
`"agent"` for a name with no slug characters (e.g. non-Latin text), and `"agent"` is also the SDK's default root agent. A child named
"研究员" becomes agent `agent` delegating to `agent`. Suggested: fallback `f"{parent_agent}-child"` or `agent-<n>`, tested in AC-1's
hostile-argument case.

**F13. The waterfall tooltip and aggregate rows are not bound to `contentText`.** The security note binds labels, but "hover tooltip from a
single portal node" and `aggregate(subtree)` are not listed among the places that must use `describe()`/`contentText` and never raw
`attributes`. The existing leak surface is exactly a tooltip that prints `attributes` (`SECURITY.md:46`). Suggested: name the tooltip
fields (type, agent, kind, status, duration, start offset, `describe(opener)`) and add the tooltip to AC-6's withheld check and to the
mutation list ("render attributes in the tooltip").

**F14. Step 4 ships behaviour the plan calls "D4 fallback already exists" before step 5 pins it, and the rollout note for `cost/rebuild` is
incomplete.** `POST /v1/cost/rebuild` re-summarises (plan risk 4), which after F3 also rewrites `runs.agent_slug` for runs whose first
event was a skewed child; that changes analytics attribution and Phase 10's cost-spike baseline key (`../phase-10/...:67`). Suggested:
state in `api-v1.md` and the ADR that a rebuild may move a run between agents, and keep step 5's summarizer test in step 4's commit so
the version bump and its contract land together.

**F15. `continue_run` must bypass `Run.__init__` and `Run.__exit__` semantics that the plan does not mention.** `Run.__init__` emits
`run.started` unconditionally (`client.py:309`) and `__exit__` calls `end()` which emits `run.completed/failed` (`:447,383-397`); adapters
rely on `run.ended` (`:311-314`). The plan says `end()` becomes a no-op for remote runs and `ended` is never set, so an adapter attached in
the child keeps emitting after the parent run finished (acceptable, but undocumented), and `Run.__init__` needs a constructor path with
no `run.started`. Suggested: a `_Run.__init__(..., remote=False)` branch and a line in ADR-070 that a remote run is never "ended".

**F16. `useSearchParams` needs a Suspense boundary on the run page.** `RunsListPage.tsx:3,23` uses it and its page wraps it in `<Suspense>`
(`app/w/[workspace]/projects/[project]/runs/page.tsx:12-14`); the plan adds `useSearchParams` to `RunDetail` without naming the page file.
Without the boundary `next build` fails the static-prerender check. Suggested: add `app/.../runs/[runId]/page.tsx` to Affected files.

### P3

**F17. Env propagation hazards to document.** `BLACKBOX_TRACE_CONTEXT` is inherited by grandchildren that do not re-export it, so a
grandchild's `continue_run(trace_context_from_env())` parents to the *grandparent's* span; and `env={**os.environ, ...}` passes
`BLACKBOX_API_KEY` to every subprocess, including tools the agent runs. Add both to the SDK README section and the ADR consequences.

**F18. Root-agent rule needs a tie-break.** "the `agent_id` of `run.started`": two `run.started` events (replay with a new event id, or two
emitters) are both accepted; define "the first `run.started` in canonical order", and the fallback for runs created by `POST /v1/runs`
whose events never include `run.started`.

**F19. `multiprocessing` with the `spawn` start method (macOS default) neither forks nor inherits the `Run`.** The D4 fork rule does not
apply; such children must use the env context. One sentence in the README and a test that `os.getpid() != run._pid` is not the only path.

**F20. `derive_spans`/golden parity trap on withheld input.** For a VIEWER the API replaces `shell.command` with `[withheld]`
(`content.py:13,30-35`), so the browser's derived span name is the marker string; the plan's AC-6 expects `[withheld]` but `withheld.ts:7`
renders `WITHHELD_TEXT` ("content withheld for your role"). Pick one wording for labels and assert that one.

**F21. Docs the plan omits.** `docs/architecture/sdk-python.md:22-29` lists env vars and `bind`; `docs/TESTING.md:37-72` lists every E2E
script and CI job. Both need a line (sibling plans update TESTING.md; Phase 9 does not).

**F22. Mutation checks worth adding.** "emit `agent.spawned` without `agent.child_id`" (server `attribute_required`, `event.py:280-286`),
"derive `parent_agent_id` from the spawned event's `agent_id` only", "derive the run's `trace_id` from `ordered[0]`" (F6), "treat an
unregistered closer as CLOSE in the port" (F9), "keep a cycle member out of the tree" (F1).

**F23. AC-2's `os.fork` test and `atexit`.** The forked child must `os._exit` after `flush` as `test_client.py:400-409` does, or the parent's
`atexit` handlers run twice; say so in the test description.

**F24. `agent.spawned` emitted at top level has `span_id = None`.** `AgentSpan.start()` "emits `agent.spawned` ... `span_id` = parent span":
when there is no current span the event has no span and the child `agent.started` has no parent (a root). Fine, but D2's "delegating span"
language should say "when there is one".

**F25. Step 11's adapter change touches `integrations/langgraph` tests that pin SDK `>=0.2.0`** (`integrations/*/pyproject.toml`); if the
nesting fix needs `agent_id=` on spans, bump the pin and say so, as Phase 8 did for `parent=` (`handler.py:293`).

---

## 4. Attack walk-through of the plan's "Security review note"

| Item | Result |
| --- | --- |
| Content leakage through the new views | Labels bound to `contentText` (plan); tooltip and aggregate rows not bound (F13); agent names and `agent.child_id` are metadata by SECURITY.md:69 and KI-078 (VERIFIED `test_content_withheld.py:31`) |
| Hostile agent ids / context strings | `agent_id` is server-validated (`event.py:115`), so the 64-char truncation applies to `agent.child_id` (4,096) only; `TraceContext.parse` bounds are adequate as described; nothing new reaches the API |
| Writing into another run | Pre-existing capability of a project key; `RUN_PROJECT_MISMATCH` per event (`store.py:147-152`); the context carries no secret. New: the forged `trace_id` path (F6) and the env inheritance (F17) |
| Unbounded growth | `summary.agents` bounded (but see F8); `run.agent(name)` makes minting agent rows per call trivial (`store.py:124-130`), which is KI-018 as the plan says; the `(other)` fold exists only in rollups, not in `agents`/`agent_versions` tables |
| Payload gating | `Redactor.apply` drops payloads outside FULL (`redaction.py:281-287`); `input`/`set_result` go through `_emit`'s payload path, so inherited for free (VERIFIED by reading) |
| No new credential surface | Correct: no route, no header the API reads, OpenAPI untouched (`summary` is `dict[str, Any]`, `schemas.py:36`) |
| Logs | The SDK must not log the context value (plan says so); no API change |

## 5. Cross-plan items (merge order 9, 10, 16, 11)

1. **`SUMMARY_VERSION`**: Phase 9 = 3 (step 4). Phase 16 must change its constant, its comment, its AC-8/AC-11 lines and KI-081 to **4**
   (F3). Phase 10 leaves it at 2 and is unaffected. Phase 9's plan must say "next bump is 4" so the Phase 16 implementer sees it on rebase.
2. **Route counts / OpenAPI**: Phase 9 adds no operation (36 stays; `test_openapi.py:55`). After Phase 10: 37; after Phase 16: 37
   (recommended decision 1) or 38 (its alternative); Phase 11's "36 -> 50" (`../phase-11/...:296,353`) must be rebased to 51/52. Nothing for
   Phase 9 to change; record the sequence in its Merge notes so the numbers are in one place.
3. **Migrations**: Phase 9 has none; the alembic head stays 0047 after it merges, so Phase 10's `0060 (down_revision "0047")` merges
   unchanged, Phase 16 sets `0080.down_revision = "0060"`, Phase 11 sets `0070.down_revision = "0080"`. Phase 9's reserved 0050-0059 stay
   free; its Merge notes already say this correctly.
4. **Shared hot files** Phase 9 must declare and keep append-only: `apps/api/src/abb_api/runs/summary.py` (9, 16), `docs/architecture/api-v1.md`
   (all four), `apps/web/src/components/RunDetail.tsx` (9, 10, 11), `apps/web/src/fixtures/{runs,index}.ts`, `tests/fixtures.test.ts`,
   `e2e/fixtures.spec.ts` (9, 10, 16), `playwright.config.ts` (9, 11), `Makefile` and `.github/workflows/ci.yml` (9, 10, 11),
   `scripts/quality.sh` (9 only), `docs/{KNOWN_ISSUES,DECISIONS,PROJECT_STATE,IMPLEMENTATION_PLAN,TESTING}.md`, `README.md`, `ARCHITECTURE.md`
   (all). ADR ids do not collide (9: 070/071; 10: 080/081; 11: 090-092; 16: 100/101). KI ids: Phase 16 claims KI-080..083; Phase 9 should
   claim KI-084..086 explicitly (F10).
5. **Analytics attribution** (F2) touches Phase 10's cost-spike baseline, which keys on `runs.agent_slug` (`../phase-10/...:67`): the root-agent
   fix in step 4 is what makes that key stable under skew, so Phase 9 should merge before Phase 10 (as planned) and say why.

## 6. Disposition requested

- Fix F1-F5 in the plan before step 1 (F1, F4, F5 change the web design; F2, F3 change ADR-070's text and the API step).
- F6-F16 are plan edits of a few lines each; F17-F25 are optional.
- The decisions list (1-5) stands; decision 4's wording must change per F2 whatever the user chooses.
