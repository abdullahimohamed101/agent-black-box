# Python SDK

Package `agent-black-box` (import `agent_blackbox`), `packages/sdk-python`. Python >= 3.10, **no runtime dependencies** (ADR-013).
Spec §67-68, ADR-010 (client-side redaction), INV-4 (the SDK never harms the host), INV-5.

```python
from agent_blackbox import BlackBox

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
| `run.agent(name, agent_id=, agent_version=, input=, attributes=, parent=)` | a child agent (Phase 9): records `agent.spawned` (delegator's identity, `agent.child_id`; `input` is a payload) then `agent.started`/`agent.completed` with the child's own `agent_id`; everything inside inherits it. `set_result(value)` is the payload of `agent.completed`. A name with no slug characters, or equal to the delegator's agent id, becomes `<delegator>-child-<n>`. |
| `run.spawn_context(agent_id=, parent=, input=)` / `run.trace_context()` | delegate to another process: `spawn_context` records `agent.spawned` and returns the context string (`abb1;run=...;trace=...;span=...;agent=...`, <= 256 characters); `trace_context` returns the current position without an event. |
| `bb.continue_run(context, agent_id=, agent_version=)` | join a run another process started: no `run.started`/`run.completed` (`end()` is a counted no-op, `ended` stays False), root spans parent to the context's span, `sequence` omitted. As a context manager it wraps the block in an agent span when an agent id is known. An invalid context returns an inert run. |
| `agent_blackbox.trace_context_from_env()` | the context in `BLACKBOX_TRACE_CONTEXT` or `None`. HTTP header name constant `TRACE_CONTEXT_HEADER` (`X-Blackbox-Trace-Context`); the SDK does no HTTP for you. |
| `run.llm_call(provider, model, temperature=, max_tokens=)` | `llm.request.*`; `record_usage(input, output, cached_input_tokens=, cost_usd=, cache_write_tokens=, cache_write_1h_tokens=, service_tier=, data_residency=)` (the last four are billing modifiers, ADR-044; omit what you do not know). Cost: pass token counts and the server prices them (ADR-040); `cost_usd` is your own estimate and `provider_cost_usd` a provider-reported figure. |
| `run.event(type, attributes, payload=, status=)` / `bb.event(...)` | point event (`custom.<name>` if no dot). |
| `@bb.observe(kind=, name=)` | sync or async functions; untraced outside a run; arguments/results are never captured. |
| `bb.flush(timeout)`, `bb.shutdown(timeout)`, `bb.stats()` | bounded, idempotent, never raise. `stats()` holds every drop counter. |
| `bb.guard(...)`, `@guarded(...)` | cooperative policy checks, off by default: see **Policy** below. |
| `agent_blackbox.bind(fn)` | threads do not inherit context: wrap callables you hand to a thread/pool. asyncio tasks inherit it. |

User exceptions inside `with` blocks always propagate unchanged.

## Policy: `guard()` and `@guarded` (Phase 14, ADR-140/141)

Cooperative enforcement (D1): before a sensitive action the SDK asks the policy service, which answers allow, deny, or "wait for a person".
It stops an agent that goes through the SDK; it does not stop one that bypasses it, and no part of the product claims otherwise.
**Off by default**: with `policy_mode="off"` every call returns at once (`via="disabled"`) and makes no network call, so nothing changes for an
existing user. Turn it on with `BlackBox(policy_mode="enforce")` or `BLACKBOX_POLICY=enforce`. The API key must be bound to a project and hold the
scope `policy:check` or `events:write` (an existing SDK key already does); a workspace-wide key or a missing scope is `unauthorised`.

```python
bb = BlackBox(policy_mode="enforce")
with bb.run("deploy") as run:
    try:
        decision = bb.guard("github.push", "origin main", arguments={"branch": "main"})   # waits if a person must approve
    except agent_blackbox.PolicyDenied as denied:     # also ApprovalDenied, ApprovalExpired
        print(denied.policy_id, denied.policy_version, denied.reason)
    except agent_blackbox.PolicyUnavailable:          # the service could not be asked and the fail mode is closed
        ...
```

| Call | Notes |
| --- | --- |
| `bb.guard(capability, resource=None, risk_class=None, arguments=None, *, on_error=None, run=None) -> Decision` | `agent_blackbox.guard(...)` uses the current run's client (no run and no client: `via="disabled"`). `Decision(decision, policy_id, policy_version, reason, approval_id, via)`; `via` is `policy`, `approval`, `fail_open`, `expiry_allow` or `disabled`. A deny raises `PolicyDenied(policy_id, policy_version, reason)`; a denied approval `ApprovalDenied`; an expired or cancelled one `ApprovalExpired` (both subclasses of `PolicyDenied`); an unreachable service under a closed fail mode `PolicyUnavailable(reason)`. All derive from `PolicyError`. Nothing else is ever raised (INV-4). |
| `await bb.aguard(...)` / `await agent_blackbox.aguard(...)` | the same on a worker thread of the SDK's own 64-thread pool (not the loop's default executor, which a few pending approvals would exhaust). Cancelling the task withdraws a pending approval at the next long-poll round (at most 5 s). |
| `@guarded(capability, resource=None, risk_class=None, arguments=None, *, on_error=None, client=None)` | decorator for functions, methods and coroutine functions. `resource` and `arguments` are values or callables receiving the wrapped function's own call arguments (`self` first for a method). A denial raises before the function runs. A callable that raises is `invalid_intent` and follows the fail mode. Outside a run, or with policy off, the function simply runs. It is the integration path for LangGraph, OpenAI and Anthropic tool functions. |

Options (also `BLACKBOX_*`): `policy_mode` (`off`\|`enforce`, env `BLACKBOX_POLICY`), `policy_timeout` (2.0 s, one decide, consume or cancel call), `policy_wait_max`
(86,400 s, absolute cap on one approval wait), `policy_wait_round` (25 s, one long-poll round), `policy_fail_modes` (`{"shell.execute": "closed"}`, env
`BLACKBOX_POLICY_FAIL_MODES="shell.execute=closed,tool.*=open"`), `policy_on_approval` (a callback `fn(ApprovalPending)` called once when a person has to
approve, with the approval id; it runs on the calling thread, so it must return quickly; its exceptions are swallowed).

**Flow.** `POST /v1/policy/decide` with the intent (`run_id`, `agent_id`, `capability`, `resource`, `risk_class`, `arguments`, `trace_id`, `idempotency_key`; the key is stable
for one logical call, so a retry after a 429 is safe). `allow` returns; `deny` raises. `require_approval` long-polls `GET /v1/approvals/{id}/wait?timeout=<=25` in bounded rounds
until the state is not `REQUESTED`, then: `APPROVED` -> `POST /v1/approvals/{id}/consume` with the **whole intent again** (the server recomputes the hash; the SDK never computes
or sees one) and returns `via="approval"`; `DENIED` -> `ApprovalDenied`; `EXPIRED`/`CANCELLED` -> the rule's `on_expiry`: `deny` (default) raises `ApprovalExpired`, `allow`
proceeds with `via="expiry_allow"` and a `policy.warning`.

**Arguments** are JSON: strings, integers, booleans, null, lists and objects. **Floats are converted to strings** (the server refuses floats; there is no portable shortest-float
form), tuples to lists; anything else is `invalid_intent`. Limits (checked before sending, ADR-141 point 1): depth 8, 512 values, strings 4,096 characters, keys 256, resource 2,048,
capability 128, canonical size 24 KiB. An intent over a limit is **never sent**: the fail mode applies with reason `intent_too_large` or `invalid_intent`. Only top-level scalar
arguments are what a rule's `attributes` can match; the integrations send only the attributes the dry-run mapping names, never command text or file content.

**Bounds** (nothing waits forever): a decision call ends by `policy_timeout` (the whole exchange, not each socket operation; name resolution is the one step the OS bounds), after at
most one pause of <= 2 s on a 429 with a short `Retry-After`; an approval wait ends at the approval's `expires_at` plus 5 s or `policy_wait_max`, whichever is first (then the
request is withdrawn and `ApprovalExpired` raised); each long-poll round has its own timeout; transient wait errors back off with jitter and five consecutive failures abandon the wait
as `PolicyUnavailable` (**closed whatever the fail mode**: a rule that asked for a person never falls open on a network blip). `KeyboardInterrupt` or `SystemExit` during an approval
wait withdraws the request (`DELETE`, one second) and is re-raised unchanged. `guard()` runs on the calling thread; no background thread is started for it.

**Consume is one attempt.** A consume that did not finish (a network error, a timeout, a 5xx, any 409 `NOT_APPROVED`/`EXPIRED`/`CONSUMED`/`INTENT_MISMATCH`/`POLICY_DENIED`) is a
`PolicyDenied`: the server may have spent the approval and lost the response, and an idempotent consume would be a replay window (ADR-141 point 6, KI-145). The action waits for a
fresh approval.

### Fail modes (the service cannot be asked)

Applies when the decide call fails: connection refused, DNS, timeout, 5xx, 429 beyond the retry, 401/403 (`unauthorised`: a misconfiguration, still follows the fail mode), a malformed or
over-64 KiB response, an intent that cannot be sent, no active run, or a bug in the SDK. Authority, first hit wins: the `on_error=` argument; the operator's `policy_fail_modes`
(exact capability beats `prefix.*`, longest prefix wins); the server's fail-mode table cached **before** the outage (`GET /v1/policies/fail-modes`, fetched after the first
successful decision, refreshed at most every 5 minutes or when the policy version moves, with a 1 s bound, never blocking a decision longer; a rule-level `on_error` is only known if the
table was cached before the outage, and a rule with attribute conditions is believed only when it says `closed`); then the risk class: **R0-R2 and unclassified open, R3-R4 closed**.

| Mode | Result | Recorded |
| --- | --- | --- |
| open | `Decision(decision="allow", via="fail_open")`, the action proceeds | `policy.warning` |
| closed | `PolicyUnavailable(reason)` | `policy.warning` |

`policy.warning` carries only `policy.capability` and `policy.reason`, one of a closed vocabulary: `policy_service_unreachable`, `policy_timeout`, `policy_error`, `unauthorised`,
`intent_too_large`, `invalid_intent`, `internal_error`, `approval_consume_failed`, `approval_expired_allowed`. Never content, never error text. Arguments, resources and server error
bodies are never logged.

### What is enforced where (D1/D5)

| Integration | Behaviour |
| --- | --- |
| `CodingRecorder` | with `policy_mode="enforce"`, `read_file` (`filesystem.read`, resource the path), `write_file`/`delete_file` (`filesystem.write`, resource the path, argument `operation`), `run_command` and every `git_*` helper that runs a command (`shell.execute`, resource the executable name, `risk_class` from the classifier, argument `category`) and `git_push` (`github.push`, resource `<remote> <branch>`, argument `branch`, `risk_class` the classifier's class for the push it runs: R2, or R3 with `force=True` which runs `--force-with-lease`; never invented, unset if it cannot be derived, ADR-176; then its shell command) ask first. A denied action does not happen and records no step of its own (the server records `policy.action.blocked`). `guard_command/read/write/push(remote, branch, force=False)` are the same checks for a host that acts itself. The three-line git metadata probes the recorder runs for attributes are not actions the agent chose and are not guarded. |
| `blackbox_mcp.instrument` | `call_tool` is guarded as `tool.<name>`, resource `mcp.call_tool`; a denied call returns an MCP error result and is not made. |
| LangGraph, OpenAI, Anthropic adapters | not hooked: they do not execute tools. Put `@guarded` on the tool function. |
| Anything that bypasses the SDK | not stopped. |

The capability, resource and attributes of every helper follow `apps/api/src/abb_api/policies/mapping.py`, so a dry run of a draft policy predicts what enforcement does; a test
executes that file and compares.

## Multi-agent and other processes

ADR-070. One run and one trace per job; a child agent is an `agent.*` span with its own `agent_id` (`Span.agent_id`/`agent_version` are
inherited: explicit, else the parent span's, else the run's). Contextvars carry the current run and span in-process; threads need `bind()`.
Across processes the explicit `TraceContext` travels in `BLACKBOX_TRACE_CONTEXT` (or the `X-Blackbox-Trace-Context` header). Parsing is strict
and bounded (256 characters, fixed key set, every id checked against the contract's patterns); anything else yields `None` and a warning that
does not contain the value.

- `sequence` is only comparable within one emitter (§65.1). A continued run, and a forked child that keeps using its parent's `Run`
  (`os.getpid()` differs from the run's), omit it, so the run is ordered by `occurred_at` (`ordering_mode: time`). Clock skew between
  processes therefore shows in the chronological list; span nesting never depends on time.
- Hazards: the variable is inherited by grandchildren and tools that do not overwrite or remove it (a grandchild calling
  `trace_context_from_env()` would parent to the grandparent's span); `env={**os.environ, ...}` also passes `BLACKBOX_API_KEY` to the
  child, so tools get a filtered environment; `multiprocessing` with the `spawn` start method (macOS default) neither forks nor inherits the
  `Run`, so such children must use the environment context.
- A context lets its holder write into that run with a key of the same project (the server rejects other projects' runs); treat it like a
  request id. `agent.spawned` payloads follow `PayloadMode` and the redactor like every other payload; NUL characters in payload strings and
  keys are removed (the contract rejects them).

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
