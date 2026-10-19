# Python examples

| File | What it is |
| --- | --- |
| `scripted_agent.py` | A small support agent with one tool failure and one retry. Deterministic, no key, no network needed. |
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

Tests: `uv run --project packages/sdk-python pytest examples/python/tests -q -p no:cacheprovider`.
