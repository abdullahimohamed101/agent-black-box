"""Trace-context propagation (Phase 9, D3/D4/D13): threads, asyncio tasks, forks, subprocesses."""

import asyncio
import json
import logging
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from abb_event_schema.parse import parse_event_in

import blackbox
from blackbox import (
    TRACE_CONTEXT_ENV,
    TRACE_CONTEXT_HEADER,
    BlackBox,
    PayloadMode,
    TraceContext,
    bind,
    trace_context_from_env,
)
from tests.helpers import offline

RUN = "run_01J9ZZZZZZZZZZZZZZZZZZZZZY"
TRACE = "trc_01J9ZZZZZZZZZZZZZZZZZZZZZY"
SPAN = "spn_01J9ZZZZZZZZZZZZZZZZZZZZZY"


def taken(bb: BlackBox) -> list[dict[str, Any]]:
    """Queued events in creation order (events of a remote emitter have no sequence)."""
    return bb.buffered_events()


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["event_type"] for e in events]


# ------------------------------------------------------------------------------ the wire form


def test_the_header_and_variable_names_are_the_documented_ones() -> None:
    assert TRACE_CONTEXT_ENV == "BLACKBOX_TRACE_CONTEXT"
    assert TRACE_CONTEXT_HEADER == "X-Blackbox-Trace-Context"


@pytest.mark.parametrize(
    "context",
    [
        TraceContext(RUN, TRACE),
        TraceContext(RUN, TRACE, SPAN),
        TraceContext(RUN, TRACE, None, "coder"),
        TraceContext(RUN, TRACE, SPAN, "a" * 64),
    ],
)
def test_a_context_round_trips_and_stays_within_the_length_bound(context: TraceContext) -> None:
    text = context.serialize()
    assert text.startswith("abb1;run=") and len(text) <= 256
    assert TraceContext.parse(text) == context


def test_the_serialized_form_is_the_documented_one() -> None:
    text = TraceContext(RUN, TRACE, SPAN, "coder").serialize()
    assert text == f"abb1;run={RUN};trace={TRACE};span={SPAN};agent=coder"


MALFORMED = [
    "",
    "abb1",
    "abb1;",
    "abb2;run=" + RUN + ";trace=" + TRACE,
    f"abb1;run={RUN}",  # no trace
    f"abb1;trace={TRACE}",  # no run
    f"abb1;run={RUN};run={RUN};trace={TRACE}",  # duplicate key
    f"abb1;run={TRACE};trace={TRACE}",  # wrong id prefix
    f"abb1;run={RUN};trace={RUN}",
    f"abb1;run={RUN};trace={TRACE};span={RUN}",
    f"abb1;run={RUN};trace={TRACE};agent=Bad Slug",
    f"abb1;run={RUN};trace={TRACE};agent=" + "a" * 65,
    f"abb1;run={RUN};trace={TRACE};agent=",
    f"abb1;run={RUN};trace={TRACE};evil=1",  # unknown key
    f"abb1;run={RUN};trace={TRACE};span={SPAN};agent=a;extra=1",  # too many parts
    f"abb1;run={RUN}\x00;trace={TRACE}",  # NUL
    f"abb1;run={RUN};trace={TRACE};span=spn_{'Z' * 26}",  # not a valid ULID start
    f"abb1;run=run_{'0' * 25};trace={TRACE}",  # short ulid
    f"abb1;run={RUN};trace=trc_{'I' * 26}",  # excluded alphabet
    f"abb1;run={RUN};trace={TRACE}" + ";" * 300,  # overlong
    "x" * 10_000_000,  # 10 MB
    f"abb1;run=run;trace={TRACE}=x;span=;;",
    f"abb1;run={RUN};trace={TRACE};span={SPAN};span={SPAN}",
]


@pytest.mark.parametrize("value", MALFORMED)
def test_malformed_contexts_parse_to_none_quickly_without_echoing_the_value(
    value: str, caplog: pytest.LogCaptureFixture
) -> None:
    started = time.perf_counter()
    with caplog.at_level(logging.DEBUG, logger="blackbox"):
        assert TraceContext.parse(value) is None
    assert time.perf_counter() - started < 0.5
    assert "evil" not in caplog.text and RUN not in caplog.text
    assert len(caplog.text) < 200


@pytest.mark.parametrize("value", [None, 5, b"abb1", ["abb1"], object()])
def test_non_string_input_parses_to_none(value: object) -> None:
    assert TraceContext.parse(value) is None


def test_the_environment_reader_returns_none_when_unset_or_invalid() -> None:
    assert trace_context_from_env({}) is None
    assert trace_context_from_env({TRACE_CONTEXT_ENV: "garbage"}) is None
    good = TraceContext(RUN, TRACE, SPAN, "coder")
    assert trace_context_from_env({TRACE_CONTEXT_ENV: good.serialize()}) == good


# ------------------------------------------------------------------------------ spawn_context


def test_spawn_context_records_the_delegation_and_returns_a_context_for_the_child() -> None:
    bb = offline(agent_id="planner", payload_mode=PayloadMode.FULL)
    with bb.run("job") as run:
        with run.span("delegate", kind="tool") as tool:
            text = run.spawn_context("Tester", input={"cmd": "pytest"})
    evs = taken(bb)
    (spawned,) = [e for e in evs if e["event_type"] == "agent.spawned"]
    context = TraceContext.parse(text)
    assert context == TraceContext(run.run_id, run.trace_id, tool.span_id, "tester")
    assert spawned["agent_id"] == "planner" and spawned["span_id"] == tool.span_id
    assert spawned["attributes"]["agent.child_id"] == "tester"
    assert spawned["payload"] == {"input": {"cmd": "pytest"}}
    parse_event_in(spawned)


def test_spawn_context_without_a_span_or_name_uses_the_run_level_and_the_child_slug() -> None:
    bb = offline(agent_id="planner")
    with bb.run("job") as run:
        context = TraceContext.parse(run.spawn_context())
    assert context is not None
    assert context.parent_span_id is None and context.agent_id == "planner-child-1"
    spawned = next(e for e in taken(bb) if e["event_type"] == "agent.spawned")
    assert "span_id" not in spawned


def test_spawn_context_never_raises_on_hostile_arguments() -> None:
    bb = offline()
    with bb.run("job") as run:
        for hostile in (None, 3, b"x", object(), "###", "\x00"):
            text = run.spawn_context(hostile, parent=hostile, input=object())  # type: ignore[arg-type]
            assert TraceContext.parse(text) is not None
    for e in taken(bb):
        parse_event_in(e)


def test_trace_context_alone_emits_nothing() -> None:
    bb = offline()
    with bb.run("job") as run, run.span("s") as span:
        before = len(bb._buffer)
        context = TraceContext.parse(run.trace_context())
        assert len(bb._buffer) == before
    assert context == TraceContext(run.run_id, run.trace_id, span.span_id, None)


# ------------------------------------------------------------------------------ continue_run


def child_run(bb: BlackBox, **kw: Any) -> blackbox.Run:
    return bb.continue_run(TraceContext(RUN, TRACE, SPAN, "tester"), **kw)


def test_a_continued_run_emits_no_lifecycle_events_and_is_never_ended() -> None:
    bb = offline()
    run = child_run(bb)
    assert run.run_id == RUN and run.trace_id == TRACE and run.agent_id == "tester"
    assert taken(bb) == []
    run.end("error")
    run.end()
    assert run.ended is False
    assert bb.stats()["remote_run_end_ignored"] == 2
    assert not any(t.startswith("run.") for t in types(taken(bb)))


def test_a_continued_run_parents_root_spans_to_the_context_span_and_omits_sequence() -> None:
    bb = offline()
    run = child_run(bb)
    with run.span("pytest", kind="shell") as outer, run.span("inner", kind="tool"):
        pass
    evs = taken(bb)
    assert all("sequence" not in e for e in evs)
    started = {e["span_id"]: e for e in evs if e["event_type"].endswith(".started")}
    assert started[outer.span_id]["parent_span_id"] == SPAN
    assert {e["agent_id"] for e in evs} == {"tester"}
    assert {e["run_id"] for e in evs} == {RUN} and {e["trace_id"] for e in evs} == {TRACE}
    for e in evs:
        parse_event_in(e)


def test_entering_a_continued_run_opens_the_agent_span_and_exit_closes_it_with_the_outcome() -> (
    None
):
    bb = offline()
    with pytest.raises(ValueError):
        with child_run(bb) as run:
            with run.span("work", kind="tool"):
                pass
            raise ValueError("boom")
    evs = taken(bb)
    assert types(evs) == [
        "agent.started",
        "tool.call.started",
        "tool.call.completed",
        "agent.completed",
    ]
    agent_started, tool_started, _, agent_done = evs
    assert agent_started["parent_span_id"] == SPAN and agent_started["agent_id"] == "tester"
    assert tool_started["parent_span_id"] == agent_started["span_id"]  # nested in the agent span
    assert agent_done["status"] == "error" and agent_done["span_id"] == agent_started["span_id"]
    assert not any(t.startswith("run.") or t == "agent.spawned" for t in types(evs))
    assert bb.stats()["remote_run_end_ignored"] == 1


def test_a_continued_run_without_an_agent_id_opens_no_agent_span() -> None:
    bb = offline(agent_id="worker")
    with bb.continue_run(TraceContext(RUN, TRACE, SPAN)) as run:
        run.event("custom.hi")
    assert types(taken(bb)) == ["custom.hi"]
    assert bb.continue_run(TraceContext(RUN, TRACE), agent_id="Named Agent").agent_id == (
        "named-agent"
    )


def test_continue_run_accepts_a_string_and_an_invalid_one_gives_an_inert_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bb = offline()
    run = bb.continue_run(TraceContext(RUN, TRACE, None, "a").serialize())
    assert run.run_id == RUN
    for bad in ("nonsense-secret-token", None, 5):
        inert = bb.continue_run(bad)  # type: ignore[arg-type]
        with inert as r, r.span("x"), r.agent("y"):
            r.event("custom.nothing")
        inert.spawn_context("z")
    assert taken(bb) == []
    assert "nonsense-secret-token" not in caplog.text


def test_a_disabled_client_continues_into_an_inert_run() -> None:
    bb = offline(mode="disabled")
    with bb.continue_run(TraceContext(RUN, TRACE, SPAN, "a")) as run:
        run.event("custom.x")
    assert bb.buffered_events() == []


def test_a_continued_run_can_delegate_again() -> None:
    bb = offline()
    with child_run(bb) as run:
        text = run.spawn_context("grandchild")
    evs = taken(bb)
    spawned = next(e for e in evs if e["event_type"] == "agent.spawned")
    started = next(e for e in evs if e["event_type"] == "agent.started")
    assert spawned["agent_id"] == "tester" and spawned["span_id"] == started["span_id"]
    context = TraceContext.parse(text)
    assert context is not None and context.parent_span_id == started["span_id"]


# ------------------------------------------------------------- threads, tasks, fork, processes


def test_a_thread_through_bind_and_an_asyncio_task_share_the_run_and_the_current_agent() -> None:
    bb = offline(agent_id="planner")
    seen: dict[str, Any] = {}

    async def in_task() -> None:
        run = blackbox.current_run()
        seen["task"] = (run, blackbox.current_span())
        assert run is not None
        run.event("custom.in_task")

    def in_thread() -> None:
        seen["thread"] = (blackbox.current_run(), blackbox.current_span())
        assert blackbox.current_run() is not None
        blackbox.current_run().event("custom.in_thread")  # type: ignore[union-attr]

    with bb.run("job") as run, run.agent("worker") as worker:
        t = threading.Thread(target=bind(in_thread))
        t.start()
        t.join()
        asyncio.run(_wrap(in_task))
    assert seen["thread"] == (run, worker) and seen["task"] == (run, worker)
    by = {e["event_type"]: e for e in taken(bb)}
    assert by["custom.in_thread"]["agent_id"] == "worker"
    assert by["custom.in_task"]["agent_id"] == "worker"
    assert by["custom.in_thread"]["span_id"] == worker.span_id
    # in-process emitters keep their sequence
    assert all("sequence" in e for e in taken(bb))


async def _wrap(fn: Any) -> None:
    await asyncio.create_task(fn())


@pytest.mark.skipif(not hasattr(os, "fork"), reason="no fork")
def test_a_forked_child_that_keeps_using_the_parents_run_omits_sequence(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    bb = BlackBox(mode="local", local_path=str(path), flush_interval=0.02)
    with bb.run("parent") as run:
        run.event("custom.before_fork")
        pid = os.fork()
        if pid == 0:  # child: ends with os._exit so the parent's atexit handlers run once
            code = 1
            try:
                run.event("custom.in_child")
                bb.flush(2)
                code = 0
            finally:
                os._exit(code)
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0
        run.event("custom.after_fork")
    bb.flush(2)
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    by = {e["event_type"]: e for e in lines}
    assert "sequence" not in by["custom.in_child"]
    assert by["custom.before_fork"]["sequence"] < by["custom.after_fork"]["sequence"]
    assert by["custom.in_child"]["run_id"] == by["custom.before_fork"]["run_id"]
    bb.shutdown(2)


CHILD = textwrap.dedent(
    """
    import sys
    import blackbox

    bb = blackbox.BlackBox(mode="local", local_path=sys.argv[1], flush_interval=0.02)
    with bb.continue_run(blackbox.trace_context_from_env()) as run:
        with run.span("pytest", kind="shell"):
            pass
    bb.shutdown(5)
    """
)


def test_a_real_subprocess_started_with_the_context_produces_nested_events(tmp_path: Path) -> None:
    parent_path = tmp_path / "parent.jsonl"
    child_path = tmp_path / "child.jsonl"
    bb = BlackBox(mode="local", local_path=str(parent_path), flush_interval=0.02, agent_id="coder")
    with bb.run("job") as run:
        with run.span("run tests", kind="tool") as tool:
            context = run.spawn_context("tester")
            env = {**os.environ, TRACE_CONTEXT_ENV: context}
            result = subprocess.run(  # noqa: S603
                [sys.executable, "-c", CHILD, str(child_path)],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
    assert result.returncode == 0, result.stderr
    bb.shutdown(5)
    parent = [json.loads(x) for x in parent_path.read_text().splitlines()]
    child = [json.loads(x) for x in child_path.read_text().splitlines()]
    assert [e["event_type"] for e in child] == [
        "agent.started",
        "shell.command.started",
        "shell.command.completed",
        "agent.completed",
    ]
    assert all("sequence" not in e for e in child)
    assert {e["run_id"] for e in child} == {run.run_id}
    assert {e["trace_id"] for e in child} == {run.trace_id}
    agent_started = child[0]
    assert agent_started["agent_id"] == "tester" and agent_started["parent_span_id"] == tool.span_id
    assert child[1]["parent_span_id"] == agent_started["span_id"]
    spawned = next(e for e in parent if e["event_type"] == "agent.spawned")
    assert spawned["attributes"]["agent.child_id"] == "tester"
    for e in [*parent, *child]:
        parse_event_in(e)


def test_an_inert_continued_run_hands_out_no_context(caplog: pytest.LogCaptureFixture) -> None:
    """A context for a random nonexistent run would let a child write a phantom run (review F5)."""
    bb = offline()
    inert = bb.continue_run("nonsense")
    assert inert.spawn_context("z") == "" and inert.trace_context() == ""
    assert bb.continue_run(inert.spawn_context("z")).spawn_context("w") == ""  # inert all the way
    assert taken(bb) == []


def test_a_parent_span_of_another_run_is_ignored_not_adopted() -> None:
    """The SDK coerces arguments and never raises into the host (review F6)."""
    bb = offline()
    with bb.run("a") as run_a, bb.run("b") as run_b:
        foreign = run_a.span("foreign")
        foreign.start()
        context = TraceContext.parse(run_b.spawn_context("w", parent=foreign))
        assert context is not None and context.parent_span_id != foreign.span_id
        assert context.run_id == run_b.run_id
        child = run_b.agent("w2", parent=foreign)
        assert child._parent is None
        span = run_b.span("s", parent=foreign)
        assert span._parent is None and span.agent_id == run_b.agent_id
        foreign.end()
