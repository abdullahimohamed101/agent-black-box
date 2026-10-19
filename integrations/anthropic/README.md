# agent-black-box-anthropic

Anthropic client wrapper for Agent BlackBox (spec 18, 70; ADR-050..052). Runtime dependency: `agent-black-box` only;
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

Billing modifiers (ADR-044): `cache_creation_input_tokens` is also reported as `llm.cache_write_tokens` (still part of `llm.input_tokens`) and the one-hour part of `cache_creation` as `llm.cache_write_1h_tokens`; `usage.service_tier` becomes `llm.service_tier` (`speed: "fast"` makes it `fast`; `priority` is passed through and left unpriced by the server), `usage.inference_geo` becomes `llm.data_residency`. The server prices them. Span ends that arrive after the run ended (an abandoned stream collected late) are dropped.

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
