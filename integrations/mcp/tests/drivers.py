"""Conformance drivers for the MCP wrapper: a fake session and the real `mcp` package in memory."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from abb_conformance import LLM_CACHED, LLM_INPUT, LLM_OUTPUT, SECRET, ScenarioError
from abb_conformance.scenarios import CONCURRENCY, TOOL_NAME
from blackbox import BlackBox

from blackbox_mcp import instrument

SUPPORTED = frozenset(
    "tool tool_failure nested concurrent sensitive sensitive_full hostile redactor_raises".split()
)


class FakeSession:
    def __init__(self, error: BaseException | None = None, result: Any = "default") -> None:
        self.error = error
        self.result = SimpleNamespace(content=[SimpleNamespace(text="ok")], is_error=False)
        if result != "default":
            self.result = result
        self.calls: list[tuple[Any, ...]] = []

    async def call_tool(self, name: Any = None, arguments: Any = None, **kwargs: Any) -> Any:
        await asyncio.sleep(0)
        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return self.result


def real_available() -> bool:
    try:
        from mcp import Client  # noqa: F401
        from mcp.server.mcpserver import MCPServer  # noqa: F401
    except ImportError:
        return False
    return True


def build_server() -> Any:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("conformance")

    def register(name: str) -> None:
        @server.tool(name=name)
        def tool(q: str = "") -> str:
            """A tool."""
            return "ok"

    register(TOOL_NAME)
    for i in range(CONCURRENCY):
        register(f"{TOOL_NAME}-{i}")

    @server.tool()
    def broken() -> str:
        """Always fails."""
        raise ValueError("nope")

    return server


@asynccontextmanager
async def real_session() -> Any:
    from mcp import Client

    async with Client(build_server()) as client:
        yield client


class Driver:
    llm_provider, llm_model = "acme", "m1"

    def __init__(self, real: bool) -> None:
        self.real = real
        self.name = "mcp-real" if real else "mcp-fake"
        self.supports = SUPPORTED - ({"hostile", "tool_failure"} if real else set())

    def perform(self, scenario: str, bb: BlackBox) -> None:
        asyncio.run(self._perform(scenario, bb))

    async def _perform(self, scenario: str, bb: BlackBox) -> None:
        capture = scenario == "sensitive_full"
        if self.real:
            async with real_session() as raw:
                await self._drive(scenario, bb, instrument(raw, bb, capture_payloads=capture))
            return
        error = ScenarioError("boom") if scenario == "tool_failure" else None
        await self._drive(
            scenario, bb, instrument(FakeSession(error), bb, capture_payloads=capture)
        )

    async def _drive(self, scenario: str, bb: BlackBox, session: Any) -> None:
        async with bb.run("conformance") as run:
            if scenario == "nested":
                with run.span("step", kind="agent"):
                    await session.call_tool(TOOL_NAME, {"q": "x"})
                    with run.llm_call(self.llm_provider, self.llm_model) as llm:
                        llm.record_usage(LLM_INPUT, LLM_OUTPUT, cached_input_tokens=LLM_CACHED)
            elif scenario == "concurrent":
                await asyncio.gather(
                    *(session.call_tool(f"{TOOL_NAME}-{i}", {}) for i in range(CONCURRENCY))
                )
            elif scenario in ("sensitive", "sensitive_full"):
                await session.call_tool(TOOL_NAME, {"q": SECRET})
            elif scenario == "hostile":
                weird: Any = object()
                await session.call_tool(None, weird)
                await session.call_tool(123)
                await session.call_tool(["x"], arguments=weird)
                await session.call_tool("x" * 100_000, {})
                session.result = weird
                await session.call_tool(TOOL_NAME)
                session.result = None
                await session.call_tool(TOOL_NAME)
            else:
                await session.call_tool(TOOL_NAME, {"q": "x"})
