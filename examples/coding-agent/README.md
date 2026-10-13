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

### The `wrong-fix` variant (comparison and experiments)

`run_demo.py --variant wrong-fix` plays the same first moves and then a plausible but wrong second patch (a docstring in
`app/oauth.py`), so the tests keep failing, nothing is committed or pushed, and the run ends in error (`run.failed`, exit
code 1). Recorded next to the default `baseline` run it is the pair the run-comparison view and the experiments
view are demonstrated with (`scripts/gen_compare_fixture.py` builds the offline test fixture from both). Scripted model only.

## The policy scenario (`--policy-demo`, Phase 14)

Policy enforcement is cooperative: the SDK asks the policy service before the agent acts, and an agent that goes through the SDK (here, the coding recorder) is stopped or paused. It does not
stop an agent that bypasses the SDK.

```bash
# 1. a project key with events:write (or policy:check), and a signed-in owner/admin to publish the policy and approve
export BLACKBOX_API_KEY=abb_live_...  BLACKBOX_ENDPOINT=http://localhost:8000
# 2. publish examples/coding-agent/policies/starter.yaml (Settings, Policies in the web app; see docs/development/policy-sdk.md)
# 3. run the scenario (BLACKBOX_POLICY=enforce is set for you by the flag)
uv run --project packages/sdk-python python examples/coding-agent/run_demo.py --policy-demo
```

What happens: the scripted agent tries to read `.env` and is **denied** (rule `deny-env-read`; the read never happens, the model is told "blocked by policy" and carries on), fixes the bug,
commits on `main` and pushes to `main`, which **pauses** (rule `approve-push-main`). The demo prints `{"event": "approval_pending", "approval_id": "apr_...", ...}` and waits; approve it on the
Approvals page (or `POST /v1/approvals/{id}/decision` as a person with the admin role) and the push proceeds; deny it, or wait 15 minutes, and it does not. `--approval-timeout 60` stops waiting
after a minute. The run page shows both decisions. The final JSON line lists `blocked` tools and whether the push happened.

`policies/starter.yaml` is the policy: deny reads of `**/.env*`, approval (admin, 15 minutes) for pushes to `main` and `production` and for R3 commands, deny R4 commands. The tests
(`tests/test_policy_demo.py`) run the scenario against a fake policy server.

## What is captured, and what is not

- Capture is opt-in: the demo sets `PayloadMode.FULL`. Without it the SDK records metadata only and uploads nothing.
- The environment is never recorded. Commands run with a minimal environment (no inherited credentials), and the values of
  secret-looking environment variables are masked wherever they appear in output.
- The demo plants secrets in a log the agent prints (an AWS key, a bearer token, a database password, a private key and, if
  you set `ABB_DEMO_ENV_SECRET`, a secret of unknown shape). They are redacted before upload; the E2E test searches the
  database, the artifact files, the API responses and the page for them.
- Commands are classified R0-R4 (observation only ... catastrophic). Classes are observed by default; with `--policy-demo` a published policy can act on them (cooperatively).

## Real-model mode (optional, never required)

> **Warning: a real model runs arbitrary shell commands as your user, with no sandbox.** Recorded commands get a minimal
> environment (no `HOME`, no inherited credentials) and their output is redacted, but they can still read and write
> your files and use your network. Run this only in a disposable VM or container. The flag
> `--i-understand-this-runs-commands` is required; scripted mode needs no flag and runs only the fixed plan.

`--model anthropic` uses `ANTHROPIC_API_KEY` from your environment with the Messages API. It has not been run by the project (KI-044).

## Layout

`repo_template/` is the broken repository; `coding_agent/` is the loop, tools, models and workspace builder; `tests/` checks the
scripted run end to end without a server.
