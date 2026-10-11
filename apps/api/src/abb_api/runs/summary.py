"""Deriving run state from events: a pure function (INV-2, spec §78, plan D6-D8).

Everything about a run that is not an event (status, timings, counters, spans, ordering mode) is
computed here from the complete event list, so it can always be rebuilt and never drifts. The
function is order-independent: it sorts events canonically first, so any arrival order, batching
or repetition of the same events yields the same result.

Bump SUMMARY_VERSION when a rule below changes; stored summaries record the version they used.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from abb_event_schema.enums import EventStatus, RunStatus, SpanKind, can_transition
from abb_event_schema.event import Event
from abb_event_schema.ordering import sort_events
from abb_event_schema.spans import Span, derive_spans

from abb_api.cost.builtin import builtin_price_book
from abb_api.cost.engine import CostEngine, CostLine, cost_summary
from abb_api.cost.retries import retry_call_ids

# 2: cost comes from the CostEngine (ADR-040) and carries retry attribution (ADR-042).
# 3: per-agent breakdown (`agents`, `agent_count`) and the run's agent and trace id come from
#    `run.started` instead of the first event (Phase 9, ADR-070). The next bump is 4 (Phase 16).
SUMMARY_VERSION = 3

MAX_AGENTS = 50  # entries in `summary.agents`; more agents fold into one "(other)" row (KI-018)
OTHER_AGENTS = "(other)"

_ERROR_STATUSES = {EventStatus.ERROR, EventStatus.TIMEOUT}
_RUN_COMPLETED_STATUS = {
    None: RunStatus.SUCCESS,
    EventStatus.SUCCESS: RunStatus.SUCCESS,
    EventStatus.ERROR: RunStatus.FAILED,
    EventStatus.TIMEOUT: RunStatus.TIMED_OUT,
    EventStatus.BLOCKED: RunStatus.BLOCKED,
    EventStatus.CANCELLED: RunStatus.CANCELLED,
}


@dataclass(frozen=True)
class RunDerivation:
    status: RunStatus
    ordering_mode: str  # "sequence" when every event carries a sequence, else "time"
    started_at: datetime
    completed_at: datetime | None
    duration_ms: float | None
    trace_id: str
    agent_slug: str
    name: str | None
    summary: dict[str, Any]
    spans: dict[str, Span]
    cost_lines: tuple[CostLine, ...] = ()


def _status_after(event: Event) -> RunStatus | None:
    """The status a lifecycle event asks for, or None if it does not concern run status."""
    kind = event.event_type
    if kind == "run.started":
        return RunStatus.RUNNING
    if kind == "run.completed":
        return _RUN_COMPLETED_STATUS.get(event.status, RunStatus.SUCCESS)
    if kind == "run.failed":
        return RunStatus.FAILED
    if kind == "run.cancelled":
        return RunStatus.CANCELLED
    if kind == "approval.requested":
        return RunStatus.WAITING_FOR_APPROVAL
    if kind in ("approval.granted", "approval.denied"):
        return RunStatus.RUNNING
    return None


@dataclass
class _AgentTally:
    agent_id: str
    parent_agent_id: str | None = None
    event_count: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    error_count: int = 0
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None


def _agent_state(
    agent_id: str, tally: _AgentTally, agent_spans: list[Span], root: str, status: RunStatus
) -> str:
    if tally.event_count == 0:
        return "spawned"  # named by an `agent.spawned` and never heard from (D8)
    if agent_spans:
        last = agent_spans[-1]
        if last.ended_at is None:
            return "started"
        return "failed" if last.status in _ERROR_STATUSES else "completed"
    if agent_id == root and status.is_terminal:
        return "completed" if status is RunStatus.SUCCESS else "failed"
    return "started"


def _agents(
    ordered: list[Event],
    spans: dict[str, Span],
    cost_lines: tuple[CostLine, ...],
    root: str,
    status: RunStatus,
) -> tuple[list[dict[str, Any]], int]:
    """The per-agent breakdown, in order of first appearance, bounded at MAX_AGENTS entries.

    Returns (entries, exact agent count). Each agent's events are those carrying its `agent_id`
    (so a child's model calls are the child's, ADR-040); an agent only named by `agent.spawned`
    is listed with no events.
    """
    tallies: dict[str, _AgentTally] = {}
    spawned_by: dict[str, str] = {}  # child -> the first agent that spawned it
    openers: dict[str, Event] = {}  # agent -> its first agent.started
    for event in ordered:
        tally = tallies.get(event.agent_id)
        if tally is None:
            tally = tallies[event.agent_id] = _AgentTally(event.agent_id)
        tally.event_count += 1
        tally.first_event_at = tally.first_event_at or event.occurred_at
        tally.last_event_at = (
            event.occurred_at
            if tally.last_event_at is None
            else max(tally.last_event_at, event.occurred_at)
        )
        kind = event.event_type
        if kind in ("llm.request.completed", "llm.request.failed"):
            tally.llm_calls += 1
        if kind in ("tool.call.completed", "tool.call.failed"):
            tally.tool_calls += 1
        if event.status in _ERROR_STATUSES or kind.endswith(".failed"):
            tally.error_count += 1
        if kind == "agent.started":
            openers.setdefault(event.agent_id, event)
        elif kind == "agent.spawned":
            child = event.attributes.get("agent.child_id")
            if isinstance(child, str) and child:
                child = child[:64]  # a label by contract; never a key format (KI-078)
                spawned_by.setdefault(child, event.agent_id)
                if child not in tallies:
                    tallies[child] = _AgentTally(child)

    for agent_id, tally in tallies.items():
        opener = openers.get(agent_id)
        parent_span = spans.get(opener.parent_span_id) if opener and opener.parent_span_id else None
        parent = parent_span.agent_id if parent_span else None
        if parent is None or parent == agent_id:
            parent = spawned_by.get(agent_id)
        tally.parent_agent_id = None if parent == agent_id or agent_id == root else parent

    cost_by_agent: dict[str, list[CostLine]] = {}
    for line in cost_lines:
        cost_by_agent.setdefault(line.agent_id, []).append(line)
    spans_by_agent: dict[str, list[Span]] = {}
    for span in spans.values():
        if span.kind is SpanKind.AGENT and span.started_at is not None:
            spans_by_agent.setdefault(span.agent_id, []).append(span)
    for group in spans_by_agent.values():
        group.sort(key=lambda sp: (sp.started_at, sp.span_id))

    def entry(tally: _AgentTally) -> dict[str, Any]:
        lines = cost_by_agent.get(tally.agent_id, [])
        return {
            "agent_id": tally.agent_id,
            "parent_agent_id": tally.parent_agent_id,
            "state": _agent_state(
                tally.agent_id, tally, spans_by_agent.get(tally.agent_id, []), root, status
            ),
            "event_count": tally.event_count,
            "llm_calls": tally.llm_calls,
            "tool_calls": tally.tool_calls,
            "error_count": tally.error_count,
            "estimated_cost_usd": cost_summary(lines)["estimated_cost_usd"],
            "first_event_at": tally.first_event_at.isoformat() if tally.first_event_at else None,
            "last_event_at": tally.last_event_at.isoformat() if tally.last_event_at else None,
        }

    ordered_tallies = list(tallies.values())
    if len(ordered_tallies) <= MAX_AGENTS:
        return [entry(t) for t in ordered_tallies], len(ordered_tallies)
    kept, rest = ordered_tallies[: MAX_AGENTS - 1], ordered_tallies[MAX_AGENTS - 1 :]
    rest_lines = [ln for t in rest for ln in cost_by_agent.get(t.agent_id, [])]
    firsts = [t.first_event_at for t in rest if t.first_event_at]
    lasts = [t.last_event_at for t in rest if t.last_event_at]
    folded = {
        "agent_id": OTHER_AGENTS,
        "parent_agent_id": None,
        "state": "mixed",
        "event_count": sum(t.event_count for t in rest),
        "llm_calls": sum(t.llm_calls for t in rest),
        "tool_calls": sum(t.tool_calls for t in rest),
        "error_count": sum(t.error_count for t in rest),
        "estimated_cost_usd": cost_summary(rest_lines)["estimated_cost_usd"],
        "first_event_at": min(firsts).isoformat() if firsts else None,
        "last_event_at": max(lasts).isoformat() if lasts else None,
    }
    return [*(entry(t) for t in kept), folded], len(ordered_tallies)


def _int_attr(event: Event, key: str) -> int:
    value = event.attributes.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def derive_run(events: list[Event], engine: CostEngine | None = None) -> RunDerivation:
    """Derive everything about one run from all of its events. `events` must not be empty.

    `engine` prices the run's model calls (built-in table when omitted); overrides
    arrive through it.
    """
    if not events:
        raise ValueError("cannot derive a run from no events")
    ordered = sort_events(events)

    status = RunStatus.RUNNING
    completed_at: datetime | None = None
    for event in ordered:
        requested = _status_after(event)
        # Invalid transitions (for example anything after a terminal state) are ignored: late or
        # contradictory events must not fail the run's derivation.
        if requested is not None and requested is not status and can_transition(status, requested):
            status = requested
            completed_at = event.occurred_at if status.is_terminal else None

    started_at = min(e.occurred_at for e in events)
    duration_ms = (
        (completed_at - started_at).total_seconds() * 1000 if completed_at is not None else None
    )
    # The run's agent and trace come from the root emitter's `run.started`: the first one that
    # carries a `sequence` (SDK roots always send one; remote and forked emitters omit it, so a
    # sequence-less `run.started` cannot displace the root), else the earliest `run.started`. A
    # skewed or forged child event can sort first or carry another trace id, and must not become
    # the run's. This is a consistency rule, not authentication: a same-project write key can
    # already write any event into that project's runs. Runs created through POST /v1/runs have
    # no run.started: the first event stands in.
    starts = [e for e in ordered if e.event_type == "run.started"]
    root = next((e for e in starts if e.sequence is not None), starts[0] if starts else ordered[0])
    run_name = next(
        (
            e.attributes["run.name"]
            for e in ordered
            if e.event_type == "run.started" and isinstance(e.attributes.get("run.name"), str)
        ),
        None,
    )

    models: set[str] = set()
    modified_files: set[str] = set()
    llm_calls = tool_calls = retry_count = error_count = retries_unattributed = 0
    input_tokens = output_tokens = 0
    cost_events: list[Event] = []
    for event in ordered:
        kind = event.event_type
        if kind in ("llm.request.completed", "llm.request.failed"):
            llm_calls += 1
            model = event.attributes.get("llm.model")
            if isinstance(model, str):
                models.add(model)
        if kind == "llm.request.completed":
            input_tokens += _int_attr(event, "llm.input_tokens")
            output_tokens += _int_attr(event, "llm.output_tokens")
            cost_events.append(event)
        if kind in ("tool.call.completed", "tool.call.failed"):
            tool_calls += 1
        if kind == "retry.attempted":
            retry_count += 1
            if event.span_id is None:
                retries_unattributed += (
                    1  # names no operation, so no cost can be attributed (ADR-042)
                )
        if event.status in _ERROR_STATUSES or kind.endswith(".failed"):
            error_count += 1  # each event counts once, however it qualifies
        if kind in ("file.created", "file.modified", "file.deleted"):
            path = event.attributes.get("file.path")
            if isinstance(path, str):
                modified_files.add(path)

    spans = derive_spans(events)
    pricing = engine or CostEngine(builtin_price_book())
    retried = retry_call_ids(ordered, spans)
    cost_lines = tuple(pricing.calculate(e).with_retry(e.event_id in retried) for e in cost_events)

    # Finished tool/model spans: the dashboard's success rates come from here, not from `spans`.
    finished = [s for s in spans.values() if s.status is not None]
    tool_spans = [s for s in finished if s.kind is SpanKind.TOOL]
    llm_spans = [s for s in finished if s.kind is SpanKind.LLM]

    agents, agent_count = _agents(ordered, spans, cost_lines, root.agent_id, status)
    summary: dict[str, Any] = {
        "event_count": len(events),
        "duration_ms": duration_ms,
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        # Decimal sums are exact and order-independent: the total never depends on summation order.
        **cost_summary(list(cost_lines)),
        "tool_spans_finished": len(tool_spans),
        "tool_spans_ok": sum(1 for s in tool_spans if s.status is EventStatus.SUCCESS),
        "llm_spans_finished": len(llm_spans),
        "llm_spans_ok": sum(1 for s in llm_spans if s.status is EventStatus.SUCCESS),
        "retry_count": retry_count,
        "retries_unattributed": retries_unattributed,
        "error_count": error_count,
        "files_modified": len(modified_files),
        "models": sorted(models),
        "agents": agents,
        "agent_count": agent_count,
        "first_event_at": started_at.isoformat(),
        "last_event_at": max(e.occurred_at for e in events).isoformat(),
    }
    return RunDerivation(
        status=status,
        ordering_mode="sequence" if all(e.sequence is not None for e in events) else "time",
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        trace_id=root.trace_id,
        agent_slug=root.agent_id,
        name=run_name,
        summary=summary,
        spans=spans,
        cost_lines=cost_lines,
    )
