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

Limits worth knowing:
- `GraphInterrupt` (and other bubble-up control flow) ends the run and the open span as `cancelled`, not as an error.
- `handler.last_run_id` is the most recent run this handler opened; with concurrent invocations on one handler it is racy, so read run ids from your own `bb.run(...)` when you need them.
- When more than `max_tracked` (10,000) framework runs are in flight the oldest are forgotten (`handler.dropped`): their spans are never ended and, if one was an adapter-owned root, its run is never ended either.
- Span ends that arrive after their run already ended are dropped (`handler.late_dropped`): nothing is recorded after a run's end event.
- LLM spans need `agent-black-box>=0.2.0` to nest under their node; with an older SDK they are kept but unnested.

## Policy: guard the tool function with `@guarded` (Phase 14)

This adapter does not run your tools, so it cannot guard them for you (D5). Put `@guarded` on the tool function; the SDK asks the policy service
before the body runs (cooperative enforcement: it covers calls that go through the decorated function, not a tool that bypasses it). Needs
`agent-black-box>=0.4.0`, `BLACKBOX_POLICY=enforce` (or `BlackBox(policy_mode="enforce")`) and a project key with `policy:check` or `events:write`. With policy off the decorator
changes nothing.

```python
from blackbox import PolicyDenied, guarded


@guarded(
    "tool.lookup", resource=lambda query: query
)  # capability tool.<name>, as a dry run reads it
def lookup(query: str) -> str: ...


try:
    lookup("customers")  # allowed: runs; waits here if a person must approve
except PolicyDenied as denied:  # also ApprovalDenied, ApprovalExpired
    result = f"blocked by policy: {denied.reason}"  # hand the model an error result instead
```

`PolicyUnavailable` is raised when the service cannot be asked and the fail mode is closed (default for risk classes R3 and R4; tool calls are unclassified, so they fail open
unless you set `policy_fail_modes={"tool.*": "closed"}`). A tested example is in `tests/test_guarded_tool.py`.
