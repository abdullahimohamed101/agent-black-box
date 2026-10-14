# Canonical Event Contract (schema version 1.0)

Reference for `packages/event-schema`. Normative sources, in order: the Pydantic models
(`event.py`, `registry.py`), the generated JSON Schema (`packages/event-schema/schemas/1.0/`), then
this document. Spec: §15, §64-66; decisions: ADR-001, ADR-006, ADR-007, ADR-011.

## Two models

| Model | Who produces it | Tenant fields | Server fields |
| --- | --- | --- | --- |
| `EventIn` | an SDK or adapter | `workspace_id`, `project_id` optional; if present they must match the API key | none |
| `Event` | the server after authentication | required | `received_at` |

`finalize(event_in, workspace_id, project_id, received_at)` converts one to the other and rejects
(never silently overwrites) a client that names a different tenant.

## Envelope

| Field | Type | Notes |
| --- | --- | --- |
| `schema_version` | `"MAJOR.MINOR"` | required; any `1.x` accepted, other majors -> `EVENT_SCHEMA_UNSUPPORTED` |
| `event_id` | `evt_` + ULID | required; globally unique; the idempotency key with `workspace_id` |
| `run_id`, `trace_id` | `run_`/`trc_` + ULID | required |
| `span_id`, `parent_span_id` | `spn_` + ULID | optional; lifecycle events require `span_id`; a parent requires a span and cannot be itself |
| `agent_id` | slug `[a-z0-9][a-z0-9._-]{0,63}` | required; the server resolves it to an agent row |
| `agent_version` | string <= 128 | optional fingerprint (§63.3) |
| `event_type` | dot-delimited, 2-4 lowercase segments, <= 64 chars | required |
| `occurred_at` | RFC 3339 with offset | required; naive timestamps rejected; normalised to UTC |
| `sequence` | integer 0..2^53-1 | optional logical clock per emitter |
| `status` | `success\|error\|timeout\|cancelled\|blocked` | optional; set on completion-style events |
| `duration_ms` | number >= 0 | optional |
| `attributes` | flat map | see below |
| `payload` | small JSON object, <= 64 KB | optional inline content (FULL mode before artifacts exist) |
| `payload_ref` | `artifact://...` | optional pointer to a large payload (Phase 6) |
| `tags` | up to 16 strings <= 64 chars | optional |
| `sdk` | `{name, version}` | optional |

Unknown top-level fields are ignored by validation (forward compatibility). Whole event <= 256 KB.
IDs are canonical uppercase Crockford-base32 ULIDs with a kind prefix; they convert losslessly to
UUID for PostgreSQL (`ids.to_uuid`). No string anywhere may contain U+0000 (PostgreSQL cannot store it).

## Attributes

Flat, namespaced keys (`^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$`, <= 128 chars). Values: string (<= 4096
chars), integer (within +/-2^53-1), finite number, boolean, or a flat list (<= 64) of those. At most
64 attributes. Unknown keys are preserved untouched. Keys listed in the registry
(`registry.KNOWN_ATTRIBUTES`, e.g. `llm.input_tokens`, `shell.exit_code`, `tool.name`) must have the
declared type and minimum wherever they appear. Large, sensitive or unbounded data belongs in
`payload`/`payload_ref`, not attributes (§64.4).

### Coding-agent attributes (Phase 6, ADR-030, ADR-031)

All optional and additive (schema 1.0 is unchanged). Content is never an attribute: diffs and terminal output are artifacts, referenced as `artifact://<art_id>`.

| Event | Attributes |
| --- | --- |
| `file.read` `.created` `.modified` `.deleted` | `file.path` (required), `file.operation`, `file.language`, `file.size_before/after`, `file.hash_before/after` (`sha256:<hex>`), `file.lines_added/removed`, `diff.artifact`, `diff.withheld` (`sensitive_path\|too_large`: why no diff was uploaded) |
| `git.diff` `.commit` `.branch_created` `.push` | `git.repo`, `git.branch`, `git.base_commit`, `git.head_commit`, `git.commit_hash`, `git.changed_files`, `git.diff_stat_files`, `git.push_target`, `git.pr_number`, `diff.artifact` |
| `shell.command.*` | `shell.command` (required), `shell.cwd`, `shell.exit_code`, `shell.duration_ms`, `shell.risk_class` (`R0`..`R4`), `shell.category` (`READ_ONLY\|MODIFY_FILES\|NETWORK\|PACKAGE_INSTALL\|PROCESS_CONTROL\|DESTRUCTIVE`), `shell.stdout_artifact`, `shell.stderr_artifact`, `shell.stdout_bytes`, `shell.stderr_bytes`, `shell.output_truncated`, `shell.output_withheld` (`may_reach_secrets`: the command could print a secret file; output not stored) |
| test runs (closing `shell.command.*` event) | `test.framework`, `test.suite`, `test.total`, `test.passed`, `test.failed`, `test.skipped`, `test.failing` (identifiers) |

A non-zero exit closes the span with `shell.command.failed` and `status=error`. The environment of a command is never recorded.

### Server redaction markers (Phase 13, ADR-130)

Written by the server when it redacts or strips an event at ingestion, before the event is accepted. Metadata, never content: every role that
can read the run receives them unchanged.

| Attribute | Type | Meaning |
| --- | --- | --- |
| `redaction.server_count` | integer | secret-shaped values the server found in this event (replaced in place, or inside a payload that was then dropped) |
| `redaction.server_kinds` | list of strings | sorted kind names (`aws_access_key`, `private_key`, `github_token` ...), never values |
| `redaction.dropped_reason` | string | `mode_metadata_only`, `mode_disabled`, `scan_failed` or `too_large`; absent unless something was dropped. When several apply: `scan_failed` > `too_large` > `mode_disabled` > `mode_metadata_only` |

Client-supplied values for these keys are discarded. Capture modes (per project, enforced on the server, the SDK's names): `FULL` stores
payloads after redaction; `METADATA_ONLY` (the default) drops `payload`, `payload_ref` and the artifact references `diff.artifact`,
`shell.stdout_artifact`, `shell.stderr_artifact` (other attributes stay, as in the SDK); `DISABLED` also drops `error.message` and `metadata.*`.
Secret-shaped text becomes `[REDACTED:<label>]`. A value that cannot be scanned is dropped (a required attribute becomes `[dropped]`).
Events already stored are never rewritten.

### Policy decision attributes (Phase 14, ADR-140)

Labels the policy service writes on `policy.decision` (an allowed or approval-gated decision), `policy.action.blocked` (a denial) and the `approval.*`
events. Metadata for every role: closed vocabularies, ids and the policy author's own words, never anything the agent did.

| Attribute | Type | Meaning |
| --- | --- | --- |
| `policy.id` | string | the id of the rule that decided (empty when the policy default applied) |
| `policy.version` | integer >= 0 | the stored version of the policy that decided; 0 when no policy was published |
| `policy.decision` | string | `allow`, `deny` or `require_approval` (required on `policy.decision`) |
| `policy.capability` | string | the capability that was asked about, such as `filesystem.read` or `tool.github` |
| `policy.reason` | string | fixed text: the matching rule's description, `Matched rule <id>.`, or one of two constant sentences; never built from the action |
| `approval.id`, `approval.capability` | string | the approval request (`apr_...`) and the capability it concerns |
| `policy.warnings_dropped` | integer | with `policy.reason = policy_warnings_dropped`: how many `policy.warning` events the SDK lost to its bounded spool during an outage |
| `approval.state` | string | `REQUESTED`, `APPROVED`, `DENIED`, `EXPIRED` or `CANCELLED` |

**Who writes them.** `policy.decision`, `policy.action.blocked` and every `approval.*` event of a decision are written by the **server** (ADR-141), in the
transaction of the state change they record, with `sdk.name = "abb-policy-service"` (a name ingestion refuses from clients) and no `sequence` (the server is a
second emitter of the run, ADR-070, so such a run is ordered by time). Their ids are derived from the approval and the transition, so a redelivery is an
idempotent duplicate and each transition has one event. Ingestion rejects these types from clients, per event, with `RESERVED_EVENT_TYPE` (ADR-141 Amendment 1); `policy.warning` is still client-writable. `approval.consumed` is the approval being spent for exactly one action; `approval.expired` and `approval.cancelled` end a request
by time or by the agent's withdrawal (a run leaves "waiting for approval" on any of `granted`, `denied`, `expired` or `cancelled`).

## Event families

| Class | Types |
| --- | --- |
| run | `run.started` `run.completed` `run.failed` `run.cancelled` |
| agent | `agent.started` `agent.completed` `agent.state_changed` `agent.spawned` |
| llm | `llm.request.started` `.completed` `.failed` (require `llm.provider`, `llm.model`; cost attributes: `cost.provider_usd` = reported by the provider, `cost.estimated_usd` = the caller's estimate, `cost.pricing_version`; the server prices tokens itself, ADR-040; optional billing modifiers `llm.cache_write_tokens`, `llm.cache_write_1h_tokens`, `llm.service_tier` (`standard\|batch\|flex\|fast\|ultrafast`), `llm.data_residency` (`global`, `us`, `eu` ...): absent means standard processing, ADR-044) |
| tool | `tool.call.started` `.completed` `.failed` (require `tool.name`) |
| file | `file.read` `file.created` `file.modified` `file.deleted` (require `file.path`) |
| git | `git.diff` `git.commit` `git.branch_created` `git.push` |
| shell | `shell.command.started` `.completed` `.failed` (require `shell.command`) |
| database | `db.query.started` `.completed` `.failed` |
| network | `http.request` `http.response` |
| reliability | `retry.attempted` `timeout.occurred` `loop.detected` `rate_limit.hit` |
| security | `policy.warning` `policy.action.blocked` `policy.decision` (requires `policy.decision`) `secret.detected` |
| approval | `approval.requested` `.granted` `.denied` `.consumed` `.expired` `.cancelled` (require `approval.id`; the last three are Phase 14 step 2, ADR-141) |
| span | `span.started` `.completed` `.failed` for custom spans (`span.name`, `span.kind`) |
| custom | `custom.<name>`: anything an application wants to record |

The full machine-readable list, with required attributes and SDK drop priority, is
`schemas/1.0/event-types.json`. A well-formed name that is not registered is accepted as a
custom-class event.

## Spans

Spans are derived, not sent. A `*.started` event opens a span for its `span_id`, `*.completed`/
`*.failed` close it, point events attach to it. `spans.derive_spans` builds them from whatever
has arrived; a late parent, an open span or a close before its open are normal. Only contradictions
are reported by `spans.check_span_relationships`: two parents, a cycle, one span id in two traces.

## Ordering

`ordering.sort_events`: if every event has a `sequence`, order by `(sequence, occurred_at,
received_at, event_id)`; otherwise by `(occurred_at, sequence, received_at, event_id)`. Arrival order
is never used. `sequence` is comparable only within one emitter (see "Propagation" for multi-process runs).

## Agents and delegation (Phase 9, ADR-070)

One run, one trace, many agents (spec §84). A child agent is an `agent.started` ... `agent.completed` span with its own `agent_id` (and
optionally `agent_version`) inside the parent's run; every event emitted inside it carries the child's `agent_id`. The run's own
`agent_id` stays the root agent's. Delegation is recorded by both sides:

| Who | Event | Identity and shape |
| --- | --- | --- |
| delegator | `agent.spawned` (point) | the delegator's `agent_id`; in the delegating span when there is one, else at run level with no `span_id`; `agent.child_id` = the child's slug (required); the optional delegation input is the payload `{"input": ...}` |
| child | `agent.started` (opens a span) | the child's `agent_id`; `parent_span_id` = the delegating span when there is one, else a root span |
| child | `agent.completed` (closes it) | `status`; the optional result is the payload `{"result": ...}` |

A child that never starts is still visible as a spawn without a start. Payloads are gated by the payload mode and redacted like every other
payload. A child slug that would be empty (a name with no slug characters) or equal to the delegator's becomes `<delegator>-child-<n>`.
There is no `agent.failed` type: a failed agent closes with `agent.completed` and `status: "error"`. `agent.child_id` is a label (any string,
up to 4,096 characters); readers render it as text and truncate it.

## Propagation (Phase 9, ADR-070)

In one process the SDK follows contextvars (asyncio tasks inherit; threads use `blackbox.bind`). Across processes a serializable trace
context carries the address of the delegating span: `abb1;run=run_<ULID>;trace=trc_<ULID>;span=spn_<ULID>;agent=<slug>` (`span` and `agent`
optional, at most 256 characters; every id matches the contract patterns). Conventions: the environment variable `BLACKBOX_TRACE_CONTEXT`, the
HTTP header name `X-Blackbox-Trace-Context` (the API never reads it). The string is an address, not a credential: the child still needs an API key
of the same project, and the server rejects events whose run belongs to another project (`RUN_PROJECT_MISMATCH`). W3C `traceparent` cannot carry
a 128-bit span id; mapping it belongs to the OpenTelemetry adapter (ADR-007, KI-086).

A continued run emits no `run.*` events (the root owns the run) and parents its root spans to the context's span. **Ordering consequence (D4):**
a continued run, and a forked child that kept using its parent's `Run`, omit `sequence`. A run with any event lacking `sequence` is ordered by
`(occurred_at, sequence, received_at, event_id)` (`ordering_mode: time`), so under clock skew the chronological list interleaves emitters by
their own clocks and the earliest `occurred_at` sets the run's `started_at` and the page's `t0` (KI-085). Structure never depends on time: spans
nest by `parent_span_id` only, so a skewed child still nests under its parent (§65.3). The run's root agent and `trace_id` come from the first
`run.started` in canonical order (fallback: the first event for runs created by `POST /v1/runs`), so a skewed or forged child event cannot become
the run's agent or trace.

## Delivery and idempotency

At-least-once submission, idempotent persistence on `(workspace_id, event_id)`, first write wins.
`dedup.content_hash` (SHA-256 of the canonical JSON excluding `received_at` and tenant ids) separates
a harmless retry from the same id with different content, which the server counts as a conflict and
does not store. Since Phase 13 the hash is of the event *after* server redaction, so a redelivery after the project's capture mode changed is
a conflict (the first accepted version is kept).

## Errors

`EventValidationError(code, message, issues)`. Codes: `EVENT_INVALID`, `EVENT_TOO_LARGE`,
`EVENT_SCHEMA_UNSUPPORTED`. Each issue is `{loc, code, message}`. Messages never contain submitted
values.

## Evolution rules (spec §64.5)

Adding an optional field, attribute key or event type is backward compatible. Renames need a
deprecation window; removal or a changed meaning needs a major version. Use the `add-event-type` skill.
