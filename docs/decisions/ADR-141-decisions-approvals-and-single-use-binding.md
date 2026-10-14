# ADR-141: Decisions, Approvals and Single-Use Binding

Status: Accepted (Phase 14, step 2). Implements D3 and D4 of the Phase 14 plan and spec §88, §89, §90, §151, §154; builds on ADR-140 (the policy
model), ADR-130 (redaction before storage), ADR-061 and ADR-062 (authorization and audit) and ADR-022 (the NOTIFY wake-up). Refines D3's wording
about the fail mode (point 9) and records two deviations from the roadmap: waiting is long polling (already pointed to by ADR-140) and the SDK
consumes by presenting the whole intent, not a hash (point 2).
Date: 2026-10-13
Phase: 14

## Context

Phase 14 only keeps its promise if three things are true at once: an approval authorizes exactly the action a person looked at, it authorizes it once,
and the people and keys that can create, decide and spend approvals are exactly the ones we say. Every part of that is an attack surface: an agent
that replays an approval for a different command, a second use, a race between two deciders, a key that decides its own request, an approval from
another workspace, a flood of requests that buries the approvers, an SDK that forges the record of what the policy said. The decisions below close
each of these in the database and the code, and the tests attack them directly (`apps/api/tests/approvals/`, `tests/authz/`).

## Decision

### 1. The intent hash (cross-implementation, with golden vectors)

`intent_hash` is the SHA-256 of `b"abb.intent.v1\n"` followed by the canonical UTF-8 bytes of exactly these fields as the SDK sent them, before any
scrubbing: `workspace_id`, `project_id` (both from the API key, never from the body), `run_id`, `agent_id`, `capability`, `resource`, `risk_class`
(or null) and `arguments`. The reference implementation is `packages/event-schema/src/abb_event_schema/intent.py`; the shared proof is
`packages/event-schema/tests/data/intent-hash-vectors.json` (11 valid vectors with the canonical text and the hash, 9 invalid ones with the refusal code).
The SDK (step 3) reproduces that file byte for byte; the API test (`tests/approvals/test_intent.py`) checks the server's own path against the same file.

Canonical form, version 1:

- One JSON object with the eight keys above in code point order (`agent_id, arguments, capability, project_id, resource, risk_class, run_id, workspace_id`);
  `resource` is a string (empty if none), `risk_class` a string or `null`, `arguments` an object (empty if none).
- No whitespace. Object keys are sorted by Unicode **code point** at every depth (not UTF-16 code units, which differ above U+FFFF).
- Every string, keys included, is normalised to **NFC** first; two keys equal after normalisation are an error (`duplicate_key`), never merged. Strings are
  written as raw UTF-8 with only these escapes: `\"`, `\\`, `\b`, `\f`, `\n`, `\r`, `\t` and `\u00xx` (lower-case hex) for any other code point below U+0020.
  U+007F and everything non-ASCII stay raw. U+0000 and lone surrogates are errors.
- Numbers are **integers only**, decimal, no `+`, no leading zeros, no exponent, |n| at most 2^53 - 1. A float (even `1.0`) is `float_not_allowed`: there is no
  portable shortest-float form to agree on, so an SDK sends decimals as strings. `true`, `false`, `null`, arrays and objects are the only other values.
- Bounds, enforced while walking: depth 8, 512 values, strings 4,096 characters, keys 256, capability 128, resource 2,048, canonical bytes 24 KiB. An error
  carries a fixed code (`too_deep`, `string_too_long`, ...) and never the value. The API answers `422 INTENT_INVALID` with `details.reason` set to that code.

NFC means text that differs only by composition (`é` as one code point or as `e` plus an accent) is the same intent. That is deliberate (the same words to a
person) and is the one place the hash is looser than "byte for byte what was sent". The hash is stored on the approval row and compared in memory with
`hmac.compare_digest`; it is **never returned, logged, put in an event or an audit row**. It is not reversible to content, but it confirms a guess of one, so
it is treated as sensitive. Tests search every response, event, audit row and captured log line for it.

### 2. The decision endpoint

`POST /v1/policy/decide` takes `{run_id, agent_id, capability, resource?, risk_class?, arguments?, trace_id?, idempotency_key?}` (unknown fields refused; the
body is read with a 64 KiB limit before it is parsed, so an oversized body is a 413 before any work). Credential: an API key bound to a project, with scope
`policy:check` **or** `events:write` (action `policy.decide`). Both scopes work so an existing SDK key needs no change and a key that may only ask can be made;
a session is refused (403). A workspace-wide key is `PROJECT_KEY_REQUIRED` (the run's project must be known). A run that belongs to another project of the
workspace is the same `RUN_NOT_FOUND` as an unknown one.

The server resolves the effective policy (the project's latest version, else the workspace base, else the empty policy: ADR-140), runs the pure evaluator, and
returns `{decision, policy_id, policy_version, reason, on_error, approval_id?, approval_state?, expires_at?, on_expiry?}`. `policy_id` is the matching rule's id
(null when the policy default applied); `reason` is the rule's own description, scanned for secrets, never built from the action. The rule's `attributes` match
the **top-level scalar arguments** (strings, integers, booleans; at most 64, each string at most 4,096 characters, and an intent beyond either bound is refused: Amendment 2), so `arguments: {"force": true}` satisfies
`attributes: {force: true}` and the dry-run mapping of ADR-140 and enforcement agree. The response has **no `intent_hash`**: consumption recomputes it from the intent
(point 6), so the client never needs it and it never leaves the server.

- `allow` writes a `policy.decision` event; `deny` a `policy.action.blocked` event. No row, no audit row (an allow is an event, not an administrative act).
- `require_approval` creates an approval and writes `policy.decision` (with `approval.id`) and `approval.requested`, in the same transaction. The approval's
  `expires_at` is `now + expires_in` of the rule, `on_expiry` is the rule's fallback, and the rule's `approver_role` and `allow_self_approval` are copied onto
  the row, so a later policy publish never changes what a pending request needs.
- **Idempotent**: the same intent (same run, same key, same hash) while an unexpired `REQUESTED` or an unspent `APPROVED` approval exists returns *that* approval and
  writes nothing. A transaction advisory lock on `(workspace, run, key, hash)` serialises concurrent asks and a partial unique index is the backstop (a test sends
  50 at once and gets one approval and one `approval.requested`). Approvals belong to the key that asked: another key of the same project asking the same intent gets its
  own request. An expired one is swept first, so the same intent asked after expiry gets a fresh request.
- `on_error` in the response is the matching rule's `on_error`, else `closed` for R3 and R4 and `open` for R0-R2 **and unclassified** actions. It tells the SDK what
  to do the next time the service is unreachable for an action like this (point 9).
- Failure modes: an unreadable stored policy is `503 POLICY_UNAVAILABLE` (retryable; the SDK applies its fail mode); database trouble is the usual 503.

### 3. The server writes the `policy.*` and `approval.*` events

A decision is the server's statement, so the **server** appends the events, in the same transaction as the state change they describe: the SDK cannot forge a
`policy.reason`, and a transaction that rolls back takes its event with it. The events are accepted events (INV-1; new rows only) written through the
ingestion store, so they get the same idempotency on `(workspace_id, event_id)`, the outbox job that re-derives the run, and the NOTIFY that wakes live streams.

- Event ids are derived with an **HMAC under a server-only secret** (Amendment 2; the first version hashed public inputs only and an agent could pre-claim them): over the
  workspace, the approval id and the transition (`REQUESTED`, `APPROVED`, ..., `consumed`) for approval events, and over the run, the `idempotency_key` and the intent hash for
  allow and deny. A redelivery or a second attempt at the same transition is an idempotent duplicate; a transition happens at most once, so exactly one event per transition.
  Allow and deny without an `idempotency_key` get random ids (two identical actions in a run are two decisions).
- Event types: the existing `policy.decision`, `policy.action.blocked`, `approval.requested`, `.granted`, `.denied`, and three new ones registered here per the
  add-event-type checklist: `approval.consumed`, `approval.expired`, `approval.cancelled` (category approval, priority P0, require `approval.id`). Attributes are
  metadata only: `policy.id` (the rule, or `default`), `policy.version`, `policy.capability`, `policy.decision`, `policy.reason`, `approval.id`, `approval.state`,
  `approval.capability`. No resource, argument, command, path or comment ever enters an event, and no new attribute was needed.
- Provenance: the events carry `sdk.name = "abb-policy-service"`, and ingestion refuses that name from clients (`emitter_reserved`), so the name says who wrote an
  event. **Reserved event types (Amendment 1, KI-143):** the ingestion endpoints also reject, per event, the types the server writes: `policy.decision`,
  `policy.action.blocked` and `approval.requested`, `.granted`, `.denied`, `.consumed`, `.expired`, `.cancelled`, with the code `RESERVED_EVENT_TYPE` (issue code
  `event_type_reserved`). The check reads the raw `event_type` before validation, so the answer does not depend on which attributes the forger sent. It follows the
  `REDACTION_FAILED` pattern: the batch is still `202`, the other events are stored, nothing of the rejected event is stored, and the SDK counts it as rejected and does
  not retry. The single-event endpoint answers `422 EVENT_INVALID`. The policy service writes its own events through `PgEventStore` directly and never through
  ingestion, so it needs no bypass. `policy.warning` stays client-writable (the SDK writes it when the policy service is unreachable). Events of these types stored
  before this change remain (INV-1); the UI still shows provenance (step 4).
- Ordering: the events carry no `sequence`, because the server is a second emitter of the run (ADR-070). A run with a decision therefore sorts by time, like a
  multi-process run, and a skew between the server and the host's clock can misplace a decision against the action (KI-142). The summary treats
  `approval.expired` and `approval.cancelled` like `granted` and `denied`: the run leaves `WAITING_FOR_APPROVAL`.
- A run with no events yet gets its trace from `trace_id` (a hint, used only then), else a trace derived from the run id; the SDK sends its own.

### 4. The state machine is enforced by the database

`approvals` (migration 0093) holds one row per request: the bound intent (scrubbed resource and arguments, `risk_class`, rule id and version, `approver_role`,
`allow_self_approval`, `on_expiry`, `requested_at`, `expires_at`), the requesting key and the user that key belongs to, the state, and the facts of the end of the
request (`decided_by_user_id`, `decided_at`, `closed_at`, `decision_comment`, `consumed_at`, `consumed_run_id`).

| From | To | What changes | Allowed when |
| --- | --- | --- | --- |
| `REQUESTED` | `APPROVED`, `DENIED` | state, decider, `decided_at`, comment | `decided_at` <= `expires_at` |
| `REQUESTED` | `CANCELLED` | state, `closed_at` | any time |
| `REQUESTED` | `EXPIRED` | state, `closed_at` | `closed_at` >= `expires_at` |
| `APPROVED`, unspent | `APPROVED`, spent | `consumed_at`, `consumed_run_id` only (NULL to a value, once) | `consumed_at` <= `expires_at` |
| `APPROVED`, unspent | `EXPIRED` | state, `closed_at` (the decision stays) | `closed_at` >= `expires_at` |
| anything else | | final | never |

Two layers hold it. CHECK constraints fix the shape of every state (who decided, which times are set, `consumed_*` only on `APPROVED`, hash format, sizes, closed
vocabularies) and bind every role. A `BEFORE UPDATE` trigger (`approvals_guard`) refuses every statement that is not one of the rows above and refuses any change
to a column of the requested intent; a `BEFORE INSERT` trigger lets a row be born only as `REQUESTED` and unspent. The runtime role has SELECT, INSERT and UPDATE
but no DELETE or TRUNCATE (like `audit_log`); `tests/approvals/test_state_machine_db.py` connects as that role and fails each of: changing the hash or any bound field,
un-consuming, consuming twice, leaving a terminal state, deciding after expiry, expiring early, a no-op update, inserting a decided row, deleting, truncating.
All times are supplied by the application clock (never the database's), so the rules hold under the injectable test clock.

**Limit, stated plainly:** the trigger guards *integrity*, not *authorization*. A holder of the runtime credential can still make a legal transition (approve a
`REQUESTED` row) by hand. That is why every transition by the application is also an event and an audit row, and why the runtime credential is the thing to protect.

### 5. Atomic transitions, races and expiry

Every transition is one statement that names the state it expects and returns the row: `UPDATE approvals SET ... WHERE id = :id AND workspace_id = :w AND state = 'REQUESTED'
AND expires_at > :now RETURNING *`. Nothing reads then writes. A concurrent approve, deny, cancel or expiry therefore has exactly one winner; the loser's statement matches
no row and the caller answers `409 APPROVAL_NOT_REQUESTED` (with `details.state`). The winner, in the same transaction, appends the transition's event and its audit row, so
a transition leaves exactly one of each and a loser leaves none (tests race approve/deny, approve/approve, deny/cancel and approve/expiry and count events and audit rows).

**Expiry.** `expires_at` is `requested_at + expires_in` (1 minute to 7 days; the rule's value). An approval is usable only until then, approved or not: an `APPROVED`
approval that was never spent also becomes `EXPIRED`. Three things close an approval, all through the same statement: (a) a job `expire_approval` enqueued with each approval and due at its
`expires_at` (the repo's outbox is the timer, so it survives restarts and any worker claims it; it sweeps the whole workspace, so one firing also closes other
due approvals, and it is idempotent); (b) a lazy sweep on every read that matters (wait, consume, cancel, decide, detail, list, count, and the ask for the same intent);
(c) the guard trigger, which refuses a late decision or a late spend even if a bug skipped (b). Each expiry writes `approval.expired` and an audit row with actor kind `system`
(migration 0093 widens the audit actor kinds). The rule's `on_expiry` (`deny` default, `allow`) is stored and returned by the wait endpoint; it is the **SDK's fallback**:
an expired approval never authorizes anything by itself and cannot be consumed.

### 6. Single use: consumption

`POST /v1/approvals/{id}/consume {intent}` takes the whole intent again. The server recomputes the hash from it (in the key's workspace and project) and spends the
approval only if all of these hold, in one statement: the approval is in this workspace and project and was requested **by this key**; it is `APPROVED`; it is not
expired; it is unspent; its stored hash equals the recomputed one (compared first with `hmac.compare_digest`, and repeated in the statement's `WHERE`); its run is the intent's run.
Anything else is a distinct fixed code that echoes nothing:

| Code | Status | When |
| --- | --: | --- |
| `APPROVAL_NOT_FOUND` | 404 | unknown id, another workspace, another project, or another key's approval: one body for all |
| `APPROVAL_NOT_APPROVED` | 409 | still `REQUESTED`, `DENIED` or `CANCELLED` |
| `APPROVAL_EXPIRED` | 409 | expired (marked or not yet) |
| `APPROVAL_CONSUMED` | 409 | already spent (also: the second of two simultaneous calls) |
| `APPROVAL_INTENT_MISMATCH` | 409 | approved, live and unspent, but for a different intent (command, resource, argument, agent, risk class, capability or run) |
| `APPROVAL_POLICY_DENIED` | 409 | everything above holds, but the policy published now denies the presented intent (Amendment 1) |

**Amendment 1: consume re-evaluates the current policy (KI-144).** After the intent checks and before spending, consume reads the policy that governs the project now and
runs the pure evaluator on the presented intent. If the decision is `deny` (a rule, or the policy default), consume answers `APPROVAL_POLICY_DENIED` and spends
nothing: the approval stays `APPROVED` and unspent (it expires on its own), so publishing an allow again, or a rule that no longer matches, makes it usable
again. The refusal is committed with a `policy.action.blocked` event (rule id and version of the policy that denied; one event per approval and policy version, so
retries do not repeat it) and the audit row `approval.consume_refused` (reason code, rule, version; no intent). If the decision is `allow` or `require_approval` the
consume proceeds: a granted approval satisfies a rule that still requires approval, and a policy that changed from `require_approval` to `allow` also proceeds
(the approval is simply unnecessary, not harmful). The successful `approval.consumed` event carries `policy.current_id` and `policy.current_version` (the policy
re-evaluated) beside `policy.id`/`policy.version` (the version that required the approval), and the `approval.consume` audit row carries `current_policy_rule` and
`current_policy_version`. An unreadable stored policy is `POLICY_UNAVAILABLE` (503, retryable), as for decide.

*Atomicity guarantee (honest form).* The consume transaction takes shared advisory locks on the workspace-base and project policy scopes before reading the policy;
publishers hold the same keys exclusively until they commit (`lock_scope`). Therefore every consume is ordered against every publish of a scope that governs it: a
deny whose publish commits before the consume's policy read is always honoured, because the read happens after the lock and sees committed data (READ COMMITTED);
a publish that starts while a consume holds the lock waits for that consume to commit, so the consume spends under the older policy and the deny is honoured from the
next attempt. What this does not give: a deny published after the consume committed cannot recall an action already authorised, and the action itself runs
outside the server, so a deny published between the consume and the action does not stop it. Locks are shared, so concurrent consumes do not block each other; the
cost to a publisher is waiting for in-flight consumes (milliseconds).

The caller learns which step failed and nothing about the values. Success appends `approval.consumed` and the audit row `approval.consume` (actor: the key) in the same
transaction. Fifty simultaneous consumes give one 200 and 49 `APPROVAL_CONSUMED`. **A lost response cannot be retried**: the second call is `APPROVAL_CONSUMED`. An idempotent
consume would be a replay window by definition (the agent can always repeat the request it already sent), so the SDK must treat a consume it could not complete as a
failure and ask again; the action does not happen without a fresh approval (fail closed; KI-145). Rejected consumes are counted in the log as a reason code only.

`DELETE /v1/approvals/{id}` withdraws a waiting request (the key that asked, while `REQUESTED`; otherwise `409 APPROVAL_NOT_REQUESTED`), with event `approval.cancelled` and audit `approval.cancel`.

### 7. Who may decide

Deciding is `POST /v1/approvals/{id}/decision {decision: approve|deny, comment?}`. It needs a **signed-in person**: the actions `approval.read` and `approval.decide` are held by
OWNER, ADMIN, DEVELOPER and SECURITY (the roles that hold `payload.read`, because the approver must see what they approve) and by **no API key scope**; VIEWER and BILLING hold
neither. The member's role is re-read on every request (a removal or demotion takes effect on the next call). On top of the action, the rule's `approver_role` narrows
who may decide that approval, as a ladder (widest authority first, a named role is satisfied by itself and everything above it):

| `approver_role` | Satisfied by |
| --- | --- |
| unset | OWNER, ADMIN |
| `owner` | OWNER |
| `admin` | OWNER, ADMIN |
| `security` | OWNER, ADMIN, SECURITY |
| `developer` | OWNER, ADMIN, SECURITY, DEVELOPER |

(`tests/approvals/test_roles.py` holds the literal table and checks it both ways; `test_decision.py` runs every role against every row through the API.) A role outside the ladder
satisfies nothing. A failed check is `403 APPROVER_ROLE_REQUIRED` and is audit-logged as a denial (bounded per person, like every denial).

**Self-approval.** Each approval records `requested_by_user_id`: the person the requesting API key was created by (`api_keys.created_by`; null for CLI-made keys). The person who
created the key cannot decide that key's requests unless the rule sets `allow_self_approval` (`403 SELF_APPROVAL_FORBIDDEN`, audited). This is the best the model can do:
a person who can mint a key can mint another, so the rule stops accidents and a single compromised agent session, not a malicious insider; separation of duties needs two people.
A key with no creator imposes no restriction.

The decider's comment is scanned (a secret is replaced; a comment that cannot be scanned is dropped, never stored raw) and bounded to 1,000 characters. The audit row `approval.decide`
carries the approval id, the decision, the decider's role and the rule id, and **never** content. The agent's key never sees the comment or who decided.

Reading: `GET /v1/approvals` (`state=pending|decided|all`, `project_id`, `limit` 1-100, `cursor`), `GET /v1/approvals/pending-count`, `GET /v1/approvals/{id}` need `approval.read`.
They return the scrubbed resource and arguments, the rule and version, `approver_role`, times and the decision, and **no hash**. A foreign, unknown or malformed id is one `APPROVAL_NOT_FOUND`.

### 8. Long polling, with caps

`GET /v1/approvals/{id}/wait?timeout=<=25` is for the asking key only. It returns at once if the approval is no longer `REQUESTED`; otherwise it returns at the first change or after
`timeout` seconds with the current state, `expires_at`, `on_expiry`, the rule id and version and `consumed` (no content). The wake-up is the existing NOTIFY channel (ADR-022): every
transition appends events through the ingestion store, which notifies the run's subscribers, and the waiter re-reads; a fallback poll (`ABB_APPROVAL_WAIT_POLL_SECONDS`, default 1 s)
covers a missed notification, and a minimum gap of 250 ms between reads stops a chatty run from turning wake-ups into a spin. **No database connection is held while waiting**: each read
borrows one for a statement. Waiters are capped per workspace (20) and per process (200): beyond that `429 TOO_MANY_WAITERS` with `Retry-After`. The slot is released when the waiter returns, errors
or is cancelled. Tests show: early return on a decision, the timeout returning the current state, the wake coming from the notification (a 10 s poll still returns within 3 s), the caps, ordinary
requests during parked waits, and release on disconnect.

### 9. The fail-mode table (a refinement of D3)

D3 says a rule's `on_error` decides what the SDK does when the service is down. A client cannot read a rule from a server it cannot reach, so **a rule-level `on_error` is only known to an
SDK that cached it before the outage.** `GET /v1/policies/fail-modes` (an agent key: its own project; a person: `project_id`, else the workspace base) returns `{project_id, source, policy_version,
rules[], defaults}`: `rules` lists, in policy order, those whose `on_error` is set explicitly, each with `rule_id`, `capability` pattern, `resource` glob, `risk_classes`,
`has_attribute_conditions` and `on_error`; `defaults` is `R0-R2: open`, `R3-R4: closed`, `unclassified: open`. The SDK reads it at startup and refreshes it when it sees a newer `policy_version`. An SDK that
has no cached table, or whose action matches no listed rule, uses the risk-class defaults and its own configuration. The table is an approximation by design: it cannot carry attribute
conditions (`has_attribute_conditions` says so; a client should treat such a rule conservatively) or the first-match order between a listed rule and an unlisted one before it. It is not a
promise that a rule's `on_error` is honoured when the service is down; it is the only information that can be.

### 10. Limits

Per key and per workspace, per process: decide, wait (at its start), consume and cancel draw from one budget (`ABB_POLICY_DECIDE_PER_MINUTE_KEY`=1200, `..._WORKSPACE`=6000; `429 RATE_LIMITED` with
`Retry-After`); a flood is refused at 429 with the exact allowance served (a test sends 200 into a budget of 30) and does not slow ordinary requests. Waiters as above. Open requests are capped per key (100)
and per workspace (1000): `429 TOO_MANY_OPEN_APPROVALS`, so a runaway agent cannot bury the approvers. Bodies are bounded (64 KiB), arguments are bounded (point 1). All limits are per process (KI-141).

### 11. Threat model (spec §90), row by row

| Threat | Closed by | Evidence |
| --- | --- | --- |
| Replay: the approval for command A used for B (or another resource, argument, agent, risk class, run) | hash over the unscrubbed canonical intent, recomputed at consume, compared in constant time and again in the `UPDATE`; mismatch is a fixed code | `test_consume.py` (9 variants), golden vectors |
| Second use, including a race | one `UPDATE ... consumed_at IS NULL`; the trigger refuses a second write | 50 concurrent consumes: one 200; DB test |
| Use after expiry or before approval | `expires_at > now` in the statement, the lazy sweep, the trigger; `APPROVAL_EXPIRED` / `APPROVAL_NOT_APPROVED` | `test_consume.py`, `test_expiry.py` |
| Confused deputy: an agent approving or reading approvals | no key scope holds `approval.read` or `approval.decide`; sessions hold neither `policy.decide` nor `approval.use` | authz matrix rows, `test_decision.py` |
| A person approving their own agent's action | `requested_by_user_id` compared with the decider | `test_decision.py` |
| Cross-workspace, cross-project, cross-key | every statement carries the workspace, agents also project and key; one `APPROVAL_NOT_FOUND` for all | cross-workspace probes in `tests/authz`, `test_consume.py` |
| Race between deciders / cancel / expiry | single-statement transitions with a state predicate | `test_decision.py` race tests count events and audit rows |
| A hostile SDK forging the policy record | the server writes the events; its emitter name and types are reserved | `test_decide.py`, `test_reserved_events.py` (reserved emitter and reserved types; KI-143 resolved) |
| A hostile SDK **pre-claiming** the server's event ids so its insert is swallowed (review P1-1) | event ids are an HMAC under a server-only secret; a conflict on a server write is never swallowed: it aborts the transition | `test_event_ids.py` (the reviewer's probe as a test; a taken id fails the transition loudly) |
| Approving what the approver could not see (review P2-3, P2-4) | a shell intent carries its exact command, shown scrubbed; an approval whose content the scanner withheld cannot be approved (`APPROVAL_CONTENT_UNAVAILABLE`), only denied | `test_shell_binding.py`, `test_content.py` |
| Content leaking from an approval | scrubbed before storage (fail closed), never in events/audit/logs/agent responses; the hash never leaves | `test_content.py` plants sentinels and secrets |
| A flood of asks, waits or requests | per-key and per-workspace budgets, waiter caps, open-request caps, body limits | `test_flood.py`, `test_wait.py` |
| Rewriting history with the app credential | CHECKs + guard trigger, no DELETE/TRUNCATE | `test_state_machine_db.py` (limit: legal transitions by a holder of the credential remain possible) |

### Amendment 2 (Phase 14 review, 2026-10-13)

**P1-1: server event ids are unforgeable, and a conflict is loud.** The review showed an agent key could ingest innocuous events at the ids the server would derive
(public inputs), after which the server's `INSERT ... ON CONFLICT DO NOTHING` reported a `conflict` that `lifecycle.emit` swallowed: the approval was granted and spent and the
run held no `approval.granted` or `approval.consumed`. Fixed in three parts.

1. *Ids.* `derived_event_id(key, workspace, *parts)` is `HMAC-SHA256(key, "abb.server-event.v2" || workspace || length-prefixed parts)`, truncated to a ULID-shaped `evt_` id. `key` is a random
   32-byte installation secret in the append-only table `server_secrets` (migration 0094; the runtime role can SELECT and INSERT, not UPDATE, DELETE or TRUNCATE), created on first use under an
   advisory lock and cached per process, so every API process and worker shares it, it survives restarts, and nothing in code or configuration holds it. Allow and deny decision events with an
   `idempotency_key` mix the HMAC and the intent hash (the client's key alone no longer names the event; the same key for another intent is its own event).
   **Rotation:** an operator inserts a new row as the owner role (`INSERT INTO server_secrets (name, secret) VALUES ('event-id', decode('<64 hex characters from openssl rand -hex 32>', 'hex'))`; `gen_random_bytes` needs the `pgcrypto` extension, which the stock image does not load: corrected by ADR-150) and restarts the processes; the newest row
   wins. Rotation changes the ids of future events only; stored events are never rewritten (INV-1), and a transition retried across a rotation simply writes a new event under the new key.
   Anyone who can read the table (the owner role, a database administrator) can compute the ids; the secret protects against agent keys, not against the database.
2. *Atomicity.* `emit` accepts a `conflict` in exactly one case, a retry of the server's own write: the stored event has the reserved emitter, the same type and the same approval id (the
   retry has a different `occurred_at`, hence a different body). Anything else logs the fixed code `SERVER_EVENT_ID_CONFLICT` (no content) and raises `500 EVENT_RECORD_FAILED`.
   Because the transition, its event and its audit row share one transaction, the whole transition rolls back: **a decision never takes effect without its record.** An agent that somehow
   occupied an id causes a refused approval, not a silent one. (A person's approve then fails with a 500 until the cause is found; the cost is a stuck approval, the alternative was an
   unrecorded spend.)
3. *Tests* (`apps/api/tests/approvals/test_event_ids.py`): the reviewer's probe (ask, ingest innocuous events at the formerly derivable ids, approve, consume: the run shows `approval.granted` and
   `approval.consumed`); the same for an `idempotency_key`; a client event at an id computed with the key is accepted as the client's own and the server's transition is refused and rolled back;
   a retry of the server's own write is accepted; the key is created once and stable.

**P2-2: attribute limits.** A rule matches every top-level scalar argument; there are at most 64 and each string is at most 4,096 characters (the canonical string limit). More is refused
(`422 INTENT_INVALID`, `too_many_attributes` or `attribute_too_long`) instead of silently ignored, in the server and in the SDK's pre-send check, which share
`packages/event-schema/tests/data/attribute-limit-vectors.json`. A refusal in the SDK follows the fail mode with `invalid_intent` (closed for R3/R4).

**P2-3: shell intents carry the command.** `shell.execute` intents carry `arguments.command`, the exact command (at most 4,096 characters), beside `category`; the resource stays the executable name
for matching. The approval therefore binds to the exact command, and the approver sees it, scrubbed, in the Approvals detail (replay test: approve `git push origin main`, present `git push --force origin main`:
`APPROVAL_INTENT_MISMATCH`). A longer command cannot be shown or bound and fails closed in the SDK whatever its risk class. The canonical form is unchanged. The dry-run mapping adds the same `command` attribute.

**P2-4: withheld content cannot be approved.** An approval whose resource or arguments the scanner withheld (`scrub_dropped`: too large, out of time, scan error) answers an approve with `409 APPROVAL_CONTENT_UNAVAILABLE`
(the request stays open; deny stays possible). The panel says the content could not be shown, not that the role hides it.

**Smaller fixes.** Consume refusals for a different intent are audited (`approval.consume_refused`, reason code only), at most one per approval per minute per process (P3-8). The expiry job re-arms itself when it
fired before the approval was due by the application clock (P3-2). The waiter slot is released if the subscription fails (P3-3). A key with only `policy:check` cannot create a run by asking about an unknown one
(`RUN_NOT_FOUND`; P3-4). The open-request caps are checked under a per-workspace advisory lock (P3-5). **Recorded, not changed:** P3-1: a grant stays valid after a rule's `approver_role` is tightened (consume
re-evaluates the *decision*, not the approver's seniority); publishing a deny stops it, and it expires on its own (KI to file).

## Alternatives considered

- **Return the hash and consume with `intent_hash` (the plan's contract wording).** The server would then compare a client-supplied hash with the stored one, trusting the client's canonicalisation
  and putting a content-confirming value on the wire. Recomputing from the intent costs one more request body and removes both.
- **An idempotent consume (token or hash-keyed).** Rejected: it is a replay window (point 6).
- **Per-project approvals visible to every key of the project.** A key could cancel or wait on another agent's approvals; binding to the asking key costs nothing and the 404 hides it. After a key rotation,
  pending approvals of the old key are not usable by the new one: the agent asks again.
- **A WebSocket.** No standard-library client (ADR-140 point 11).
- **A periodic scanner job instead of per-approval timers.** One more always-on loop and a latency of the period; the outbox timer is exact, durable and already operated. The lazy sweep covers its gaps.
- **Enforcing the machine only in the service.** A bug or a hand-written statement would then rewrite an approval; the trigger costs microseconds per update.

## Consequences

- New: migration 0093, `approvals/` in the API, one job type (`expire_approval`), nine routes (decide, fail-modes, wait, consume, cancel, list, pending count, detail, decision), five actions
  (`policy.decide`, `approval.use`, `approval.read`, `approval.decide`, `policy.fail_modes`; `approval.decide` is no longer reserved), three event types, audit actor kind `system`, the intent hash in `event-schema`.
- Step 3 (SDK): `guard()` posts the intent, sends the same intent to consume, treats a refused consume as a denial, treats `TOO_MANY_*`, 429 and 503 as service failures, and reproduces the vectors.
- Step 4 (web): the approvals page uses `GET /v1/approvals`; show `approval.*` provenance; the run page can show `seconds_left`.
- Open (filed in KNOWN_ISSUES): KI-142 (server events put a run into time order), KI-143 (resolved by Amendment 1), KI-144 (resolved by Amendment 1: re-evaluated against a policy
  published after the approval), KI-145 (a lost consume response is not retryable), KI-146 (the fail-mode table is approximate), KI-147 (per-process limits).
- Existing tests changed (also in the step report): `test_matrix_data.py` (new grants; `approval.decide` no longer reserved), `test_role_matrix.py` and `registry.py`/`world.py` (nine routes, a seeded gate and approval),
  `test_registry_is_complete.py` (the operation count), `test_runtime_role.py` (`approvals` is update-only through its trigger), `test_migrations.py` (0093), `test_run_summary.py` (additions), `conftest.make_settings`
  (high decision limits for the matrix), event-schema `test_event_parse.py` (three event types).
