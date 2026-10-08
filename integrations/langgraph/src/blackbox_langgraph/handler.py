"""LangChain / LangGraph callback handler that records canonical Agent Black Box events (ADR-052).

    from blackbox import BlackBox
    from blackbox_langgraph import BlackBoxCallback

    graph.invoke(state, config={"callbacks": [BlackBoxCallback(BlackBox(...))]})

The handler is duck-typed: it works with any object that calls the callback methods, and subclasses
`langchain_core`'s `BaseCallbackHandler` when that package is installed. Nothing here raises into
the framework (INV-4) and no input or output is recorded unless `capture_payloads=True` (INV-5).
"""

from __future__ import annotations

import functools
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

from blackbox import BlackBox, Run, Span, current_run

from blackbox_langgraph._extract import (
    llm_identity,
    llm_options,
    preview,
    run_name,
    span_name,
    tool_name,
    usage_of,
)

try:  # the framework is optional (ADR-050)
    from langchain_core.callbacks.base import BaseCallbackHandler as _Base
except ImportError:  # pragma: no cover - exercised by the fake-driver tests
    _Base = object  # type: ignore[assignment,misc]

log = logging.getLogger("blackbox.langgraph")
F = TypeVar("F", bound=Callable[..., Any])

FRAMEWORK = "langgraph"
MAX_TRACKED = 10_000
CostFn = Callable[[str, str, int | None, int | None, int | None], float | None]
_INTERRUPTS = ("GraphInterrupt", "GraphBubbleUp", "ParentCommand", "NodeInterrupt")


def _guarded(fn: F) -> F:
    """Run a callback so that nothing it does can reach the host framework."""

    @functools.wraps(fn)
    def wrapper(self: BlackBoxCallbackHandler, *args: Any, **kwargs: Any) -> None:
        try:
            fn(self, *args, **kwargs)
        except Exception:
            self._record_error()

    return wrapper  # type: ignore[return-value]


class _Node:
    """An in-flight framework run: its span (None when hidden or the run itself) and parenting."""

    __slots__ = ("anchor", "identity", "owned_run", "payload_in", "run", "span")

    def __init__(
        self, span: Span | None, anchor: Span | None, run: Run, owned_run: bool, payload_in: Any
    ) -> None:
        self.span = span
        self.anchor = anchor  # nearest recorded span at or above this node
        self.run = run
        self.owned_run = owned_run
        self.payload_in = payload_in
        self.identity: tuple[str, str] = ("unknown", "unknown")  # (provider, model) of a model call


class BlackBoxCallbackHandler(_Base):
    """Maps LangChain/LangGraph callbacks to runs, spans, tool calls and model calls."""

    name = "blackbox"
    raise_error = False  # the framework must not re-raise our failures
    run_inline = True  # cheap and ordered: no executor hop for async graphs

    def __init__(
        self,
        bb: BlackBox | None = None,
        *,
        run_name: str | None = None,
        cost_fn: CostFn | None = None,
        capture_payloads: bool = False,
        max_tracked: int = MAX_TRACKED,
    ) -> None:
        super().__init__()
        self._bb = bb if isinstance(bb, BlackBox) else BlackBox()
        self._run_name = run_name if isinstance(run_name, str) else None
        self._cost_fn = cost_fn if callable(cost_fn) else None
        self._capture = capture_payloads is True
        self._max = max(1, int(max_tracked)) if isinstance(max_tracked, int) else MAX_TRACKED
        self._nodes: OrderedDict[str, _Node] = OrderedDict()
        self._lock = threading.Lock()
        self.last_run_id: str | None = None  # the most recent run this handler opened itself
        self.errors = 0  # adapter-internal failures (callbacks that raised and were contained)
        self.dropped = 0  # in-flight runs forgotten because the tracking bound was reached

    # -- bookkeeping ------------------------------------------------

    def _record_error(self) -> None:
        self.errors += 1
        log.debug("blackbox-langgraph: a callback failed and was contained", exc_info=True)

    def _enter(
        self,
        run_id: Any,
        parent_run_id: Any,
        name: str,
        make: Callable[[Run, Span | None], Span] | None,
        *,
        payload_in: Any = None,
        root_is_run: bool = False,
    ) -> _Node:
        """Register a framework run. `make` builds its span, or None for hidden steps; a chain that
        starts a run of its own is that run (`root_is_run`) and gets no span."""
        parent_key = None if parent_run_id is None else str(parent_run_id)
        with self._lock:
            parent = self._nodes.get(parent_key) if parent_key is not None else None
        owned = False
        if parent is not None:
            run, anchor = parent.run, parent.anchor
        else:
            anchor = None
            run = current_run() or self._bb.run(name)
            owned = run is not current_run()
            if owned:
                self.last_run_id = run.run_id
        span = None
        if make is not None and not (owned and root_is_run):
            try:
                span = make(run, anchor)
                span.start()
            except Exception:  # keep the node so children still find their run; just no span
                self._record_error()
                span = None
        node = _Node(span, span if span is not None else anchor, run, owned, payload_in)
        with self._lock:
            self._nodes[str(run_id)] = node
            while len(self._nodes) > self._max:
                self._nodes.popitem(last=False)
                self.dropped += 1
        return node

    def _leave(self, run_id: Any) -> _Node | None:
        with self._lock:
            return self._nodes.pop(str(run_id), None)

    def _finish(
        self,
        run_id: Any,
        error: BaseException | None = None,
        output: Any = None,
        annotate: Callable[[Span], None] | None = None,
    ) -> None:
        node = self._leave(run_id)
        if node is None:
            return
        status = "success"
        if error is not None:
            status = (
                "cancelled"
                if not isinstance(error, Exception) or type(error).__name__ in _INTERRUPTS
                else "error"
            )
        try:
            if node.span is not None:
                if annotate is not None and error is None:
                    annotate(node.span)
                if self._capture:
                    node.span.set_payload({"input": node.payload_in, "output": preview(output)})
                node.span.end(status, error)
        finally:
            if node.owned_run:  # the handler opened this run, so it ends it
                self._sweep(node.run)
                node.run.end(status, error)

    def _sweep(self, run: Run) -> None:
        """Close spans of a finished run whose end callback never came (cancellation, crashes)."""
        with self._lock:
            stale = [key for key, node in self._nodes.items() if node.run is run]
            nodes = [self._nodes.pop(key) for key in stale]
        for node in nodes:
            if node.span is not None:
                node.span.end("cancelled")

    def _framework_attrs(self, metadata: Any) -> dict[str, Any]:
        attrs: dict[str, Any] = {"framework.name": FRAMEWORK}
        if isinstance(metadata, dict):
            node = metadata.get("langgraph_node")
            step = metadata.get("langgraph_step")
            if isinstance(node, str):
                attrs["framework.node"] = node
            if isinstance(step, int) and not isinstance(step, bool) and step >= 0:
                attrs["framework.step"] = step
        return attrs

    # -- chains and graph nodes -------------------------------------

    @_guarded
    def on_chain_start(
        self,
        serialized: Any,
        inputs: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Any = None,
        metadata: Any = None,
        **kwargs: Any,
    ) -> None:
        name = span_name(serialized, kwargs, "chain")
        hidden = isinstance(tags, (list, tuple, set)) and "langsmith:hidden" in tags
        attrs = self._framework_attrs(metadata)

        def make(run: Run, parent: Span | None) -> Span:
            return run.span(name, kind="custom", attributes=attrs, parent=parent)

        self._enter(
            run_id,
            parent_run_id,
            self._run_name or run_name(kwargs, name),
            None if hidden else make,
            payload_in=preview(inputs) if self._capture else None,
            root_is_run=True,
        )

    @_guarded
    def on_chain_end(self, outputs: Any, *, run_id: Any, **kwargs: Any) -> None:
        self._finish(run_id, output=outputs if self._capture else None)

    @_guarded
    def on_chain_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self._finish(run_id, error)

    # -- model calls ------------------------------------------------

    @_guarded
    def on_chat_model_start(
        self,
        serialized: Any,
        messages: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Any = None,
        metadata: Any = None,
        **kwargs: Any,
    ) -> None:
        self._llm_start(serialized, messages, run_id, parent_run_id, metadata, kwargs)

    @_guarded
    def on_llm_start(
        self,
        serialized: Any,
        prompts: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Any = None,
        metadata: Any = None,
        **kwargs: Any,
    ) -> None:
        self._llm_start(serialized, prompts, run_id, parent_run_id, metadata, kwargs)

    def _llm_start(
        self,
        serialized: Any,
        prompt: Any,
        run_id: Any,
        parent_run_id: Any,
        metadata: Any,
        kwargs: dict[str, Any],
    ) -> None:
        provider, model = llm_identity(serialized, metadata, kwargs)
        options = llm_options(metadata)
        attrs = self._framework_attrs(metadata)

        def make(run: Run, parent: Span | None) -> Span:
            return run.llm_call(
                provider,
                model,
                attributes=attrs,
                parent=parent,
                **options,
            )

        node = self._enter(
            run_id,
            parent_run_id,
            f"{provider}/{model}",
            make,
            payload_in=preview(prompt) if self._capture else None,
        )
        node.identity = (provider, model)

    @_guarded
    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        with self._lock:
            node = self._nodes.get(str(run_id))
        provider, model = node.identity if node is not None else ("unknown", "unknown")
        tokens_in, tokens_out, cached = usage_of(response)

        def annotate(span: Span) -> None:
            record = getattr(span, "record_usage", None)
            if record is None:
                return
            cost = None
            if self._cost_fn is not None:
                try:
                    cost = self._cost_fn(provider, model, tokens_in, tokens_out, cached)
                except Exception:
                    self._record_error()
            record(tokens_in, tokens_out, cached_input_tokens=cached, cost_usd=cost)

        self._finish(run_id, output=response if self._capture else None, annotate=annotate)

    @_guarded
    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self._finish(run_id, error)

    # -- tools and retrievers ---------------------------------------

    @_guarded
    def on_tool_start(
        self,
        serialized: Any,
        input_str: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Any = None,
        metadata: Any = None,
        **kwargs: Any,
    ) -> None:
        name = tool_name(serialized, kwargs)
        attrs = self._framework_attrs(metadata)
        self._enter(
            run_id,
            parent_run_id,
            name,
            lambda run, parent: run.span(name, kind="tool", attributes=attrs, parent=parent),
            payload_in=preview(kwargs.get("inputs") or input_str) if self._capture else None,
        )

    @_guarded
    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        self._finish(run_id, output=output if self._capture else None)

    @_guarded
    def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self._finish(run_id, error)

    @_guarded
    def on_retriever_start(
        self,
        serialized: Any,
        query: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Any = None,
        metadata: Any = None,
        **kwargs: Any,
    ) -> None:
        name = span_name(serialized, kwargs, "retriever")
        attrs = self._framework_attrs(metadata)
        self._enter(
            run_id,
            parent_run_id,
            name,
            lambda run, parent: run.span(name, kind="retrieval", attributes=attrs, parent=parent),
            payload_in=preview(query) if self._capture else None,
        )

    @_guarded
    def on_retriever_end(self, documents: Any, *, run_id: Any, **kwargs: Any) -> None:
        count = len(documents) if isinstance(documents, (list, tuple)) else None

        def annotate(span: Span) -> None:
            if count is not None:
                span.set_attribute("retrieval.result_count", count)

        self._finish(run_id, output=documents if self._capture else None, annotate=annotate)

    @_guarded
    def on_retriever_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self._finish(run_id, error)
