# Independent security review of the Phase 9 implementation (multi-agent tracing and waterfall)

Reviewed: `feature/phase-9-multi-agent-tracing` at 0d672e9 (14 commits on main c212058), worktree `../abb-worktrees/phase-9`.
Stance: adversarial, against the implemented code, assuming nothing from the plan's closing evidence. Nothing but this file was
written; every mutation below was reverted with `git checkout --` before the next one and the tree is clean apart from this file.

Severity: **P0** critical, **P1** must fix before merge, **P2** should fix, **P3** optional.
Evidence: **VERIFIED** (command run here, or code read at the cited file:line), **UNVERIFIED** (not exercised here), **ASSUMED**.

Summary: no P0. **One P1**: the "pre-existing defect fix" that strips NUL from payloads runs *after* the redactor, so a secret
or a denied key split by a NUL byte leaves the SDK unredacted and is stored, where before this branch the server rejected the
whole event (fail closed). Two P2 (the root-identity claim is overstated for a forged `run.started`; the example's filtered
child environment is a documented security property with no test: that mutation survived). Four P3. Everything else attacked
below held: 12 of 13 mutation checks failed a test as expected; the VIEWER projection is inherited by every label, tooltip,
aggregate row, lane header and aria-label; hostile structure (cycles, self-parent, 10,000-deep chain, NaN) is handled without
recursion; `TraceContext.parse` is bounded and value-free; `RUN_PROJECT_MISMATCH` still guards other projects' keys.

## Scope and method

Read: AGENTS.md invariants, the plan and its review dispositions, ADR-070/071, the Phase 15 security review (viewer rules),
then the code: `packages/sdk-python/src/blackbox/{client,events,propagation,redaction}.py`, `apps/api/src/abb_api/runs/
{summary,service,content}.py`, `ingestion/store.py`, `packages/event-schema/{spans,ordering,registry}.py` and the golden test,
`apps/web/src/lib/{spans,waterfall,agents,useSpans,timeline,withheld}.ts`, `components/{Waterfall,AgentTree,RunDetail,
Timeline,ViewSwitch,Analytics}.tsx`, `fixtures/builder.ts`, the example, `scripts/multiagent-e2e.sh`, `e2e/multiagent.spec.ts`,
the new tests in all four packages, and the sibling plans (read only). Ran: the SDK, event-schema, web and API Phase 9 suites
as baselines (SDK 90 passed, event-schema golden 3 passed, web 107 passed over 6 files, API 148 passed), three probe scripts
from the scratchpad (NUL-split secrets through the SDK; forged `run.started` and `(other)` collision through `derive_run`),
and 13 mutation checks (table at the end). No scratch database was needed: the API tests create their own.

## Verified correct

| Claim | Evidence |
| --- | --- |
| `TraceContext.parse` checks type and length (<= 256) before splitting; ids match the contract's ULID pattern; agent is a slug; duplicate keys, unknown keys, 3..5 parts enforced; returns `None`, logs a fixed string, never the value | `propagation.py:62-77`; `test_propagation.py:70-108` (23 malformed forms incl. 10 MB and NUL; log text < 200 chars, value absent): VERIFIED |
| `continue_run` with an invalid or missing context yields an inert `Run` that emits nothing, including through `span`, `agent`, `event`, `spawn_context` | `client.py:760-782`, `_emit` returns on `_inert` (`:625`); `test_propagation.py:238-250`: VERIFIED |
| A continued run never emits `run.*`, `end()` is a counted no-op, `ended` stays `False`; root spans parent to the context span; no `sequence` | `client.py:396-404,593-598,631`; `test_propagation.py:177-201`: VERIFIED; mutation M-SDK-1 killed |
| A forked child that inherits a `Run` omits `sequence` (`os.getpid() != self._pid`) | `client.py:631`; `test_propagation.py:309-330` (child ends with `os._exit`): VERIFIED |
| `agent.spawned` input and `agent.completed` result go through `Redactor.apply` (payload mode, deny keys, patterns) before `check_payload` | `client.py:357-375,1003-1011`; `test_agents.py:105-140`; mutation M-SDK-2 killed: VERIFIED |
| A child slug equal to the delegator's, or unusable, becomes `<delegator>-child-<n>` within 64 chars | `client.py:360-365,469-472`; mutation M-SDK-3 killed: VERIFIED |
| Hostile `run.agent` arguments (None, bytes, object, list, control chars) never raise and the events validate | `test_agents.py:168-178`: VERIFIED |
| `RUN_PROJECT_MISMATCH` rejects an event whose run belongs to another project of the same workspace (a stolen context plus another project's key) | `store.py:147-152`; mutation M-API-3 killed by `test_an_event_cannot_borrow_a_run_of_another_project`: VERIFIED. A key of another *workspace* cannot address the run at all (owners are looked up per tenant): ASSUMED from the tenant-scoped query, unchanged by this phase |
| Root agent and `trace_id` come from the first `run.started` in canonical order, so a skewed child, a forged child `trace_id` and a *duplicated* `run.started` cannot move them | `summary.py:233-236`; mutation M-API-1 killed by three tests: VERIFIED. Limit in F2 below |
| `summary.agents` is bounded at 50 entries including `(other)`, `agent_count` exact, spawned-only agents listed with `state: "spawned"`; list items omit `agents`; order-independent | `summary.py:103-200`, `service.py:70-75,210`; mutation M-API-2 killed; `test_run_summary.py` agent tests and `test_runs_api.py:836-855`: VERIFIED |
| `_agents` working dicts hold one entry per distinct `agent_id`/`agent.child_id` seen in the run's events (bounded by the run's events, which is KI-022's existing cap problem, not a new one); `agent.child_id` is truncated to 64 characters before it becomes a key | `summary.py:116-136`: VERIFIED |
| Cost by agent keys on the cost line's `agent_id`; run metrics and `active_agents` on the root | `test_analytics_api.py:274-307`: VERIFIED |
| `SUMMARY_VERSION` 3, comment says the next bump is 4; Phase 16's plan says 4 after rebase; Phase 10 does not depend on the value; analytics wording changed and asserted | `summary.py:24-27`; `../phase-16/.../phase-16-search-investigation.md:285,503`; `../phase-10/...:207`; `analytics.test.tsx:55-72`: VERIFIED |
| No OpenAPI, migration, authz matrix or route change | `git diff --stat c212058..HEAD` lists none of `openapi.json`, `schema.d.ts`, `migrations/`, `authz/`: VERIFIED |
| Web: every rendered string in the waterfall comes from `nodeLabel`/`spanLabel` (`contentText` + `describe`), `tooltipFields`, `agentChip` or constants; `rawName` is never imported outside `lib/spans.ts`; React renders text, the aria-label is an attribute | `waterfall.ts:491-530`, `Waterfall.tsx:402,421-425,501,539`; `rg rawName apps/web/src` finds only `spans.ts`: VERIFIED; mutations M-WEB-1 and M-WEB-5 killed |
| A VIEWER-shaped event list shows `WITHHELD_TEXT` and never the command or the `[withheld]` marker in labels, aggregate rows, tooltip, DOM | `waterfall.test.ts:366-424`, `Waterfall.test.tsx:240-280`; the real-API E2E checks rows, tooltip, lanes, timeline and drawer for the tester's command as a VIEWER after proving the OWNER sees it (`multiagent.spec.ts:98-134`): VERIFIED by reading; E2E not re-run here (UNVERIFIED) |
| Typed shell spans carry the command as `shell.command` (withheld), never as `span.name`; `describe()` of a shell/file/git head goes through `contentText`; a generic `span.started` shows `span.name` to a VIEWER, which is KI-078 (unchanged) | `client.py:132-137`, `timeline.ts:103-126`, `waterfall.ts:493-499`, `content.py:14-24`: VERIFIED |
| Lane headers, agent chips and `summary.agents` expose only agent ids (slugs validated by the server) or `agent.child_id` (metadata by contract, Phase 15 `test_content_withheld.py:31`), clipped to 64 chars | `Waterfall.tsx:377-383,495-500`, `AgentTree.tsx:45`: VERIFIED |
| Cycles cut at the smallest id, self-parent survives, 10,000-deep chain walked iteratively with indent capped at 32 and the real level kept, thousands of roots sorted once, NaN timestamps fall back, duplicate span ids deduped | `waterfall.ts:107-128,172-192`, `ms()` `:70-73`, `byId` `:96-97`; mutations M-WEB-2/3/4 killed: VERIFIED |
| Critical path is the last-finishing-child walk from the synthetic root, clamped, iterative; forest and open-span cases tested | `waterfall.ts:441-477`; mutation M-WEB-3 killed five tests: VERIFIED |
| `?view=` accepts only `waterfall`, else timeline; written with `replaceState` on `usePathname()` plus the existing query (URL-encoded by `URLSearchParams`) | `RunDetail.tsx:181-196`; `run-view.test.tsx:41-56`: VERIFIED |
| Only visible rows are mounted (virtualizer, overscan 10); `SpanIndex` holds one map entry per event and span, references the same event objects | `Waterfall.tsx:118-124`, `spans.ts:141-176`; E2E "at most 80 rows" (UNVERIFIED here): VERIFIED by reading |
| No keyboard trap: the treegrid is one tab stop, twisties are `tabIndex=-1`, Escape routes to the page | `Waterfall.tsx:338-348,467-494`: VERIFIED |
| Golden parity is not tautological: the JSON is generated by Python and replayed by TypeScript, each case also reversed; the deep chain is replayed from its generator with first/last span and count asserted | `test_spans_golden.py:1-11`, `spans.test.ts:52-66,134-151`: VERIFIED |
| Known environment fault acknowledged: the Postgres clock offset affects `test_summarizer.py::test_a_burst...` only; not re-run here | plan "Environment observation": UNVERIFIED (env) |

## Findings

### P1

**F1. NUL bytes are stripped from payloads *after* the redactor ran, so a NUL-split secret leaves the process unredacted and is stored.**
`packages/sdk-python/src/blackbox/client.py:1003-1011` applies `self._redactor.apply(event)` and only then `check_payload`, which
now calls `_without_nul` (`events.py:145-157`) on every payload string and key. The redactor's pattern and deny-key rules see
the string with the NUL inside and match nothing; the strip then reassembles the secret. VERIFIED with a probe
(`BlackBox(mode="offline", payload_mode=FULL)`, scratchpad `nul_probe.py`), output of the buffered events:

**Resolution (e81fde0):** NUL is stripped before any redaction (`BlackBox._emit` on the whole built event; `Redactor.redact_string/redact_text/_key_denied`, `secretscan.mask_text/strip_ansi/withhold_sensitive_hunks`, so the coding recorder's `sanitize` sees the joined string); `check_payload` keeps a strip as defence in depth. Artifact names turn NUL into a space before redaction. Tests: `tests/test_secret_nul.py` (AWS, GitHub, Slack, denied key, attributes, tags, nested, agent input/result, coding `sanitize`); 7 of 8 fail without the fix.

```
custom.probe    {"a": "AKIAIOSFODNN7EXAMPLE", "password": "hunter2", "b": "ghp_AAAA...(36)"}
agent.spawned   {"input": {"token": "[REDACTED:token]"}}          <- key without NUL: still redacted
agent.completed {"result": {"r": "xoxb-1234567890abcdef"}}
```

Inputs were `"AKIA\x00IOSFODNN7EXAMPLE"`, key `"pass\x00word"`, `"ghp_\x00AAAA..."`, `"xox\x00b-..."`. On `main` the same
events are rejected by the API (`string_contains_nul`, `abb_event_schema/event.py:56`) and never stored: fail closed. On this
branch they are accepted and persisted; the API has no redactor of its own (`rg redact apps/api/src` finds nothing), so
INV-5's "redact before export" is the only line, and it is bypassed. Captured telemetry is hostile input (AGENTS.md): tool
output and model responses that contain NUL are ordinary (binary files, terminal output), and the regression test added for
this change (`test_contract.py:173-180`) only proves the strip happens, not where in the pipeline.
Attack: any payload the agent captures (`set_payload`, `agent(..., input=)`, `set_result`) that holds a credential with a NUL
in it: the credential is stored in `events.payload` and shown to every actor with `payload.read`; deny-key names with a NUL
(`"pass\x00word"`) also bypass the field rule. Attributes are not stripped, so an attribute with NUL still rejects the event at
the server: the asymmetry means the hole is payload-only.
Fix: strip NUL **before** redaction (in `build_event`, or at the top of `BlackBox._emit` for `attributes`, `tags` and
`payload` alike), keep `check_payload` purely a size/shape check, and add a test with a NUL-split AWS key and a NUL-split denied
key asserting both come out as `[REDACTED:...]`. Alternatively revert the strip (fail closed, as before) and leave the NUL
rejection documented. Security-sensitive per AGENTS.md (redaction): the plan's security note did not list this change.

### P2

**F2. "A skewed or forged child event cannot become the run's agent or trace" is false for a forged `run.started`.**
`summary.py:236` takes the first `run.started` in canonical order. In a time-ordered run (any event without `sequence` puts
the whole run in time order, `ordering.py`), a same-project emitter that sends a sequence-less `run.started` with an earlier
`occurred_at`, agent `evil` and its own `trace_id` becomes the root. VERIFIED by probe (`summary_probe.py` on
`multi_emitter()` plus one such event): `agent_slug: evil`, `trace_id` replaced, `name: None`. This is within what a project
write key could always do (it could set any agent on the first event before this phase), so it is not an escalation; but
ADR-070 (`:15-17`), `api-v1.md` (diff `+11`, `+88`) and the plan's "Why 9 merges before 10" (Phase 10's cost-spike baseline keys
on `runs.agent_slug`) state the stronger property, and the tests only cover a *duplicated* `run.started` (same agent) and
forged child *agent* events. Fix: either (a) reword the three places to "a forged child agent event or a replayed
`run.started`", or (b) cheaply harden: prefer the first `run.started` that carries a `sequence` (only the root emitter has
one under D4; a continued run never sends it), falling back to any `run.started`, then the first event; add the probe as a
test either way.

**Resolution (f26b11e):** `derive_run` takes the first `run.started` carrying a `sequence`, else the earliest `run.started`; ADR-070, `api-v1.md` and the plan now say it is consistency, not authentication (a same-project key can still write a `run.started` with a `sequence`). Tests: `test_a_sequenceless_earlier_run_started_cannot_displace_the_root` (fails without the fix) and a fallback test.

**F3. The example's filtered child environment, which the plan and README present as the mitigation for handing
`BLACKBOX_API_KEY` to tools, has no test: the mutation survived.** Replacing
`env = {k: os.environ[k] for k in _CHILD_ENV_KEYS if k in os.environ}` with `env = dict(os.environ)` in
`examples/multi-agent/multi_agent/workers.py:60` leaves all 8 example tests green (VERIFIED, M-EX-1). The property is cited
by D3/D11, the security note ("tools get a filtered environment"), ADR-070 and both READMEs. Fix: in `test_example.py` plant a
variable (`FAKE_SECRET=1`) and `BLACKBOX_TRACE_CONTEXT=garbage` in the parent environment and assert, through a unit test of
`child_environment`, that the child env equals the allowlist plus the fresh context and nothing else.

**Resolution (30fee27):** `tests/test_example.py` spies on `subprocess.run` and asserts the child's environment lacks a planted `SOME_SECRET_TOKEN` and is a subset of the documented variables; a unit test pins `child_environment`. With `env = dict(os.environ)` both fail.

### P3

**F4. A client child id of `(other)` collides with the fold row.** `summary.py:124-129` keys tallies by the client's
`agent.child_id` and `:181` names the fold row `OTHER_AGENTS = "(other)"`. VERIFIED by probe: with 60 agents and one
`agent.spawned` naming `(other)`, `summary.agents` has two entries with `agent_id == "(other)"`; `AgentTree.tsx:37` uses
`agentId` as the React key (duplicate-key warning, one row dropped by React's reconciliation), and the real agent is
indistinguishable from the fold. Fix: mark the fold row (`folded: true`) and render it from that flag, or map a client id
equal to `OTHER_AGENTS` to a different label.

**Resolution (8d201df):** a client `agent.child_id` equal to `(other)` is listed as `(other) (client id)`; the fold label is reserved and agent ids are unique, so the React key is unique. Test `test_a_client_child_id_of_other_does_not_collide_with_the_fold_row` fails without the fix.

**F5. `spawn_context` on an inert continued run hands the child a context for a run that does not exist.** `continue_run` with
an invalid context builds a `Run` with fresh random ids (`client.py:777-781`) and `spawn_context` on it (inert: emits nothing)
still returns `TraceContext(self.run_id, ...)` (`:496`, exercised by `test_propagation.py:248`). A grandchild with a valid key
then writes a phantom run (no `run.started`, created by `store.py` from its first event) into the project. Fix: return `""`
from `spawn_context`/`trace_context` when `_inert` (the child parses it to `None` and is inert too), and count it.

**Resolution (6a424b6):** `spawn_context` and `trace_context` return `""` on an inert run (the child parses it to `None` and is inert too). Test `test_an_inert_continued_run_hands_out_no_context`.

**F6. `spawn_context(parent=...)`, `run.agent(parent=...)` and `Span(parent=...)` accept a `Span` of another `Run`.**
`_delegating_span` (`client.py:513-517`) checks only `isinstance(parent, Span)`; the implicit path checks `current.run is
self`. The child then parents to a span of another run/trace (an orphan to the server; `check_span_relationships` is not
called). Programmer error rather than attack, but the SDK's contract is "arguments are coerced, never raised on". Fix: treat a
foreign-run parent as `None`.

**Resolution (6a424b6):** a parent `Span` of another `Run` is ignored (debug log, never raised) in `Span.__init__` and `_delegating_span`. Test `test_a_parent_span_of_another_run_is_ignored_not_adopted`.

**F7. E2E viewer assertions key on one string.** `multiagent.spec.ts:98-134` proves the tester's command never appears to a
VIEWER (after proving the OWNER sees it, so the check is not vacuous) but does not assert that the `[withheld]` marker never
renders, nor that the drawer shows the payload as withheld. Add `expect(everything()).not.toContain("[withheld]")` and a
`payload_withheld` check in the viewer drawer test. Optional.

**Resolution (ff3d277):** the viewer E2E asserts `[withheld]` never renders and walks every timeline event's drawer, requiring at least one `payload-withheld` notice and no command or marker. `scripts/multiagent-e2e.sh` passed (6 Playwright tests). Not mutation-proved.

### Dropped (checked, not supportable as findings)

- `Makefile`, `.github/workflows/ci.yml`, `README.md`, `ARCHITECTURE.md`, `.prettierignore` changes exist (the diff stat I
  first read was truncated); `make schema-check` compares the registry copy with `cmp` (`Makefile:79-83`).
- `summary.agents` reaches a VIEWER: it holds ids and counts only; no stream frame carries a summary (`rg summary
  apps/api/src/abb_api/streaming` finds only a docstring).
- Threads/asyncio: `Run.__enter__/__exit__` set and reset both contextvars; `bind()` copies; tested across a thread and a task
  (`test_propagation.py:275-306`).
- The forged second `run.started` *with* a sequence sorts after the real one under sequence ordering: cannot win.

## Mutation checks

Each applied with `sed`, run, then `git checkout -- <file>`; tree verified clean after each package.

| # | Mutation (file) | Expected failing test | Result |
| --- | --- | --- | --- |
| M-API-1 | `root = ordered[0]` (`runs/summary.py`) | skewed child / forged trace / duplicated `run.started` | **killed**: 3 failed (`test_a_skewed_child_never_becomes_the_runs_agent_or_trace`, `..._forged_child_trace_id_...`, `..._duplicated_run_started_...`) |
| M-API-2 | `MAX_AGENTS = 100000` | 50-bound fold test | **killed**: `test_more_than_50_agents_fold_into_one_other_row_with_an_exact_count` |
| M-API-3 | drop the project check (`if False:`) in `ingestion/store.py:149` | project-mismatch tests | **killed**: `test_an_event_cannot_borrow_a_run_of_another_project` |
| M-SDK-1 | continued/forked run emits `sequence` (`client.py:631`) | propagation tests | **killed**: 3 failed (continued run, forked child, real subprocess) |
| M-SDK-2 | redactor keeps payloads outside FULL (`redaction.py`) | payload-mode test | **killed**: `test_input_and_result_are_payloads_only_in_full_mode` |
| M-SDK-3 | child may share the delegator's slug (`client.py:360`) | slug tests | **killed**: 3 failed (`test_child_slugs_are_coerced...` x2, `test_fallback_slugs_count_per_run...`) |
| M-EX-1 | example forwards the whole `os.environ` to the subprocess (`workers.py:60`) | an environment-filter test | **SURVIVED**: 8 passed (F3) |
| M-WEB-1 | `spanLabel` reads raw `attributes["shell.command"]` (`waterfall.ts:495`) | viewer label tests | **killed**: 2 failed (`waterfall.test.ts` viewer, `Waterfall.test.tsx` viewer) |
| M-WEB-2 | never cut a cycle (`waterfall.ts:125`) | cycle tests | **killed**: 2 failed (cycle at smallest id; self-parent and two-span cycle) |
| M-WEB-3 | critical path takes the earliest-finishing child (`waterfall.ts:453`) | forest/fan-out/chain/open tests | **killed**: 5 failed incl. "starts at the last finisher of a forest, not at the first root" |
| M-WEB-4 | drop the indent cap (`waterfall.ts:290`) | deep-chain test | **killed**: "walks a 10,000-deep chain iteratively, caps the indent and keeps the real level" |
| M-WEB-5 | tooltip "Span" field from `rawName` (`waterfall.ts:509`) | viewer tooltip tests | **killed**: `Waterfall.test.tsx` viewer tooltip (the pure `waterfall.test.ts` case alone would not have caught it because its fixture carries a visible `span.name`; the component test did) |
| probe | NUL-split secrets through `BlackBox._emit` | (no test exists) | **bypass confirmed** (F1) |

## Disposition requested

- F1 before merge (redaction order, with the two-case regression test; `review-change` pass on `events.py`/`client.py`).
- F2 and F3 are small: a wording fix plus one test each; F2's option (b) is a two-line hardening worth taking while the tests
  are open.
- F4-F7 optional; file F4 and F5 as KIs if not fixed now.
