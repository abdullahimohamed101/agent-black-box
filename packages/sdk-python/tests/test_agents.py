"""Agent spans (Phase 9, D1/D2/D13): own agent id, `agent.spawned`, identity inheritance."""

from typing import Any

import pytest
from abb_event_schema.parse import parse_event_in

from blackbox import AgentSpan, PayloadMode
from tests.helpers import events_of, offline, types_of


def by_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] == event_type]


def test_run_level_agent_spawns_then_starts_with_the_childs_identity() -> None:
    bb = offline(agent_id="planner")
    with bb.run("job") as run:
        with run.agent("researcher") as researcher:
            assert isinstance(researcher, AgentSpan)
    evs = events_of(bb)
    assert types_of(evs) == [
        "run.started",
        "agent.spawned",
        "agent.started",
        "agent.completed",
        "run.completed",
    ]
    spawned, started, completed = evs[1], evs[2], evs[3]
    # the delegating side: the parent's identity, no span at run level
    assert spawned["agent_id"] == "planner"
    assert "span_id" not in spawned and "parent_span_id" not in spawned
    assert spawned["attributes"]["agent.child_id"] == "researcher"
    # the child's side
    assert started["agent_id"] == completed["agent_id"] == "researcher"
    assert started["span_id"] == completed["span_id"]
    assert "parent_span_id" not in started
    assert completed["status"] == "success"
    assert evs[0]["agent_id"] == evs[-1]["agent_id"] == "planner"
    for e in evs:
        parse_event_in(e)


def test_agent_inside_a_span_spawns_in_that_span_and_parents_to_it() -> None:
    bb = offline(agent_id="planner")
    with bb.run("job") as run:
        with run.span("delegate", kind="tool") as tool, run.agent("coder") as coder:
            pass
    evs = events_of(bb)
    spawned = by_type(evs, "agent.spawned")[0]
    started = by_type(evs, "agent.started")[0]
    assert spawned["span_id"] == tool.span_id and spawned["agent_id"] == "planner"
    assert started["parent_span_id"] == tool.span_id and started["span_id"] == coder.span_id
    assert started["agent_id"] == "coder"


def test_nested_events_inherit_the_childs_agent_id_and_version() -> None:
    bb = offline(agent_id="planner", agent_version="1.0")
    with bb.run("job") as run:
        with run.agent("researcher", agent_version="2.5") as researcher:
            with run.llm_call("openai", "gpt-4.1") as llm:
                llm.record_usage(1, 2)
            with run.span("search", kind="tool") as tool:
                tool.event("custom.hit")
            run.event("custom.note")
            researcher.event("custom.direct")
        run.event("custom.after")
    evs = events_of(bb)
    inside = [e for e in evs if e["event_type"] not in ("run.started", "run.completed")]
    for e in inside:
        if e["event_type"] == "agent.spawned":
            assert e["agent_id"] == "planner" and e["agent_version"] == "1.0"
        elif e["event_type"] == "custom.after":
            assert e["agent_id"] == "planner" and e["agent_version"] == "1.0"
        else:
            assert e["agent_id"] == "researcher", e["event_type"]
            assert e["agent_version"] == "2.5", e["event_type"]
    assert by_type(evs, "custom.after")[0]["agent_id"] == "planner"
    # the llm call's parent is the agent span
    llm_started = by_type(evs, "llm.request.started")[0]
    assert llm_started["parent_span_id"] == researcher.span_id


def test_agents_nest_and_each_delegation_is_recorded_by_its_delegator() -> None:
    bb = offline(agent_id="planner")
    with bb.run("job") as run, run.agent("coder"), run.agent("tester"):
        pass
    spawns = by_type(events_of(bb), "agent.spawned")
    assert [(e["agent_id"], e["attributes"]["agent.child_id"]) for e in spawns] == [
        ("planner", "coder"),
        ("coder", "tester"),
    ]


def test_an_agent_that_fails_closes_with_error_status() -> None:
    bb = offline()
    with pytest.raises(RuntimeError):
        with bb.run("job") as run, run.agent("flaky"):
            raise RuntimeError("boom")
    completed = by_type(events_of(bb), "agent.completed")[0]
    assert completed["status"] == "error" and completed["agent_id"] == "flaky"
    assert completed["attributes"]["error.type"] == "RuntimeError"


def test_input_and_result_are_payloads_only_in_full_mode() -> None:
    full = offline(payload_mode=PayloadMode.FULL, agent_id="planner")
    with full.run("job") as run, run.agent("r", input={"q": "oauth"}) as agent:
        agent.set_result({"notes": 3})
    evs = events_of(full)
    assert by_type(evs, "agent.spawned")[0]["payload"] == {"input": {"q": "oauth"}}
    assert by_type(evs, "agent.completed")[0]["payload"] == {"result": {"notes": 3}}

    for mode in (PayloadMode.METADATA_ONLY, PayloadMode.DISABLED):
        bb = offline(payload_mode=mode)
        with bb.run("job") as run, run.agent("r", input={"q": "oauth"}) as agent:
            agent.set_result({"notes": 3})
        assert all("payload" not in e for e in events_of(bb)), mode


def test_secrets_in_the_delegation_input_and_result_are_redacted() -> None:
    bb = offline(payload_mode=PayloadMode.FULL)
    secret = "AKIAIOSFODNN7EXAMPLE"
    with bb.run("job") as run:
        with run.agent("r", input={"task": f"use key {secret}", "api_key": "hunter2"}) as agent:
            agent.set_result({"out": f"key={secret}"})
    blob = repr(events_of(bb))
    assert secret not in blob and "hunter2" not in blob


@pytest.mark.parametrize(
    ("name", "kwargs", "expected"),
    [
        ("Researcher Bot", {}, "researcher-bot"),
        ("planner", {}, "planner-child-1"),  # equals the root's slug
        ("!!!", {}, "planner-child-1"),  # no slug characters
        ("", {}, "planner-child-1"),
        ("ok", {"agent_id": "Explicit.ID"}, "explicit.id"),
        ("ok", {"agent_id": "planner"}, "planner-child-1"),
        ("ok", {"agent_id": "###"}, "ok"),  # unusable explicit id: fall back to the name
        ("x" * 1000, {}, "x" * 64),
    ],
)
def test_child_slugs_are_coerced_and_never_share_the_roots_identity(
    name: str, kwargs: dict[str, Any], expected: str
) -> None:
    bb = offline(agent_id="planner")
    with bb.run("job") as run, run.agent(name, **kwargs):
        pass
    evs = events_of(bb)
    assert by_type(evs, "agent.started")[0]["agent_id"] == expected
    assert by_type(evs, "agent.spawned")[0]["attributes"]["agent.child_id"] == expected
    for e in evs:
        parse_event_in(e)


def test_fallback_slugs_count_per_run_and_stay_within_64_characters() -> None:
    root = "r" * 64
    bb = offline(agent_id=root)
    with bb.run("job") as run:
        for _ in range(3):
            with run.agent(root):
                pass
    ids = [e["agent_id"] for e in by_type(events_of(bb), "agent.started")]
    assert ids == [(root[: 64 - 8] + f"-child-{n}") for n in (1, 2, 3)]
    assert all(len(i) <= 64 for i in ids)


@pytest.mark.parametrize("hostile", [None, 5, b"x", object(), ["a"], "\x00\x1f"])
def test_hostile_arguments_never_raise_and_events_stay_valid(hostile: Any) -> None:
    bb = offline(payload_mode=PayloadMode.FULL)
    with bb.run("job") as run:
        with run.agent(hostile, agent_id=hostile, agent_version=hostile, input=hostile) as a:
            a.set_result(hostile)
        with run.agent("ok", attributes=hostile, parent=hostile):
            pass
    for e in events_of(bb):
        parse_event_in(e)


def test_agent_version_falls_back_to_the_configured_one_and_non_str_is_ignored() -> None:
    bb = offline(agent_version="9.9")
    with bb.run("job") as run, run.agent("a", agent_version=123):  # type: ignore[arg-type]
        pass
    versions = {e["agent_version"] for e in events_of(bb)}
    assert versions == {"9.9"}


def test_a_disabled_client_returns_an_inert_agent() -> None:
    bb = offline(mode="disabled")
    with bb.run("job") as run, run.agent("a", input={"x": 1}) as agent:
        agent.set_result(1)
    assert bb.buffered_events() == []


def test_the_agent_context_is_visible_in_a_thread_through_bind() -> None:
    import threading

    from blackbox import bind

    bb = offline(agent_id="planner")
    with bb.run("job") as run, run.agent("worker"):
        worker = threading.Thread(target=bind(lambda: run.event("custom.in_thread")))
        worker.start()
        worker.join()
        unbound = threading.Thread(target=lambda: run.event("custom.unbound"))
        unbound.start()
        unbound.join()
    evs = events_of(bb)
    assert by_type(evs, "custom.in_thread")[0]["agent_id"] == "worker"
    assert by_type(evs, "custom.unbound")[0]["agent_id"] == "planner"
