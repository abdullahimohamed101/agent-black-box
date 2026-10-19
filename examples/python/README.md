# Python examples

| File | What it is |
| --- | --- |
| `scripted_agent.py` | A small support agent with one tool failure and one retry. Deterministic, no key, no network needed. |
| `ops_agent.py` | A release-ops agent that reads a `.env`, runs a forced `git clean` and force-pushes `main` (the harmful commands are recorded, never run): the run the security findings come from. |
| `trace_an_agent.py` | The shortest possible sample (a tool span and a model call); it is the SDK snippet in the README. Needs `BLACKBOX_API_KEY`. |

## `scripted_agent.py`

The agent plans (model call), looks a ticket up, the ticket service **times out** (the tool span is recorded as failed), the agent
**retries** (`retry.attempted`), the lookup works, and a second model call writes the answer. The run ends successfully, with a
failed span and a retry in it so there is something to look at.

```bash
# offline: nothing leaves the process (the tests use this)
uv run --project packages/sdk-python python examples/python/scripted_agent.py --offline

# recorded in a running Agent BlackBox (a project key with events:write)
export BLACKBOX_API_KEY=abb_live_...  BLACKBOX_ENDPOINT=http://localhost:8000
uv run --project packages/sdk-python python examples/python/scripted_agent.py
```

It prints one JSON line with the run id. The scripted model has fixed replies, fixed token counts and a made-up price table, so
every run has the same spans, calls, error, retry and costs (ids and timestamps differ).

### `--live` (optional, not run by the project)

`--live` replaces only the model with one plain Anthropic Messages API call (`ANTHROPIC_API_KEY` from your environment, optional
`ANTHROPIC_MODEL`). The ticket lookup stays scripted and nothing runs commands. The project has never run `--live` (KI-044), so
treat it as available but untested.

`--variant` changes what the ticket lookup does: `recovers` (default), `clean`, `flaky` (five timeouts, then it answers), `outage` (it never answers, the agent escalates and the run fails with `ConnectionTimeout`) and `denied` (the service refuses, the run fails with `PermissionDenied`); `--ticket T-1043` picks another scripted ticket (the run is named after its subject, "Answer ticket: login loop on iOS"; the id stays in the lookup payload) and `--pace 1` adds pretend latencies. `make demo` uses them all.

`--started-ago HOURS`, `--tokens-scale N` and `--model-name scripted-large` (also on `ops_agent.py`, and `--started-ago` on `examples/coding-agent/run_demo.py`) make a run look like real work without waiting: the run is stamped that many hours in the past, its model and tool calls last seconds (virtual time, `demo_clock.py`: it patches this process's own two clock functions, the way `examples/multi-agent` patches the tester's), and the token counts are multiplied so the cost reads in cents. Everything still goes through the SDK and the ingestion API. `make demo` uses them for a week-long spread.

Tests: `uv run --project packages/sdk-python pytest examples/python/tests -q -p no:cacheprovider`.
