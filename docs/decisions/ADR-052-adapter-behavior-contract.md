# ADR-052: Adapter Behavior Contract (never raise, own the run only when needed, no payloads by default)

Status: Accepted
Date: 2026-10-07

## Context

Adapters run inside host frameworks that call them from arbitrary threads and event loops with framework-defined, sometimes malformed
arguments. INV-4 (SDK failure never crashes the host), INV-5 (opt-in payload capture), INV-6 (typed events) and INV-8 (external conventions
mapped at the boundary) must hold in code the user did not write.

## Decision

1. **Never raise.** Every entry point an adapter exposes to a framework is wrapped in a guard that catches `Exception`, counts it
   (`adapter.errors`), logs at debug without argument values, and returns `None`. Wrapped *host* calls (client methods) always propagate the
   host's own exception unchanged; only the adapter's own bookkeeping is guarded. `BaseException` (cancellation, interrupts) is never swallowed.
2. **Run ownership.** If the host already opened a `bb.run(...)` (SDK context), the adapter records into it and never ends it. Otherwise the
   LangGraph adapter opens a run when the outermost framework run starts and ends it when that run ends (`success`, `error`, `cancelled`);
   client wrappers (OpenAI/Anthropic/MCP) outside a run are a transparent pass-through (no events, no cost), like `@bb.observe`.
3. **Explicit parenting, no context variables.** Callbacks may fire on other threads or tasks, so spans are started with `Span.start()` and an
   explicit parent (nearest recorded ancestor) and are never pushed into the SDK's context. Framework run ids are map keys, never event ids.
4. **Mapping** (framework -> canonical): tool -> `tool.call.*`; model call -> `llm.request.*` with `llm.provider`, `llm.model`,
   `llm.input_tokens`, `llm.output_tokens`, `llm.cached_input_tokens`; retriever -> `span.*` kind `retrieval`; chain/graph node -> `span.*`
   kind `custom`; every adapter span carries `framework.name`. Cost is never invented: `cost.estimated_usd` is set only through the user's
   `cost_fn` callback until Phase 7 provides pricing. Framework-specific keys are confined to `framework.*` attributes.
5. **Payloads are off.** Inputs, outputs, prompts and messages are never read into events by default. Opt-in `capture_payloads=True` attaches a
   bounded, JSON-safe preview via `set_payload`, which the SDK still gates by `payload_mode` and redacts (ADR-010).
6. **Bounded state**: tracking maps hold at most 10,000 in-flight framework runs; the oldest is dropped (counted) beyond that. Entries are
   removed on end/error; stale entries from callbacks that never complete cannot grow without limit.
7. **Hidden/internal framework steps** (LangChain `langsmith:hidden` tag) create no spans; their children attach to the nearest recorded ancestor.

## Alternatives

Reading `contextvars` for the current span (breaks across threads/executors); capturing payload previews by default (violates INV-5);
guessing cost from a bundled price table (a Phase 7 concern, prices rot); raising on misuse in development (violates INV-4).

## Consequences

Traces are structurally correct under concurrency and nesting. A bug in an adapter shows up as a counter and missing events, never as a host
crash. Cost is absent unless the user supplies it, which is the honest default.

## Revisit

When Phase 7 lands server-side pricing, drop `cost_fn` defaults (keep as an override). When Phase 14 adds policy hooks, add a blocking path.
