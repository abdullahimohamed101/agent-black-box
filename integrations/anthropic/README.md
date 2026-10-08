# agent-black-box-anthropic

Anthropic client wrapper for Agent Black Box (spec 18, 70; ADR-050..052). Runtime dependency: `agent-black-box` only;
`pip install agent-black-box-anthropic[anthropic]` adds the client.

```python
from blackbox_anthropic import instrument

client = instrument(Anthropic(), bb)  # or AsyncAnthropic(); the only change
with bb.run("support bot"):
    client.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=[...])
```

- Wrapped: `messages.create`, sync and async, `stream=True` included. Each call is an `llm.request.*` span (`llm.provider=anthropic`,
  `llm.model`, `llm.temperature`, `llm.max_tokens`, tokens) nested under the current span.
- Token mapping: Anthropic reports uncached input apart from cache reads and writes. `llm.input_tokens` is the whole prompt
  (`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`), `llm.cached_input_tokens` is `cache_read_input_tokens`,
  so providers compare equally. Streams accumulate usage from `message_start` and `message_delta`.
- Outside `bb.run(...)` a call is a transparent pass-through; a stream the caller abandons ends `cancelled`.
- Cost: pass `cost_fn(provider, model, tokens_in, tokens_out, cached) -> usd`; otherwise none is recorded.
- Prompts and responses are not recorded; `capture_payloads=True` attaches bounded previews, gated by `payload_mode` and redacted.
- Not wrapped: the `messages.stream()` helper, `beta.*`, raw-response variants, batches (KI-062). Host exceptions propagate unchanged.
