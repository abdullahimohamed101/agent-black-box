"""A small LangGraph agent traced by Agent Black Box. The only Black Box lines are marked.

    cd integrations/langgraph
    BLACKBOX_API_KEY=... BLACKBOX_ENDPOINT=http://localhost:8000 uv run python ../../examples/langgraph/agent.py

The model is a local fake (no network, no provider key): it answers with fixed token usage.
"""

import os
from typing import Any, TypedDict

from blackbox import BlackBox  # Black Box
from blackbox_langgraph import BlackBoxCallback  # Black Box
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph


class DemoModel(BaseChatModel):
    """Stands in for a hosted model: fixed answer, fixed usage."""

    tokens: tuple[int, int, int] = (120, 30, 20)

    @property
    def _llm_type(self) -> str:
        return "demo"

    def _get_ls_params(self, stop: Any = None, **kwargs: Any) -> Any:
        return {"ls_provider": "demo", "ls_model_name": "demo-1", "ls_model_type": "chat"}

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any) -> Any:
        tokens_in, tokens_out, cached = self.tokens
        usage = {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
            "input_token_details": {"cache_read": cached},
        }
        message = AIMessage(content="done", usage_metadata=usage)  # type: ignore[arg-type]
        return ChatResult(generations=[ChatGeneration(message=message)])


@tool
def search(query: str) -> str:
    """Search the issue tracker."""
    return "3 results"


class State(TypedDict, total=False):
    notes: str


def plan(state: State, config: RunnableConfig) -> State:
    DemoModel().invoke("plan the work", config)
    return {"notes": "plan"}


def act(state: State, config: RunnableConfig) -> State:
    return {"notes": search.invoke({"query": "oauth timeout"}, config)}


def answer(state: State, config: RunnableConfig) -> State:
    DemoModel(tokens=(200, 50, 0)).invoke("summarise", config)
    return {"notes": "answered"}


graph = StateGraph(State)
for name, fn in (("plan", plan), ("act", act), ("answer", answer)):
    graph.add_node(name, fn)
graph.add_edge(START, "plan")
graph.add_edge("plan", "act")
graph.add_edge("act", "answer")
graph.add_edge("answer", END)
app = graph.compile()

bb = BlackBox(api_key=os.environ["BLACKBOX_API_KEY"], endpoint=os.environ.get("BLACKBOX_ENDPOINT"))  # Black Box
handler = BlackBoxCallback(bb, run_name="langgraph-demo")  # Black Box
app.invoke({}, config={"callbacks": [handler]})  # Black Box: the config argument
bb.shutdown()  # Black Box
print(f"run {handler.last_run_id}")  # noqa: T201
