"""Mapping, error results, passthrough and containment of the MCP wrapper (ADR-052)."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from abb_conformance import ScenarioError, capture
from blackbox import BlackBox

from blackbox_mcp import instrument
from tests.drivers import FakeSession, real_available, real_session


def of(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] == event_type]


def run_async(coro_fn: Any, **options: Any) -> list[dict[str, Any]]:
    def drive(bb: BlackBox) -> None:
        async def main() -> None:
            async with bb.run("r"):
                await coro_fn(bb)

        asyncio.run(main())

    return capture(drive, **options)


def test_success_records_server_operation_and_result_count() -> None:
    async def go(bb: BlackBox) -> None:
        session = instrument(FakeSession(), bb, server="github")
        assert (await session.call_tool("search", {"q": "x"})).is_error is False

    events = run_async(go)
    started = of(events, "tool.call.started")[0]["attributes"]
    assert started["tool.name"] == "search" and started["mcp.server"] == "github"
    assert started["tool.operation"] == "mcp.call_tool" and started["framework.name"] == "mcp"
    done = of(events, "tool.call.completed")[0]
    assert done["attributes"]["tool.result_count"] == 1 and done["status"] == "success"


@pytest.mark.parametrize("flag", ["is_error", "isError"])
def test_error_results_are_failures_but_still_returned(flag: str) -> None:
    result = SimpleNamespace(content=[], **{flag: True})

    async def go(bb: BlackBox) -> None:
        session = instrument(FakeSession(result=result), bb)
        assert await session.call_tool("t") is result

    failed = of(run_async(go), "tool.call.failed")[0]
    assert (
        failed["status"] == "error" and failed["attributes"]["tool.error_type"] == "ToolResultError"
    )


def test_exceptions_propagate_unchanged_and_cancellation_is_recorded() -> None:
    error = ScenarioError("timeout")

    async def go(bb: BlackBox) -> None:
        with pytest.raises(ScenarioError) as info:
            await instrument(FakeSession(error), bb).call_tool("t")
        assert info.value is error
        with pytest.raises(asyncio.CancelledError):
            await instrument(FakeSession(asyncio.CancelledError()), bb).call_tool("c")

    events = run_async(go)
    assert of(events, "tool.call.failed")[0]["attributes"]["error.type"] == "ScenarioError"
    assert of(events, "tool.call.completed")[0]["status"] == "cancelled"


def test_pass_through_without_a_run_or_when_disabled() -> None:
    async def go() -> None:
        def drive_unused() -> None:
            return None

        bb = BlackBox(api_key="k", mode="offline")
        session = instrument(FakeSession(), bb)
        assert (await session.call_tool("t")).is_error is False
        assert bb.buffered_events() == []
        off = BlackBox(mode="disabled")
        async with off.run("r"):
            await instrument(FakeSession(), off).call_tool("t")
        assert off.buffered_events() == []

    asyncio.run(go())


def test_idempotent_garbage_tolerant_and_sync_methods() -> None:
    class Sync:
        def call_tool(self, name: str, arguments: Any = None) -> Any:
            return SimpleNamespace(content="not a list", is_error=False)

    def drive(bb: BlackBox) -> None:
        for junk in (None, 5, object(), SimpleNamespace(call_tool=None)):
            assert instrument(junk, bb) is junk
        session = FakeSession()
        instrument(instrument(session, bb), bb)
        sync = instrument(Sync(), bb)
        with bb.run("r"):
            assert sync.call_tool("a").is_error is False
            asyncio.run(session.call_tool("b"))

    events = capture(drive)
    assert len(of(events, "tool.call.started")) == 2


def test_bookkeeping_failure_never_breaks_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from blackbox import Run

    def explode(self: Run, *a: Any, **k: Any) -> None:
        raise RuntimeError("sdk bug")

    monkeypatch.setattr(Run, "span", explode)

    async def go(bb: BlackBox) -> None:
        result = await instrument(FakeSession(), bb).call_tool("t")
        assert result.is_error is False

    run_async(go)


def test_payloads_are_off_unless_requested_and_redacted_when_on() -> None:
    async def go(bb: BlackBox, on: bool) -> None:
        session = instrument(FakeSession(), bb, capture_payloads=on)
        await session.call_tool("t", {"note": "private text", "token": "abc"})

    off = run_async(lambda bb: go(bb, False), payload_mode="full")
    assert all("payload" not in e for e in off) and "private text" not in str(off)
    on = run_async(lambda bb: go(bb, True), payload_mode="full")
    payload = of(on, "tool.call.completed")[0]["payload"]
    assert payload["arguments"]["note"] == "private text"
    assert payload["arguments"]["token"] != "abc"  # key-based redaction applies
    assert all("payload" not in e for e in run_async(lambda bb: go(bb, True)))


@pytest.mark.skipif(not real_available(), reason="mcp is not installed")
def test_real_server_tool_error_and_success() -> None:
    def drive(bb: BlackBox) -> None:
        async def main() -> None:
            async with real_session() as raw, bb.run("r"):
                client = instrument(raw, bb, server="local")
                good = await client.call_tool("lookup", {"q": "x"})
                bad = await client.call_tool("broken", {})
                assert good.is_error is False and bad.is_error is True

        asyncio.run(main())

    events = capture(drive)
    assert of(events, "tool.call.completed")[0]["attributes"]["tool.result_count"] == 1
    assert of(events, "tool.call.failed")[0]["attributes"]["tool.name"] == "broken"


def test_failures_while_finishing_a_span_are_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    from blackbox import Span

    def explode(self: Span, *a: Any, **k: Any) -> None:
        raise RuntimeError("sdk bug")

    async def go(bb: BlackBox) -> None:
        ok = instrument(FakeSession(), bb)
        bad = instrument(FakeSession(ScenarioError("x")), bb)
        monkeypatch.setattr(Span, "end", explode)
        monkeypatch.setattr(Span, "set_attribute", explode)
        assert (await ok.call_tool("t")).is_error is False
        with pytest.raises(ScenarioError):
            await bad.call_tool("t")

    run_async(go)


def test_unusual_arguments_and_unrenderable_payloads() -> None:
    class Unrenderable:
        def __str__(self) -> str:
            raise RuntimeError("no")

    async def go(bb: BlackBox) -> None:
        session = instrument(FakeSession(result=Unrenderable()), bb, capture_payloads=True)
        await session.call_tool("t", {"big": "x" * 10_000})
        await session.call_tool(name="  ")

    events = run_async(go, payload_mode="full")
    names = [e["attributes"]["tool.name"] for e in of(events, "tool.call.started")]
    assert names == ["t", "unknown"]
