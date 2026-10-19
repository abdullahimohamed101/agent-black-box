# Runbook: policies and approvals (Phase 14)

For operators of a deployment and for the people who write policies and approve requests. Design: ADR-140 (the policy model and versions), ADR-141 (decisions,
approvals and single-use binding, with Amendment 1), ADR-142 (the SDK guard contract), `docs/development/policy-sdk.md` (how a developer wires an agent),
`docs/SECURITY.md`. End-to-end evidence: `scripts/policy-e2e.sh` (`make policy-e2e`) and the plan `docs/plans/completed/phase-14-policy-and-approvals.md` once it is closed.

## Read this first: what happens during a policy outage

**With the policy service unreachable, every R0-R2 action and every unclassified action goes ahead, and every MCP tool call is unclassified.** Only R3/R4 actions and rules or capabilities you set
closed are refused (and a wait for a person that breaks is always closed). The trace is a `policy.warning` per fallen-back call, which the SDK holds in a bounded in-memory spool (1,000) and re-sends
when the API answers again or at flush/shutdown (KI-152 resolved); a process that exits during the outage loses its spool. If that is not acceptable for a capability, close it:

- per capability, set by the operator (not changeable by the agent's code): `BLACKBOX_POLICY_FAIL_MODES="tool.*=closed,shell.execute=closed,github.*=closed,database.*=closed,filesystem.write=closed"` or `BlackBox(policy_fail_modes={...})`;
- per call: `guard(..., on_error="closed")` or `@guarded(on_error="closed")`;
- per rule: `on_error: closed` (honoured only by an SDK that cached the server's fail-mode table before the outage, so it complements, not replaces, the operator setting).

Recommended for a sensitive deployment: `tool.*`, `shell.execute`, `github.*`, `database.*` and `filesystem.write` closed; `on_error: closed` on every rule that guards secrets, production or money; alert on
`policy_fail_open` in `bb.stats()` and on `policy.warning` events. The price is that a policy outage then stops those agents, which is the point. Also: ask-only keys (`policy:check`) cannot create runs, but any key
with `events:write` can, by ingesting; and the ids of the server's own events come from a secret in the append-only `server_secrets` table (rotation: insert a new `event-id` row as the owner role, restart API
and worker; stored events are unaffected).

## What this is, and what it is not

An agent that goes through the Python SDK asks the server before a sensitive action (`guard()`, the coding helpers, the MCP adapter, `@guarded`). The server
answers **allow**, **deny**, or **wait for a person**. A person who may approve decides on the Approvals tab of Review (top bar); one approval authorises exactly one action, once.
Every decision is an event on the run, written by the server, with the policy id and version.

**The guarantee is cooperative.** It stops an agent that asks. It does not stop code that bypasses the SDK, it is not a sandbox, and it is not a network or
operating-system control. Say so to the people who rely on it. A protected action that happens without a matching decision is still visible to the Phase 13
detectors from the recorded events, but nothing in this phase detects "the agent should have asked and did not".

## Turning it on

Nothing changes until an SDK process asks for it. Per agent process:

```bash
export BLACKBOX_API_KEY=abb_live_...        # a key bound to ONE project, scope events:write (or policy:check, to ask without recording)
export BLACKBOX_ENDPOINT=https://your-api
export BLACKBOX_POLICY=enforce              # off by default; with it unset no policy call is ever made
```

(or `BlackBox(policy_mode="enforce")`). Requires SDK 0.4.0 (the MCP adapter 0.2.0 requires it). On the server side policies simply do not exist until someone publishes one;
with none published every action is allowed and the decision is still recorded as `policy.decision` ("Allowed (default)").

**Keys.** The decision endpoints need a key that is **bound to a project**. A workspace-wide key is refused with `PROJECT_KEY_REQUIRED`; a key without `events:write` or
`policy:check` is refused (`unauthorised` in the SDK, which then follows its fail mode). Create keys as a signed-in person (Settings, API keys), not with the CLI, if you want the
self-approval rule to apply: a key records the person who created it, and **that person cannot approve that key's requests** unless the rule says `allow_self_approval: true`
(a CLI-made key has no creator and so no such restriction). An approval belongs to the key that asked: another key of the same project, another project's key and another
workspace's key all get the same `404 APPROVAL_NOT_FOUND`.

## Writing and publishing a policy

Settings, Policies (owners and admins edit; developers and security read; viewers and billing see "Not available for your role"). Pick the scope (the workspace base, or a project:
a project's own policy replaces the base for that project), write the YAML, read the line and column messages, run a **dry run** to see what the draft would have
denied or sent for approval over the project's recorded runs (counts, rule ids and id-only samples; no content), then publish with an optional comment. Every publish is a new
immutable version, audit-logged as `policy.publish` (who, when, version; never the text). Two editors: the second publish is refused as `POLICY_VERSION_CONFLICT` and the page offers to reload.

Rules are checked **in the order listed and the first match wins**; nothing matching means the policy default (`allow`). A starter policy is `examples/coding-agent/policies/starter.yaml`
(deny reading `.env*`, approval for a push to `main` or `production` and for R3 shell commands, deny R4). Through the API (a session cookie and the web `Origin`):
`POST /v1/policies/validate`, `/dry-run`, `/publish`; `GET /v1/policies/current|versions|diff`. A rule that needs a person sets `approver_role` (`owner`, `admin`, `security`, `developer`;
unset means owner or admin), `expires_in` (1 minute to 7 days), `on_expiry` (`deny`, the default, or `allow`) and optionally `allow_self_approval`. These are copied onto each request when it
is made, so a later publish never changes what a pending request needs.

## Approving

Approvals tab of Review (the count beside Review in the top bar is the number waiting): Pending, Decided, All. Open a request to see the capability, the resource and arguments **after secret scrubbing**,
the policy and version, who is needed, the time left and the run it belongs to; add an optional comment; Approve or Deny with a confirm step. Who can decide: a signed-in
person with `approval.decide` (owner, admin, developer, security) whose role satisfies the rule's `approver_role` ladder, who did not create the asking key (unless allowed). The page says
which of those failed in one fixed sentence ("Your role cannot decide this request.", "You cannot decide a request made with your own API key.", "Someone else decided first: it
is now denied."); the API codes are `APPROVER_ROLE_REQUIRED`, `SELF_APPROVAL_FORBIDDEN`, `APPROVAL_NOT_REQUESTED`. API keys can never decide. Only the first decision counts (the state
machine is enforced by the database); a refused or lost decision is audit-logged (`approval.decide`, outcome `denied`) and changes nothing.

An approval is **bound to the exact action**: workspace, project, run, agent, capability, resource, risk class and canonical arguments (a SHA-256 the server keeps and never returns,
logs or records). The SDK presents the whole action again to spend it; a different command, resource, argument, risk class, agent or run is `APPROVAL_INTENT_MISMATCH` (a shell approval covers the exact command string: `git push origin main` does not cover `git push --force origin main`; replays are audited as `approval.consume_refused`, at most one per approval per minute), a second use is
`APPROVAL_CONSUMED`, after expiry `APPROVAL_EXPIRED`, before a decision or after a denial `APPROVAL_NOT_APPROVED`, and a deny rule published after the approval was granted wins
(`APPROVAL_POLICY_DENIED`; the approval stays usable if the deny is removed before it expires). Asking again for the same action after it was used creates a **new** request: the push needs a person again.

## Failure modes and defaults (what the agent does when the service cannot be asked)

Decided in the SDK, in this order: the `on_error=` argument of the call; the operator's `policy_fail_modes` / `BLACKBOX_POLICY_FAIL_MODES="shell.execute=closed,tool.*=open"`; the server's
fail-mode table **cached before the outage** (a rule's `on_error`, refreshed at most every five minutes or when the policy version moves); then the risk class: **R0-R2 and unclassified fail open,
R3 and R4 fail closed**. Open means the action proceeds and a `policy.warning` with a fixed reason (`policy_service_unreachable`, `policy_timeout`, ...) is recorded; closed raises `PolicyUnavailable`.
A decision call ends after `policy_timeout` (2 s by default) for the whole exchange. A wait for an approval that breaks (five consecutive failed rounds, a few seconds) is
**always closed** whatever the mode: a rule that asked for a person never falls open on a network blip. The warning is an ordinary event, but one the exporter drops after its bounded retry window (five attempts, about eight seconds) is kept in a bounded spool and re-sent when the API answers (KI-152 resolved); the
counters in `bb.stats()` (`policy_fail_open`, `policy_fail_closed`, `policy_warnings_lost`) are always there. A malformed `deny` is a deny, and an unknown decision or a require-approval without an approval fails closed whatever the risk class; a `429` on a wait is pacing, not failure.

## Expiry, waiting and limits

An approval is usable until its `expires_at`, approved or not. Three things close it: an `expire_approval` job due at that instant (the outbox is the timer, so it survives restarts and any worker may take it),
a lazy sweep on every read that matters, and the database trigger. An expired request becomes `EXPIRED` with an `approval.expired` event and an audit row with actor kind `system`; the SDK then applies `on_expiry`.
A cancelled request (the agent was interrupted, `Ctrl-C`, or its wait cap `policy_wait_max` ended) is `CANCELLED` with `approval.cancelled`. The run's headline shows "Waiting for approval" with the time left while it waits.

Per API process (KI-141, KI-147 - with N processes the effective limits are N times higher): decision-style calls of a key (decide, consume, cancel, the start of a wait) 1,200 a minute per key and 6,000 per
workspace (`ABB_POLICY_DECIDE_PER_MINUTE_KEY`, `..._WORKSPACE`; `429 RATE_LIMITED` with `Retry-After`); long polls 20 per workspace and 200 per process (`ABB_APPROVAL_WAITERS_PER_WORKSPACE`, `..._PER_PROCESS`; `429 TOO_MANY_WAITERS`);
open requests 100 per key and 1,000 per workspace (`ABB_APPROVAL_MAX_OPEN_PER_KEY`, `..._PER_WORKSPACE`; `429 TOO_MANY_OPEN_APPROVALS`); policy publishes 30, validations 120 and dry runs 20 a minute per workspace
(`ABB_POLICY_WRITES_PER_MINUTE`, `..._VALIDATIONS_...`, `..._DRY_RUNS_...`). Bodies are limited to 64 KiB. A waiting agent holds one HTTP connection per 25-second round and **no database connection** between reads.
Per agent process: `aguard` uses a 64-thread pool of its own (KI-151), so more than 64 simultaneous pending approvals in one process queue.

## Rotating keys

Revoke (Settings, API keys, or `DELETE /v1/api-keys/{id}`; a developer may revoke only keys they created) and issue a new key bound to the same project. Takes effect on the key's next request. **Pending approvals of the old
key cannot be used by the new key** (an approval belongs to the key that asked): the agent asks again and a person approves again; the old requests expire on their own. Allow for that when you rotate while people are approving.

## Operating it

- `GET /v1/approvals?state=pending` and the count in the top bar are the queue; the Approvals tab re-reads about every ten seconds while the tab is visible. A person who is not watching the page is told through an alert rule on approval requests (webhook, Slack or email; [alerting](alerting.md)).
- Audit log (Settings, Audit log): `policy.publish`, `approval.decide`, `approval.cancel`, `approval.consume`, `approval.consume_refused`, system expiries. Content never appears in an audit row, an event, a log line or an agent-visible response; the tests plant sentinels and search all four.
- Stuck "waiting" runs: confirm the worker is running (`docs/runbooks/worker-lag.md`): the run state follows the server-written events through the summariser; a request that was never decided becomes `EXPIRED` at its expiry regardless of the agent.
- A fail-open warning on a run means the agent acted without an answer from the policy service: check the API's health and the time of the warning against an outage.
- Database: the runtime role cannot delete or truncate `approvals`, and a trigger refuses every update that is not a legal transition or that touches the bound intent. A holder of the runtime credential can still make a legal transition by hand (approve a waiting request): protect that credential, and rely on the audit trail and events to notice.
- An approval whose content the scanner withheld (too large, out of time, a scan error) shows "could not be shown" and cannot be approved (`APPROVAL_CONTENT_UNAVAILABLE`); it can be denied, and the agent asks again with smaller arguments. Open-request caps are enforced per workspace under a lock.
- Deploy notes: migrations 0092 (policy versions), 0093 (approvals) and 0094 (server secrets) apply with `alembic upgrade head`; SDK 0.4.0 and MCP adapter 0.2.0 are needed on agents that enforce; the default is `policy_mode` off, so a deploy changes nothing by itself.
