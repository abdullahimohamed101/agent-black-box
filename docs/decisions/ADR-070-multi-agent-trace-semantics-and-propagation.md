# ADR-070: Multi-Agent Trace Semantics and Context Propagation

Status: Accepted
Date: 2026-10-10
Phase: 9

## Context
Spec §84 asks for the delegating parent, the child identity, the input, the result and the timing overlap of cooperating agents; §67.2 for
explicit propagation across threads, processes and sub-agents; §65.1 allows `sequence` to be per emitter. Phase 1 left multi-emitter ordering
open (its risk R1). The envelope already carries `agent_id` per event and the registry already has `agent.started`, `agent.completed` and
`agent.spawned`, so no schema change is needed.

## Decision
- **One run, one trace, many agents.** A child agent is an `agent.*` span with its own `agent_id` (and optional `agent_version`) inside the
  parent's run; events inside it carry that identity. The run's `agent_id` stays the root agent's. The run's root agent and `trace_id` are those of
  the first `run.started` that carries a `sequence` (SDK roots always send one; remote and forked emitters omit it), else the earliest
  `run.started`, else the first event (runs created by `POST /v1/runs`). So a skewed clock, a forged child agent event, a forged child
  `trace_id` or a replayed or sequence-less `run.started` cannot change them (`runs/summary.py`). This is a consistency rule, not
  authentication: a write key of the run's project can already write any event into that project's runs, including a `run.started` with a
  `sequence`; the run-to-project check (`RUN_PROJECT_MISMATCH`) is the only boundary, and it is per project.
- **Delegation is recorded by both sides.** The delegator emits `agent.spawned` (`agent.child_id`, optional payload `{"input": ...}`; in the
  delegating span when there is one); the child emits `agent.started` (`parent_span_id` = the delegating span) and `agent.completed` (status,
  optional payload `{"result": ...}`). Payloads obey the payload mode and the redactor (INV-5). A child slug that would be empty or equal to the
  delegator's becomes `<delegator>-child-<n>`.
- **Propagation.** In process: contextvars (asyncio inherits, threads use `bind`). Across processes: an explicit `TraceContext`
  (`abb1;run=...;trace=...;span=...;agent=...`, at most 256 characters, ids validated by the contract patterns), produced by
  `run.spawn_context()` and consumed by `bb.continue_run()`; the environment variable is `BLACKBOX_TRACE_CONTEXT`, the header name
  `X-Blackbox-Trace-Context` (SDK-side only, the API does not read it). Invalid input yields an inert run and a warning that never echoes the
  value. A continued run never emits `run.*`, is never "ended" (`end()` is a counted no-op, `remote_run_end_ignored`) and parents its spans to the
  context span.
- **Remote emitters omit `sequence`.** A continued run, and a forked child that keeps using its parent's `Run`, send no `sequence`
  (`build_event(sequence=None)`); the run is then ordered by the contract's existing time fallback (`ordering_mode: time`). The waterfall nests by
  parentage, so clock skew cannot un-nest a child (§65.3).
- **Analytics attribution.** Cost by agent follows the event's `agent_id` (ADR-040), so a child's model calls appear under the child; run
  counts, success rates and `active_agents` follow the run's root agent. `summary.agents` (run detail only, at most 50 entries including an
  `(other)` row, `agent_count` exact) is derived server-side; `SUMMARY_VERSION` becomes 3 (the next bump is 4).

## Alternatives
A run per agent linked by `trace_id` (splits cost, the live stream and the page; contradicts §84). A per-emitter sequence namespace (a new field,
a new cursor mode, still no cross-clock total order). W3C `traceparent` as the wire form (its 64-bit parent id cannot carry a 128-bit span ULID;
the mapping belongs to the OpenTelemetry adapter, ADR-007, KI-086). Attributing cost lines to the root agent (hides child cost from analytics;
would amend ADR-040).

## Consequences
A child process needs a key of the same project. Chronological order and the run's `started_at`, `duration_ms`, list position and analytics day
bucket across emitters follow their clocks (KI-085, documented). The context variable is inherited by grandchildren and tools unless
re-exported or removed; `env={**os.environ, ...}` also hands them `BLACKBOX_API_KEY` (give tools a filtered environment); `multiprocessing` with the
`spawn` start method neither forks nor inherits the `Run`, so those children use the environment context. The context string is an address, not a
credential. Per-agent run metrics are not offered (KI-084). A cost rebuild re-derives a run, which may move one whose first event was a skewed
child to its true root agent. Adapters can set `agent_id` on spans when a framework exposes sub-agent identity (KI-061). Revisit when a trace
must span runs, or when the OpenTelemetry adapter lands.
