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

## Event families

| Class | Types |
| --- | --- |
| run | `run.started` `run.completed` `run.failed` `run.cancelled` |
| agent | `agent.started` `agent.completed` `agent.state_changed` `agent.spawned` |
| llm | `llm.request.started` `.completed` `.failed` (require `llm.provider`, `llm.model`; cost attributes: `cost.provider_usd` = reported by the provider, `cost.estimated_usd` = the caller's estimate, `cost.pricing_version`; the server prices tokens itself, ADR-040) |
| tool | `tool.call.started` `.completed` `.failed` (require `tool.name`) |
| file | `file.read` `file.created` `file.modified` `file.deleted` (require `file.path`) |
| git | `git.diff` `git.commit` `git.branch_created` `git.push` |
| shell | `shell.command.started` `.completed` `.failed` (require `shell.command`) |
| database | `db.query.started` `.completed` `.failed` |
| network | `http.request` `http.response` |
| reliability | `retry.attempted` `timeout.occurred` `loop.detected` `rate_limit.hit` |
| security | `policy.warning` `policy.action.blocked` `secret.detected` |
| approval | `approval.requested` `.granted` `.denied` (require `approval.id`) |
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
is never used. `sequence` is comparable only within one emitter; multi-process agents are a Phase 9
concern.

## Delivery and idempotency

At-least-once submission, idempotent persistence on `(workspace_id, event_id)`, first write wins.
`dedup.content_hash` (SHA-256 of the canonical JSON excluding `received_at` and tenant ids) separates
a harmless retry from the same id with different content, which the server counts as a conflict and
does not store.

## Errors

`EventValidationError(code, message, issues)`. Codes: `EVENT_INVALID`, `EVENT_TOO_LARGE`,
`EVENT_SCHEMA_UNSUPPORTED`. Each issue is `{loc, code, message}`. Messages never contain submitted
values.

## Evolution rules (spec §64.5)

Adding an optional field, attribute key or event type is backward compatible. Renames need a
deprecation window; removal or a changed meaning needs a major version. Use the `add-event-type` skill.
