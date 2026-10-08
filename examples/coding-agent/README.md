# Coding-agent demo

A real, small coding agent (a model loop with tools) fixes an intentionally broken OAuth session service and every step is
recorded by Agent Black Box: files read and edited (with syntax-highlighted diffs), model calls, shell commands (cwd, duration,
exit code, risk class, stdout/stderr), semantic test results, a retry, and the Git branch, commit and push.

The scenario (spec §136): the agent reads the code, searches, edits `is_expired`, runs the tests, **one test still fails**,
inspects the log, makes a second patch (the refresh token was dropped), and all tests pass.

## Run it (scripted model, no credentials)

```bash
# a project key with events:write + artifacts:write, and a read key for the web app
python -m abb_api.cli create-key --workspace <ws> --project <project> --scopes events:write artifacts:write
export BLACKBOX_API_KEY=abb_live_...  BLACKBOX_ENDPOINT=http://localhost:8000
uv run --project packages/sdk-python python examples/coding-agent/run_demo.py
```

The scripted model plays a fixed plan, so the run is identical every time (the E2E test uses it). Open the printed run id in the
web app. `scripts/coding-e2e.sh` does all of this against a throwaway database and a real browser.

## What is captured, and what is not

- Capture is opt-in: the demo sets `PayloadMode.FULL`. Without it the SDK records metadata only and uploads nothing.
- The environment is never recorded. Commands run with a minimal environment (no inherited credentials), and the values of
  secret-looking environment variables are masked wherever they appear in output.
- The demo plants secrets in a log the agent prints (an AWS key, a bearer token, a database password, a private key and, if
  you set `ABB_DEMO_ENV_SECRET`, a secret of unknown shape). They are redacted before upload; the E2E test searches the
  database, the artifact files, the API responses and the page for them.
- Commands are classified R0-R4 (observation only ... catastrophic). Classes are observed, never enforced.

## Real-model mode (optional, never required)

`--model anthropic` uses `ANTHROPIC_API_KEY` from your environment with the Messages API. It has not been run by the project (KI-044).

## Layout

`repo_template/` is the broken repository; `coding_agent/` is the loop, tools, models and workspace builder; `tests/` checks the
scripted run end to end without a server.
