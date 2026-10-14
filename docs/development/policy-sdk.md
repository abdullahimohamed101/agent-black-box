# Policy checks from the SDK (developer notes)

How to turn on cooperative policy enforcement in an agent, publish the starter policy, run the demo scenario and approve. The contract is in
`docs/architecture/sdk-python.md` (Policy); the server side is ADR-140 (policy model) and ADR-141 (decisions and approvals).

Enforcement is **cooperative**: it stops an agent that goes through the SDK (`guard()`, `@guarded`, the coding recorder, the MCP adapter). It does not stop an agent
that bypasses the SDK, and it is not a sandbox. `docs/SECURITY.md` says the same.

## 1. Key and switches

The decision endpoint needs an API key **bound to a project** with the scope `policy:check` or `events:write` (a key that only asks, or the key your SDK already uses):

```bash
python -m abb_api.cli create-key --workspace <ws> --project <project> --scopes events:write   # or policy:check (+ events:write to record)
export BLACKBOX_API_KEY=abb_live_...  BLACKBOX_ENDPOINT=http://localhost:8000
export BLACKBOX_POLICY=enforce       # off by default: with it unset nothing changes and no policy call is made
```

Optional: `BLACKBOX_POLICY_FAIL_MODES="shell.execute=closed,tool.*=open"` (what to do when the service cannot be asked), or `BlackBox(policy_timeout=2.0, policy_wait_max=86400, ...)`.
A workspace-wide key is refused (`PROJECT_KEY_REQUIRED`); a missing scope is a `policy.warning` with reason `unauthorised` and the fail mode applies.

## 2. Publish the starter policy

`examples/coding-agent/policies/starter.yaml` denies reading `**/.env*`, requires an admin's approval for a push to `main` or `production` and for R3 shell commands, and denies R4 commands.
Publishing is for signed-in owners and admins (no API key can do it): in the web app open **Settings, Policies**, paste the file, validate (line-level errors), optionally run a **dry run**
against recent runs, and publish. Through the API (a session cookie, same-origin `Origin`):

```bash
doc=$(python -c 'import json; print(json.dumps({"document": open("examples/coding-agent/policies/starter.yaml").read()}))')
curl -s -X POST "$API/v1/policies/validate" -H 'Content-Type: application/json' -H "Origin: $WEB" -b cookies.txt -d "$doc"
# publish: the same document plus {"project_id": "<prj_...>", "comment": "starter"} to /v1/policies/publish
```

(`docs/architecture/api-v1.md` has the request shapes of `validate`, `dry-run` and `publish`).

## 3. Run the demo scenario

```bash
uv run --project packages/sdk-python python examples/coding-agent/run_demo.py --policy-demo
```

The scripted agent reads the code, tries to read `.env` (**denied**: `PolicyDenied`, the tool step fails and the model is told "blocked by policy"), edits a file, commits on `main` and pushes to `main`.
The push is **paused**: the demo prints a JSON line `{"event": "approval_pending", "approval_id": "apr_...", ...}` and waits. Approve it on the **Approvals** page (a signed-in owner or admin who is
not the person whose key asked, unless the rule allows self-approval), or with `POST /v1/approvals/{id}/decision {"decision": "approve"}` as a person. The push then runs and the demo prints its summary.
`--approval-timeout SECONDS` gives up sooner. Denying (or letting it expire, 15 minutes) leaves the push undone and the run records it.

`scripts/policy-e2e.sh` (`make policy-e2e`) drives the same scenario against the real stack: the demo is paused for approval, a second admin signs in through the real OIDC flow in a real browser and approves
or denies it (or nobody does and a one-minute approval expires), and the same stack is used to replay approvals, stop the API mid-wait and race the state machine (`docs/TESTING.md`).

**Stopping an agent that is waiting.** `Ctrl-C` (SIGINT) raises `KeyboardInterrupt` in the waiting call: the SDK withdraws the request (`DELETE`, one second) and re-raises. SIGTERM, SIGKILL and an out-of-memory kill do not run any
Python: the request stays waiting until it expires (KI-153), and a process started in the background by a shell (`&`, `nohup`, many CI runners) has SIGINT ignored, which Python respects. If you stop agents with SIGTERM, install a
handler that raises `SystemExit` so a wait is withdrawn.

## 4. Writing your own guards

```python
from blackbox import BlackBox, PolicyDenied, guarded

bb = BlackBox(policy_mode="enforce")

@guarded("database.execute", resource=lambda sql: sql.split()[0].upper(), risk_class="R3")
def run_sql(sql: str): ...

with bb.run("migrate"):
    try:
        run_sql("DROP TABLE users")
    except PolicyDenied as denied:
        print(denied.policy_id, denied.reason)
```

Capabilities are dotted words (`shell.execute`, `filesystem.read`, `github.push`, `tool.<name>`); rules match them case-sensitively, `prefix.*` matches anything below `prefix.`.
Pick the resource and the attributes the policy author will write rules on; do not put content you would not want an approver to see (an approver sees the secret-scrubbed resource and arguments).
The integrations send only the attributes the dry-run table names (`apps/api/src/abb_api/policies/mapping.py`), so a dry run predicts what enforcement does.

## 5. Tests

`packages/sdk-python/tests/policy_server.py` is a threaded fake of the endpoints (scriptable replies, delays, drops, junk) used by `test_policy_*.py`. Adapters use
`abb_conformance.policy_stub` (a small decide-only stub) and `check_guarded_tool`.
