"""Spans derived from events (spec §16, §65.3, plan decision D6).

Spans are never sent on their own: a `*.started` event opens one, `*.completed`/`*.failed`
closes it, and point events attach to it. Because arrival order is arbitrary, a child can
arrive before its parent and a close before its open; neither is an error. Only contradictions
(two parents, a cycle, one span id in two traces) are reported.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from abb_event_schema.enums import EventStatus, SpanKind
from abb_event_schema.event import Event, EventIn
from abb_event_schema.ordering import sort_events
from abb_event_schema.registry import SpanRole, lookup


@dataclass(frozen=True)
class Span:
    span_id: str
    run_id: str
    trace_id: str
    parent_span_id: str | None
    agent_id: str
    name: str | None
    kind: SpanKind | None
    started_at: datetime | None
    ended_at: datetime | None
    status: EventStatus | None
    duration_ms: float | None
    event_count: int


@dataclass(frozen=True)
class SpanIssue:
    code: str
    span_id: str
    detail: str


def _display_name(event: Event | EventIn) -> str | None:
    attrs = event.attributes
    for key in ("span.name", "tool.name", "shell.command", "llm.model", "file.path"):
        value = attrs.get(key)
        if isinstance(value, str):
            return value
    return None


def derive_spans(events: Iterable[Event | EventIn]) -> dict[str, Span]:
    """Build spans from whatever events have arrived so far (partial data is normal)."""
    by_span: dict[str, list[Event | EventIn]] = {}
    for event in sort_events(events):
        if event.span_id is not None:
            by_span.setdefault(event.span_id, []).append(event)

    spans: dict[str, Span] = {}
    for span_id, group in by_span.items():
        opener = next((e for e in group if _role(e) is SpanRole.OPEN), None)
        closer = next((e for e in reversed(group) if _role(e) is SpanRole.CLOSE), None)
        first = group[0]
        spec = lookup((opener or first).event_type)
        parent = next((e.parent_span_id for e in group if e.parent_span_id), None)
        spans[span_id] = Span(
            span_id=span_id,
            run_id=first.run_id,
            trace_id=first.trace_id,
            parent_span_id=parent,
            agent_id=(opener or first).agent_id,
            name=_display_name(opener) if opener else _display_name(first),
            kind=spec.span_kind if spec else None,
            started_at=opener.occurred_at if opener else None,
            ended_at=closer.occurred_at if closer else None,
            status=closer.status if closer else None,
            duration_ms=closer.duration_ms if closer else None,
            event_count=len(group),
        )
    return spans


def _role(event: Event | EventIn) -> SpanRole:
    spec = lookup(event.event_type)
    return spec.span_role if spec else SpanRole.POINT


def check_span_relationships(events: Iterable[Event | EventIn]) -> list[SpanIssue]:
    """Report contradictions in parent/child structure. Orphans and late parents are not issues."""
    issues: list[SpanIssue] = []
    parents: dict[str, str] = {}
    traces: dict[str, tuple[str, str]] = {}  # span_id -> (trace_id, run_id)
    for event in sort_events(events):
        if event.span_id is None:
            continue
        where = (event.trace_id, event.run_id)
        known = traces.setdefault(event.span_id, where)
        if known != where:
            issues.append(
                SpanIssue("span_in_multiple_traces", event.span_id, "Span id used in two traces.")
            )
        if event.parent_span_id is None:
            continue
        existing = parents.setdefault(event.span_id, event.parent_span_id)
        if existing != event.parent_span_id:
            issues.append(
                SpanIssue("conflicting_parent", event.span_id, "Span reported with two parents.")
            )

    reported: set[str] = set()
    for start in parents:
        seen: list[str] = []
        node: str | None = start
        while node is not None and node not in seen:
            seen.append(node)
            node = parents.get(node)
        if node is not None:  # walked back onto a span already on this path: a cycle
            cycle = seen[seen.index(node) :]
            if not reported.intersection(cycle):
                issues.append(SpanIssue("cycle", node, "Span ancestry forms a cycle."))
                reported.update(cycle)
    return issues


def orphan_span_ids(spans: dict[str, Span]) -> set[str]:
    """Spans whose parent has not arrived (yet). Informational: late arrival is expected."""
    return {s.span_id for s in spans.values() if s.parent_span_id and s.parent_span_id not in spans}
