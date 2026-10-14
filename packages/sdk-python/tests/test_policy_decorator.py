"""@guarded: sync, async and methods; callables for resource and arguments (D5)."""

import asyncio
import inspect
from collections.abc import Iterator
from typing import Any

import pytest

import blackbox
from blackbox import BlackBox, PolicyDenied, PolicyUnavailable, guarded
from tests.policy_server import PolicyServer, R, deny, make_client


@pytest.fixture
def srv() -> Iterator[PolicyServer]:
    server = PolicyServer()
    yield server
    server.stop()


def client(srv: PolicyServer, **options: Any) -> BlackBox:
    options.setdefault("policy_timeout", 0.5)
    return make_client(srv, **options)


def test_sync_function_runs_after_an_allow(srv: PolicyServer) -> None:
    calls: list[tuple[Any, ...]] = []

    @guarded("github.merge", resource="repo#1", risk_class="R3", arguments={"k": 1})
    def merge(repo: str, number: int = 0) -> str:
        """Merges."""
        calls.append((repo, number))
        return "merged"

    bb = client(srv)
    with bb.run("t"):
        assert merge("r", number=2) == "merged"
    assert calls == [("r", 2)]
    body = srv.of("decide")[0].body
    assert body is not None
    assert (body["capability"], body["resource"], body["risk_class"], body["arguments"]) == (
        "github.merge", "repo#1", "R3", {"k": 1},
    )  # fmt: skip
    assert merge.__name__ == "merge" and merge.__doc__ == "Merges."
    assert list(inspect.signature(merge).parameters) == ["repo", "number"]
    bb.shutdown()


def test_a_denied_function_does_not_run(srv: PolicyServer) -> None:
    ran: list[int] = []

    @guarded("db.drop", "orders")
    def drop() -> None:
        ran.append(1)

    @guarded("db.drop", "orders")
    async def adrop() -> None:
        ran.append(2)

    srv.queue("decide", deny(), deny())
    bb = client(srv)
    with bb.run("t"):
        with pytest.raises(PolicyDenied):
            drop()
        with pytest.raises(PolicyDenied):
            asyncio.run(_in_run(bb, adrop))
    assert ran == []
    bb.shutdown()


async def _in_run(bb: BlackBox, fn: Any) -> Any:
    async with bb.run("a"):
        return await fn()


def test_async_function_is_guarded_and_still_a_coroutine_function(srv: PolicyServer) -> None:
    @guarded("tool.search", lambda q: q, arguments=lambda q: {"len": len(q)})
    async def search(q: str) -> str:
        await asyncio.sleep(0)
        return q.upper()

    assert inspect.iscoroutinefunction(search)
    bb = client(srv)

    async def main() -> str:
        async with bb.run("t"):
            return await search("abc")

    assert asyncio.run(main()) == "ABC"
    body = srv.of("decide")[0].body
    assert body is not None and body["resource"] == "abc" and body["arguments"] == {"len": 3}
    bb.shutdown()


def test_methods_receive_self_in_the_callables(srv: PolicyServer) -> None:
    class Tools:
        prefix = "p-"

        @guarded(
            "tool.read",
            resource=lambda self, name: self.prefix + name,
            arguments=lambda self, name: {"n": name},
        )
        def read(self, name: str) -> str:
            return name

    bb = client(srv)
    with bb.run("t"):
        assert Tools().read("x") == "x"
    body = srv.of("decide")[0].body
    assert body is not None and body["resource"] == "p-x" and body["arguments"] == {"n": "x"}
    bb.shutdown()


def test_a_callable_that_raises_follows_the_fail_mode_and_never_sends(srv: PolicyServer) -> None:
    @guarded("tool.x", resource=lambda: 1 / 0, risk_class="R3")
    def strict() -> str:
        return "ran"

    @guarded("tool.x", resource=lambda: 1 / 0, risk_class="R0")
    def relaxed() -> str:
        return "ran"

    bb = client(srv)
    with bb.run("t"):
        with pytest.raises(PolicyUnavailable) as info:
            strict()
        assert relaxed() == "ran"
    assert info.value.reason == "invalid_intent" and srv.of("decide") == []
    bb.shutdown()


def test_without_a_run_or_with_policy_off_the_function_just_runs(srv: PolicyServer) -> None:
    @guarded("tool.x", "r", "R4")
    def f() -> int:
        return 7

    assert f() == 7  # no run, no client
    off = BlackBox(api_key="k.s", endpoint=srv.url, mode="http")
    with off.run("t"):
        assert f() == 7
    assert srv.of("decide") == []
    off.shutdown()


def test_an_explicit_client_and_on_error(srv: PolicyServer) -> None:
    bb = client(srv)

    @guarded("tool.x", "r", "R4", on_error="open", client=bb)
    def f() -> int:
        return 7

    srv.default["decide"] = R(drop=True)
    with bb.run("t"):
        assert f() == 7  # on_error=open beats the R4 default
    bb.shutdown()


def test_module_helpers_are_exported() -> None:
    for name in ("guard", "aguard", "guarded", "PolicyDenied", "ApprovalDenied", "ApprovalExpired",
                 "PolicyUnavailable", "PolicyError", "Decision"):  # fmt: skip
        assert name in blackbox.__all__ and hasattr(blackbox, name)
