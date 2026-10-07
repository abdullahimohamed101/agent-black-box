"""Deriving run state from events: a pure function (INV-2, spec §78, plan D6-D8).

Everything about a run that is not an event (status, timings, counters, spans, ordering mode) is
computed here from the complete event list, so it can always be rebuilt and never drifts. The
function is order-independent: it sorts events canonically first, so any arrival order, batching
or repetition of the same events yields the same result.

Bump SUMMARY_VERSION when a rule below changes; stored summaries record the version they used.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from abb_event_schema.enums import EventStatus, RunStatus, can_transition
from abb_event_schema.event import Event
from abb_event_schema.ordering import sort_events
from abb_event_schema.spans import Span, derive_spans

SUMMARY_VERSION = 1

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


def _int_attr(event: Event, key: str) -> int:
    value = event.attributes.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _number_attr(event: Event, key: str) -> float:
    value = event.attributes.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def derive_run(events: list[Event]) -> RunDerivation:
    """Derive everything about one run from all of its events. `events` must not be empty."""
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
    first = ordered[0]
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
    llm_calls = tool_calls = retry_count = error_count = 0
    input_tokens = output_tokens = 0
    costs: list[float] = []
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
            costs.append(_number_attr(event, "cost.estimated_usd"))
        if kind in ("tool.call.completed", "tool.call.failed"):
            tool_calls += 1
        if kind == "retry.attempted":
            retry_count += 1
        if event.status in _ERROR_STATUSES or kind.endswith(".failed"):
            error_count += 1  # each event counts once, however it qualifies
        if kind in ("file.created", "file.modified", "file.deleted"):
            path = event.attributes.get("file.path")
            if isinstance(path, str):
                modified_files.add(path)

    summary: dict[str, Any] = {
        "event_count": len(events),
        "duration_ms": duration_ms,
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        # fsum is exact and order-independent, so the total never depends on summation order.
        "estimated_cost_usd": round(math.fsum(costs), 9),
        "retry_count": retry_count,
        "error_count": error_count,
        "files_modified": len(modified_files),
        "models": sorted(models),
        "first_event_at": started_at.isoformat(),
        "last_event_at": max(e.occurred_at for e in events).isoformat(),
    }
    return RunDerivation(
        status=status,
        ordering_mode="sequence" if all(e.sequence is not None for e in events) else "time",
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        trace_id=first.trace_id,
        agent_slug=first.agent_id,
        name=run_name,
        summary=summary,
        spans=derive_spans(events),
    )
