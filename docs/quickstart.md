# Quickstart

From a fresh clone to a failed agent run on your own screen, in about ten minutes. Agent BlackBox records what an agent did
(model calls, tool calls, edits, commands, retries, failures, cost) and shows it as a run you can open. Everything here runs
on your machine, with no account and no API key for any model provider.

If a step fails, [troubleshooting](troubleshooting.md) lists the usual causes. To run the stack for other people, read
[deployment](deployment.md) instead: this page uses development settings.

## What you need

| Tool | Version | Why |
| --- | --- | --- |
| Docker with Compose | a recent release | runs PostgreSQL, the API, the worker and the web app |
| [uv](https://docs.astral.sh/uv/) | 0.12 or later | runs the Python SDK and the example agents (it installs Python for you) |
| Git | any | clones the repository; the coding-agent example also uses it |

You do not need Node.js unless you want to build the web app outside Docker. Give Docker (Colima on macOS) at least 2 CPUs
and 4 GB of memory.

## 1. Get the code and the settings file

```bash
git clone https://github.com/abdullahimohamed101/agent-black-box.git
cd agent-black-box
cp .env.example .env
```

`.env` holds development values only (database passwords for a database that listens on `127.0.0.1`). It is git-ignored.

## 2. Start the stack

```bash
make up
```

This builds the images and starts PostgreSQL, the migrations, the API (`http://localhost:8000`), the background worker, the
web app (`http://localhost:3000`) and a development-only sign-in provider. The first build takes several minutes. The command
returns when everything reports healthy. To confirm:

```bash
curl -s localhost:8000/readyz
```

## 3. Create the local workspace and a key

```bash
make seed
```

This creates the workspace `local`, the project `demo`, the local owner `owner@local.test` and a development API key in
`.local/dev-api-key` (owner-only file, git-ignored). It is safe to repeat. The seed refuses to run in production, and the key
and owner exist for your laptop only.

## 4. Record a run without any server

You can try the SDK before it talks to the stack. This runs a small scripted support agent that fails one tool call, retries
it, and finishes. `--offline` keeps every event in the process; nothing is sent and no key is read.

<!-- check:run -->
```bash
uv run --project packages/sdk-python python examples/python/scripted_agent.py --offline
```

It prints one line of JSON with the run id and the SDK's counters. The scripted agent has fixed replies and a made-up price
table, so every run has the same spans, calls, error, retry and cost (ids and timestamps differ).

## 5. Record a run into your stack

The same agent, now sending to the stack with the key from step 3:

```bash
BLACKBOX_API_KEY="$(cat .local/dev-api-key)" BLACKBOX_ENDPOINT=http://localhost:8000 \
  uv run --project packages/sdk-python python examples/python/scripted_agent.py
```

## 6. Sign in and open it

Open <http://localhost:3000> and choose "Sign in". The development provider accepts any email: use `owner@local.test`. You
land in the `local` workspace. Open the run list for the `demo` project; the run from step 5 is there. Open it to see the
timeline, the tool call that failed, the retry that followed and the cost of the two model calls.

## See a failed run

The scripted support agent recovers, so its run ends well even though one step failed. For a run that ends in failure, run the
coding-agent example in its `wrong-fix` variant. A small coding agent reads a broken OAuth session service, makes a patch that
does not fix it, and gives up with failing tests. It needs a key that may also upload diffs and terminal output
(`artifacts:write`), which the seed key does not have, so create one first:

```bash
KEY="$(docker compose --profile app exec -T api python -m abb_api.cli create-key --workspace local --project demo --scopes events:write artifacts:write --name quickstart 2>/dev/null)"
BLACKBOX_API_KEY="$KEY" BLACKBOX_ENDPOINT=http://localhost:8000 \
  uv run --project packages/sdk-python python examples/coding-agent/run_demo.py --variant wrong-fix || true
```

The agent exits with status 1 on purpose (the `|| true` keeps your shell going). Reload the run list: the new run is marked as
failed. Open it to see the files the agent read and edited (with diffs), the shell commands, the test output and the point
where it went wrong. Run the default variant (leave `--variant` out) for the same task ending in success; the two runs are
the pair the run-comparison view is built for.

The coding-agent scenario, its variants and what it captures and redacts are described in
[`examples/coding-agent/README.md`](../examples/coding-agent/README.md).

## Everything in one command

`make demo` is being built and is not in the repository yet. It will run the example agents (scripted, no key, the same
traces every time) against the stack from step 2 and print links to the runs. Until it lands, steps 4 to 6 and the section
above are the demo.

[CONFIRM AFTER STEP 10] Remove this note and the marker when `make demo` exists.
```bash
make demo
```

## Trace your own agent

Install the SDK from this checkout into your agent's environment (it has no runtime dependencies):

```bash
pip install ./packages/sdk-python
```

Then wrap what your agent does:

```python
from blackbox import BlackBox

bb = BlackBox(api_key=..., endpoint="http://localhost:8000", project="demo")
with bb.run("Fix OAuth timeout") as run:
    with run.span("run-tests", kind="tool"):
        agent.execute()
bb.shutdown()
```

By default only metadata is recorded: prompts, tool results and file contents are dropped unless you opt in
(`payload_mode="full"`), and the SDK redacts secrets it recognises before anything leaves your process (best effort: keep secrets out of events when you can). The SDK never raises into your agent.
The full API is in the [Python SDK reference](architecture/sdk-python.md); framework adapters (LangGraph, OpenAI, Anthropic,
MCP) are in [integrations](architecture/integrations.md). The adapters are tested offline against fakes and the real
frameworks, not against live providers.

## Stop and clean up

```bash
make down
```

This stops the containers and keeps your data in Docker volumes. To start again, run `make up`. To delete the database and
artifacts as well, remove the volumes (`docker compose --profile app down -v`); that is permanent.

## Where to go next

- [Documentation index](README.md)
- [Troubleshooting](troubleshooting.md)
- [Deployment on one machine](deployment.md)
- [Events, API and SDK references](README.md#reference)
