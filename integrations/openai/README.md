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
- Billing modifiers (ADR-044): the response's `service_tier` (`default` -> `standard`, `priority`/`fast` -> `fast`, `flex`; other words kept and left unpriced) as `llm.service_tier`, `cache_write_tokens` from either usage shape as `llm.cache_write_tokens`, and a regional base URL (`eu.api.openai.com`) as `llm.data_residency`. Batch API calls are not visible to the client; record `service_tier="batch"` yourself.
- Prompts and responses are not recorded; `capture_payloads=True` attaches bounded previews, gated by `payload_mode` and redacted.
- Also traced: `client.with_options(...)` / `client.copy()` (derived clients stay instrumented) and `with_raw_response.create` (usage is read by parsing
  the response; the SDK caches the parse, so your own `.parse()` is unaffected; a raw streaming call is recorded without tokens and carries
  `llm.usage_unavailable`).
- Not wrapped (KI-062): `with_streaming_response`, `chat.completions.parse`, `beta.*`, `embeddings`, `images`, audio. The host's exceptions propagate
  unchanged; the adapter's own failures are contained.

Span ends that arrive after the run ended (an abandoned stream collected late) are dropped: nothing is recorded after a run's end event.

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
