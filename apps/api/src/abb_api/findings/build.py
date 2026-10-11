"""Assembling a `DetectorContext` from events: the one place events are ordered and folded.

Status, completion and counters are folded from the loaded events with the same rules as
`derive_run`, so the facts and the events a detector sees are one snapshot (ADR-080 (1)); only the
cost figures and the baseline come from outside the event set.
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from abb_event_schema.enums import EventStatus, RunStatus, SpanKind, can_transition
from abb_event_schema.event import Event
from abb_event_schema.ordering import sort_events
from abb_event_schema.spans import Span, derive_spans

from abb_api.findings.model import CostBaseline, DetectorConfig, DetectorContext, RunFacts
from abb_api.findings.tokens import is_error
from abb_api.runs.summary import _status_after


def fold_facts(
    ordered: Sequence[Event],
    spans: Mapping[str, Span],
    *,
    cost_usd: Decimal = Decimal(0),
    retry_cost_usd: Decimal = Decimal(0),
) -> RunFacts:
    """Facts from canonically ordered, non-empty events (the rules of `derive_run`)."""
    status = RunStatus.RUNNING
    terminal: Event | None = None
    for event in ordered:
        requested = _status_after(event)
        if requested is not None and requested is not status and can_transition(status, requested):
            status = requested
            terminal = event if status.is_terminal else None
    retry_count = retries_unattributed = error_count = 0
    for event in ordered:
        if event.event_type == "retry.attempted":
            retry_count += 1
            if event.span_id is None:
                retries_unattributed += 1
        if is_error(event):
            error_count += 1
    tool_spans = [s for s in spans.values() if s.status is not None and s.kind is SpanKind.TOOL]
    tool_spans_ok = sum(1 for s in tool_spans if s.status is EventStatus.SUCCESS)
    return RunFacts(
        status=status,
        terminal_event_id=terminal.event_id if terminal is not None else None,
        started_at=min(e.occurred_at for e in ordered),
        event_count=len(ordered),
        error_count=error_count,
        retry_count=retry_count,
        retries_unattributed=retries_unattributed,
        tool_spans_finished=len(tool_spans),
        tool_spans_ok=tool_spans_ok,
        ordering_mode="sequence" if all(e.sequence is not None for e in ordered) else "time",
        agent_slug=ordered[0].agent_id,
        cost_usd=cost_usd,
        retry_cost_usd=retry_cost_usd,
    )


def build_context(
    events: Iterable[Event],
    *,
    cost_usd: Decimal = Decimal(0),
    retry_cost_usd: Decimal = Decimal(0),
    baseline: CostBaseline | None = None,
    config: DetectorConfig | None = None,
) -> DetectorContext:
    ordered = tuple(sort_events(events))
    if not ordered:
        raise ValueError("cannot analyse a run without events")
    spans = derive_spans(ordered)
    facts = fold_facts(ordered, spans, cost_usd=cost_usd, retry_cost_usd=retry_cost_usd)
    return DetectorContext(
        events=ordered,
        spans=spans,
        facts=facts,
        config=config or DetectorConfig(),
        baseline=baseline,
        event_ids=frozenset(e.event_id for e in ordered),
        position={e.event_id: i for i, e in enumerate(ordered)},
    )


def make_baseline(
    rows: Sequence[tuple[datetime, Decimal]],
    *,
    agent_slug: str,
    window_days: int,
    min_runs: int,
) -> CostBaseline | None:
    """Median and nearest-rank p95 of the baseline runs' costs, or None below `min_runs`.

    `rows` are (started_at, cost_usd) of the same project and agent's finished, summarised runs
    (the runs module selects them: newest 200 within the window).
    """
    if len(rows) < max(1, min_runs):
        return None
    costs = sorted(cost for _, cost in rows)
    n = len(costs)
    middle = n // 2
    median = costs[middle] if n % 2 else (costs[middle - 1] + costs[middle]) / 2
    p95 = costs[max(1, math.ceil(n * 0.95)) - 1]  # nearest rank
    return CostBaseline(
        n=n,
        median_usd=median,
        p95_usd=p95,
        window_days=window_days,
        newest_started_at=max(started for started, _ in rows),
        agent_slug=agent_slug,
    )
