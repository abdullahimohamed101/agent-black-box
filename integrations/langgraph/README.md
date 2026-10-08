# agent-black-box-langgraph

LangGraph / LangChain callback adapter for Agent Black Box (spec 18, 70; ADR-050..052). Runtime dependency: `agent-black-box` only;
`pip install agent-black-box-langgraph[langgraph]` adds the framework.

```python
from blackbox import BlackBox
from blackbox_langgraph import BlackBoxCallback

bb = BlackBox(api_key=..., endpoint=...)
app.invoke(state, config={"callbacks": [BlackBoxCallback(bb)]})  # the only change
bb.shutdown()
```

| Framework callback | Canonical events |
| --- | --- |
| outermost chain / graph | the run (`run.started` ... `run.completed` / `run.failed` / `run.cancelled`); inside an existing `bb.run` it becomes a span instead |
| graph node, chain | `span.started` / `span.completed` / `span.failed`, kind `custom`, `framework.node`, `framework.step` |
| chat model / LLM | `llm.request.*`: `llm.provider`, `llm.model`, `llm.input_tokens`, `llm.output_tokens`, `llm.cached_input_tokens`, `llm.temperature`, `llm.max_tokens` |
| tool | `tool.call.*` with `tool.name` |
| retriever | `span.*` kind `retrieval`, `retrieval.result_count` |

- Cost is never guessed: pass `cost_fn=lambda provider, model, tokens_in, tokens_out, cached: usd` to set `cost.estimated_usd`.
- Prompts, messages, tool inputs and outputs are not recorded. `capture_payloads=True` attaches bounded previews, still gated by the SDK `payload_mode` and redacted.
- Callbacks never raise into LangGraph; contained failures are counted in `handler.errors`. Steps tagged `langsmith:hidden` get no span.
- `GraphInterrupt` (human-in-the-loop pause) ends the run as `cancelled`. State changes and checkpoints are not mapped (KI-061).
- Tests: `uv run pytest` runs the shared conformance suite against a fake callback dispatcher and against real `langgraph`.
