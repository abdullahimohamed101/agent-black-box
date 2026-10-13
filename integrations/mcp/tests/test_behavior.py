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


def test_nothing_is_emitted_after_the_run_ended() -> None:
    from blackbox import current_run

    class EndsRun(FakeSession):
        async def call_tool(self, name: Any = None, arguments: Any = None, **kw: Any) -> Any:
            run = current_run()
            assert run is not None
            run.end()
            if self.error is not None:
                raise self.error
            return self.result

    def drive(bb: BlackBox) -> None:
        async def main() -> None:
            async with bb.run("r"):
                ok = instrument(EndsRun(), bb)
                await ok.call_tool("t")
                bad = instrument(EndsRun(ScenarioError("x")), bb)
                with pytest.raises(ScenarioError):  # run already ended: pass-through, no events
                    await bad.call_tool("t")
            async with bb.run("second"):
                failing = instrument(EndsRun(ScenarioError("y")), bb)
                with pytest.raises(ScenarioError):  # fails after ending its own run
                    await failing.call_tool("u")

        asyncio.run(main())

    events = capture(drive)
    assert [e["event_type"] for e in events][-1] == "run.completed"
    assert len(of(events, "tool.call.started")) == 2
    assert not of(events, "tool.call.completed") and not of(events, "tool.call.failed")


# -- policy (Phase 14, D5): tool calls are guarded as `tool.<name>` ---------------------------


def guarded_session(
    stub: Any, session: Any = None, **options: Any
) -> tuple[BlackBox, Any, FakeSession]:
    bb = stub.client(**options)
    fake = session or FakeSession()
    return bb, instrument(fake, bb), fake


def test_policy_asks_before_each_call_with_the_dry_run_intent() -> None:
    from abb_conformance import policy_stub

    with policy_stub() as stub:
        bb, session, fake = guarded_session(stub)

        async def go() -> None:
            async with bb.run("r"):
                await session.call_tool("search issues!", {"q": "SECRET-ARG"})

        asyncio.run(go())
        bb.shutdown(1)
        assert len(stub.intents) == 1 and len(fake.calls) == 1
        asked = stub.intents[0]
        assert asked["capability"] == "tool.search_issues_" and asked["resource"] == "mcp.call_tool"
        assert asked["arguments"] == {} and asked["risk_class"] is None
        assert "SECRET-ARG" not in str(stub.intents)


def test_a_denied_call_is_not_made_and_returns_an_mcp_error_result() -> None:
    from abb_conformance import policy_stub

    with policy_stub({"tool.delete_all": "Deleting is not allowed."}) as stub:
        bb, session, fake = guarded_session(stub)
        results: list[Any] = []

        async def go() -> None:
            async with bb.run("r"):
                results.append(await session.call_tool("delete_all", {"confirm": True}))
                results.append(await session.call_tool("search", {}))

        asyncio.run(go())
        bb.shutdown(1)
        assert [c[0] for c in fake.calls] == ["search"]  # the denied tool never ran
        blocked = results[0]
        assert (blocked.is_error if hasattr(blocked, "is_error") else blocked.isError) is True
        text = blocked.content[0].text
        assert "Blocked by policy (stub-rule v1): Deleting is not allowed." in text
        assert "confirm" not in text
        assert results[1].is_error is False
        started = [e for e in stub.events if e["event_type"] == "tool.call.started"]
        assert [e["attributes"]["tool.name"] for e in started] == ["search"]


def test_a_closed_fail_mode_blocks_the_call_without_raising() -> None:
    with_stub_down = BlackBox(
        api_key="k.s", mode="offline", policy_mode="enforce", policy_fail_modes={"tool.*": "closed"}
    )
    fake = FakeSession()
    session = instrument(fake, with_stub_down)

    async def go() -> Any:
        async with with_stub_down.run("r"):
            return await session.call_tool("anything", {})

    result = asyncio.run(go())
    assert result.is_error is True and "could not be asked" in result.content[0].text
    assert fake.calls == []


def test_an_open_fail_mode_lets_the_call_through() -> None:
    bb = BlackBox(api_key="k.s", mode="offline", policy_mode="enforce")
    fake = FakeSession()
    session = instrument(fake, bb)

    async def go() -> Any:
        async with bb.run("r"):
            return await session.call_tool("anything", {})

    assert asyncio.run(go()).is_error is False and len(fake.calls) == 1


def test_policy_off_and_no_run_do_not_ask() -> None:
    from abb_conformance import policy_stub

    with policy_stub({"tool.x": "no"}) as stub:
        off = BlackBox(api_key="k.s", endpoint=stub.url, mode="http")  # policy_mode defaults off
        fake = FakeSession()
        session = instrument(fake, off)
        on, session_on, fake_on = guarded_session(stub)

        async def go() -> None:
            async with off.run("r"):
                await session.call_tool("x", {})
            await session_on.call_tool("x", {})  # enforcing, but no run to bind a decision to

        asyncio.run(go())
        assert stub.intents == [] and len(fake.calls) == 1 and len(fake_on.calls) == 1
        off.shutdown(1)
        on.shutdown(1)


def test_a_sync_session_is_guarded_too() -> None:
    from abb_conformance import policy_stub

    class SyncSession:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def call_tool(self, name: str, arguments: Any = None) -> Any:
            self.calls.append(name)
            return SimpleNamespace(content=[], is_error=False)

    with policy_stub({"tool.rm": "no"}) as stub:
        bb = stub.client()
        sync = SyncSession()
        session = instrument(sync, bb)
        with bb.run("r"):
            blocked = session.call_tool("rm", {})
            fine = session.call_tool("ls", {})
        bb.shutdown(1)
        assert sync.calls == ["ls"] and blocked.is_error is True and fine.is_error is False


def test_a_hostile_tool_name_cannot_break_the_guard() -> None:
    from abb_conformance import policy_stub

    with policy_stub() as stub:
        bb, session, fake = guarded_session(stub)

        async def go() -> None:
            async with bb.run("r"):
                for name in (None, 123, ["x"], "", "   ", "x" * 5000, "a b\nc\x00d", "..."):
                    await session.call_tool(name, {})

        asyncio.run(go())
        bb.shutdown(1)
        assert len(fake.calls) == 8
        capabilities = [i["capability"] for i in stub.intents]
        # names that cannot make a valid capability (`tool.` + 5,000 characters, `...`) are never
        # sent: the fail mode applies (open for an unclassified tool call) and the call is made
        assert capabilities.count("tool.unknown") == 5 and "tool.a_b_c_d" in capabilities
        assert all(len(c) <= 128 for c in capabilities)


def test_the_guard_is_cooperative_only_instrumented_sessions_are_asked() -> None:
    from abb_conformance import policy_stub

    with policy_stub({"tool.x": "no"}) as stub:
        bb = stub.client()
        raw = FakeSession()

        async def go() -> None:
            async with bb.run("r"):
                await raw.call_tool("x", {})  # not instrumented: nothing asks

        asyncio.run(go())
        assert stub.intents == [] and len(raw.calls) == 1
        bb.shutdown(1)


def test_the_error_result_falls_back_when_the_mcp_package_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from abb_conformance import policy_stub

    monkeypatch.setitem(sys.modules, "mcp.types", None)  # `from mcp.types import ...` fails
    with policy_stub({"tool.x": "no"}) as stub:
        bb, session, _fake = guarded_session(stub)

        async def go() -> Any:
            async with bb.run("r"):
                return await session.call_tool("x", {})

        result = asyncio.run(go())
        bb.shutdown(1)
    assert result.is_error is True and result.isError is True
    assert result.content[0].type == "text" and "Blocked by policy" in result.content[0].text
