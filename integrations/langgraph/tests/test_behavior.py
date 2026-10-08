"""Mapping, ownership, containment and bounds of the LangChain/LangGraph handler (ADR-052)."""

import asyncio
import importlib
import sys
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from abb_conformance import ScenarioError, capture
from abb_conformance.scenarios import LLM_CACHED, LLM_INPUT, LLM_OUTPUT
from blackbox import BlackBox

import blackbox_langgraph.handler as handler_module
from blackbox_langgraph import BlackBoxCallback
from tests.fakes import FakeFramework, llm_result


def by_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] == event_type]


def run_with(fn: Any, **handler_options: Any) -> tuple[list[dict[str, Any]], BlackBoxCallback]:
    holder: list[BlackBoxCallback] = []

    def drive(bb: BlackBox) -> None:
        handler = BlackBoxCallback(bb, **handler_options)
        holder.append(handler)
        fn(FakeFramework(handler), bb)

    return capture(drive), holder[0]


# -- token and cost mapping ----------------------------------------


@pytest.mark.parametrize("shape", ["usage_metadata", "token_usage"])
def test_tokens_map_from_both_langchain_shapes(shape: str) -> None:
    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root, fw.llm(root, result=llm_result(shape=shape)):
            pass

    events, _ = run_with(go)
    attrs = by_type(events, "llm.request.completed")[0]["attributes"]
    assert (attrs["llm.input_tokens"], attrs["llm.output_tokens"]) == (LLM_INPUT, LLM_OUTPUT)
    assert attrs["llm.cached_input_tokens"] == LLM_CACHED
    assert "cost.estimated_usd" not in attrs  # cost is never invented


def test_anthropic_style_usage_names_are_understood() -> None:
    result = SimpleNamespace(
        generations=[], llm_output={"usage": {"input_tokens": 7, "output_tokens": 3}}
    )

    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root, fw.llm(root, result=result):
            pass

    events, _ = run_with(go)
    attrs = by_type(events, "llm.request.completed")[0]["attributes"]
    assert (attrs["llm.input_tokens"], attrs["llm.output_tokens"]) == (7, 3)
    assert "llm.cached_input_tokens" not in attrs


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -5, "output_tokens": True},
        {"input_tokens": "12", "output_tokens": None},
        {"input_tokens": float("nan")},
        {},
    ],
)
def test_invalid_usage_is_ignored_not_fatal(usage: dict[str, Any]) -> None:
    message = SimpleNamespace(usage_metadata=usage)
    result = SimpleNamespace(generations=[[SimpleNamespace(message=message)]], llm_output=None)

    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root, fw.llm(root, result=result):
            pass

    events, handler = run_with(go)
    attrs = by_type(events, "llm.request.completed")[0]["attributes"]
    assert "llm.input_tokens" not in attrs and "llm.output_tokens" not in attrs
    assert handler.errors == 0


def test_cost_fn_receives_provider_model_and_tokens() -> None:
    seen: list[tuple[Any, ...]] = []

    def cost(provider: str, model: str, tin: Any, tout: Any, cached: Any) -> float:
        seen.append((provider, model, tin, tout, cached))
        return 0.0123

    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root, fw.llm(root):
            pass

    events, _ = run_with(go, cost_fn=cost)
    assert seen == [("acme", "m1", LLM_INPUT, LLM_OUTPUT, LLM_CACHED)]
    assert by_type(events, "llm.request.completed")[0]["attributes"]["cost.estimated_usd"] == 0.0123


@pytest.mark.parametrize("bad", [-1.0, None, "free", float("inf")])
def test_unusable_cost_is_dropped(bad: Any) -> None:
    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root, fw.llm(root):
            pass

    events, handler = run_with(go, cost_fn=lambda *a: bad)
    assert "cost.estimated_usd" not in by_type(events, "llm.request.completed")[0]["attributes"]
    assert handler.errors == 0


def test_a_raising_cost_fn_is_contained() -> None:
    def boom(*args: Any) -> float:
        raise RuntimeError("pricing table missing")

    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root, fw.llm(root):
            pass

    events, handler = run_with(go, cost_fn=boom)
    assert handler.errors == 1
    done = by_type(events, "llm.request.completed")[0]
    assert done["attributes"]["llm.input_tokens"] == LLM_INPUT  # the call is still recorded


def test_model_options_come_from_langchain_metadata() -> None:
    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root, fw.llm(root, chat=False):
            pass

    events, _ = run_with(go)
    started = by_type(events, "llm.request.started")[0]["attributes"]
    assert started["llm.temperature"] == 0.2 and started["llm.model"] == "m1"


# -- run ownership, statuses, structure ----------------------------


def test_an_existing_sdk_run_is_used_and_not_ended() -> None:
    def drive(bb: BlackBox) -> None:
        fw = FakeFramework(BlackBoxCallback(bb))
        with bb.run("host-owned"):
            with fw.chain("Graph") as root, fw.tool("t", root):
                pass

    events = capture(drive)
    types = [e["event_type"] for e in events]
    assert types.count("run.started") == 1 and types.count("run.completed") == 1
    assert types[-1] == "run.completed"
    assert by_type(events, "span.started")[0]["attributes"]["span.name"] == "Graph"
    assert (
        by_type(events, "tool.call.started")[0]["parent_span_id"]
        == by_type(events, "span.started")[0]["span_id"]
    )


def test_error_cancellation_and_interrupt_statuses() -> None:
    class GraphInterrupt(Exception):
        pass

    cases = [
        (ValueError("x"), "run.failed", "error"),
        (asyncio.CancelledError(), "run.cancelled", "cancelled"),
        (GraphInterrupt("pause"), "run.cancelled", "cancelled"),
    ]
    for exc, run_type, status in cases:

        def go(fw: FakeFramework, bb: BlackBox, exc: BaseException = exc) -> None:
            try:
                with fw.chain("g") as root, fw.tool("t", root):
                    raise exc
            except BaseException as caught:
                assert caught is exc

        events, _ = run_with(go)
        assert by_type(events, run_type), (exc, [e["event_type"] for e in events])
        assert by_type(
            events, "tool.call.completed" if status == "cancelled" else "tool.call.failed"
        )


def test_hidden_framework_steps_create_no_span_and_children_reattach() -> None:
    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root, fw.chain("step", root, node="step") as step:
            with fw.chain("ChannelWrite", step, tags=["langsmith:hidden"]) as hidden:
                with fw.tool("t", hidden):
                    pass

    events, _ = run_with(go)
    assert [e["attributes"].get("span.name") for e in by_type(events, "span.started")] == ["step"]
    tool = by_type(events, "tool.call.started")[0]
    assert tool["parent_span_id"] == by_type(events, "span.started")[0]["span_id"]


def test_ending_the_run_closes_spans_whose_end_never_arrived() -> None:
    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root:
            fw.tool("lost", root)  # started, end callback never delivered

    events, _ = run_with(go)
    closed = by_type(events, "tool.call.completed")
    assert len(closed) == 1 and closed[0]["status"] == "cancelled"


def test_standalone_model_call_gets_its_own_run() -> None:
    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.llm(None):
            pass

    events, _ = run_with(go)
    assert events[0]["event_type"] == "run.started"
    assert by_type(events, "llm.request.completed") and by_type(events, "run.completed")


def test_retriever_records_result_count() -> None:
    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root, fw.retriever("docs", root, ["a", "b", "c"]):
            pass

    events, _ = run_with(go)
    done = [
        e
        for e in by_type(events, "span.completed")
        if e["attributes"].get("span.kind") == "retrieval"
    ]
    assert done[0]["attributes"]["retrieval.result_count"] == 3


# -- payloads ------------------------------------------------------


def test_payloads_off_by_default_even_in_full_mode() -> None:
    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root, fw.tool("t", root, input_str="top secret text"):
            pass

    events = capture(lambda bb: go(FakeFramework(BlackBoxCallback(bb)), bb), payload_mode="full")
    assert all("payload" not in e for e in events)
    assert "top secret text" not in str(events)


def test_opt_in_payload_is_bounded_redacted_and_gated_by_the_sdk() -> None:
    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with (
            fw.chain("g") as root,
            fw.tool("t", root, input_str="x" * 50_000, output="password=hunter2"),
        ):
            pass

    def drive(mode: str) -> list[dict[str, Any]]:
        return capture(
            lambda bb: go(FakeFramework(BlackBoxCallback(bb, capture_payloads=True)), bb),
            payload_mode=mode,
        )

    full = drive("full")
    payload = by_type(full, "tool.call.completed")[0]["payload"]
    assert len(payload["input"]) <= 4096 and "hunter2" not in payload["output"]
    assert all("payload" not in e for e in drive("metadata_only"))


# -- containment ---------------------------------------------------


def test_sdk_failures_do_not_reach_the_framework(monkeypatch: pytest.MonkeyPatch) -> None:
    def drive(bb: BlackBox) -> None:
        handler = BlackBoxCallback(bb)
        fw = FakeFramework(handler)

        def explode(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("sdk bug")

        monkeypatch.setattr(bb, "run", explode)
        with fw.chain("g") as root, fw.tool("t", root):
            pass
        assert handler.errors >= 1

    capture(drive)


def test_span_start_failure_keeps_the_node_so_children_still_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from blackbox import Span

    original = Span.start
    calls = {"n": 0}

    def flaky(self: Span) -> Span:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original(self)

    monkeypatch.setattr(Span, "start", flaky)

    def go(fw: FakeFramework, bb: BlackBox) -> None:
        with fw.chain("g") as root, fw.chain("step", root) as step, fw.tool("t", step):
            pass

    events, handler = run_with(go)
    assert handler.errors == 1 and by_type(events, "tool.call.completed")


def test_disabled_and_closed_clients_are_silent() -> None:
    for make in (lambda: BlackBox(mode="disabled"), lambda: _closed()):
        bb = make()
        fw = FakeFramework(BlackBoxCallback(bb))
        with fw.chain("g") as root, fw.tool("t", root), fw.llm(root):
            pass
        assert bb.buffered_events() == []


def _closed() -> BlackBox:
    bb = BlackBox(api_key="k", mode="offline")
    bb.shutdown(0)
    return bb


def test_host_exceptions_propagate_unchanged() -> None:
    error = ScenarioError("mine")
    seen: list[BaseException] = []

    def drive(bb: BlackBox) -> None:
        fw = FakeFramework(BlackBoxCallback(bb))
        try:
            with fw.chain("g") as root, fw.tool("t", root):
                raise error
        except ScenarioError as exc:
            seen.append(exc)

    capture(drive)
    assert seen == [error] and seen[0] is error


def test_in_flight_tracking_is_bounded() -> None:
    def drive(bb: BlackBox) -> None:
        handler = BlackBoxCallback(bb, max_tracked=5)
        fw = FakeFramework(handler)
        with fw.chain("g") as root:
            for _ in range(20):
                fw.tool("never-ends", root)
            assert len(handler._nodes) <= 5
        assert handler.dropped >= 15

    capture(drive)


# -- concurrency ---------------------------------------------------


def test_threads_with_separate_roots_get_separate_runs() -> None:
    def drive(bb: BlackBox) -> None:
        fw = FakeFramework(BlackBoxCallback(bb))
        barrier = threading.Barrier(8)

        def one(i: int) -> None:
            barrier.wait()
            with fw.chain(f"g{i}") as root, fw.tool(f"t{i}", root):
                pass

        threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    events = capture(drive)
    runs: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        runs.setdefault(e["run_id"], []).append(e)
    assert len(runs) == 8
    for items in runs.values():
        assert sorted(e["event_type"] for e in items) == sorted(
            ["run.started", "tool.call.started", "tool.call.completed", "run.completed"]
        )
        names = {e["attributes"]["tool.name"] for e in items if e["event_type"].startswith("tool")}
        assert len(names) == 1


def test_async_real_langgraph_concurrent_invocations() -> None:
    pytest.importorskip("langgraph")
    import operator
    from typing import Annotated, TypedDict

    from langchain_core.runnables import RunnableConfig
    from langchain_core.tools import StructuredTool
    from langgraph.graph import END, START, StateGraph

    class State(TypedDict, total=False):
        log: Annotated[list[str], operator.add]

    async def work(q: str) -> str:
        await asyncio.sleep(0.01)
        return q

    tools = [
        StructuredTool.from_function(coroutine=work, name=f"a{i}", description="d")
        for i in range(2)
    ]

    def node(i: int) -> Any:
        async def run(state: State, config: RunnableConfig) -> State:
            await tools[i].ainvoke({"q": "x"}, config)
            return {"log": [str(i)]}

        return run

    graph = StateGraph(State)
    for i in range(2):
        graph.add_node(f"n{i}", node(i))
        graph.add_edge(START, f"n{i}")
        graph.add_edge(f"n{i}", END)
    app = graph.compile()

    def drive(bb: BlackBox) -> None:
        handler = BlackBoxCallback(bb)

        async def main() -> None:
            await asyncio.gather(
                *(app.ainvoke({"log": []}, config={"callbacks": [handler]}) for _ in range(3))
            )

        asyncio.run(main())
        assert handler.errors == 0 and not handler._nodes

    events = capture(drive)
    runs = {e["run_id"] for e in events}
    assert len(runs) == 3
    assert len(by_type(events, "tool.call.completed")) == 6
    assert len(by_type(events, "run.completed")) == 3


# -- hostile input and packaging -----------------------------------


def test_hostile_arguments_never_raise_and_do_not_break_later_calls() -> None:
    def drive(bb: BlackBox) -> None:
        h = BlackBoxCallback(bb)
        junk: list[Any] = [None, object(), 7, "s", [], {}, b"\xff", float("nan"), -1, 10**40]
        for value in junk:
            for call in (h.on_chain_start, h.on_tool_start, h.on_chat_model_start, h.on_llm_start):
                call(
                    value,
                    value,
                    run_id=value,
                    parent_run_id=value,
                    tags=value,
                    metadata=value,
                    name=value,
                )
            for call in (h.on_chain_end, h.on_tool_end, h.on_llm_end, h.on_retriever_end):
                call(value, run_id=value)
            for call in (h.on_chain_error, h.on_tool_error, h.on_llm_error, h.on_retriever_error):
                call(value, run_id=value)
        h.on_chain_start({"name": "ok"}, {}, run_id="fine", parent_run_id=None)
        h.on_chain_end({}, run_id="fine")

    capture(drive)


def test_the_adapter_imports_and_works_without_the_framework(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in [m for m in sys.modules if m.startswith("langchain_core")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "langchain_core", None)
    try:
        module = importlib.reload(handler_module)
        assert module._Base is object
        events, _ = _drive_with(module.BlackBoxCallbackHandler)
        assert by_type(events, "tool.call.completed")
    finally:
        monkeypatch.undo()
        importlib.reload(handler_module)


def _drive_with(cls: Any) -> tuple[list[dict[str, Any]], Any]:
    holder: list[Any] = []

    def drive(bb: BlackBox) -> None:
        handler = cls(bb)
        holder.append(handler)
        fw = FakeFramework(handler)
        with fw.chain("g") as root, fw.tool("t", root):
            pass

    return capture(drive), holder[0]


def test_subclasses_the_framework_handler_when_installed() -> None:
    base = pytest.importorskip("langchain_core.callbacks.base")
    assert issubclass(BlackBoxCallback, base.BaseCallbackHandler)
