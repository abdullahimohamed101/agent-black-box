"""Conformance drivers: the same operations through the fake dispatcher and the real LangGraph."""

import operator
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any, TypedDict

from abb_conformance import SECRET, ScenarioError
from abb_conformance.scenarios import CONCURRENCY, PASSWORD_ASSIGNMENT, TOOL_NAME
from blackbox import BlackBox
from langchain_core.runnables import RunnableConfig

from blackbox_langgraph import BlackBoxCallback
from tests.fakes import FakeFramework

ALL = frozenset(
    "tool tool_failure llm llm_failure nested concurrent sensitive sensitive_full hostile "
    "redactor_raises".split()
)


def handler_for(scenario: str, bb: BlackBox) -> BlackBoxCallback:
    return BlackBoxCallback(bb, capture_payloads=scenario == "sensitive_full")


class FakeDriver:
    name = "langgraph-fake"
    supports = ALL
    llm_provider, llm_model = "acme", "m1"

    def perform(self, scenario: str, bb: BlackBox) -> None:
        fw = FakeFramework(handler_for(scenario, bb))
        with fw.chain("LangGraph") as root:
            if scenario in ("tool", "redactor_raises"):
                with fw.tool(TOOL_NAME, root):
                    pass
            elif scenario == "tool_failure":
                with fw.tool(TOOL_NAME, root):
                    raise ScenarioError("boom")
            elif scenario == "llm":
                with fw.llm(root):
                    pass
            elif scenario == "llm_failure":
                with fw.llm(root):
                    raise ScenarioError("boom")
            elif scenario == "nested":
                with fw.chain("step", root, node="step", step=1) as step:
                    with fw.tool(TOOL_NAME, step):
                        pass
                    with fw.llm(step):
                        pass
            elif scenario == "concurrent":

                def one(i: int) -> None:
                    with fw.tool(f"{TOOL_NAME}-{i}", root):
                        pass

                with ThreadPoolExecutor(CONCURRENCY) as pool:
                    list(pool.map(one, range(CONCURRENCY)))
            elif scenario in ("sensitive", "sensitive_full"):
                with fw.tool(TOOL_NAME, root, input_str=SECRET, output=PASSWORD_ASSIGNMENT):
                    pass
            elif scenario == "hostile":
                self._hostile(fw, root)

    @staticmethod
    def _hostile(fw: FakeFramework, root: Any) -> None:
        h = fw.h
        weird: Any = object()
        # malformed arguments on balanced calls
        rid = "not-a-uuid"
        h.on_tool_start(weird, weird, run_id=rid, parent_run_id=root, name=["x"], tags=7)
        h.on_tool_end(weird, run_id=rid)
        rid2 = 12345
        h.on_chat_model_start(None, None, run_id=rid2, parent_run_id=root, metadata="bad")
        h.on_llm_end(weird, run_id=rid2)
        h.on_chain_start(None, None, run_id="c", parent_run_id=root, metadata={"langgraph_node": 5})
        h.on_chain_error("not an exception", run_id="c")  # type: ignore[arg-type]
        # calls for runs that never started, and a huge name
        h.on_tool_end("x", run_id="never-started")
        h.on_llm_error(ValueError("x"), run_id="never-started")
        h.on_retriever_end(weird, run_id="never-started")
        with fw.tool("t" * 100_000, root, input_str="\x00" * 10):
            pass


# -- the real framework --------------------------------------------


def real_available() -> bool:
    try:
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    return True


class _State(TypedDict, total=False):
    log: Annotated[list[str], operator.add]


def _model(fail: bool) -> Any:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from tests.fakes import LLM_CACHED, LLM_INPUT, LLM_OUTPUT

    class AcmeChat(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "acme"

        def _get_ls_params(self, stop: Any = None, **kwargs: Any) -> Any:
            return {"ls_provider": "acme", "ls_model_name": "m1", "ls_model_type": "chat"}

        def _generate(
            self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any
        ) -> Any:
            if fail:
                raise ScenarioError("boom")
            message = AIMessage(
                content="ok",
                usage_metadata={
                    "input_tokens": LLM_INPUT,
                    "output_tokens": LLM_OUTPUT,
                    "total_tokens": LLM_INPUT + LLM_OUTPUT,
                    "input_token_details": {"cache_read": LLM_CACHED},
                },
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

    return AcmeChat()


def _tool(name: str, fail: bool = False) -> Any:
    from langchain_core.tools import StructuredTool

    def run(q: str) -> str:
        if fail:
            raise ScenarioError("boom")
        return PASSWORD_ASSIGNMENT

    return StructuredTool.from_function(run, name=name, description="lookup")


class RealDriver:
    """Drives genuine `langgraph` graphs; callbacks arrive exactly as in production."""

    name = "langgraph-real"
    supports = ALL - {"hostile"}  # hostile arguments cannot be injected through a real graph
    llm_provider, llm_model = "acme", "m1"

    def perform(self, scenario: str, bb: BlackBox) -> None:
        from langgraph.graph import END, START, StateGraph

        graph = StateGraph(_State)
        names: list[str]
        if scenario == "concurrent":
            names = [f"n{i}" for i in range(CONCURRENCY)]
            for i, name in enumerate(names):
                tool = _tool(f"{TOOL_NAME}-{i}")

                def make(t: Any = tool) -> Any:
                    def node(state: _State, config: RunnableConfig) -> _State:
                        t.invoke({"q": "x"}, config)
                        return {"log": ["ok"]}

                    return node

                graph.add_node(name, make())
                graph.add_edge(START, name)
                graph.add_edge(name, END)
        else:
            names = ["step"]
            tool = _tool(TOOL_NAME, fail=scenario == "tool_failure")
            model = _model(fail=scenario == "llm_failure")

            def step(state: _State, config: RunnableConfig) -> _State:
                if scenario in ("tool", "tool_failure", "redactor_raises", "nested"):
                    tool.invoke({"q": SECRET}, config)
                if scenario in ("llm", "llm_failure", "nested"):
                    model.invoke("hello", config)
                if scenario in ("sensitive", "sensitive_full"):
                    tool.invoke({"q": SECRET}, config)
                return {"log": ["ok"]}

            graph.add_node("step", step)
            graph.add_edge(START, "step")
            graph.add_edge("step", END)
        app = graph.compile()
        app.invoke({"log": []}, config={"callbacks": [handler_for(scenario, bb)]})
