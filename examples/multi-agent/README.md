# Multi-agent example

Scripted only, by design: there is no `--live` mode. The point of this example is the shape of the trace (threads, an asyncio task, another
process, clock skew), which a real model would not change, and it keeps the example free of keys and network.

A planner delegates a job to cooperating agents and every one of them is recorded in a single run (spec §84):

```text
planner (root agent, run.started)
 +- delegate (span)
     +- researcher   thread           run.agent(...)          -> agent.spawned + agent.started/completed
     +- coder        thread           run.agent(...)
     |   +- tester   OS process       bb.continue_run(trace_context_from_env())   (no run.* events, no sequence)
     +- reviewer     asyncio task     async with run.agent(...)
```

Models are fakes with fixed token counts, so the run is identical every time and needs no network or credentials beyond a
project key.

## Run it

```bash
python -m abb_api.cli create-key --workspace <ws> --project <project> --scopes events:write
export BLACKBOX_API_KEY=abb_live_...  BLACKBOX_ENDPOINT=http://localhost:8000
uv run --project packages/sdk-python python examples/multi-agent/agent.py                 # prints the run id
uv run --project packages/sdk-python python examples/multi-agent/agent.py --skew-ms 1500  # tester's clock 1.5 s behind
uv run --project packages/sdk-python python examples/multi-agent/agent.py --fail-tool     # one failed tool call, run succeeds
```

Open the run and switch to **Waterfall**: five lanes, the tester nested under the coder, the critical path highlighted. With
`--skew-ms` the tester's own timestamps precede the coder's, so the chronological timeline interleaves it early and the row
carries a "clock skew" badge; the waterfall still nests it by parentage (spec §65.3). `--full-payloads` captures delegation
inputs, results and responses (redacted first); the default records metadata only.

## What it shows

| Concern | How |
| --- | --- |
| Threads | `threading.Thread(target=blackbox.bind(fn))`: threads do not inherit the SDK context, `bind` carries it |
| asyncio | tasks inherit the context; `asyncio.to_thread` copies it |
| Another process | `ctx = run.spawn_context("tester")` records `agent.spawned`; the child gets `BLACKBOX_TRACE_CONTEXT=ctx` and calls `bb.continue_run(blackbox.trace_context_from_env())` |
| Clock skew | `tester_proc.py --skew-ms N` patches this example's timestamp function inside the child; the SDK has no clock setting |
| Ordering | the child sends no `sequence`, so the run is time-ordered (`ordering_mode: time`) |

## Hazards (also in the SDK README)

- The child needs a key of the **same project** as the parent. `workers.child_environment` passes a filtered environment (only
  `PATH` and the `BLACKBOX_*` settings) because `env={**os.environ, ...}` would also hand the child every other secret you hold.
  A tool the agent runs (not an agent itself) should get no `BLACKBOX_API_KEY` and no `BLACKBOX_TRACE_CONTEXT` at all.
- The context variable is inherited by grandchildren that do not overwrite or remove it; re-export it per child.
- `multiprocessing` with the `spawn` start method (the macOS default) neither forks nor inherits the `Run`; use the environment
  context there too.
- The context string is an address, not a credential: treat it like a request id.

## Layout

`agent.py` is the command line; `multi_agent/planner.py` the root agent; `workers.py` the researcher, coder and reviewer;
`tester_proc.py` the separate process; `fake_model.py` the model. `tests/` runs the whole job offline (the tester writes to a
local file) and checks the event shapes: five agents, parents, the missing `sequence`, payload gating and redaction.
`scripts/multiagent-e2e.sh` runs it against a real API and web server.
