import random
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from abb_event_schema.dedup import content_hash, dedupe
from abb_event_schema.enums import EventStatus, SpanKind
from abb_event_schema.event import EventIn, finalize
from abb_event_schema.ordering import sort_events
from abb_event_schema.parse import parse_event_in
from abb_event_schema.spans import check_span_relationships, derive_spans, orphan_span_ids
from tests.helpers import PRJ, WS, evt, make_event, spn

T0 = datetime(2026, 10, 6, 20, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def ev(n: int, *, seq: Any = ..., t: float = 0, **extra: Any) -> EventIn:
    return parse_event_in(
        make_event(
            event_id=evt(n), sequence=seq if seq is not ... else n, occurred_at=at(t), **extra
        )
    )


# ------------------------------------------------------------------ ordering


def test_sorting_by_sequence_ignores_arrival_order_and_skewed_clocks() -> None:
    # Wall clocks disagree with the logical sequence (clock skew): sequence wins.
    events = [ev(1, t=50), ev(2, t=10), ev(3, t=30), ev(4, t=0), ev(5, t=20)]
    assert [e.sequence for e in sort_events(events)] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize("seed", range(25))
def test_every_permutation_sorts_identically(seed: int) -> None:
    rng = random.Random(seed)
    events = [ev(n, t=rng.uniform(0, 100)) for n in range(1, 40)]
    expected = [e.event_id for e in sort_events(events)]
    shuffled = events[:]
    rng.shuffle(shuffled)
    assert [e.event_id for e in sort_events(shuffled)] == expected


def test_without_sequences_order_falls_back_to_time_then_id() -> None:
    events = [ev(3, seq=None, t=5), ev(1, seq=None, t=5), ev(2, seq=None, t=1)]
    assert [e.event_id for e in sort_events(events)] == [evt(2), evt(1), evt(3)]


def test_mixed_sequence_availability_uses_time_for_everyone() -> None:
    events = [ev(1, seq=1, t=30), ev(2, seq=None, t=10), ev(3, seq=2, t=20)]
    assert [e.event_id for e in sort_events(events)] == [evt(2), evt(3), evt(1)]


def test_ties_break_on_received_at_then_event_id() -> None:
    a = finalize(
        ev(2, seq=7, t=1), workspace_id=WS, project_id=PRJ, received_at=T0 + timedelta(seconds=9)
    )
    b = finalize(
        ev(1, seq=7, t=1), workspace_id=WS, project_id=PRJ, received_at=T0 + timedelta(seconds=8)
    )
    c = finalize(
        ev(3, seq=7, t=1), workspace_id=WS, project_id=PRJ, received_at=T0 + timedelta(seconds=8)
    )
    assert [e.event_id for e in sort_events([a, b, c])] == [evt(1), evt(3), evt(2)]


def test_sort_does_not_mutate_input_and_handles_empty() -> None:
    events = [ev(2), ev(1)]
    sort_events(events)
    assert [e.sequence for e in events] == [2, 1]
    assert sort_events([]) == []


# ------------------------------------------------------------------ dedup


def test_content_hash_is_stable_across_key_order_and_event_in_vs_event() -> None:
    a = parse_event_in(make_event(attributes={"tool.name": "x", "vendor.a": 1, "vendor.b": 2}))
    b = parse_event_in(make_event(attributes={"vendor.b": 2, "vendor.a": 1, "tool.name": "x"}))
    assert content_hash(a) == content_hash(b)
    final = finalize(a, workspace_id=WS, project_id=PRJ, received_at=T0)
    later = finalize(a, workspace_id=WS, project_id=PRJ, received_at=T0 + timedelta(hours=1))
    assert content_hash(final) == content_hash(a) == content_hash(later)


def test_content_hash_changes_with_content() -> None:
    assert content_hash(ev(1)) != content_hash(parse_event_in(make_event(status="error")))


def test_dedupe_splits_retries_from_conflicts_and_keeps_first() -> None:
    first = ev(1)
    retry = ev(1)
    conflicting = parse_event_in(make_event(event_id=evt(1), status="error"))
    other = ev(2)
    result = dedupe([first, other, retry, conflicting, retry])
    assert [e.event_id for e in result.unique] == [evt(1), evt(2)]
    assert result.unique[0] is first
    assert result.duplicates == 2
    assert result.conflicts == [evt(1)]


# ------------------------------------------------------------------ spans


def span_events() -> list[EventIn]:
    return [
        ev(
            1,
            seq=1,
            t=0,
            event_type="agent.started",
            span_id=spn(1),
            parent_span_id=...,
            attributes={},
            status=...,
        ),
        ev(
            2,
            seq=2,
            t=1,
            event_type="tool.call.started",
            span_id=spn(2),
            parent_span_id=spn(1),
            attributes={"tool.name": "github"},
            status=...,
        ),
        ev(
            3,
            seq=3,
            t=2,
            event_type="file.read",
            span_id=spn(2),
            parent_span_id=spn(1),
            attributes={"file.path": "a.py"},
        ),
        ev(
            4,
            seq=4,
            t=3,
            event_type="tool.call.completed",
            span_id=spn(2),
            parent_span_id=spn(1),
            attributes={"tool.name": "github"},
            status="success",
            duration_ms=2000,
        ),
        ev(
            5,
            seq=5,
            t=4,
            event_type="agent.completed",
            span_id=spn(1),
            parent_span_id=...,
            attributes={},
            status="success",
        ),
    ]


def test_derive_spans_builds_tree_with_timings() -> None:
    spans = derive_spans(span_events())
    assert set(spans) == {spn(1), spn(2)}
    tool = spans[spn(2)]
    assert tool.parent_span_id == spn(1)
    assert tool.kind is SpanKind.TOOL and tool.name == "github"
    assert tool.status is EventStatus.SUCCESS and tool.duration_ms == 2000
    assert tool.event_count == 3
    assert spans[spn(1)].kind is SpanKind.AGENT
    assert not check_span_relationships(span_events())


def test_arrival_order_does_not_change_the_result() -> None:
    events = span_events()
    forward = derive_spans(events)
    for seed in range(10):
        shuffled = events[:]
        random.Random(seed).shuffle(shuffled)
        assert derive_spans(shuffled) == forward


def test_open_span_and_late_parent_are_not_errors() -> None:
    child_only = [e for e in span_events() if e.span_id == spn(2)][:2]  # no close, no parent yet
    spans = derive_spans(child_only)
    assert spans[spn(2)].ended_at is None
    assert orphan_span_ids(spans) == {spn(2)}
    assert check_span_relationships(child_only) == []


def test_close_before_open_is_tolerated() -> None:
    events = [e for e in span_events() if e.span_id == spn(2)]
    close_only = [e for e in events if e.event_type.endswith("completed")]
    span = derive_spans(close_only)[spn(2)]
    assert span.started_at is None and span.ended_at is not None


def test_conflicting_parents_are_reported() -> None:
    events = [
        *span_events(),
        ev(
            9,
            seq=9,
            t=5,
            event_type="file.read",
            span_id=spn(2),
            parent_span_id=spn(7),
            attributes={"file.path": "x"},
        ),
    ]
    assert {i.code for i in check_span_relationships(events)} == {"conflicting_parent"}


def test_cycles_are_reported_once() -> None:
    cyc = [
        ev(
            1,
            seq=1,
            event_type="file.read",
            span_id=spn(1),
            parent_span_id=spn(2),
            attributes={"file.path": "a"},
        ),
        ev(
            2,
            seq=2,
            event_type="file.read",
            span_id=spn(2),
            parent_span_id=spn(3),
            attributes={"file.path": "a"},
        ),
        ev(
            3,
            seq=3,
            event_type="file.read",
            span_id=spn(3),
            parent_span_id=spn(1),
            attributes={"file.path": "a"},
        ),
    ]
    issues = check_span_relationships(cyc)
    assert [i.code for i in issues] == ["cycle"]


def test_span_in_two_traces_is_reported() -> None:
    other_trace = "trc_01J9ZZZZZZZZZZZZZZZZZZZZZX"
    events = [
        *span_events(),
        ev(
            9,
            seq=9,
            event_type="file.read",
            span_id=spn(2),
            parent_span_id=spn(1),
            attributes={"file.path": "x"},
            trace_id=other_trace,
        ),
    ]
    assert "span_in_multiple_traces" in {i.code for i in check_span_relationships(events)}
