# ADR-140: Policy Model, Evaluation and Versioning

Status: Accepted (Phase 14, step 1). Implements D1 and D2 of the Phase 14 plan and spec §33, §88, §151; amends nothing in the spec. Step 2 (decisions and
approvals) records its own ADR; this one fixes the contract it builds on.
Date: 2026-10-13
Phase: 14

## Context

Phase 14 lets a project say what its agents may do and makes an integrated agent ask before a sensitive action. Everything downstream (the decision
endpoint, approvals, the SDK's `guard()`, the Policies screen) depends on three things being right: the policy language is small enough to reason about
and to validate; evaluating it is deterministic and cannot be turned against the server; and what a policy said at the time of a decision can always
be shown later. The policy text is also security-sensitive input written by a person and read by a machine.

## Decision

1. **The guarantee is cooperative, and every surface says so (D1).** The server decides; an agent that goes through the SDK (`guard()` or an
   integration that calls it) is stopped or paused, an agent that bypasses the SDK is not. The management API's descriptions, the docs and, in step 4, the
   UI use the words "ask the policy service" and carry no "protected" badge. Phase 13's detectors still show a protected action that happened without a
   matching decision.
2. **The language is the §151 YAML, kept small on purpose (D2).** `version: 1`; optional `default: allow | deny` (default `allow`); `policies:` an ordered
   list of rules. A rule has `id` (lowercase slug, unique), optional one-line `description`, `when`, `decision` (`allow | deny | require_approval`) and,
   for `require_approval` only, `approver_role`, `expires_in` (`15m`, `1h`, `2d`; 1 minute to 7 days; default 15 minutes), `on_expiry` (`deny` default |
   `allow`) and `allow_self_approval` (default false). `on_error` (`open | closed`, any decision) is optional; when absent the fail mode is derived from
   the risk class when the decision is asked (R0-R2 open, R3-R4 closed; step 2). `when` has at least one of `capability`, `resource`, `risk_class`
   (`R0`-`R4` or a list) and `attributes` (exact matches of text, numbers or booleans), and every given condition must hold.
   - **Deliberately absent:** regular expressions, boolean logic (`or`, `not`), comparison operators, variables or templates, includes, time or user
     conditions, per-rule priorities (order is the priority) and `allow_with_constraints`. A need for any of them must come with a real use case
     (§151: "intentionally small until actual use cases prove the need").
   - `approver_role` is one of `owner`, `admin`, `developer`, `security` (case-insensitive in the document, stored upper case): the roles that hold
     `payload.read`, because the approver must see what they approve (D4). Absent means "an owner or an admin". How a named role relates to the others is
     decided with the approval endpoints in step 2; this step only validates and stores the name.
   - Capabilities are dotted words (`filesystem.read`, `github.merge`, `tool.<name>`), case sensitive; `prefix.*` matches anything below `prefix.` (at
     least one more segment); there is no bare `*`. Resources are globs (below). Risk classes are the SDK classifier's `R0`-`R4`.
3. **Evaluation: ordered, first match wins, then the default.** `evaluate(policy, intent) -> Decision{decision, rule_id, policy_version, reason}` is a pure
   function of its arguments (no clock, no I/O, no hidden state), so the same policy and intent always give the same decision. Rules are tried in
   listed order; the first whose every condition holds decides; no match applies the policy `default`. An explicit `allow` above a broader `deny` is the
   exception mechanism, and tests pin it. `rule_id` is the decision's `policy_id` in the plan's contract (UI: "Denied by deny-env-read v3").
   - **Case:** capabilities, resources, attribute names and attribute string values are compared case sensitively. An attribute `true` is not `1` and `"1"`
     is not `1`; `1` equals `1.0`.
   - **Reason text is fixed and policy-authored.** It is the matching rule's `description`, else "Matched rule `<id>`.", else one of two constant
     sentences for the default and for "no policy is published". It is never built from the intent. The `policy.reason` event attribute is classified
     metadata (it holds the author's words and no captured content), and tests plant a sentinel in every intent field to show no reason contains it.
     A hostile SDK can still write any text into an attribute of its own events; that is true of every metadata attribute and is why the server, not the
     client, writes the `policy.*` events of a decision (step 2).
   - **Globs, with no regular expressions.** `*` matches any run except `/`; `**` matches any run including `/`; `**/` at the start or after a `/` matches
     zero or more whole directories (`**/.env*` matches `.env` and `a/b/.env.local`); `?` matches one character except `/`; everything else is literal; the
     whole text must match. The matcher compiles the pattern to a small NFA whose live states are one integer, so a match costs one step per character and
     cannot backtrack (ReDoS is impossible by construction; tests include adversarial shapes and compare 3,000 random pattern/text pairs with a regex
     oracle). A text over 4,096 characters never matches, and the validator caps patterns at 256 characters.
4. **The reader.** A document is checked, in order, for size (64 KiB, in bytes, before anything is parsed), control and invisible formatting characters
   (Unicode categories Cc, Cf, Cs, Zl, Zp, which also closes "trojan source" look-alikes in a security document), then read with PyYAML's **event
   stream only**: nothing is ever constructed from the input, so a tag cannot name a class. Anchors, aliases (no expansion attack), tags, merge keys, a
   second document, nesting deeper than 8 and more than 5,000 nodes are refused as soon as seen; duplicate and unknown keys are refused; scalars are typed
   by the module's own rules (`true`/`false`, integers, decimals, `null`), so `no`, `on`, `0777` and `1:30` stay text. At most 200 rules per policy. Every
   problem is `{line, column, code, message}`; messages are fixed sentences that may name the offending key (cut to 64 characters) and never a value, so
   an error cannot echo a pasted secret. All 33 codes are listed in `parser.ISSUE_CODES` and each has a test.
5. **Dependency: PyYAML, already locked.** The five questions: *does the platform provide it?* No (the standard library has no YAML). *Maintained?*
   Yes (6.0.3, widely deployed). *Surface area?* Only `yaml.parse(text, Loader=yaml.SafeLoader)` is called, an event iterator; no `load`, no
   constructors. *Could a small local implementation be clearer?* A YAML subset parser (indentation, flow collections, quoting, block scalars) is a
   few hundred lines of security-relevant code and a worse risk than a mature scanner whose output we restrict; and the document format is the one the
   spec names. *Lock-in?* None: the event stream is the only coupling and the DSL is ours. PyYAML was already in `uv.lock` (a transitive dependency of
   `uvicorn[standard]`); it is now a direct dependency of `apps/api` (`pyyaml>=6.0.2`), so removing uvicorn's extra could not drop it silently. The SDK is
   unaffected (it never reads a policy), so its zero-dependency budget stands. There are no PyYAML type stubs in the lock; `mypy` ignores the missing
   stubs for `yaml` (like `asyncpg`) instead of adding `types-PyYAML`.
6. **Versions are immutable rows (INV-1).** `policy_versions(workspace_id, id, project_id nullable, version, document_text, document_sha256, rule_count,
   created_at, created_by, comment)`, migration 0092. `project_id` null is the workspace-wide base; each scope numbers its own versions 1, 2, 3 and
   the current policy of a scope is its highest version, so there is no mutable pointer to drift. The runtime role may SELECT and INSERT but not UPDATE,
   DELETE or TRUNCATE (like `events` and `audit_log`; a test connects as that role and fails each statement). Partial unique indexes keep versions unique
   per scope and a composite foreign key refuses another workspace's project. **Resolution:** a project's own latest version, else the workspace base,
   else the built-in empty policy (default `allow`, version 0). A project policy **replaces** the base for that project; there is no merging, so what a
   project does is always readable in one document.
7. **Publishing is one transaction.** Validation first (a bad draft costs no transaction and writes no audit row), then, in one transaction, an advisory lock on
   the scope, the next version number, the insert and the `policy.publish` audit row (`details`: scope, version, SHA-256, rule count; never the text or
   the comment, because audit rows are read by more roles than policies are). An optional `base_version` is compared with the current version under the
   lock so two editors cannot overwrite each other (409 `POLICY_VERSION_CONFLICT`, `details.current_version`).
8. **Management API** (details in `docs/architecture/api-v1.md`): `GET /v1/policies/current|versions|versions/{n}|diff` need `policy.read` (owner, admin,
   developer, security); `POST /v1/policies/validate|publish|dry-run` need `policy.write` (owner, admin). No API key scope grants either action: policy
   management is for signed-in people (a cookie write also needs the web app's `Origin`). Unknown and foreign projects are the same 404. Limits per
   workspace and process: 30 publishes, 120 validations and 20 dry runs a minute (`ABB_POLICY_*_PER_MINUTE`), 429 with `Retry-After`.
9. **The dry run** replays a draft over the project's newest 100 runs (at most 20,000 mapped events, a 5 s budget for the evaluation, an 8 s statement
   timeout for the read, in a read-only transaction) and returns counts by decision and by rule plus up to 25 samples of denied or approval-gated actions.
   Events are read through `EventQueries.scan` (attributes only, never a payload) and the loop runs in a worker thread. The mapping from events to
   intents is a documented contract (`policies/mapping.py`) that the integrations of step 3 must use when they call `guard()`:

   | Event | Capability | Resource | Risk class | Attributes |
   | --- | --- | --- | --- | --- |
   | `shell.command.started` | `shell.execute` | the executable name (first word after `NAME=value` and `sudo`/`env`/`nohup`/`time`/`nice`, last path segment) | `shell.risk_class` | `category`, `command` (the exact command, only when it is at most 4,096 characters; Phase 14 review P2-3) |
   | `file.read` | `filesystem.read` | `file.path` | none | none |
   | `file.created`, `.modified`, `.deleted` | `filesystem.write` | `file.path` | none | `operation` |
   | `git.push` | `github.push` | `git.push_target` | none | `branch` |
   | `tool.call.started` | `tool.<name>` | `tool.operation` | none | none |

   A merge has no event type (it appears as `shell.execute`), so there is no `github.merge` mapping yet. A dry run needs read access to content (it
   evaluates commands and paths), so it takes `policy.write`, which only roles that also hold `payload.read` have (a test pins this), and it returns
   no resource, command, path or argument: samples carry run and event ids, capability, risk class, decision and rule id. Measured (2026-10-13, Apple
   silicon laptop, in the test harness): a 10,000-event run with 60 rules, end to end through the API, 0.19 s; parsing a 51 KB, 200-rule document 28 ms;
   a full miss over 200 rules 0.17 ms.
10. **Event registry.** One new typed event, `policy.decision` (security, point, P1; requires `policy.decision`), for allowed and approval-gated
    decisions (a denial stays `policy.action.blocked`). New known attributes: `policy.version` (integer >= 0), `policy.decision` (`allow | deny |
    require_approval`), `approval.state` (`REQUESTED | APPROVED | DENIED | EXPIRED | CANCELLED`). `policy.decision` and `approval.state` join
    `REVIEWED_METADATA` in the fail-closed classification test; the schema, TypeScript types, the web copy of `event-types.json`, an example and
    `docs/architecture/events.md` are regenerated and updated. Additive: no existing event or attribute changes.
11. **Waiting is long polling, not WebSocket (pointer for step 2).** The plan's roadmap wording said "WebSocket/control endpoint". The SDK has a
    near-zero dependency budget (INV-4) and Python has no standard-library WebSocket client, so `guard()` will wait for an approval by long-polling
    `GET /v1/approvals/{id}/wait` in bounded rounds over plain HTTP. Step 2's ADR records the endpoint contract; this is the deviation's pointer.

## Alternatives considered

- **A mutable "current policy" row with history on the side.** Simpler reads, but two sources of truth and a window where the current row and the
  history disagree. The highest version of an append-only table cannot disagree with itself.
- **Merging the base into project policies.** Convenient for small edits, but a reader cannot see what governs a project without evaluating a merge.
  Replacement keeps one document per decision; a project that wants the base copies it.
- **Regular expressions for resources.** Familiar and strictly more powerful, and a ReDoS surface in a security feature. Globs cover the §151 examples.
- **Writing a YAML-subset parser.** Rejected under the five questions (point 5).
- **Policy in JSON.** No comments and harder for humans to review in a diff; the spec's example is YAML.
- **Trusting the document size from the HTTP layer.** The parser checks bytes itself, because the same function reads stored text and a draft.

## Consequences

- Step 2 (decisions and approvals) takes `evaluate` and `compile_cached` as they are, adds the decision endpoint, approvals, `policy.*` and `approval.*`
  events written by the server, the `policy:check` key scope's actual route, and the expiry job. Nothing here changes when it lands.
- Step 3 must call `guard()` with the mapping above; a rule that works in a dry run then works at enforcement.
- Rate limits are per process (KI-141).
- A project policy that is deleted cannot be: a published version is permanent. "Remove" is a new version with an empty rule list.
- Existing tests changed (listed in the step report): the reserved-action list (`policy.write` is now granted), the literal role tables and the route
  registry (seven routes), the runtime-role privilege test (the append-only set now includes `policy_versions`), the migration history, the
  classification test (two keys), `make_settings` (high policy rate limits for the matrix tests).
