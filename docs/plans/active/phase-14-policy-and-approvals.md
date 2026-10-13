# Phase 14: Policy engine and approvals

Status: Planned (2026-10-13). Decisions D1-D6 were settled with the user one question at a time (grill, 2026-10-13).
Owner: api + sdk + integrations + web. Branch: `feature/phase-14-policy-and-approvals` (from `main` `cb4102f`).
Depends on: Phase 6 (command risk classes), Phase 8 (adapters), Phase 13 (detectors, redaction, audit), Phase 15 (roles, sessions, audit log), Phase 22 (run page), Phase 12 (replay shows decisions as events).
Spec refs: §33, §88 (policy engine), §89 (approval workflow), §151 (policy DSL), §154 (approval state machine), §62 invariants (INV-3 tenancy, INV-4 SDK failure never crashes the host and every retry/queue is bounded, INV-5 sensitive capture), §90 threat model (approval replay).

## Outcome

A project can declare policies. When an integrated agent is about to do something sensitive, the SDK asks the server; the answer is allow, deny, or "wait for a human". A protected action is blocked or paused until a person decides, an approval authorizes exactly one action once, and every decision is recorded with the policy version that made it.

## Non-goals

- No `allow_with_constraints` decision; no regular expressions or boolean logic in rules.
- No email, chat or push notifications (Phase 17); approvers use the Approvals page.
- No out-of-process enforcement (proxy, sandbox): enforcement is cooperative and stated as such in the UI and docs.
- No findings that detect a bypass of the policy (a policy-versus-reality mismatch detector is later work).
- No personal policies and no policy sync from git.
- No WebSocket: waiting for approval is long-polling over plain HTTP (the SDK is standard-library only).

## Decisions (settled with the user)

- D1 Guarantee: cooperative enforcement, stated plainly. The SDK gets `guard(capability, resource, risk_class, arguments)` that integrations call before a sensitive action; the server decides; the action proceeds, waits for approval, or raises a policy error. Our integrations call it automatically for their capabilities. We claim to stop an agent that goes through the SDK and do not claim to stop one that bypasses it; the UI and docs say so in plain words and use no "protected" badge that overstates it. A protected action that happens without a matching decision is still visible to Phase 13's detectors from the recorded events.
- D2 Policy model: YAML with `version: 1` and an ordered list of rules (id, description, `when` {capability, resource glob, risk_class, exact attribute values}, `decision` allow|deny|require_approval, and for approvals `approver_role` and `expires_in`, plus optional `on_error` open|closed and `on_expiry` deny|allow|... default deny). Rules are checked in listed order and the first match wins, so order is priority and an explicit allow above a broader deny is the exception mechanism. No match applies the project default (`allow` by default). Policies live on the server per project with an optional workspace-wide base; every save creates a new immutable version; every decision records policy id and version. Owners and admins edit them in a settings page with line-level validation errors, a version history with diffs, and a dry run that evaluates a draft against the project's recorded events and shows what it would have denied or sent for approval; publishing is audit-logged (who, when, version).
- D3 Asking and failing: `guard()` posts the action intent to one decision endpoint and gets the decision, policy id and version, and a reason; a deny raises `PolicyDenied`; an allow returns at once. On `require_approval` the server creates an approval request and the SDK waits by long-polling a read endpoint in bounded rounds (about 25 s) until approved, denied or expired. This deviates from the roadmap's "WebSocket/control endpoint" wording because the SDK has a near-zero dependency budget (INV-4) and Python has no built-in WebSocket client; an ADR records it. The decision call has a strict timeout (about 2 s by default); waiting is bounded by the rule's expiry. When the policy service is down or slow the rule's `on_error` decides, defaulting by risk class: R0-R2 fail open (the action proceeds and a `policy.warning` is recorded), R3 and R4 fail closed. An expired approval falls back to deny unless the rule says otherwise. The SDK's own bugs never crash the agent: `guard()` raises only the intended policy errors and every internal failure follows the fail mode.
- D4 Approvals: only a signed-in person (never an API key or the agent) holding the role the rule names (`approver_role`, default owner or admin) and the right to read payloads (the approver must see what they approve) can decide; viewers and billing cannot. An approval is bound to a hash of the exact action intent (workspace, project, run, agent, capability, resource, risk class, canonical arguments) and authorizes that one intent once: the SDK presents the approval and the intent hash when it proceeds and the server consumes the approval atomically; a second use, a different intent, a different run, or an expired approval is rejected. State machine per §154: REQUESTED then APPROVED, DENIED, EXPIRED or CANCELLED; only REQUESTED is mutable; transitions are atomic and audit-logged with approver, time and optional comment; consumption is recorded separately. The approver sees capability, resource and arguments after the Phase 13 secret scrubbing, the policy and version, and the time left. Notification is a pending-approvals page, a top-bar count and a run-page banner (external notifications are Phase 17). The requester cannot self-approve unless the rule allows it.
- D5 Integrations: automatic guarding in the coding helpers (`shell.execute` with the classifier's risk class, `filesystem.read`, `filesystem.write`, and git push and merge actions) and the MCP adapter (tool calls; the tool name is the capability). The LangGraph, OpenAI and Anthropic adapters do not execute tools themselves, so they get a documented `@guarded(capability=...)` decorator and adapter docs, not automatic hooking. Capabilities are plain strings (`shell.execute`, `filesystem.read`, `github.merge`, `database.execute`). A starter policy template ships with the demo (deny reading `.env*`, require approval for a production merge and for a force push, deny R4 commands). The demo extends `examples/coding-agent`: the scripted agent tries to read `.env` (denied, the policy error visible in the run) then to push to main (paused until a human approves), driven through the real API by a script, including a replayed approval being rejected. Every decision is a typed event (`policy.action.blocked`, `approval.requested`, `approval.granted`, `approval.denied`, plus a new `policy.decision` for allows) so the run page and replay show them without new plumbing.
- D6 Screens and gates. Screens: Policies settings (monospace editor, validation, version history with diffs, dry run, publish); an Approvals page (Pending and Decided with capability, scrubbed resource, risk class, run link, time left, policy and version; a detail view with Approve and Deny and an optional comment; a count in the top bar); on the run page decisions appear in the timeline with a glyph and a word ("Denied by deny-env-read v3", "Waiting for approval, 12 min left"), the existing "Awaiting approval" headline state gets real data, and red stays reserved for run failure. Gates: (1) the protected demo action is blocked or paused until a human decides, end to end through the real SDK, API and browser; (2) a replayed approval is rejected in every way (same approval on a different command, a second use, after expiry, from a different run); (3) evaluation is deterministic (ordered, first match wins, defaults, policy id and version on every decision; property tests and golden policies); (4) failure modes hold (service down, timeout, expiry behave per risk class and per rule); (5) the state machine is atomic (a concurrent approve and deny leaves exactly one winner, every transition audited); (6) authorization (only signed-in role holders approve, API keys cannot, viewers and billing cannot, nothing crosses workspaces, the self-approval rule holds); (7) SDK safety (`guard()` never crashes the host beyond the intended policy errors, waits are bounded, no dependency added); (8) the dry run is correct and decision latency stays inside a stated bound without regressing ingestion.

## Contract between the parts

- Action intent (SDK to server): `{workspace/project from the key, run_id, agent_id, capability, resource, risk_class, arguments}`; the server canonicalizes arguments (sorted keys, scrubbed by the Phase 13 scanner before storage and before hashing display) and computes `intent_hash` = SHA-256 over a canonical JSON of the bound fields; the hash is over the unscrubbed canonical intent as the SDK sent it so a different command can never match, but only the scrubbed form is stored or shown.
- Decision response: `{decision, policy_id, policy_version, reason, approval_id?, expires_at?}`.
- Approval wait: `GET /v1/approvals/{id}/wait?timeout=` returns the current state and returns early on change.
- Consume: `POST /v1/approvals/{id}/consume` with `intent_hash`; succeeds once.
- Event attributes (metadata, registered and classified like Phase 13's): `policy.id`, `policy.version`, `policy.capability`, `policy.decision`, `policy.reason` (fixed policy text, never content), `approval.id`, `approval.state`.

## Known issues considered

Phase 13 left detectors observation-only; Phase 14 is where enforcement starts. KI-018 and KI-019 (S1, quotas and failed-authentication throttling) stay with Phase 19, but the decision and approval endpoints are authenticated and rate-limited like other write endpoints, and this phase adds a test that a flood of decision calls is bounded. KI-135 (residual over-redaction) is unaffected.

## Acceptance criteria

The eight gates of D6, each evidenced with a command and its observed output, plus: no existing test weakened (modified tests listed with reasons); authz matrix, route registry and withheld-content classification updated and green; the SDK suite, event-schema suite and every adapter's suite green; `scripts/quality.sh full` green in CI; adversarial review with a security pass and no open P0/P1.

## Verification plan

Unit and property tests for the DSL parser, validator and evaluator; API tests for decision, approvals, state machine races, hashing, authz and audit; SDK tests with a fake server for waits, timeouts and fail modes; adapter tests; web component tests and fixtures Playwright; an API-backed `policy-e2e.sh` (real SDK, real API, worker, browser, a scripted approver) with a CI job; a benchmark of the decision endpoint; my own gate run; then a Fable review with a security pass.

## Risks

- A bug in approval binding or consumption defeats the phase's main promise: tests attack it directly (different intent, repeat, race, expiry, cross-run, cross-workspace).
- Long-polling holds a connection and a worker slot per waiting agent: bound the wait rounds, cap concurrent waiters, and test that waiters do not starve ordinary requests.
- Fail-open on policy outage must be visible: always record `policy.warning`.
- Overclaiming: copy and docs state the cooperative guarantee.
- Policy edits are security-sensitive: owner/admin only, versioned, audited, validated before publishing.

## Ordered steps (one commit unit each)

1. Policy model: DSL parser and validator, deterministic evaluator, versioned store and migration, CRUD and dry-run API, audit, event registry additions, ADR.
2. Decisions and approvals backend: decision endpoint, approval state machine with intent hash and atomic consumption, long-poll wait, expiry job, fail-mode metadata, authz, audit, rate limits, ADR.
3. SDK and integrations: `guard()`, `@guarded`, long-poll client with fail modes, coding helpers, MCP adapter, adapter docs, the demo scenario and starter policy.
4. Web: Policies settings with history, diff and dry run; Approvals page and top-bar count; run-page decision rendering and banner.
5. Verification suite: `policy-e2e.sh` and CI job, replay-rejection and race tests, benchmark, docs and closing evidence.
6. Review, fixes, push and PR.

## Progress

- Step 1 (policy model, 2026-10-13): built on `feature/phase-14-policy-and-approvals`. DSL reader and validator (`abb_api/policies/parser.py`), pure first-match evaluator
  with a linear-time glob matcher, append-only `policy_versions` (migration 0092), seven management routes (current, versions, one version, diff, validate, publish,
  dry-run) with `policy.read`/`policy.write`, the `policy.decision` event and three attributes, ADR-140, KI-140/141. Steps 2-6 open. Contract for step 2: `evaluate(policy,
  intent) -> Decision{decision, rule_id, policy_version, reason}`, `store.PgPolicyStore.effective(project_id)`, `parser.compile_cached(text)`, action `approval.decide`
  still reserved.
- Step 2 (decisions and approvals backend, 2026-10-13): migration 0093 (`approvals`: CHECK constraints plus the `approvals_guard` and `approvals_born` triggers; the runtime role cannot
  DELETE or TRUNCATE; audit actor kind `system`), `POST /v1/policy/decide`, `GET /v1/approvals/{id}/wait`, `POST /v1/approvals/{id}/consume`, `DELETE /v1/approvals/{id}`,
  `GET /v1/approvals`, `/pending-count`, `/{id}`, `POST /v1/approvals/{id}/decision`, `GET /v1/policies/fail-modes`, the `expire_approval` outbox timer plus lazy sweeps, server-written
  `policy.*`/`approval.*` events with the reserved emitter `abb-policy-service`, three new event types (`approval.consumed`, `.expired`, `.cancelled`), the intent hash in
  `packages/event-schema` with golden vectors (`tests/data/intent-hash-vectors.json`), five actions (`policy.decide`, `approval.use`, `approval.read`, `approval.decide`,
  `policy.fail_modes`), ADR-141, KI-142 to KI-147. Contract changes for step 3 against the plan's "Contract between the parts": consume takes the **whole intent** (the server recomputes
  the hash; no hash is ever returned), the decide response is `{decision, policy_id, policy_version, reason, on_error, approval_id?, approval_state?, expires_at?, on_expiry?}`, and
  approvals belong to the asking key. Gates 2, 4 (server side), 5 and 6 are evidenced by `apps/api/tests/approvals/` and `apps/api/tests/authz/`; gate 8's decision latency is in
  `docs/benchmarks/phase-14-decide.md`. Steps 3-6 open.
- Step 2b (security gaps, 2026-10-13): KI-144 and KI-143 closed (ADR-141 Amendment 1). Consume re-evaluates the current policy under shared advisory locks on the policy
  scopes (a deny published before the consume's read is honoured; `APPROVAL_POLICY_DENIED`, approval unchanged, `policy.action.blocked` event and `approval.consume_refused` audit
  row). Ingestion rejects the eight server-written event types per event (`RESERVED_EVENT_TYPE`); `policy.warning` stays client-writable. Tests:
  `apps/api/tests/approvals/test_policy_recheck.py`, `test_reserved_events.py`. SDK step 3 note: a consume answered `APPROVAL_POLICY_DENIED` is a denial.
- Step 3 (SDK and integrations, 2026-10-13): `blackbox.policy` (`guard`, `aguard`, `@guarded`, `Decision`, `PolicyError`/`PolicyDenied`/`ApprovalDenied`/`ApprovalExpired`/`PolicyUnavailable`, ADR-142) with four
  helpers (`policy_intent`: the server's limits checked before sending and floats as strings, run against the golden vectors; `policy_transport`: one exchange under a whole-exchange deadline and a
  64 KiB body cap; `policy_failmodes`: the authority order argument, operator override, cached server table, risk class; `policy_glob`: a copy of the server's matcher, compared in a test). Off by default
  (`policy_mode`, `BLACKBOX_POLICY`); SDK 0.4.0. The coding recorder asks before `read_file`, `write_file`, `delete_file`, `run_command` and `git_push` (and exposes `guard_*` for hosts that act themselves)
  with the capability, resource and attributes of `policies/mapping.py` (a test executes that file and compares); the MCP adapter asks as `tool.<name>` and returns an MCP error result instead of making a
  denied call; LangGraph, OpenAI and Anthropic get `@guarded` docs and a conformance-checked example each (`abb_conformance.check_guarded_tool`, `policy_stub`). The demo is `run_demo.py --policy-demo`
  with `policies/starter.yaml` (validated and evaluated against the real parser and evaluator in the API's environment). Deviations from the plan, recorded in ADR-142: a wait that breaks is always closed;
  a `policy_on_approval` callback so the demo can print the approval id; `aguard` on a 64-thread pool of its own instead of `asyncio.to_thread`; the SDK's P0 event list gained the three step-2 approval
  types (its contract test was failing at the start of the step). Gate 7 (SDK safety): `guard()` raises only the policy exceptions (a 120-draw exception fuzz per scenario over 17 internal calls and 18
  exception types; 32-thread test; hostile-argument test), waits are bounded (tests with a short margin, the absolute cap and a dribbling server), the policy modules import only the standard library
  (an AST test), nothing is logged or recorded from arguments (a sentinel test), and 26 mutations of the defences (consume altered or allowed, no catch-all, deny returned as allow, no wait cap, R3/R4 open, ignored `on_error`, a wait that falls open, a cleared spent flag, no response cap, no size check, a recorder method left unguarded, ...) are each killed by a test (three survived at first and gained tests). The https path is tested against a throwaway local certificate. KI-148 to KI-151 filed as deferrals.
  Evidence (2026-10-13): SDK suite 1,199 passed on Python 3.10.22 (coverage 94.6%, gate 90) and on 3.12.15 (baseline 922; 277 new tests in `test_policy_*.py`); conformance 20, MCP 53, LangGraph 75, OpenAI 79,
  Anthropic 66 on 3.10 (conformance and MCP also on 3.12; the other three adapters on 3.12 are UNVERIFIED (env): their framework wheels for 3.12 are not in the offline cache); the example 22 passed, 1 skipped (the starter-policy
  check needs the API environment, where it was run by hand: parse ok, `.env` read deny, push to main and production require approval, R3 shell requires approval, R4 shell denied). Steps 4-6 open.
