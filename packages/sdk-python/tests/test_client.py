"""Public API behaviour: runs, spans, context propagation, modes, observe, never-raise."""

import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

import blackbox
from blackbox import BlackBox, bind, current_run, current_span
from tests.helpers import events_of, offline, types_of


def by_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] == event_type]


def test_a_simple_run_has_ordered_sequences_and_a_completed_end() -> None:
    bb = offline()
    with bb.run("fix", metadata={"issue": 7}, tags=("a",)) as run:
        assert current_run() is run
    assert current_run() is None
    evs = events_of(bb)
    assert types_of(evs) == ["run.started", "run.completed"]
    assert [e["sequence"] for e in evs] == [1, 2]
    started, done = evs
    assert started["attributes"] == {"run.name": "fix", "metadata.issue": 7}
    assert started["tags"] == ["a"] and done["status"] == "success" and done["duration_ms"] >= 0
    assert len({e["trace_id"] for e in evs}) == 1 and started["agent_id"] == "demo"


def test_nested_spans_form_a_tree_and_close_in_order() -> None:
    bb = offline()
    with bb.run("r") as run:
        with run.span("outer", kind="agent") as outer:
            assert current_span() is outer
            with run.span("inner", kind="tool") as inner:
                assert current_span() is inner
                run.event("custom.inside")
            assert current_span() is outer
        assert current_span() is None
    evs = events_of(bb)
    outer_start = by_type(evs, "agent.started")[0]
    inner_start = by_type(evs, "tool.call.started")[0]
    assert "parent_span_id" not in outer_start
    assert inner_start["parent_span_id"] == outer_start["span_id"]
    inside = by_type(evs, "custom.inside")[0]
    assert inside["span_id"] == inner_start["span_id"]
    assert by_type(evs, "tool.call.completed")[0]["span_id"] == inner_start["span_id"]
    assert [e["sequence"] for e in evs] == list(range(1, len(evs) + 1))


def test_exceptions_propagate_unchanged_and_mark_span_and_run_failed() -> None:
    bb = offline()
    error = ValueError("nope")
    with pytest.raises(ValueError) as caught:
        with bb.run("r") as run:
            with run.span("s", kind="tool"):
                raise error
    assert caught.value is error
    evs = events_of(bb)
    failed = by_type(evs, "tool.call.failed")[0]
    assert failed["status"] == "error" and failed["attributes"]["error.type"] == "ValueError"
    assert failed["attributes"]["error.message"] == "nope"
    assert types_of(evs)[-1] == "run.failed"


@pytest.mark.parametrize(
    ("exc", "run_type", "status"),
    [
        (KeyboardInterrupt(), "run.cancelled", "cancelled"),
        (SystemExit(0), "run.completed", "success"),
        (SystemExit(2), "run.cancelled", "cancelled"),
        (asyncio.CancelledError(), "run.cancelled", "cancelled"),
    ],
)
def test_base_exceptions_are_classified_and_still_propagate(
    exc: BaseException, run_type: str, status: str
) -> None:
    bb = offline()
    with pytest.raises(type(exc)):
        with bb.run("r"):
            raise exc
    last = events_of(bb)[-1]
    assert last["event_type"] == run_type and last["status"] == status


def test_ending_a_run_twice_or_explicitly_is_safe() -> None:
    bb = offline()
    run = bb.start_run("r")
    run.end("timeout")
    run.end("success")
    with run:
        pass
    evs = events_of(bb)
    assert types_of(evs) == ["run.started", "run.completed"] and evs[-1]["status"] == "timeout"


def test_span_without_with_block_can_be_started_and_ended_manually() -> None:
    bb = offline()
    run = bb.run("r")
    span = run.span("manual").start()
    span.end()
    span.end()
    assert types_of(events_of(bb)) == ["run.started", "span.started", "span.completed"]


def test_llm_call_records_usage_and_failures() -> None:
    bb = offline()
    with bb.run("r") as run:
        with run.llm_call("anthropic", "claude", temperature=0.5, max_tokens=64) as llm:
            llm.record_usage(100, 50, cached_input_tokens=10, cost_usd=0.002)
            llm.record_usage(-1, True, cost_usd=-3)  # invalid values are ignored
        with pytest.raises(RuntimeError), run.llm_call("anthropic", "claude"):
            raise RuntimeError("overloaded")
    evs = events_of(bb)
    done = by_type(evs, "llm.request.completed")[0]["attributes"]
    assert done["llm.provider"] == "anthropic" and done["llm.input_tokens"] == 100
    assert done["llm.output_tokens"] == 50 and done["cost.estimated_usd"] == 0.002
    assert (
        done["llm.latency_ms"] >= 0
        and by_type(evs, "llm.request.started")[0]["attributes"]["llm.temperature"] == 0.5
    )
    assert by_type(evs, "llm.request.failed")[0]["status"] == "error"


def test_set_attribute_lands_on_the_closing_event() -> None:
    bb = offline()
    with (
        bb.run("r") as run,
        run.span("search", kind="tool", attributes={"tool.operation": "grep"}) as s,
    ):
        s.set_attribute("tool.result_count", 3)
        s.set_attributes({"x.y": True})
    evs = events_of(bb)
    assert by_type(evs, "tool.call.started")[0]["attributes"] == {
        "tool.operation": "grep",
        "tool.name": "search",
    }
    done = by_type(evs, "tool.call.completed")[0]["attributes"]
    assert done["tool.result_count"] == 3 and done["x.y"] is True and "tool.latency_ms" in done


def test_context_flows_through_asyncio_tasks_without_leaking_between_runs() -> None:
    bb = offline()

    async def worker(name: str) -> None:
        async with bb.run(name) as run:
            async with run.span("step"):
                await asyncio.sleep(0.01)
                assert current_run() is run
                await asyncio.gather(child(run))
            await asyncio.sleep(0.01)
            assert current_run() is run and current_span() is None

    async def child(run: blackbox.Run) -> None:
        assert current_run() is run  # asyncio tasks copy the context
        await asyncio.sleep(0)
        run.event("custom.from_task")

    async def main() -> None:
        await asyncio.gather(*(worker(f"r{i}") for i in range(5)))

    asyncio.run(main())
    evs = events_of(bb)
    runs = {e["run_id"] for e in evs}
    assert len(runs) == 5
    for run_id in runs:
        mine = [e for e in evs if e["run_id"] == run_id]
        starts = by_type(mine, "span.started")
        assert (
            len(starts) == 1
            and by_type(mine, "custom.from_task")[0]["span_id"] == starts[0]["span_id"]
        )
    assert current_run() is None


def test_threads_need_bind_and_then_share_the_run() -> None:
    bb = offline()
    seen: dict[str, Any] = {}

    def probe(label: str) -> None:
        seen[label] = current_run()
        run = current_run()
        if run is not None:
            run.event("custom.thread", {"label": label})

    with bb.run("r") as run:
        with ThreadPoolExecutor(2) as pool:
            pool.submit(probe, "plain").result()
            pool.submit(bind(probe), "bound").result()
        t = threading.Thread(target=bind(probe), args=("thread",))
        t.start()
        t.join()
    assert seen["plain"] is None and seen["bound"] is run and seen["thread"] is run
    labels = sorted(e["attributes"]["label"] for e in by_type(events_of(bb), "custom.thread"))
    assert labels == ["bound", "thread"]


def test_exiting_a_span_in_a_different_context_does_not_raise() -> None:
    bb = offline()
    run = bb.run("r")
    span = run.span("s")
    span.__enter__()
    import contextvars

    contextvars.copy_context().run(lambda: span.__exit__(None, None, None))  # wrong context
    assert types_of(events_of(bb))[-1] == "span.completed"  # closed and nothing raised


def test_observe_wraps_sync_and_async_functions_and_ignores_missing_runs() -> None:
    bb = offline()

    @bb.observe(kind="tool")
    def add(a: int, b: int) -> int:
        return a + b

    @bb.observe
    async def aadd(a: int, b: int) -> int:
        return a + b

    @bb.observe(name="custom-name")
    def boom() -> None:
        raise KeyError("k")

    assert add(1, 2) == 3 and asyncio.run(aadd(1, 2)) == 3  # no run: untraced, no events
    assert bb.buffered_events() == []
    assert add.__name__ == "add" and aadd.__name__ == "aadd"
    with bb.run("r"):
        assert add(2, 3) == 5
        assert asyncio.run(aadd(2, 3)) == 5
        with pytest.raises(KeyError):
            boom()
    evs = events_of(bb)
    assert by_type(evs, "tool.call.started")[0]["attributes"]["tool.name"].endswith("add")
    assert any(e["attributes"].get("span.name") == "custom-name" for e in evs)
    assert "span.failed" in types_of(evs)


def test_bb_event_uses_the_current_run_and_is_ignored_otherwise() -> None:
    bb = offline()
    bb.event("custom.orphan")
    with bb.run("r"):
        bb.event("note", {"a": 1})  # bare names become custom.<name>
    assert "custom.note" in types_of(events_of(bb)) and "custom.orphan" not in types_of(
        events_of(bb)
    )


def test_invalid_event_types_are_counted_not_raised() -> None:
    bb = offline()
    with bb.run("r") as run:
        bad_types: tuple[Any, ...] = ("", "Bad.Type", "a" * 80 + ".b", None, 5)
        for bad in bad_types:
            run.event(bad)
    assert bb.stats()["dropped_invalid"] == 5


def test_disabled_mode_records_nothing_and_costs_nothing() -> None:
    bb = BlackBox(mode="disabled")
    assert not bb.enabled
    with bb.run("r") as run, run.span("s"), run.llm_call("p", "m") as llm:
        llm.record_usage(1, 2)
    assert bb.stats()["events_created"] == 0 and bb.buffered_events() == []
    assert bb.flush() and bb.shutdown()


def test_missing_api_key_in_http_mode_disables_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("BLACKBOX_API_KEY", raising=False)
    bb = BlackBox(project="demo")
    assert not bb.enabled and "no API key" in caplog.text
    with bb.run("r"):
        pass
    assert bb.stats()["events_created"] == 0


def test_environment_variables_configure_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLACKBOX_API_KEY", "abb_live_x.y")
    monkeypatch.setenv("BLACKBOX_ENDPOINT", "http://example.invalid:1")
    monkeypatch.setenv("BLACKBOX_MODE", "offline")
    bb = BlackBox()
    assert bb.config.api_key == "abb_live_x.y" and bb.config.mode == "offline"
    assert bb.config.endpoint == "http://example.invalid:1"


def test_invalid_options_fall_back_to_defaults_and_never_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bb = BlackBox(
        api_key="k",
        mode="warp",
        batch_size="lots",
        flush_interval=-1,
        max_queue=3,
        payload_mode="nope",
        http_timeout=float("nan"),
    )
    assert bb.config.mode == "disabled"
    bb2 = BlackBox(
        api_key="k",
        mode="offline",
        batch_size="lots",
        flush_interval=-1,
        max_queue=3,
        payload_mode="nope",
        http_timeout=float("nan"),
        agent_id="My Agent!",
    )
    c = bb2.config
    assert (c.batch_size, c.flush_interval, c.max_queue, c.http_timeout) == (100, 0.25, 10_000, 2.0)
    assert c.payload_mode is blackbox.PayloadMode.METADATA_ONLY and c.agent_id == "my-agent"


def test_local_mode_writes_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    bb = BlackBox(mode="local", local_path=str(path), flush_interval=0.02)
    with bb.run("r") as run:
        run.event("custom.x")
    assert bb.shutdown(3)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["event_type"] for e in lines] == ["run.started", "custom.x", "run.completed"]
    assert bb.stats()["events_written_local"] == 3


def test_local_mode_survives_an_unwritable_path(tmp_path: Path) -> None:
    bb = BlackBox(
        mode="local", local_path=str(tmp_path / "missing" / "t.jsonl"), flush_interval=0.02
    )
    with bb.run("r"):
        pass
    bb.shutdown(3)
    assert bb.stats()["dropped_export_failed"] == 2


def test_internal_failures_never_reach_the_application(monkeypatch: pytest.MonkeyPatch) -> None:
    bb = offline()

    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("sdk bug")

    monkeypatch.setattr("blackbox.client.build_event", explode)
    with bb.run("r") as run, run.span("s") as s, run.llm_call("p", "m"):
        s.set_attribute("a", 1)
        run.event("custom.x")
    assert bb.stats()["internal_errors"] >= 6 and bb.flush() and bb.shutdown()


def test_the_queue_is_bounded_when_nothing_is_ever_sent() -> None:
    bb = offline(max_queue=50)
    with bb.run("r") as run:
        for _ in range(5000):
            run.event("http.request", {"http.method": "GET"})  # P2
    stats = bb.stats()
    assert stats["queue_size"] <= 50 and stats["dropped_p2"] >= 4900
    evs = bb.buffered_events()
    assert "run.started" in types_of(evs)  # lifecycle (P0) survived the flood


def test_p0_events_survive_a_flood_of_everything_else() -> None:
    bb = offline(max_queue=20)
    runs = [bb.run(f"r{i}") for i in range(10)]
    with bb.run("noisy") as noisy:
        for i in range(1000):
            noisy.event("custom.spam" if i % 2 else "http.response", {"http.status_code": 200})
    for r in runs:
        r.end()
    evs = bb.buffered_events()
    got = set(types_of(evs))
    assert "run.completed" in got and len(by_type(evs, "run.started")) == 11
    assert bb.stats()["dropped_p0"] == 0


def test_flush_and_shutdown_in_offline_mode_do_not_block() -> None:
    bb = offline()
    with bb.run("r"):
        pass
    assert bb.flush(0.1) is False  # offline events are never delivered
    assert bb.shutdown(0.1) is True and bb.shutdown(0.1) is True  # idempotent, never blocks
    assert len(bb.buffered_events()) == 2  # still inspectable after shutdown


def test_forked_children_start_with_a_clean_queue_and_their_own_exporter(tmp_path: Path) -> None:
    if not hasattr(os, "fork"):
        pytest.skip("no fork")
    path = tmp_path / "t.jsonl"
    bb = BlackBox(mode="local", local_path=str(path), flush_interval=0.02)
    with bb.run("parent"):
        pass
    bb.flush(2)
    pid = os.fork()
    if pid == 0:  # child: must not crash or resend the parent's events
        code = 1
        try:
            with bb.run("child"):
                pass
            bb.flush(2)
            code = 0
        finally:
            os._exit(code)
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0
    names = [
        json.loads(line).get("attributes", {}).get("run.name")
        for line in path.read_text().splitlines()
    ]
    assert names.count("parent") == 1 and names.count("child") == 1
    bb.shutdown(2)


def test_an_explicit_parent_wins_over_the_current_span() -> None:
    bb = offline()
    with bb.run("r") as run:
        a = run.span("a").start()
        with run.span("b"):
            with run.span("c", parent=a):
                pass
        a.end()
    evs = events_of(bb)
    c = next(e for e in by_type(evs, "span.started") if e["attributes"]["span.name"] == "c")
    assert c["parent_span_id"] == a.span_id


def test_a_span_never_adopts_a_parent_from_another_run() -> None:
    bb = offline()
    with bb.run("outer") as outer, outer.span("outer-span"):
        other = bb.run("other")
        with other.span("inner"):
            pass
    inner = next(
        e for e in by_type(events_of(bb), "span.started") if e["attributes"]["span.name"] == "inner"
    )
    assert "parent_span_id" not in inner


def test_oversized_payloads_are_dropped_but_the_event_survives() -> None:
    bb = offline(payload_mode="full")
    with bb.run("r") as run:
        run.event("custom.big", payload={"text": "x" * 70_000})
        run.event("custom.fine", payload={"text": "x" * 1_000})
    evs = events_of(bb)
    assert "payload" not in by_type(evs, "custom.big")[0]
    assert "payload" in by_type(evs, "custom.fine")[0]
    assert bb.stats()["payloads_dropped"] == 1


def test_option_bounds_are_inclusive() -> None:
    c = offline(batch_size=1, flush_interval=0.01, max_queue=10, http_timeout=0.1).config
    assert (c.batch_size, c.flush_interval, c.max_queue, c.http_timeout) == (1, 0.01, 10, 0.1)
    c2 = offline(batch_size=1000).config
    assert c2.batch_size == 1000
    assert offline(batch_size=1001).config.batch_size == 100  # out of range: default


class Hostile:
    def __str__(self) -> str:
        raise RuntimeError("hostile __str__")

    __repr__ = __str__


HOSTILE: list[Any] = [
    None,
    5,
    2.5,
    float("nan"),
    True,
    "text",
    b"bytes",
    ["a", 1, None],
    ("x",),
    {"a": 1},
    {1: 2},
    {frozenset(): 1},
    {"k": object()},
    [[]],
    [{}],
    {()},
    object(),
    Hostile(),
    print,
    type,
    lambda: 1,
    10**30,
    "x" * 100_000,
    "\x00",
    {"a": {"b": {"c": [Hostile()]}}},
]  # includes unhashable values (lists, dicts, sets) and objects with hostile __str__


def test_hostile_arguments_never_raise_from_any_public_call() -> None:
    bb = offline(payload_mode="full")
    for bad in HOSTILE:
        with bb.run(bad, metadata=bad, agent_id=bad, tags=bad) as run:
            run.end(bad)
            sp = run.span(bad, kind=bad, attributes=bad, parent=bad)
            with sp:
                sp.set_attribute(bad, bad)
                sp.set_attributes(bad)
                sp.set_payload(bad)
                sp.event(bad, bad, payload=bad, status=bad)
            sp.end(bad, bad)
            llm = run.llm_call(bad, bad, temperature=bad, max_tokens=bad, attributes=bad)
            with llm:
                llm.record_usage(bad, bad, cached_input_tokens=bad, cost_usd=bad)
                llm.set_attribute(bad, bad)
            run.event(bad, bad, payload=bad, status=bad)
        bb.event(bad, bad, payload=bad, status=bad)

        @bb.observe(kind=bad, name=bad)
        def traced() -> int:
            return 1

        with bb.run("r"):
            assert traced() == 1
        assert bb.flush(bad) in (True, False) and isinstance(bb.stats(), dict)
    assert bb.shutdown(bad) in (True, False)


@pytest.mark.parametrize("bad", HOSTILE)
def test_hostile_constructor_options_never_raise(bad: Any) -> None:
    for name in (
        "api_key",
        "project",
        "endpoint",
        "mode",
        "agent_id",
        "agent_version",
        "local_path",
        "batch_size",
        "flush_interval",
        "max_queue",
        "http_timeout",
        "max_attempts",
        "payload_mode",
        "deny_keys",
        "allow_keys",
        "redactor",
        "tags",
        "wait",
    ):
        bb = BlackBox(**{name: bad})
        with bb.run("r") as run:
            run.event("custom.x")
        bb.shutdown(0.2)
