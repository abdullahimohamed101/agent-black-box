"""Run derivation: pure, order-independent, rebuildable."""

import random
import uuid
from typing import Any

import pytest
from abb_event_schema.enums import RunStatus
from abb_event_schema.event import Event

from abb_api.runs.summary import SUMMARY_VERSION, derive_run
from abb_api.tenancy import TenantContext
from tests.ingest_helpers import Tenant, build_event, make_run_ids

TENANT = Tenant(
    context=TenantContext(uuid.uuid4()),
    workspace_id="ws_01J9ZZZZZZZZZZZZZZZZZZZZZZ",
    projects={"p": "prj_01J9ZZZZZZZZZZZZZZZZZZZZZZ"},
    project_uuids={},
)


def ev(run: dict[str, str], n: int, event_type: str = "tool.call.completed", **kw: Any) -> Event:
    attributes = kw.pop("attributes", None)
    if attributes is None:
        attributes = {"tool.name": "t"} if event_type.startswith("tool.") else {}
    return build_event(TENANT, run, n=n, event_type=event_type, attributes=attributes, **kw)


def lifecycle(*items: tuple[str, dict[str, Any]]) -> list[Event]:
    run = make_run_ids()
    return [ev(run, n, kind, **extra) for n, (kind, extra) in enumerate(items, start=1)]


# ------------------------------------------------------------------ status


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([("tool.call.completed", {})], RunStatus.RUNNING),  # no lifecycle events at all
        ([("run.started", {})], RunStatus.RUNNING),
        ([("run.started", {}), ("run.completed", {})], RunStatus.SUCCESS),
        ([("run.started", {}), ("run.completed", {"status": "success"})], RunStatus.SUCCESS),
        ([("run.started", {}), ("run.completed", {"status": "error"})], RunStatus.FAILED),
        ([("run.started", {}), ("run.completed", {"status": "timeout"})], RunStatus.TIMED_OUT),
        ([("run.started", {}), ("run.completed", {"status": "blocked"})], RunStatus.BLOCKED),
        ([("run.started", {}), ("run.completed", {"status": "cancelled"})], RunStatus.CANCELLED),
        ([("run.started", {}), ("run.failed", {})], RunStatus.FAILED),
        ([("run.started", {}), ("run.cancelled", {})], RunStatus.CANCELLED),
    ],
)
def test_status_from_lifecycle_events(
    events: list[tuple[str, dict[str, Any]]], expected: RunStatus
) -> None:
    assert derive_run(lifecycle(*events)).status is expected


def test_approvals_pause_and_resume_the_run() -> None:
    approval = {"attributes": {"approval.id": "apr_1"}}
    waiting = lifecycle(("run.started", {}), ("approval.requested", approval))
    assert derive_run(waiting).status is RunStatus.WAITING_FOR_APPROVAL
    resumed = lifecycle(
        ("run.started", {}), ("approval.requested", approval), ("approval.granted", approval)
    )
    assert derive_run(resumed).status is RunStatus.RUNNING
    done = lifecycle(
        ("run.started", {}), ("approval.requested", approval), ("approval.denied", approval),
        ("run.failed", {}),
    )  # fmt: skip
    assert derive_run(done).status is RunStatus.FAILED


def test_terminal_status_is_final_so_late_events_cannot_reopen_a_run() -> None:
    late = lifecycle(
        ("run.started", {}), ("run.completed", {}), ("tool.call.completed", {}),
        ("run.started", {}), ("approval.requested", {"attributes": {"approval.id": "a"}}),
        ("run.failed", {}),
    )  # fmt: skip
    derived = derive_run(late)
    assert derived.status is RunStatus.SUCCESS
    assert derived.completed_at == late[1].occurred_at
    assert derived.summary["event_count"] == 6  # late events still count as activity


def test_timing_comes_from_the_events_not_arrival() -> None:
    events = lifecycle(("run.started", {}), ("tool.call.completed", {}), ("run.completed", {}))
    derived = derive_run(events)
    assert derived.started_at == events[0].occurred_at
    assert derived.completed_at == events[2].occurred_at
    assert derived.duration_ms == 2000.0 and derived.summary["duration_ms"] == 2000.0
    running = derive_run(events[:2])
    assert running.completed_at is None and running.duration_ms is None


def test_run_name_trace_and_agent_are_taken_from_the_earliest_events() -> None:
    events = lifecycle(
        ("run.started", {"attributes": {"run.name": "Fix login bug"}}),
        ("tool.call.completed", {"agent_id": "reviewer"}),
    )
    derived = derive_run(events)
    assert derived.name == "Fix login bug"
    assert derived.agent_slug == "coding-agent" and derived.trace_id == events[0].trace_id


# ------------------------------------------------------------------ counters


def test_counters_follow_the_documented_rules() -> None:
    run = make_run_ids()
    llm = {"llm.provider": "p", "llm.model": "m1", "llm.input_tokens": 100, "llm.output_tokens": 20}
    events = [
        ev(run, 1, "run.started"),
        ev(run, 2, "llm.request.completed", attributes={**llm, "cost.estimated_usd": 0.1}),
        ev(run, 3, "llm.request.completed",
           attributes={**llm, "llm.model": "m2", "cost.estimated_usd": 0.2}),
        ev(run, 4, "llm.request.failed", attributes={"llm.provider": "p", "llm.model": "m1"}),
        ev(run, 5, "tool.call.completed"),
        ev(run, 6, "tool.call.failed"),
        ev(run, 7, "retry.attempted", attributes={"retry.attempt": 1}),
        ev(run, 8, "retry.attempted", attributes={"retry.attempt": 2}),
        ev(run, 9, "file.modified", attributes={"file.path": "a.py"}),
        ev(run, 10, "file.modified", attributes={"file.path": "a.py"}),  # same file twice
        ev(run, 11, "file.created", attributes={"file.path": "b.py"}),
        ev(run, 12, "file.read", attributes={"file.path": "c.py"}),  # reads do not count
        ev(run, 13, "shell.command.completed", attributes={"shell.command": "x"}, status="error"),
        ev(run, 14, "timeout.occurred", status="timeout"),
    ]  # fmt: skip
    s = derive_run(events).summary
    assert (s["llm_calls"], s["tool_calls"], s["retry_count"], s["files_modified"]) == (3, 2, 2, 2)
    assert (s["input_tokens"], s["output_tokens"]) == (200, 40)  # failed calls add no tokens
    assert s["estimated_cost_usd"] == pytest.approx(0.3)
    assert s["models"] == ["m1", "m2"]
    # tool.call.failed + llm.request.failed + two error/timeout statuses = 4 (each counted once)
    assert s["error_count"] == 4
    assert s["event_count"] == 14


def test_an_event_that_is_both_failed_and_error_counts_once() -> None:
    run = make_run_ids()
    s = derive_run([ev(run, 1, "tool.call.failed", status="error")]).summary
    assert s["error_count"] == 1


def test_cost_total_is_exact_and_independent_of_order() -> None:
    run = make_run_ids()
    base = {"llm.provider": "p", "llm.model": "m"}
    prices = [0.1, 0.2, 0.3, 1e-9, 0.7, 0.000001]
    events = [
        ev(run, n, "llm.request.completed", attributes={**base, "cost.estimated_usd": p})
        for n, p in enumerate(prices, start=1)
    ]
    first = derive_run(events).summary["estimated_cost_usd"]
    for seed in range(20):
        shuffled = events[:]
        random.Random(seed).shuffle(shuffled)
        assert derive_run(shuffled).summary["estimated_cost_usd"] == first
    assert first == pytest.approx(sum(prices))


def test_ordering_mode_reflects_whether_every_event_has_a_sequence() -> None:
    run = make_run_ids()
    sequenced = [ev(run, 1), ev(run, 2)]
    assert derive_run(sequenced).ordering_mode == "sequence"
    mixed = [ev(run, 1), ev(run, 2, sequence=...)]
    assert derive_run(mixed).ordering_mode == "time"


# ------------------------------------------------------------------ rebuildability


def rich_run() -> list[Event]:
    run = make_run_ids()
    llm = {"llm.provider": "p", "llm.model": "m", "llm.input_tokens": 5, "cost.estimated_usd": 0.01}
    return [
        ev(run, 1, "run.started", attributes={"run.name": "demo"}),
        ev(run, 2, "llm.request.completed", attributes=llm),
        ev(run, 3, "tool.call.completed"),
        ev(run, 4, "tool.call.failed"),
        ev(run, 5, "retry.attempted", attributes={"retry.attempt": 1}),
        ev(run, 6, "file.modified", attributes={"file.path": "a"}),
        ev(run, 7, "run.completed"),
        ev(run, 8, "tool.call.completed"),  # late
    ]


@pytest.mark.parametrize("seed", range(25))
def test_any_arrival_order_gives_the_same_derivation(seed: int) -> None:
    events = rich_run()
    expected = derive_run(events)
    shuffled = events[:]
    random.Random(seed).shuffle(shuffled)
    assert derive_run(shuffled) == expected


def test_deriving_twice_is_stable_and_the_version_is_recorded() -> None:
    events = rich_run()
    assert derive_run(events) == derive_run(events)
    assert SUMMARY_VERSION == 1


def test_no_events_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="no events"):
        derive_run([])
