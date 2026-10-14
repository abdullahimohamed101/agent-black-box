# agent-black-box (Python SDK)

Standard library only (ADR-013). See `docs/architecture/sdk-python.md` in the repository for the API, modes, delivery and redaction rules.

## Policy: ask before a sensitive action (Phase 14)

Off by default. `BlackBox(policy_mode="enforce")` or `BLACKBOX_POLICY=enforce` turns it on; the project key needs the scope `policy:check` or `events:write`.

```python
import blackbox
from blackbox import PolicyDenied, PolicyUnavailable, guarded

with bb.run("deploy"):
    try:
        bb.guard(
            "github.push", "origin main", arguments={"branch": "main"}
        )  # waits (bounded) if a person must approve
        push()
    except PolicyDenied as denied:  # also ApprovalDenied and ApprovalExpired
        print(denied.policy_id, denied.policy_version, denied.reason)
    except PolicyUnavailable:  # the service could not be asked and the fail mode is closed
        ...


@guarded(
    "tool.lookup", resource=lambda query: query
)  # sync, async and methods; the integration path for LangGraph, OpenAI and Anthropic tools
def lookup(query): ...
```

- `guard()` returns a `Decision` or raises a `PolicyError` subclass and nothing else; it never blocks beyond `policy_timeout` (2 s) for a decision or the approval's expiry (capped by
  `policy_wait_max`, 24 h) for a wait. `await bb.aguard(...)` is the asyncio form (a pool of its own; cancelling the task withdraws a pending approval).
- When the service cannot be asked: R0-R2 and unclassified actions **fail open** (the action proceeds and a `policy.warning` is recorded), R3-R4 **fail closed** (`PolicyUnavailable`).
  Override per capability with `policy_fail_modes={"shell.execute": "closed"}` or `BLACKBOX_POLICY_FAIL_MODES="shell.execute=closed"`, or per call with `on_error="open"|"closed"`.
- Arguments are JSON; floats are sent as strings; oversized or malformed intents are never sent (the fail mode applies). Arguments and resources are never logged.
- A consume that did not finish is a denial (the server may have spent the approval); the action waits for a fresh approval.
- The coding recorder (`CodingRecorder`) and the MCP adapter ask automatically; the LangGraph, OpenAI and Anthropic adapters use `@guarded`. Enforcement is cooperative: it stops an agent that
  goes through the SDK, not one that bypasses it.

Full contract: `docs/architecture/sdk-python.md` (Policy).

## Multi-agent and other processes

One run, many agents (spec §84): a child agent is a span with its own `agent_id` inside the parent's run.

```python
with bb.run("Ship feature") as run:
    with run.agent(
        "researcher", input={"q": "oauth"}
    ) as researcher:  # agent.spawned + agent.started
        with run.llm_call("openai", "gpt-4.1") as llm:
            ...  # carries agent_id="researcher"
        researcher.set_result({"notes": 3})  # payload of agent.completed
    context = run.spawn_context("tester")  # agent.spawned now; a string for the child process
    subprocess.run(cmd, env={**agent_env, "BLACKBOX_TRACE_CONTEXT": context})
```

```python
# in the child process (it needs a key of the same project)
with bb.continue_run(blackbox.trace_context_from_env()) as run:  # no run.started / run.completed
    with run.span("pytest", kind="shell"):
        ...
```

- Threads do not inherit context: wrap callables with `blackbox.bind`. asyncio tasks inherit it.
- A continued run (and a forked child that keeps using its parent's `Run`) sends no `sequence`; the run is then ordered by time.
  Under clock skew the chronological list interleaves processes by their own clocks; the waterfall nests by parentage.
- An invalid context gives an inert run and a warning that never echoes the value. The context is not a credential, only an address.
- Hazards: the environment variable is inherited by grandchildren and tools unless you overwrite or remove it (re-export it per
  child, or delete it for tools that are not agents); `env={**os.environ, ...}` also hands `BLACKBOX_API_KEY` to the child, so give
  tools a filtered environment; `multiprocessing` with the `spawn` start method (the macOS default) neither forks nor inherits the
  `Run`, so those children must use the environment context.
