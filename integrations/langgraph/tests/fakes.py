"""A stand-in for LangChain's callback dispatch: calls the handler exactly as the framework does.

Signatures mirror `langchain_core.callbacks.base` (keyword-only run ids, `name` in kwargs, tags and
metadata). It lets the whole adapter be tested without the framework installed (ADR-050).
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from abb_conformance import LLM_CACHED, LLM_INPUT, LLM_OUTPUT


def llm_result(*, shape: str = "usage_metadata") -> Any:
    """An `LLMResult`-like object carrying the standard test usage in one of the real shapes."""
    if shape == "token_usage":
        return SimpleNamespace(
            generations=[],
            llm_output={
                "token_usage": {
                    "prompt_tokens": LLM_INPUT,
                    "completion_tokens": LLM_OUTPUT,
                    "prompt_tokens_details": {"cached_tokens": LLM_CACHED},
                }
            },
        )
    message = SimpleNamespace(
        usage_metadata={
            "input_tokens": LLM_INPUT,
            "output_tokens": LLM_OUTPUT,
            "input_token_details": {"cache_read": LLM_CACHED},
        }
    )
    return SimpleNamespace(generations=[[SimpleNamespace(message=message)]], llm_output=None)


class FakeFramework:
    def __init__(self, handler: Any) -> None:
        self.h = handler

    @staticmethod
    def new_id() -> uuid.UUID:
        return uuid.uuid4()

    @contextmanager
    def _scope(self, end: Any, error: Any, result: Any, run_id: uuid.UUID) -> Iterator[uuid.UUID]:
        try:
            yield run_id
        except BaseException as exc:
            error(exc, run_id=run_id)
            raise
        else:
            end(result, run_id=run_id)

    def chain(
        self,
        name: str,
        parent: uuid.UUID | None = None,
        *,
        tags: Any = None,
        node: str | None = None,
        step: int | None = None,
    ) -> Any:
        run_id = self.new_id()
        metadata = {"langgraph_node": node, "langgraph_step": step} if node else None
        self.h.on_chain_start(
            {"name": name},
            {"in": 1},
            run_id=run_id,
            parent_run_id=parent,
            tags=tags,
            metadata=metadata,
            name=name,
        )
        return self._scope(self.h.on_chain_end, self.h.on_chain_error, {"out": 1}, run_id)

    def tool(
        self, name: str, parent: uuid.UUID | None, input_str: str = "q", output: Any = "r"
    ) -> Any:
        run_id = self.new_id()
        self.h.on_tool_start(
            {"name": name}, input_str, run_id=run_id, parent_run_id=parent, name=name
        )
        return self._scope(self.h.on_tool_end, self.h.on_tool_error, output, run_id)

    def llm(
        self,
        parent: uuid.UUID | None,
        provider: str = "acme",
        model: str = "m1",
        result: Any = None,
        *,
        chat: bool = True,
    ) -> Any:
        run_id = self.new_id()
        start = self.h.on_chat_model_start if chat else self.h.on_llm_start
        start(
            {"id": ["acme", "Chat"], "kwargs": {}},
            [[]] if chat else ["prompt"],
            run_id=run_id,
            parent_run_id=parent,
            metadata={"ls_provider": provider, "ls_model_name": model, "ls_temperature": 0.2},
            invocation_params={"model": model},
        )
        return self._scope(self.h.on_llm_end, self.h.on_llm_error, result or llm_result(), run_id)

    def retriever(self, name: str, parent: uuid.UUID | None, docs: Any) -> Any:
        run_id = self.new_id()
        self.h.on_retriever_start(
            {"name": name}, "q", run_id=run_id, parent_run_id=parent, name=name
        )
        return self._scope(self.h.on_retriever_end, self.h.on_retriever_error, docs, run_id)
