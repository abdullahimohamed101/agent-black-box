"""Golden cases for `derive_spans`, shared with the web app's TypeScript port (Phase 9, D5).

The waterfall derives spans in the browser from the events the run page already holds, and the
result must equal the server's derivation, including for hostile structure (cycles, close before
open, 10,000-deep chains) and for event types the registry does not know (`agent.failed` closes
nothing). `tests/data/spans-golden.json` is generated here from the Python implementation;
the web test replays it. Regenerate with `ABB_REGEN_GOLDEN=1 pytest`.

The deep chain is stored as a generator description rather than 10,000 events, to keep the file
reviewable; both languages build the same events from it.
"""

import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from abb_event_schema.parse import parse_event_in
from abb_event_schema.registry import EVENT_TYPES, KNOWN_ATTRIBUTES, AttrType, SpanRole
from abb_event_schema.spans import Span, derive_spans
from tests.helpers import RUN, TRACE, evt, make_event, spn

GOLDEN = Path(__file__).parent / "data" / "spans-golden.json"
BASE = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
DEEP_CHAIN = 10_000


def wire(moment: datetime) -> str:
    """The API's timestamp spelling: UTC, `Z`, fractional digits only when non-zero."""
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{stamp}.{moment.microsecond:06d}Z" if moment.microsecond else f"{stamp}Z"


def at(seconds: float) -> str:
    return wire(BASE + timedelta(seconds=seconds))


_COUNTER = [0]


def ev(
    event_type: str,
    span: int | None,
    t: float,
    *,
    parent: int | None = None,
    agent: str = "root",
    seq: int | None = None,
    status: str | None = None,
    duration_ms: float | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _COUNTER[0] += 1
    return make_event(
        event_id=evt(_COUNTER[0]),
        span_id=spn(span) if span is not None else ...,
        parent_span_id=spn(parent) if parent is not None else ...,
        agent_id=agent,
        event_type=event_type,
        occurred_at=at(t),
        sequence=seq if seq is not None else ...,
        status=status if status is not None else ...,
        duration_ms=duration_ms if duration_ms is not None else ...,
        attributes=attributes or {},
        tags=...,
    )


def open_close(
    span: int,
    start: float,
    end: float | None,
    *,
    parent: int | None = None,
    agent: str = "root",
    seq: int | None = None,
    kind: str = "tool",
    name: str = "work",
    status: str = "success",
) -> list[dict[str, Any]]:
    """A tool span: `tool.call.started` and (when `end` is given) `tool.call.completed`."""
    attrs = {"tool.name": name}
    events = [
        ev(
            f"{kind}.call.started",
            span,
            start,
            parent=parent,
            agent=agent,
            seq=seq,
            attributes=attrs,
        )
    ]
    if end is not None:
        events.append(
            ev(
                f"{kind}.call.completed",
                span,
                end,
                parent=parent,
                agent=agent,
                seq=None if seq is None else seq + 1,
                status=status,
                duration_ms=(end - start) * 1000,
                attributes=attrs,
            )
        )
    return events


def _required_attributes(event_type: str) -> dict[str, Any]:
    values: dict[AttrType, Any] = {
        AttrType.STRING: "x",
        AttrType.INTEGER: 1,
        AttrType.NUMBER: 1.5,
        AttrType.BOOLEAN: True,
        AttrType.STRING_LIST: ["x"],
    }
    spec = EVENT_TYPES[event_type]
    out: dict[str, Any] = {}
    for key in sorted(spec.required_attributes):
        out[key] = values[KNOWN_ATTRIBUTES[key].type]
    if event_type == "span.started":
        out["span.kind"] = "custom"
    return out


def _spans_case(name: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": name, "events": events, "expected": _expected(events)}


def _expected(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans = derive_spans(parse_event_in(e) for e in events)
    return [_span(s) for s in spans.values()]


def _span(span: Span) -> dict[str, Any]:
    return {
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "agent_id": span.agent_id,
        "name": span.name,
        "kind": span.kind.value if span.kind else None,
        "started_at": None if span.started_at is None else wire(span.started_at),
        "ended_at": None if span.ended_at is None else wire(span.ended_at),
        "status": span.status.value if span.status else None,
        "duration_ms": span.duration_ms,
        "event_count": span.event_count,
    }


def _kind_cases() -> list[dict[str, Any]]:
    """One span per registered OPEN type, closed by its registered closer, in one run."""
    events: list[dict[str, Any]] = []
    n = 100
    for spec in EVENT_TYPES.values():
        if spec.span_role is not SpanRole.OPEN:
            continue
        n += 1
        closer = spec.event_type.replace(".started", ".completed")
        events.append(
            ev(spec.event_type, n, n - 100, attributes=_required_attributes(spec.event_type))
        )
        events.append(
            ev(
                closer,
                n,
                n - 100 + 0.5,
                status="success",
                duration_ms=500,
                attributes=_required_attributes(closer) if closer in EVENT_TYPES else {},
            )
        )
    return [_spans_case("one span per registered kind", events)]


def _random_events(rng: random.Random) -> list[dict[str, Any]]:
    types = [
        ("tool.call.started", "open"),
        ("tool.call.completed", "close"),
        ("agent.started", "open"),
        ("agent.completed", "close"),
        ("custom.note.added", "point"),
        ("agent.failed", "close"),
    ]
    events = []
    for _ in range(300):
        event_type, _role = rng.choice(types)
        span = rng.randint(1, 40)
        parent = rng.choice([None, None, rng.randint(1, 40)])
        if parent == span:
            parent = None
        # a span keeps one parent, as a well-behaved emitter would
        parent = (span * 7) % 41 if span % 3 == 0 and (span * 7) % 41 not in (0, span) else parent
        attrs = {"tool.name": "t"} if event_type.startswith("tool.") else {}
        events.append(
            ev(
                event_type,
                span,
                rng.randint(0, 20) / 4,
                parent=parent,
                agent=rng.choice(["root", "child-a", "child-b"]),
                seq=rng.choice([None, None, rng.randint(1, 50)]),
                status="success" if event_type.endswith("completed") else None,
                attributes=attrs,
            )
        )
    return events


def make_cases() -> list[dict[str, Any]]:
    _COUNTER[0] = 0
    cases: list[dict[str, Any]] = []

    # chain: 1 -> 2 -> 3
    cases.append(
        _spans_case(
            "chain",
            open_close(1, 0, 10, kind="tool", name="a")
            + open_close(2, 1, 9, parent=1, name="b")
            + open_close(3, 2, 8, parent=2, name="c"),
        )
    )
    # fan-out: one parent, three children
    cases.append(
        _spans_case(
            "fan-out",
            open_close(1, 0, 10)
            + open_close(2, 1, 4, parent=1)
            + open_close(3, 1, 9, parent=1)
            + open_close(4, 2, 6, parent=1),
        )
    )
    # late parent: the children arrive before the parent's open
    late = open_close(2, 1, 4, parent=1) + open_close(3, 2, 5, parent=1) + open_close(1, 0, 6)
    cases.append(_spans_case("late parent", late))
    # orphan: the parent never arrives
    cases.append(_spans_case("orphan", open_close(2, 1, 4, parent=99)))
    # close before open (arrival order and timestamps), and a close with no open at all
    opener, closer = open_close(1, 5, 6)
    cases.append(_spans_case("close before open", [closer, opener]))
    cases.append(_spans_case("close without open", [closer]))
    # open span: no closer yet
    cases.append(_spans_case("open span", open_close(1, 0, None) + open_close(2, 1, 2, parent=1)))
    # skewed child: its timestamps precede its parent's
    cases.append(
        _spans_case(
            "skewed child",
            open_close(1, 10, 20, agent="parent-agent")
            + open_close(2, 3, 5, parent=1, agent="child-agent"),
        )
    )
    # two agents, each with agent spans, one nested under the other's tool span
    cases.append(
        _spans_case(
            "two agents",
            [
                ev("agent.started", 1, 0, agent="planner", attributes={"span.name": "planner"}),
                *open_close(2, 1, 8, parent=1, agent="planner", name="delegate"),
                ev("agent.spawned", 2, 1, agent="planner", attributes={"agent.child_id": "coder"}),
                ev(
                    "agent.started",
                    3,
                    2,
                    parent=2,
                    agent="coder",
                    attributes={"span.name": "coder"},
                ),
                ev("agent.completed", 3, 7, parent=2, agent="coder", status="success"),
                ev("agent.completed", 1, 9, agent="planner", status="success"),
            ],
        )
    )
    # remote emitter: some events carry no sequence at all
    cases.append(
        _spans_case(
            "remote emitter without sequence",
            open_close(1, 0, 10, seq=1)
            + open_close(2, 3, 7, parent=1, agent="remote", seq=None)
            + open_close(3, 4, 6, parent=2, agent="remote", seq=None),
        )
    )
    # `agent.failed` is not in the registry: it is a point event that closes nothing
    cases.append(
        _spans_case(
            "unregistered closer",
            [
                ev("agent.started", 1, 0, attributes={"span.name": "root"}),
                ev("agent.failed", 1, 5, status="error"),
            ],
        )
    )
    # custom.* events attach to a span as points; unknown types too
    cases.append(
        _spans_case(
            "custom and unknown types",
            [
                *open_close(1, 0, 4),
                ev("custom.note.added", 1, 1),
                ev("custom.note.added", 2, 2, parent=1),
                ev("vendor.thing.happened", 3, 3, parent=1),
            ],
        )
    )
    cases.extend(_kind_cases())
    # a two-span cycle (the API accepts and stores it)
    cases.append(
        _spans_case(
            "cycle",
            open_close(1, 0, 4, parent=2) + open_close(2, 1, 3, parent=1),
        )
    )
    # a span reported with two parents: the first in canonical order wins
    cases.append(
        _spans_case(
            "two parents",
            open_close(1, 0, 9)
            + open_close(2, 1, 8)
            + open_close(3, 2, 5, parent=1)
            + [ev("custom.note.added", 3, 3, parent=2)],
        )
    )
    cases.append(_spans_case("random", _random_events(random.Random(20261010))))
    return cases


def deep_chain_events() -> list[dict[str, Any]]:
    """10,000 nested tool spans, each opened 1 ms after its parent and closed 1 ms before it."""
    start = _COUNTER[0]
    events: list[dict[str, Any]] = []
    for i in range(1, DEEP_CHAIN + 1):
        parent = i - 1 if i > 1 else None
        events.extend(
            open_close(
                i,
                i / 1000,
                (2 * DEEP_CHAIN - i + 1) / 1000,
                parent=parent,
                name="d",
            )
        )
    _COUNTER[0] = start
    return events


def make_document() -> dict[str, Any]:
    cases = make_cases()
    deep = _expected(deep_chain_events())
    return {
        "cases": cases,
        "generated": [
            {
                "name": "deep chain",
                "generator": {
                    "kind": "chain",
                    "length": DEEP_CHAIN,
                    "event_pair": ["tool.call.started", "tool.call.completed"],
                    "step_ms": 1,
                },
                "expected_count": len(deep),
                "expected_first": deep[0],
                "expected_last": deep[-1],
            }
        ],
    }


def test_the_golden_file_matches_the_python_implementation() -> None:
    rendered = json.dumps(make_document(), indent=1) + "\n"
    if os.environ.get("ABB_REGEN_GOLDEN") == "1":
        GOLDEN.write_text(rendered)
    assert GOLDEN.read_text() == rendered, "run `ABB_REGEN_GOLDEN=1 pytest` to regenerate"


def test_the_golden_cases_cover_the_hostile_and_unregistered_shapes() -> None:
    document = json.loads(GOLDEN.read_text())
    names = {c["name"] for c in document["cases"]}
    assert {
        "chain",
        "fan-out",
        "late parent",
        "orphan",
        "close before open",
        "open span",
        "skewed child",
        "two agents",
        "remote emitter without sequence",
        "unregistered closer",
        "custom and unknown types",
        "one span per registered kind",
        "cycle",
        "random",
    } <= names
    by_name = {c["name"]: c for c in document["cases"]}
    # `agent.failed` closes nothing: the root span stays unfinished
    (failed,) = by_name["unregistered closer"]["expected"]
    assert failed["ended_at"] is None and failed["event_count"] == 2
    # an open span has no end; a close without an open has no start
    assert by_name["open span"]["expected"][0]["ended_at"] is None
    assert by_name["close without open"]["expected"][0]["started_at"] is None
    # the skewed child starts before its parent and is still parented to it
    skewed = {s["span_id"]: s for s in by_name["skewed child"]["expected"]}
    parent_span, child = skewed[spn(1)], skewed[spn(2)]
    assert child["parent_span_id"] == parent_span["span_id"]
    assert child["started_at"] < parent_span["started_at"]
    # the cycle is derived as stored: two spans, each the other's parent
    cycle = by_name["cycle"]["expected"]
    assert {s["parent_span_id"] for s in cycle} == {s["span_id"] for s in cycle}
    # every registered span kind appears
    kinds = {s["kind"] for s in by_name["one span per registered kind"]["expected"]}
    assert {"agent", "llm", "tool", "shell", "db", "custom"} <= kinds
    generated = document["generated"][0]
    assert generated["expected_count"] == DEEP_CHAIN
    assert generated["expected_last"]["parent_span_id"] == spn(DEEP_CHAIN - 1)


def test_the_golden_ids_are_valid_for_the_contract() -> None:
    # make_event ids are fixed, so a malformed one would fail parse_event_in above; pin the run
    # and trace so the web replay can rely on them.
    event = make_cases()[0]["events"][0]
    assert event["run_id"] == RUN and event["trace_id"] == TRACE
