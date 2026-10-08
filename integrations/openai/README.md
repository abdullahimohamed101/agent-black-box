# agent-black-box-openai

OpenAI client wrapper for Agent Black Box (spec 18, 70; ADR-050..052). Runtime dependency: `agent-black-box` only;
`pip install agent-black-box-openai[openai]` adds the client.

```python
from blackbox_openai import instrument

client = instrument(OpenAI(), bb)  # or AsyncOpenAI(); the only change
with bb.run("support bot"):
    client.chat.completions.create(model="gpt-4.1", messages=[...])
```

- Wrapped: `chat.completions.create` and `responses.create`, sync and async, `stream=True` included. Each call is an `llm.request.*` span
  (`llm.provider` = `openai` or `provider=`, `llm.model`, `llm.temperature`, `llm.max_tokens`, `llm.input_tokens`, `llm.output_tokens`,
  `llm.cached_input_tokens`) nested under the current span.
- Outside `bb.run(...)` a call is a transparent pass-through. Streaming reports tokens only if the stream carries usage
  (chat: `stream_options={"include_usage": True}`; responses: the `response.completed` event). A stream the caller abandons ends `cancelled`.
- Cost: pass `cost_fn(provider, model, tokens_in, tokens_out, cached) -> usd`; otherwise none is recorded.
- Prompts and responses are not recorded; `capture_payloads=True` attaches bounded previews, gated by `payload_mode` and redacted.
- Not wrapped: `with_raw_response`, `with_streaming_response`, `beta.*`, `images`, `embeddings` (KI-062). The host's exceptions propagate unchanged;
  the adapter's own failures are contained.
