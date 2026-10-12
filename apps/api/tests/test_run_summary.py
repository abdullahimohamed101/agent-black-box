"""Run derivation: pure, order-independent, rebuildable."""

import random
import uuid
from typing import Any

import pytest
from abb_event_schema.enums import RunStatus
from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, new_id

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
        ("run.started", {}),
        ("approval.requested", approval),
        ("approval.denied", approval),
        ("run.failed", {}),
    )
    assert derive_run(done).status is RunStatus.FAILED


def test_terminal_status_is_final_so_late_events_cannot_reopen_a_run() -> None:
    late = lifecycle(
        ("run.started", {}),
        ("run.completed", {}),
        ("tool.call.completed", {}),
        ("run.started", {}),
        ("approval.requested", {"attributes": {"approval.id": "a"}}),
        ("run.failed", {}),
    )
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
        ev(
            run,
            3,
            "llm.request.completed",
            attributes={**llm, "llm.model": "m2", "cost.estimated_usd": 0.2},
        ),
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
    ]
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
    assert SUMMARY_VERSION == 5


def test_no_events_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="no events"):
        derive_run([])


def order_sensitive_run() -> list[Event]:
    """Facts that depend on which event comes first: the first terminal event wins, and the
    earliest events decide the run's agent and name."""
    run = make_run_ids()
    return [
        ev(run, 1, "run.started", agent_id="planner", attributes={"run.name": "first name"}),
        ev(run, 2, "run.started", agent_id="coder", attributes={"run.name": "second name"}),
        ev(run, 3, "tool.call.completed", agent_id="coder"),
        ev(run, 4, "run.failed", agent_id="coder"),
        ev(run, 5, "run.completed", agent_id="coder"),  # contradicts run.failed, arrives "later"
        ev(run, 6, "run.cancelled", agent_id="coder"),
    ]


@pytest.mark.parametrize("seed", range(30))
def test_first_terminal_event_agent_and_name_do_not_depend_on_arrival_order(seed: int) -> None:
    events = order_sensitive_run()
    expected = derive_run(events)
    assert expected.status is RunStatus.FAILED  # the earliest terminal event decides
    assert expected.agent_slug == "planner" and expected.name == "first name"
    assert expected.completed_at == events[3].occurred_at
    shuffled = events[:]
    random.Random(seed).shuffle(shuffled)
    assert derive_run(shuffled) == expected


# ------------------------------------------------------------------ agents (Phase 9)


def span() -> str:
    return new_id(IdKind.SPAN)


def at(seconds: float) -> str:
    base = 1_790_000_000 + seconds  # 2026-09
    from datetime import UTC, datetime

    return datetime.fromtimestamp(base, UTC).isoformat().replace("+00:00", "Z")


def agent_run() -> list[Event]:
    """planner delegates to researcher (which fails a tool) and names a ghost that never starts."""
    run = make_run_ids()
    deleg, researcher, llm, tool = span(), span(), span(), span()
    return [
        ev(run, 1, "run.started", agent_id="planner", span_id=...),
        ev(
            run,
            2,
            "tool.call.started",
            agent_id="planner",
            span_id=deleg,
            attributes={"tool.name": "delegate"},
        ),
        ev(
            run,
            3,
            "agent.spawned",
            agent_id="planner",
            span_id=deleg,
            attributes={"agent.child_id": "researcher"},
        ),
        ev(
            run,
            4,
            "agent.started",
            agent_id="researcher",
            span_id=researcher,
            parent_span_id=deleg,
            attributes={"span.name": "researcher", "span.kind": "agent"},
        ),
        ev(
            run,
            5,
            "llm.request.completed",
            agent_id="researcher",
            span_id=llm,
            parent_span_id=researcher,
            status="success",
            attributes={
                "llm.provider": "x",
                "llm.model": "m",
                "llm.input_tokens": 100,
                "llm.output_tokens": 50,
                "cost.provider_usd": 0.25,
            },
        ),
        ev(
            run,
            6,
            "tool.call.failed",
            agent_id="researcher",
            span_id=tool,
            parent_span_id=researcher,
            status="error",
        ),
        ev(
            run,
            7,
            "agent.completed",
            agent_id="researcher",
            span_id=researcher,
            parent_span_id=deleg,
            status="success",
        ),
        ev(
            run,
            8,
            "agent.spawned",
            agent_id="planner",
            span_id=...,
            attributes={"agent.child_id": "ghost"},
        ),
        ev(
            run,
            9,
            "llm.request.completed",
            agent_id="planner",
            span_id=span(),
            status="success",
            attributes={"llm.provider": "x", "llm.model": "m", "cost.provider_usd": 0.1},
        ),
        ev(run, 10, "tool.call.completed", agent_id="planner", span_id=deleg),
        ev(run, 11, "run.completed", agent_id="planner", span_id=...),
    ]


def test_the_summary_lists_each_agent_with_its_own_counts_and_cost() -> None:
    derived = derive_run(agent_run())
    summary = derived.summary
    assert derived.agent_slug == "planner" and summary["agent_count"] == 3
    planner, researcher, ghost = summary["agents"]
    assert [a["agent_id"] for a in summary["agents"]] == ["planner", "researcher", "ghost"]
    assert planner == {
        "agent_id": "planner",
        "parent_agent_id": None,
        "state": "completed",
        "event_count": 7,
        "llm_calls": 1,
        "tool_calls": 1,
        "error_count": 0,
        "estimated_cost_usd": 0.1,
        "first_event_at": planner["first_event_at"],
        "last_event_at": planner["last_event_at"],
    }
    assert researcher["parent_agent_id"] == "planner" and researcher["state"] == "completed"
    assert (researcher["event_count"], researcher["llm_calls"], researcher["tool_calls"]) == (
        4,
        1,
        1,
    )
    assert researcher["error_count"] == 1 and researcher["estimated_cost_usd"] == 0.25
    # a child that was named by a spawn and never started is still visible
    assert ghost["state"] == "spawned" and ghost["parent_agent_id"] == "planner"
    assert ghost["event_count"] == 0 and ghost["first_event_at"] is None
    assert ghost["last_event_at"] is None and ghost["estimated_cost_usd"] == 0.0
    # the run's cost is the sum of its agents'
    assert summary["estimated_cost_usd"] == pytest.approx(0.35)


def test_an_agent_that_closed_with_an_error_is_failed_and_an_open_one_is_started() -> None:
    run = make_run_ids()
    a, b = span(), span()
    events = [
        ev(run, 1, "run.started", agent_id="root", span_id=...),
        ev(run, 2, "agent.started", agent_id="bad", span_id=a, attributes={}),
        ev(run, 3, "agent.completed", agent_id="bad", span_id=a, status="error"),
        ev(run, 4, "agent.started", agent_id="busy", span_id=b, attributes={}),
    ]
    by_id = {x["agent_id"]: x for x in derive_run(events).summary["agents"]}
    assert by_id["bad"]["state"] == "failed" and by_id["busy"]["state"] == "started"
    assert by_id["root"]["state"] == "started"  # the run has not ended


def test_the_parent_agent_is_the_agent_that_owns_the_delegating_span() -> None:
    run = make_run_ids()
    reviewer_span, coder_span = span(), span()
    events = [
        ev(run, 1, "run.started", agent_id="planner", span_id=...),
        ev(run, 2, "agent.started", agent_id="reviewer", span_id=reviewer_span, attributes={}),
        # planner claims to have spawned the coder, but the coder runs under the reviewer's span
        ev(
            run,
            3,
            "agent.spawned",
            agent_id="planner",
            span_id=...,
            attributes={"agent.child_id": "coder"},
        ),
        ev(
            run,
            4,
            "agent.started",
            agent_id="coder",
            span_id=coder_span,
            parent_span_id=reviewer_span,
            attributes={},
        ),
    ]
    by_id = {x["agent_id"]: x for x in derive_run(events).summary["agents"]}
    assert by_id["coder"]["parent_agent_id"] == "reviewer"
    assert by_id["reviewer"]["parent_agent_id"] is None  # no delegation recorded for it


def test_the_agent_list_is_order_independent() -> None:
    events = agent_run()
    expected = derive_run(events)
    for seed in range(10):
        shuffled = events[:]
        random.Random(seed).shuffle(shuffled)
        assert derive_run(shuffled) == expected


def test_more_than_50_agents_fold_into_one_other_row_with_an_exact_count() -> None:
    run = make_run_ids()
    events = [ev(run, 1, "run.started", agent_id="root", span_id=...)]
    for i in range(2, 61):
        events.append(ev(run, i, "tool.call.completed", agent_id=f"worker-{i:02d}"))
    summary = derive_run(events).summary
    agents = summary["agents"]
    assert len(agents) == 50 and summary["agent_count"] == 60
    assert [a["agent_id"] for a in agents[:3]] == ["root", "worker-02", "worker-03"]
    other = agents[-1]
    assert other["agent_id"] == "(other)" and other["event_count"] == 11
    assert other["tool_calls"] == 11 and other["state"] == "mixed"
    assert sum(a["event_count"] for a in agents) == 60 == summary["event_count"]


def test_a_run_of_one_agent_has_one_entry() -> None:
    summary = derive_run(lifecycle(("run.started", {}), ("run.completed", {}))).summary
    assert summary["agent_count"] == 1 and summary["agents"][0]["agent_id"] == "coding-agent"


def multi_emitter(child_trace: str | None = None) -> list[Event]:
    """The root's clock is 10 s ahead of its child's; the child has no sequence (D4)."""
    run = make_run_ids()
    deleg, child = span(), span()
    trace_override = {} if child_trace is None else {"trace_id": child_trace}
    return [
        ev(run, 1, "run.started", agent_id="planner", span_id=..., occurred_at=at(100)),
        ev(run, 2, "tool.call.started", agent_id="planner", span_id=deleg, occurred_at=at(101)),
        ev(
            run,
            3,
            "agent.started",
            agent_id="tester",
            span_id=child,
            parent_span_id=deleg,
            sequence=...,
            occurred_at=at(90),
            attributes={},
            **trace_override,
        ),
        ev(
            run,
            4,
            "agent.completed",
            agent_id="tester",
            span_id=child,
            parent_span_id=deleg,
            sequence=...,
            occurred_at=at(95),
            status="success",
            **trace_override,
        ),
        ev(run, 5, "run.completed", agent_id="planner", span_id=..., occurred_at=at(110)),
    ]


def test_a_skewed_child_never_becomes_the_runs_agent_or_trace() -> None:
    events = multi_emitter()
    derived = derive_run(events)
    assert derived.ordering_mode == "time"
    assert sort_first(events).agent_id == "tester"  # the child sorts first under its skewed clock
    assert derived.agent_slug == "planner" and derived.trace_id == events[0].trace_id


def test_a_forged_child_trace_id_never_becomes_the_runs_trace() -> None:
    forged = new_id(IdKind.TRACE)
    events = multi_emitter(child_trace=forged)
    derived = derive_run(events)
    assert derived.trace_id == events[0].trace_id != forged
    assert derived.agent_slug == "planner"


def test_a_duplicated_run_started_cannot_change_the_root() -> None:
    events = multi_emitter()
    run = {"run_id": events[0].run_id, "trace_id": events[0].trace_id}
    replay = ev(
        run,
        9,
        "run.started",
        agent_id="impostor",
        span_id=...,
        sequence=...,
        occurred_at=at(150),
        trace_id=new_id(IdKind.TRACE),
    )
    derived = derive_run([*events, replay])
    assert derived.agent_slug == "planner" and derived.trace_id == events[0].trace_id


def test_a_run_without_run_started_takes_the_first_event_as_before() -> None:
    run = make_run_ids()
    events = [
        ev(run, 1, "tool.call.completed", agent_id="first"),
        ev(run, 2, "tool.call.completed", agent_id="second"),
    ]
    derived = derive_run(events)
    assert derived.agent_slug == "first" and derived.trace_id == events[0].trace_id
    assert [a["agent_id"] for a in derived.summary["agents"]] == ["first", "second"]


def sort_first(events: list[Event]) -> Event:
    from abb_event_schema.ordering import sort_events

    return sort_events(events)[0]


def test_a_sequenceless_earlier_run_started_cannot_displace_the_root() -> None:
    """A remote emitter omits `sequence`; the SDK root always carries one (review F2)."""
    events = multi_emitter()
    run = {"run_id": events[0].run_id, "trace_id": events[0].trace_id}
    forged = ev(
        run,
        9,
        "run.started",
        agent_id="evil",
        span_id=...,
        sequence=...,
        occurred_at=at(50),
        trace_id=new_id(IdKind.TRACE),
    )
    assert sort_first([*events, forged]).agent_id == "evil"  # it does sort first
    derived = derive_run([*events, forged])
    assert derived.agent_slug == "planner" and derived.trace_id == events[0].trace_id


def test_run_started_without_any_sequence_falls_back_to_the_earliest() -> None:
    run = make_run_ids()
    events = [
        ev(run, 1, "run.started", agent_id="late", span_id=..., sequence=..., occurred_at=at(20)),
        ev(run, 2, "run.started", agent_id="early", span_id=..., sequence=..., occurred_at=at(10)),
    ]
    assert derive_run(events).agent_slug == "early"


def test_a_client_child_id_of_other_does_not_collide_with_the_fold_row() -> None:
    run = make_run_ids()
    events = [
        ev(run, 1, "run.started", agent_id="root", span_id=...),
        ev(run, 2, "agent.spawned", agent_id="root", attributes={"agent.child_id": "(other)"}),
    ]
    for i in range(3, 62):
        events.append(ev(run, i, "tool.call.completed", agent_id=f"worker-{i:02d}"))
    ids = [a["agent_id"] for a in derive_run(events).summary["agents"]]
    assert len(ids) == len(set(ids)) and ids.count("(other)") == 1
