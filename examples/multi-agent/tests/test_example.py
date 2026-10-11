"""The multi-agent job end to end without a server: parent events in memory, the tester's in a local file."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from abb_event_schema.parse import parse_event_in

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blackbox import BlackBox, PayloadMode

from multi_agent.planner import PLANTED_SECRET, run_job

Events = list[dict[str, Any]]


@pytest.fixture(autouse=True)
def python_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "PATH", str(Path(sys.executable).parent) + ":" + __import__("os").environ["PATH"]
    )
    monkeypatch.delenv("BLACKBOX_TRACE_CONTEXT", raising=False)


def play(
    tmp_path: Path, *, payload_mode: PayloadMode = PayloadMode.METADATA_ONLY, **kwargs: Any
) -> tuple[Events, Events, Any]:
    child_file = tmp_path / "tester.jsonl"
    bb = BlackBox(
        api_key="abb_live_k.secret",
        mode="offline",
        project="demo",
        agent_id="planner",
        payload_mode=payload_mode,
    )
    outcome = run_job(
        bb,
        pace=0,
        payload_mode=payload_mode.value,
        child_env={"BLACKBOX_MODE": "local", "BLACKBOX_LOCAL_PATH": str(child_file)},
        **kwargs,
    )
    parent = sorted(bb.buffered_events(), key=lambda e: e["sequence"])
    child = [json.loads(line) for line in child_file.read_text().splitlines()]
    return parent, child, outcome


def of_type(events: Events, event_type: str) -> Events:
    return [e for e in events if e["event_type"] == event_type]


def test_five_agents_share_one_run_and_every_event_validates(tmp_path: Path) -> None:
    parent, child, outcome = play(tmp_path)
    assert outcome.ok
    events = parent + child
    assert {e["run_id"] for e in events} == {outcome.run_id}
    assert {e["trace_id"] for e in events} == {outcome.trace_id}
    assert {e["agent_id"] for e in events} == {
        "planner",
        "researcher",
        "coder",
        "tester",
        "reviewer",
    }
    for event in events:
        parse_event_in(event)  # the contract, as the server applies it
    spawned = {e["attributes"]["agent.child_id"]: e for e in of_type(events, "agent.spawned")}
    assert set(spawned) == {"researcher", "coder", "tester", "reviewer"}
    assert spawned["tester"]["agent_id"] == "coder"  # the delegator emits agent.spawned
    assert of_type(events, "run.completed")[0]["status"] == "success"


def test_the_subprocess_events_have_no_sequence_and_nest_under_the_coder(tmp_path: Path) -> None:
    parent, child, _ = play(tmp_path)
    assert child and all("sequence" not in e for e in child)
    assert all("sequence" in e for e in parent)
    assert not [e for e in child if e["event_type"].startswith("run.")]  # the root owns the run
    coder_span = of_type(parent, "agent.started")
    coder_span = next(e for e in coder_span if e["agent_id"] == "coder")["span_id"]
    tester_start = next(e for e in child if e["event_type"] == "agent.started")
    assert tester_start["agent_id"] == "tester" and tester_start["parent_span_id"] == coder_span
    shell = next(e for e in child if e["event_type"] == "shell.command.started")
    assert shell["parent_span_id"] == tester_start["span_id"]


def test_workers_nest_under_the_delegating_span_with_their_own_identity(tmp_path: Path) -> None:
    parent, _, _ = play(tmp_path)
    delegate = next(e for e in parent if e["event_type"] == "span.started")["span_id"]
    starts = {e["agent_id"]: e for e in of_type(parent, "agent.started")}
    assert starts["researcher"]["parent_span_id"] == delegate
    assert starts["coder"]["parent_span_id"] == delegate
    assert (
        starts["reviewer"]["parent_span_id"] == delegate
    )  # the asyncio task inherited the context
    agent_spans = {e["span_id"]: e["agent_id"] for e in of_type(parent, "agent.started")}
    for e in of_type(parent, "llm.request.started"):  # a model call carries the agent that made it
        if e["agent_id"] != "planner":
            assert agent_spans[e["parent_span_id"]] == e["agent_id"]
    assert {e["agent_id"] for e in of_type(parent, "llm.request.started")} == {
        "planner",
        "researcher",
        "coder",
        "reviewer",
    }


def test_skew_puts_the_tester_before_its_parent_in_time_but_not_in_the_tree(tmp_path: Path) -> None:
    parent, child, _ = play(tmp_path, skew_ms=1500)
    spawned = next(
        e for e in of_type(parent, "agent.spawned") if e["attributes"]["agent.child_id"] == "tester"
    )
    tester_start = next(e for e in child if e["event_type"] == "agent.started")
    assert tester_start["occurred_at"] < spawned["occurred_at"]  # a clock 1.5 s behind
    coder_span = next(e for e in of_type(parent, "agent.started") if e["agent_id"] == "coder")[
        "span_id"
    ]
    assert tester_start["parent_span_id"] == coder_span


def test_fail_tool_records_one_failed_call_and_the_run_still_succeeds(tmp_path: Path) -> None:
    parent, child, outcome = play(tmp_path, fail_tool=True)
    failed = of_type(parent + child, "tool.call.failed")
    assert len(failed) == 1 and failed[0]["agent_id"] == "researcher"
    assert outcome.ok and of_type(parent, "run.completed")[0]["status"] == "success"


def test_without_the_flag_no_tool_fails(tmp_path: Path) -> None:
    parent, child, _ = play(tmp_path)
    assert not of_type(parent + child, "tool.call.failed")


def test_default_mode_sends_no_payloads(tmp_path: Path) -> None:
    parent, child, _ = play(tmp_path)
    assert not [e for e in parent + child if e.get("payload")]
    assert PLANTED_SECRET not in json.dumps(parent + child)


def test_full_mode_captures_payloads_but_redacts_the_planted_secret(tmp_path: Path) -> None:
    parent, child, _ = play(tmp_path, payload_mode=PayloadMode.FULL)
    with_payload = [e for e in parent + child if e.get("payload")]
    assert {e["event_type"] for e in with_payload} >= {"agent.spawned", "agent.completed"}
    assert any(e["agent_id"] == "tester" for e in with_payload)
    assert PLANTED_SECRET not in json.dumps(parent + child)


def test_the_tester_subprocess_gets_a_filtered_environment_not_the_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The README's claim: tools never inherit every secret the parent holds."""
    import subprocess

    from multi_agent import workers

    monkeypatch.setenv("SOME_SECRET_TOKEN", "planted-not-for-children")
    monkeypatch.setenv("BLACKBOX_API_KEY", "abb_live_k.secret")
    seen: list[dict[str, str]] = []
    real_run = subprocess.run

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(dict(kwargs["env"]))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(workers.subprocess, "run", spy)
    play(tmp_path)
    (env,) = seen
    assert "SOME_SECRET_TOKEN" not in env
    assert set(env) <= {
        "PATH",
        "BLACKBOX_API_KEY",
        "BLACKBOX_ENDPOINT",
        "BLACKBOX_MODE",
        "BLACKBOX_LOCAL_PATH",
        "BLACKBOX_TRACE_CONTEXT",
    }
    assert env["BLACKBOX_TRACE_CONTEXT"]


def test_child_environment_is_the_allowlist_plus_a_fresh_trace_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_agent.workers import child_environment

    monkeypatch.setenv("SOME_SECRET_TOKEN", "planted")
    monkeypatch.setenv("BLACKBOX_TRACE_CONTEXT", "garbage-from-the-parent")
    env = child_environment("fresh-context", {"BLACKBOX_MODE": "local"})
    assert "SOME_SECRET_TOKEN" not in env
    assert env["BLACKBOX_TRACE_CONTEXT"] == "fresh-context"
    assert env["BLACKBOX_MODE"] == "local"
