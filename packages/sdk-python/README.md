# agent-black-box (Python SDK)

Standard library only (ADR-013). See `docs/architecture/sdk-python.md` in the repository for the API, modes, delivery and redaction rules.

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
