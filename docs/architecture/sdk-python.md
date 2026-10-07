# Python SDK

Package `agent-black-box` (import `blackbox`), `packages/sdk-python`. Python >= 3.10, **no runtime dependencies** (ADR-013).
Spec §67-68, ADR-010 (client-side redaction), INV-4 (the SDK never harms the host), INV-5.

```python
from blackbox import BlackBox

bb = BlackBox(api_key=os.environ["BLACKBOX_API_KEY"], endpoint="http://localhost:8000")
with bb.run("Fix OAuth timeout", metadata={"issue": 1842}) as run:
    with run.span("github-search", kind="tool") as span:
        span.set_attribute("tool.result_count", 3)
    with run.llm_call("openai", "gpt-4.1") as llm:
        llm.record_usage(1200, 300)
bb.shutdown()          # optional: an atexit hook flushes too
```

## API

| Call | Notes |
| --- | --- |
| `BlackBox(api_key, project, endpoint, **options)` | cheap; the exporter thread starts with the first event. Env: `BLACKBOX_API_KEY`, `BLACKBOX_ENDPOINT`, `BLACKBOX_MODE`, `BLACKBOX_LOCAL_PATH`. `project` is informational: the key determines the project. |
| `bb.run(name, metadata=, agent_id=, tags=)` | records `run.started`; as a context manager ends with `run.completed` / `run.failed` (exception) / `run.cancelled` (`KeyboardInterrupt`, `CancelledError`, non-zero `SystemExit`). `run.end(status)` ends it explicitly (`success`, `error`, `timeout`, `blocked`, `cancelled`). Works with `async with`. |
| `run.span(name, kind=, attributes=, parent=)` | `kind="tool"` -> `tool.call.*`, `"agent"` -> `agent.*`, anything else `span.*` with `span.kind`. `set_attribute(s)`, `set_payload`, `event`. Nested spans parent automatically through context. |
| `run.llm_call(provider, model, temperature=, max_tokens=)` | `llm.request.*`; `record_usage(input, output, cached_input_tokens=, cost_usd=)`. Cost is yours to supply until Phase 7. |
| `run.event(type, attributes, payload=, status=)` / `bb.event(...)` | point event (`custom.<name>` if no dot). |
| `@bb.observe(kind=, name=)` | sync or async functions; untraced outside a run; arguments/results are never captured. |
| `bb.flush(timeout)`, `bb.shutdown(timeout)`, `bb.stats()` | bounded, idempotent, never raise. `stats()` holds every drop counter. |
| `blackbox.bind(fn)` | threads do not inherit context: wrap callables you hand to a thread/pool. asyncio tasks inherit it. |

User exceptions inside `with` blocks always propagate unchanged.

## Modes

`http` (default), `local` (JSON lines to `local_path`), `offline` (queue only; `bb.buffered_events()`), `disabled` (every call a no-op).
Missing API key or an invalid endpoint downgrades to `disabled` with one warning; the constructor never raises.

## Delivery

Application thread: build -> redact -> append to a bounded buffer. One daemon thread batches (default 100 events / 250 ms / queue 10,000 /
HTTP timeout 2 s) and `POST`s `/v1/events/batch` (gzip over 1 KiB, <= 4 MiB per request, a fresh `batch_id` per batch reused on retries).

- Priorities (§67.4): P0 (run lifecycle, errors, policy blocks, approvals) is never dropped intentionally (up to 2x the queue bound, then counted);
  when full, an arriving event evicts the oldest queued event of strictly lower priority (P2 before P1).
- Retry (ADR-013): network errors / 408 / 425 / 5xx back off exponentially with full jitter (0.5 s base, 30 s cap, 5 attempts); `429`/`503`
  with `Retry-After` wait exactly that long (capped at 60 s); `413` splits the batch; other 4xx drop the batch (`dropped_rejected`). A
  keep-alive connection the server closed while idle is replaced silently.
- Per-event rejections inside a `202` are counted (`rejected_by_server`), never retried. Events over 256 KB are dropped and counted.
- After `shutdown`, or when the process is killed, undelivered events are lost; use `mode="local"` for durability. A forked child gets its own
  empty queue and exporter.

## Redaction (before serialization)

Order: key rules (default deny list: password, secret, token, api_key, authorization, cookie, ...; additive `deny_keys`, `allow_keys` exemptions)
-> secret patterns (AWS, GitHub, Slack, OpenAI/Anthropic-style keys, JWT, bearer tokens, private keys, URL credentials, `password=...`)
-> optional `redactor=callback(event) -> event | None` -> payload mode. **Detection is best effort**: keep secrets out of events when you can.

`payload_mode`: `metadata_only` (default; payloads dropped), `full` (payloads kept after redaction, <= 64 KB inline), `disabled` (metadata only and no
free text: no `error.message`, no run `metadata.*`). A raising callback fails closed: the event is dropped (P0 lifecycle events keep only structural
attributes) and counted in `dropped_redaction_error`.

## Cost and evidence

Application-thread cost is ~7-23 us per operation (`docs/benchmarks/phase-3-sdk.md`). `scripts/sdk-e2e.sh` runs
`examples/python/trace_an_agent.py` against the container stack and reads the run back.
