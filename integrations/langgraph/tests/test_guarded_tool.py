"""D5: LangGraph does not run tools for us, so a tool function carries `@guarded` itself."""

from typing import Any

from abb_conformance import check_guarded_tool
from agent_blackbox import BlackBox

from blackbox_langgraph import BlackBoxCallback
from tests.fakes import FakeFramework


def test_a_guarded_tool_inside_a_traced_graph_step() -> None:
    def build(decorate: Any, ran: list[str], bb: BlackBox) -> Any:
        fw = FakeFramework(BlackBoxCallback(bb))

        @decorate  # the whole change: @guarded("tool.<name>", ...) on the tool function
        def lookup(query: str) -> str:
            ran.append("ran")
            return f"result for {query}"

        def call(arg: str) -> Any:
            with fw.chain("graph") as root, fw.tool("lookup", root):
                return lookup(arg)

        return call

    check_guarded_tool(build)
