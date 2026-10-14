# ADR-142: The SDK's `guard()` Contract

Status: Accepted (Phase 14, step 3). Implements D1, D3 and D5 of the Phase 14 plan and gate 7; builds on ADR-013 (the SDK has no runtime dependency), ADR-140 (the dry-run mapping the
integrations must follow) and ADR-141 (the endpoints, the single-use binding and the fail-mode table).
Date: 2026-10-13
Phase: 14

## Context

`guard()` is the one place where the SDK makes a decision that can stop the host's work, and the one place it must not hurt the host (INV-4). It runs on the caller's thread, so a slow or
hostile server, a lost response or a bug in the SDK must never turn into a hang, a crash or a silent bypass the operator cannot see. The decisions below fix the contract; the tests attack it
(`packages/sdk-python/tests/test_policy_*.py`: a fake of the endpoints, an exception fuzz over every internal call, mutation checks on the defences listed at the end).

## Decision

1. **API.** `BlackBox.guard(capability, resource=None, risk_class=None, arguments=None, *, on_error=None, run=None) -> Decision`, `await aguard(...)`, module-level `blackbox.guard/aguard`
   (current run's client; no run and no client: `via="disabled"`), and `@guarded(...)` (sync, async, methods; `resource`/`arguments` may be callables of the wrapped call). `Decision(decision, policy_id,
   policy_version, reason, approval_id, via)` with `via` one of `policy`, `approval`, `fail_open`, `expiry_allow`, `disabled`. Exceptions: `PolicyError(Exception)`, `PolicyDenied(policy_id, policy_version, reason)`,
   `ApprovalDenied` and `ApprovalExpired` (both `PolicyDenied`), `PolicyUnavailable(reason)`. **Off by default** (`policy_mode`, `BLACKBOX_POLICY`): nothing changes for an existing user and no call is made.
2. **Nothing else escapes.** `guard()` catches every `Exception` from its own code, applies the fail mode and records a `policy.warning` (reason `internal_error`); if even that fails it falls back to the
   risk-class default. `KeyboardInterrupt` and `SystemExit` pass through unchanged after a best-effort withdrawal of a pending approval (`DELETE`, one second); `asyncio.CancelledError` passes through after the
   worker is told to withdraw. A fuzz test draws 120 random (internal call, exception type) pairs per scenario (allow, deny, approve, outage) from 17 calls and 18 exception types and fails on anything but a `Decision` or a `PolicyError`; a mutation (catching only `ZeroDivisionError`) makes it fail.
3. **Everything is bounded, on the calling thread, with no background thread.** A decision, consume or cancel call is one exchange with a **whole-exchange deadline** (`policy_timeout`, 2 s): the socket is
   wrapped so every read and write also checks the deadline and the body is read through a 64 KiB cap, so a server that dribbles bytes cannot stretch it. One 429 with `Retry-After` <= 2 s is retried once with the
   same `idempotency_key`; a longer one is a failure. An approval wait ends at `expires_at` (read against the server's `Date`, so clock skew does not cut it short) plus 5 s, or `policy_wait_max` (24 h), whichever is first;
   each long-poll round (<= 25 s) has its own timeout; transient errors back off with jitter; five consecutive failed rounds abandon the wait. Name resolution is the one step the deadline cannot interrupt (KI-149).
4. **A wait that breaks is closed, always.** If the approval wait cannot be completed (five failed rounds, or the key may not wait on it) the result is `PolicyUnavailable` whatever the fail mode or `on_error`: the rule
   asked for a person, and a network blip must not turn that into "allowed". The request is withdrawn and a warning recorded.
5. **Consume is one attempt.** On `APPROVED` the SDK posts the **whole intent again** (the same six fields; the server recomputes the hash; the SDK never computes or sees one). Any failure of the consume
   (409 of any code, network error, 5xx, timeout, a reply naming another approval) is a `PolicyDenied`; a wait response that already says `consumed` is a denial too. A lost response cannot be retried (ADR-141 point 6, KI-145).
6. **Fail modes.** When the decide call fails (unreachable, DNS, timeout, 5xx, 429 beyond the retry, 401/403 as `unauthorised`, a malformed or oversized reply, an intent that cannot be sent, no active run, a bug) the mode is,
   first hit wins: the `on_error=` argument; the operator's `policy_fail_modes` (exact capability beats `prefix.*`; longest prefix wins); the server's fail-mode table cached **before** the outage (a rule with attribute
   conditions is believed only when it says `closed`; ADR-141 point 9, KI-146); the risk class (R0-R2 and unclassified open, R3-R4 closed). Open: `Decision(via="fail_open")` and a `policy.warning`. Closed: a `policy.warning` and
   `PolicyUnavailable`. The table is fetched after a successful decision (so the first call is not delayed by a second request while the service is down), at most every 5 minutes or when the policy version moves, with a 1 s bound.
7. **Intents are checked before they are sent.** A port of the canonical form (not of the hash) enforces the server's limits (depth 8, 512 values, strings 4,096, keys 256, resource 2,048, capability 128, 24 KiB canonical) and the
   capability pattern; floats become strings, tuples lists; anything else is `invalid_intent`. An intent over a limit is never sent: the fail mode applies with `intent_too_large` or `invalid_intent`. The port is tested against
   the golden vectors of `packages/event-schema/tests/data/intent-hash-vectors.json` (canonical text byte for byte; every invalid case rejected with the server's code).
8. **`policy.warning` says nothing about the action.** Attributes `policy.capability` (only when it is a valid capability) and `policy.reason`, one word of a closed vocabulary: `policy_service_unreachable`,
   `policy_timeout`, `policy_error`, `unauthorised`, `intent_too_large`, `invalid_intent` (the six of the plan) plus `internal_error`, `approval_consume_failed`, `approval_expired_allowed`. Arguments, resources and server error
   text are never logged or recorded; a test plants a sentinel in every intent field and searches logs, events and exception messages.
9. **Enforcement scope is stated per integration (D1/D5).** The coding recorder asks before `read_file` (`filesystem.read`), `write_file`/`delete_file` (`filesystem.write`, argument `operation`), `run_command` and every
   `git_*` helper that runs a command (`shell.execute`, resource the executable name, the classifier's risk class, arguments `category` and `command`, the exact command: Amendment 1) and `git_push` (`github.push`, resource `<remote> <branch>`, argument `branch`, then its shell
   command, which a dry run also counts as two events). A denied action does not happen and records no step of its own (the server records `policy.action.blocked`; a `shell.command.started` for a command that never ran would be
   false). `guard_command/read/write/push` are the same checks for a host that acts itself; every recorder method performs its action, so none is record-only. The MCP adapter asks as `tool.<name>`, resource `mcp.call_tool` (the
   `tool.operation` it records), and returns an MCP error result instead of making a denied call. The LangGraph, OpenAI and Anthropic adapters do not run tools and are not hooked: `@guarded` on the tool function is the path
   (README section and a conformance-checked example in each). Capability, resource and attributes follow `policies/mapping.py` exactly; a test executes that file and compares, and compares its executable-name function.
10. **`aguard` has a pool of its own.** `asyncio.to_thread` would put one thread per pending approval on the loop's default executor, which a handful of waits would exhaust (`getaddrinfo` and every other `to_thread` share
    it). A 64-thread pool named `blackbox-guard` carries them; cancelling the task sets a flag the worker checks every round (<= 5 s) and then withdraws the approval. A test runs 40 pending waits and shows `to_thread` still works.
11. **`policy_on_approval(ApprovalPending)`** is an optional callback (approval id, rule, version, reason, seconds left; no content) so a host can show or forward the id of the approval it is about to wait for. It runs on the calling
    thread (it must return quickly); an exception in it is contained, an interrupt withdraws the approval. The demo uses it to print the id for a person or the e2e script. Not in the plan; it is the smallest way to make "the demo prints
    the approval id" possible, because `guard()` blocks until the decision.
12. **Version 0.4.0.** The surface is new public API (ADR-013's rule for minor versions); the MCP adapter now requires `agent-black-box>=0.4.0`. The SDK still has no runtime dependency; a test parses the policy modules' imports and
    fails on anything outside the standard library.

### Amendment 1 (Phase 14 review, 2026-10-13)

- **A verdict is read decision-first (P2-1).** `deny` raises `PolicyDenied` whatever else in the body is malformed (wrong types, missing fields, huge integers, extras). A 2xx reply whose `decision` is not
  `allow`, `deny` or `require_approval`, or a `require_approval` without a usable `approval_id` and `expires_at`, fails **closed** with `PolicyUnavailable(policy_error)` at every risk class: a person was
  asked for, or the server said something this client cannot interpret. A malformed `allow` (no integer `policy_version`) and a body that is not an object remain failed exchanges and follow the fail mode
  (point 6); only transport and server errors use the risk-class mode.
- **Cancelled is not expired (P2-5).** Only `EXPIRED` follows `on_expiry`; `CANCELLED` raises `ApprovalExpired` (a `PolicyDenied`) always: the requester withdrew, time did not run out.
- **Attribute limits (P2-2).** The pre-send check refuses an intent with more than 64 top-level scalar arguments (`too_many_attributes`, reason `invalid_intent`); the strings limit stays 4,096. Shared vectors with the server.
- **Shell commands are arguments (P2-3).** `guard_command` sends `arguments.command` (the exact string) so the approval binds to it and the approver sees it. A command over 4,096 characters is passed with `on_error="closed"`
  and fails closed with `intent_too_large` whatever its risk class: it can be neither shown nor bound.
- **Wait budget (P3-6, P3-7).** A `429` on the wait is the server pacing a busy key (many `aguard` waits share one key's 1,200/minute): it pauses for `Retry-After` and is not a failed round, so it never cancels a live
  approval; the approval's own deadline still bounds it. The server `Date` is believed only within 10 minutes of the local clock; past that the local clock sets the wait budget, so a skewed server cannot cancel a live approval.
- **`guard()` outside a run (P3-9).** With no current run there is nothing to bind a decision to, so the service is **not asked**: the fail mode applies with `invalid_intent` (R3/R4 refuse with `PolicyUnavailable`; R0-R2 and
  unclassified proceed, and with no run there is no event to hold a `policy.warning`). The module-level `blackbox.guard` returns `via="disabled"` only when there is no client at all. The MCP README says the same: calls outside a run pass through.
- **`policy.warning` survives an outage (KI-152).** The exporter hands warnings it drops (retries exhausted) to a bounded in-memory spool (1,000; oldest dropped and counted in `policy_warnings_lost`). They are put back in the
  buffer, with their original ids (a copy that did arrive is a duplicate, not a second event), on the next successful batch, the next successful policy decision, `flush()` and `shutdown()`. What the bound lost is reported once
  as one `policy.warning` with reason `policy_warnings_dropped` and the count in `policy.warnings_dropped` (a new registered integer attribute). It is memory only: a process that dies during an outage still loses them.

## Alternatives considered

- **Fail open on a broken approval wait when the fail mode is open.** Rejected (point 4): a human gate is not an availability feature.
- **Retry a failed consume with the same intent.** Rejected (point 5): it is a replay window, and ADR-141 makes the second call fail anyway.
- **A background thread for waits, or a shared keep-alive connection.** A thread is a second place for the host to be hurt (INV-4); a shared connection needs a lock held across a long poll. One connection per call costs a
  handshake (KI-148); revisit with measurements.
- **Record a "blocked" step in the run from the recorder.** The server already writes the typed `policy.action.blocked`; a second, client-written one is a record the server cannot vouch for (ADR-141 point 3).
- **Guard the recorder's internal metadata probes** (`git rev-parse` and similar, run to fill event attributes). They are not actions the agent chose and read hashes and names; listed as a limit (KI-151).

## Consequences

- New: `blackbox.policy` and four helper modules (`policy_intent`, `policy_transport`, `policy_failmodes`, `policy_glob`; the glob is a copy of the server's, compared in a test), `BlackBox.guard/aguard`, options `policy_mode`,
  `policy_timeout`, `policy_wait_max`, `policy_wait_round`, `policy_fail_modes`, `policy_on_approval`, five stats counters, `CodingRecorder.guard_*`, `abb_conformance.policy_stub` and `check_guarded_tool`.
- SDK event priorities now include `approval.consumed`, `approval.expired` and `approval.cancelled` as P0 (step 2 added them to the registry; the SDK mirror and its contract test were out of step).
- Open (KNOWN_ISSUES): KI-148 (a connection per decision), KI-149 (name resolution is not interruptible), KI-150 (tool names that cannot form a capability), KI-151 (`aguard` pool size and the recorder's unguarded
  metadata probes).
- Existing tests changed: none weakened. `tests/test_contract.py::test_priorities_equal_the_registrys_for_every_registered_type` failed at the start of this step (the registry's three new P0 types) and passes after the SDK's mirror
  was updated.
