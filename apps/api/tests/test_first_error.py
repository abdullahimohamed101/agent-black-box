"""First-error facts in the run summary (ADR-112): pure derivation, parity with the web client."""

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, new_id

from abb_api.runs.summary import derive_run, is_error_event
from tests.ingest_helpers import make_run_ids
from tests.test_run_summary import ev as _ev

CASES = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "packages/event-schema/tests/data/first-error-cases.json"
    ).read_text()
)["cases"]

PRICED = {"llm.provider": "p", "llm.model": "m", "cost.estimated_usd": 0.5}
UNPRICED = {"llm.provider": "nobody", "llm.model": "no-such-model", "llm.input_tokens": 100}
NONE_FOUND = {
    "first_error_event_id": None,
    "first_error_sequence": None,
    "retries_after": 0,
    "seconds_after": None,
    "cost_after_usd": None,
    "cost_after_unpriced_calls": 0,
}


REQUIRED: dict[str, dict[str, Any]] = {
    "retry.attempted": {"retry.attempt": 1},
    "llm.request.completed": {"llm.provider": "p", "llm.model": "m"},
    "llm.request.failed": {"llm.provider": "p", "llm.model": "m"},
    "policy.action.blocked": {"policy.id": "pol"},
}


def ev(run: dict[str, str], n: int, event_type: str, **kw: Any) -> Event:
    """`tests.test_run_summary.ev` plus the attributes the schema requires of the type."""
    if "attributes" not in kw and event_type in REQUIRED:
        kw["attributes"] = REQUIRED[event_type]
    return _ev(run, n, event_type, **kw)


def when(seconds: int) -> str:
    stamp = datetime(2026, 10, 7, 12, 0, tzinfo=UTC) + timedelta(seconds=seconds)
    return stamp.isoformat().replace("+00:00", "Z")


def facts(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: summary[k] for k in NONE_FOUND}


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_server_picks_the_same_first_error_as_the_web_client(case: dict[str, Any]) -> None:
    """`timeline.ts` firstError() is `events.find(isError)` over the API order; same cases there."""
    run = make_run_ids()
    events = [
        ev(run, n, e["event_type"], **({"status": e["status"]} if e["status"] else {}))
        for n, e in enumerate(case["events"], start=1)
    ]
    # event ids in the cases are labels; the real ids are generated, so map by position
    expected = case["expected"]
    labels = {e.event_id: spec["event_id"] for e, spec in zip(events, case["events"], strict=True)}
    if not events:
        return
    found = derive_run(events).summary["first_error_event_id"]
    assert (labels[found] if found else None) == expected
    assert [labels[e.event_id] for e in events if is_error_event(e)][:1] == (
        [expected] if expected else []
    )


def test_a_run_with_no_error_has_no_first_error_facts() -> None:
    run = make_run_ids()
    events = [ev(run, 1, "run.started"), ev(run, 2, "run.completed")]
    assert facts(derive_run(events).summary) == NONE_FOUND


def test_an_error_without_retries_or_later_calls_costs_nothing_more() -> None:
    run = make_run_ids()
    events = [
        ev(run, 1, "run.started"),
        ev(run, 2, "llm.request.completed", attributes=PRICED),
        ev(run, 3, "tool.call.failed", status="error"),
        ev(run, 4, "run.failed", occurred_at=when(9)),
    ]
    s = derive_run(events).summary
    error = events[2]
    assert s["first_error_event_id"] == error.event_id and s["first_error_sequence"] == 3
    assert s["retries_after"] == 0
    assert s["cost_after_usd"] == 0.0 and s["cost_after_unpriced_calls"] == 0
    # the run ended at the run.failed event (6 s later)
    assert s["seconds_after"] == pytest.approx(6.0)


def test_retries_and_spend_before_the_first_error_are_not_counted_after_it() -> None:
    run = make_run_ids()
    events = [
        ev(run, 1, "run.started"),
        ev(run, 2, "retry.attempted"),  # before: not "after"
        ev(run, 3, "llm.request.completed", attributes=PRICED),  # before
        ev(run, 4, "tool.call.failed", status="error"),
        ev(run, 5, "retry.attempted"),
        ev(run, 6, "llm.request.completed", attributes=PRICED),
        ev(run, 7, "tool.call.failed", status="error"),  # a second error is not the first
        ev(run, 8, "retry.attempted"),
        ev(run, 9, "llm.request.completed", attributes=PRICED),
        ev(run, 10, "run.failed"),
    ]
    s = derive_run(events).summary
    assert s["first_error_event_id"] == events[3].event_id
    assert s["retries_after"] == 2
    assert s["cost_after_usd"] == pytest.approx(1.0)
    assert s["estimated_cost_usd"] == pytest.approx(1.5)


def test_unpriced_calls_after_the_error_are_counted_not_priced_like_the_total() -> None:
    run = make_run_ids()
    events = [
        ev(run, 1, "tool.call.failed", status="error"),
        ev(run, 2, "llm.request.completed", attributes=PRICED),
        ev(run, 3, "llm.request.completed", attributes=UNPRICED),
    ]
    s = derive_run(events).summary
    assert s["unpriced_calls"] == 1  # the run total's own semantics
    assert s["cost_after_unpriced_calls"] == 1
    assert s["cost_after_usd"] == pytest.approx(0.5)


def test_a_running_run_measures_to_its_last_event() -> None:
    run = make_run_ids()
    events = [
        ev(run, 1, "run.started"),
        ev(run, 2, "tool.call.failed", status="error"),
        ev(run, 5, "tool.call.completed"),
    ]
    derived = derive_run(events)
    assert derived.completed_at is None
    assert derived.summary["seconds_after"] == pytest.approx(3.0)


def test_a_skewed_end_never_gives_negative_seconds() -> None:
    run = make_run_ids()
    events = [
        ev(run, 1, "run.started"),
        ev(run, 5, "tool.call.failed", status="error"),
        ev(run, 6, "run.completed", occurred_at=when(2)),  # clock behind
    ]
    assert derive_run(events).summary["seconds_after"] == 0.0


def multi_agent_run() -> list[Event]:
    run = make_run_ids()
    root, child = new_id(IdKind.SPAN), new_id(IdKind.SPAN)
    return [
        ev(run, 1, "run.started", agent_id="planner", span_id=...),
        ev(run, 2, "llm.request.completed", agent_id="planner", attributes=PRICED),
        ev(run, 3, "agent.started", agent_id="tester", span_id=child, parent_span_id=root),
        ev(run, 4, "tool.call.failed", agent_id="tester", status="error"),  # first error
        ev(run, 5, "retry.attempted", agent_id="planner"),
        ev(run, 6, "llm.request.completed", agent_id="tester", attributes=PRICED),
        ev(run, 7, "llm.request.completed", agent_id="planner", attributes=PRICED),
        ev(run, 8, "run.completed", agent_id="planner"),
    ]


def test_facts_are_run_wide_across_agents() -> None:
    events = multi_agent_run()
    s = derive_run(events).summary
    assert s["first_error_event_id"] == events[3].event_id
    assert s["retries_after"] == 1 and s["cost_after_usd"] == pytest.approx(1.0)
    assert s["seconds_after"] == pytest.approx(4.0)


@pytest.mark.parametrize("seed", range(20))
def test_facts_do_not_depend_on_arrival_order(seed: int) -> None:
    events = multi_agent_run()
    expected = facts(derive_run(events).summary)
    shuffled = events[:]
    random.Random(seed).shuffle(shuffled)
    assert facts(derive_run(shuffled).summary) == expected


def test_events_without_sequence_are_ordered_by_time() -> None:
    run = make_run_ids()
    events = [
        ev(run, 1, "retry.attempted", sequence=..., occurred_at=when(3)),
        ev(run, 2, "tool.call.failed", status="error", sequence=..., occurred_at=when(2)),
        ev(run, 3, "run.started", sequence=..., occurred_at=when(1)),
    ]
    s = derive_run(events).summary
    assert s["first_error_event_id"] == events[1].event_id
    assert s["first_error_sequence"] is None
    assert s["retries_after"] == 1
