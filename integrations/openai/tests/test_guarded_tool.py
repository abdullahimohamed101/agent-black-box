"""D5: the OpenAI wrapper does not run tools, so the tool function carries `@guarded` itself."""

from typing import Any

from abb_conformance import check_guarded_tool
from agent_blackbox import BlackBox

from blackbox_openai import instrument
from tests.fakes import fake_client


def test_a_guarded_tool_called_after_a_traced_model_call() -> None:
    def build(decorate: Any, ran: list[str], bb: BlackBox) -> Any:
        client = instrument(fake_client(), bb)

        @decorate  # the whole change: @guarded("tool.<name>", ...) on the tool function
        def lookup(query: str) -> str:
            ran.append("ran")
            return f"result for {query}"

        def call(arg: str) -> Any:
            client.chat.completions.create(model="gpt-4.1", messages=[])  # the model picks a tool
            return lookup(arg)

        return call

    check_guarded_tool(build)
