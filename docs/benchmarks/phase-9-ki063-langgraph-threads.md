# KI-063 spike: LangGraph callback threads and the SDK context (Phase 9, D10)

Question (plan D10): do `on_chain_start`/`on_chain_end` of a LangGraph node run on the thread that runs the node body, for sync and async
graphs, so that the adapter could bind the node span as the SDK's current span?

Environment: langgraph 1.2.14, langchain-core 1.6.7, Python 3.10.22 (macOS), SDK 0.3.0, handler `run_inline = True`.
Prototype: a handler that starts an SDK span in `on_chain_start` (nodes only, `metadata["langgraph_node"]`), sets it as the SDK's
current span, and resets the token in `on_chain_end`; each node body opens a user span and checks that it nests. Three-node graph, linear
and fan-out (a -> b, c). The probe is reproduced below; run with `cd integrations/langgraph && uv run python probe.py`.

| Scenario | start and body on one thread | current span in body is the node span | user span nests | `reset(token)` in `on_chain_end` |
| --- | --- | --- | --- | --- |
| sync nodes, `invoke`, linear | yes | yes | yes | ok |
| sync nodes, `invoke`, fan-out | yes | yes | yes | ok |
| sync nodes, `stream`, linear | yes | yes | yes | ok |
| async nodes, `ainvoke`, linear | yes | yes | yes | **fails: ValueError (a different context)** |
| async nodes, `ainvoke`, fan-out | yes | yes | yes | **fails: ValueError** |
| sync nodes, `ainvoke`, fan-out | **no (body runs on an executor thread)** | yes (context copied) | yes | **fails: ValueError** |

Reading: nesting inside a node would work in every scenario (LangGraph copies the context after `on_chain_start`), but the D10 gate
("callbacks run on the node's thread for sync and async graphs") is not met: in an async graph the start and end callbacks run in
different context copies, so a token-based reset is impossible and the node span would stay as the current span inside that task's
context after the node ended; and sync nodes run by an async graph execute off the callback thread. Shipping the adapter change would
need an SDK primitive that sets the current span without a token and an ownership rule for when it may clear it, which is more than a
bounded spike. Outcome: no adapter change; KI-063 stays open, retargeted to "on demand" with this measurement.

## Probe

```python
import asyncio, threading, operator
from typing import Annotated, TypedDict
from langchain_core.callbacks import BaseCallbackHandler
from langgraph.graph import StateGraph, START, END
from agent_blackbox import BlackBox
from agent_blackbox.context import _current_span

bb = BlackBox(api_key="abb_live_k.s", mode="offline", project="p")
rows = []
class H(BaseCallbackHandler):
    run_inline = True
    def __init__(self, run): self.run, self.spans, self.tokens, self.tid = run, {}, {}, {}
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, metadata=None, **kw):
        node = (metadata or {}).get("langgraph_node")
        if not node: return
        sp = self.run.span(node, kind="custom"); sp.start()
        self.spans[node] = sp; self.tokens[run_id] = _current_span.set(sp); self.tid[run_id] = threading.get_ident()
    def on_chain_end(self, outputs, *, run_id, **kw):
        tok = self.tokens.pop(run_id, None)
        if tok is not None:
            same = self.tid.pop(run_id) == threading.get_ident()
            try: _current_span.reset(tok); rows.append(("reset ok", same))
            except ValueError: rows.append(("reset FAILED (different context)", same))

class S(TypedDict):
    x: Annotated[list, operator.add]
def mk(name, h, is_async):
    def check(state):
        cur = _current_span.get()
        with h.run.span("user-" + name) as u:
            nested = u._parent is h.spans.get(name)
        rows.append((name, "body thread==start thread", threading.get_ident() == h.tid_by_node.get(name),
                     "current_span is node span", cur is h.spans.get(name), "user span nests", nested))
        return {"x": [name]}
    if is_async:
        async def f(state): return check(state)
        return f
    return check
def build(h, is_async, fan):
    h.tid_by_node = {}
    g = StateGraph(S)
    names = ["a", "b", "c"]
    for n in names: g.add_node(n, mk(n, h, is_async))
    g.add_edge(START, "a")
    if fan: g.add_edge("a", "b"); g.add_edge("a", "c"); g.add_edge("b", END); g.add_edge("c", END)
    else: g.add_edge("a", "b"); g.add_edge("b", "c"); g.add_edge("c", END)
    return g.compile()

class H2(H):
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, metadata=None, **kw):
        super().on_chain_start(serialized, inputs, run_id=run_id, parent_run_id=parent_run_id, metadata=metadata, **kw)
        node = (metadata or {}).get("langgraph_node")
        if node: self.tid_by_node[node] = threading.get_ident()

def scenario(label, is_async, fan, driver):
    rows.clear()
    with bb.run("probe") as run:
        h = H2(run); g = build(h, is_async, fan)
        driver(g, h)
    print("==", label)
    for r in rows: print("  ", r)
scenario("sync linear invoke", False, False, lambda g, h: g.invoke({"x": []}, config={"callbacks": [h]}))
scenario("sync fan-out invoke", False, True, lambda g, h: g.invoke({"x": []}, config={"callbacks": [h]}))
scenario("async nodes linear ainvoke", True, False, lambda g, h: asyncio.run(g.ainvoke({"x": []}, config={"callbacks": [h]})))
scenario("async nodes fan-out ainvoke", True, True, lambda g, h: asyncio.run(g.ainvoke({"x": []}, config={"callbacks": [h]})))
scenario("sync nodes fan-out via ainvoke", False, True, lambda g, h: asyncio.run(g.ainvoke({"x": []}, config={"callbacks": [h]})))
scenario("sync linear stream", False, False, lambda g, h: list(g.stream({"x": []}, config={"callbacks": [h]})))
```
