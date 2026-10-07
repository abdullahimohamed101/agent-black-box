# Phase 1 - Canonical Telemetry Contract

Status: Planned (not started); branch `feature/phase-1-event-contract` from `main` @ 93d1f80
Owner: coding agent
Depends on: Phase 0 (merged, CI green)
Spec: §7, §15, §16, §57.6, §62-66, §67.4 (priority classes), §101.2/§130 (error taxonomy), §141, §150, §153; ADR-001, ADR-006, ADR-007 (written this phase)

## Outcome

`packages/event-schema` is the stable spine: a dependency-light Python package that defines the
canonical event envelope, ID scheme, event families, attribute rules, versioning, size limits,
ordering and dedup semantics, and emits a versioned JSON Schema. A canonical event can be built,
serialized, validated and ordered with no framework, database or web code involved. Everything
later (API, SDK, adapters, UI) consumes this package.

## Non-Goals

- No database tables, no ingestion endpoint, no API dependency on the package (Phase 2 wires it;
  the API Docker build context changes then).
- No SDK, no redaction pipeline (Phase 3), no artifacts (Phase 6), no OpenTelemetry mapper code
  (ADR-007 only fixes where it will live and how names align).
- No multi-emitter ordering (Phase 9 revisits; see risk R1).
- No TypeScript SDK; only generated TS types for the web app.

## Current Architecture

Phase 0 skeleton only. `apps/api` has typed errors (`ErrorCategory`, error envelope) that Phase 2
will map schema errors onto. No `packages/` directory exists.

## Decisions (confirm before implementation; contestable ones become ADRs)

- **D1 Pydantic v2 models are the source of truth**; JSON Schema is generated from them and
  committed (`schemas/1.0/`), with a CI check that regeneration produces no diff. Package depends
  only on `pydantic`. *Open risk:* the Python SDK has a near-zero dependency budget; pydantic may be
  too heavy for it. Phase 3 decides (likely a stdlib builder validated by contract tests against the
  JSON Schema). Nothing in Phase 1 blocks either choice.
- **D2 IDs: prefixed ULIDs** (`evt_`, `run_`, `trc_`, `spn_`, `ws_`, `prj_`, `agt_`, `art_`,
  `eval_`, `pol_`, `apr_`), 26-char Crockford base32, generated locally (stdlib), sortable by time,
  client-generatable offline (§64.2). The database stores them as UUID (a ULID is 128 bits, lossless
  conversion helpers provided), keeping the §149 sketch valid. (ADR-001)
- **D3 Two models**: `EventIn` (what clients send: `workspace_id`/`project_id` optional because an
  SDK only knows its API key; if present they must match the key) and `Event` (canonical, stored:
  both required, plus server `received_at`). (ADR-001)
- **D4 `agent_id` is a slug** (`[a-z0-9][a-z0-9._-]{0,63}`, e.g. `coding-agent`), not an `agt_` ID:
  SDK users name agents; the server resolves slug to an agent row. §15 and §64.1 disagree; slug wins.
- **D5 Event names are dot-delimited** (`tool.call.completed`), 2-4 lowercase segments (ADR-011).
  Well-formed unknown types are accepted (forward compatibility) and flagged `is_known=False`;
  malformed names are rejected. Custom SDK events use the `custom.` family.
- **D6 Spans are derived from lifecycle events.** `*.started` with a new `span_id` opens a span,
  `*.completed`/`*.failed` with the same `span_id` closes it; point events attach to a span. A generic
  `span.started/completed/failed` family (attributes `span.name`, `span.kind`) covers custom spans.
  `SpanKind` enum from §63.5. (ADR-001)
- **D7 Attributes**: flat, namespaced keys (`^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$`), values are JSON
  scalars or flat lists of scalars; no nested objects. Unknown keys are preserved. Known event types
  declare required attributes and value types (INV-6). Limits: 64 attributes, key <=128 chars,
  string value <=4096 chars, integers within +/-(2^53-1) (JS-safe for the TS SDK).
- **D8 Payload**: optional small inline `payload` (object, <=64 KB serialized) for FULL-mode content
  before artifacts exist (Phase 6), plus `payload_ref` (`artifact://...`) for large data. Whole
  event <=256 KB (§71.4). (ADR-001)
- **D9 No NUL characters in any string**: PostgreSQL `text`/`jsonb` cannot store `\u0000`, so letting
  one through would turn into a 500 at ingestion time. Rejected at validation.
- **D10 Versioning**: `schema_version` is `MAJOR.MINOR`; this package emits `1.0`. Any `1.x` is
  accepted (minors only add optional fields, §64.5); other majors are rejected with
  `EVENT_SCHEMA_UNSUPPORTED`. Unknown top-level fields are ignored by validation (not persisted by
  the typed model); unknown attribute keys are preserved.
- **D11 Ordering** exactly per §65.1: `sequence` (events with a sequence first), then `occurred_at`,
  `received_at`, `event_id`. Pure function `event_sort_key`. Timestamps must be timezone-aware and
  are normalized to UTC; naive timestamps are rejected.
- **D12 Dedup semantics** (ADR-006): identity is `(workspace_id, event_id)`. First write wins
  (INV-1). `canonical_hash(event)` (SHA-256 of canonical JSON, excluding `received_at`) lets the
  server distinguish a harmless retry from the same ID with different content (counted as
  `duplicate_conflict`, original kept).
- **D13 Errors never echo input values** (they may be secrets). `EventValidationError` carries a
  stable `code` (`EVENT_INVALID`, `EVENT_TOO_LARGE`, `EVENT_SCHEMA_UNSUPPORTED`) and a list of
  `{loc, code, message}` issues.
- **D14 Priority classes** (§67.4) are attached to registry entries (P0 lifecycle/errors/policy/
  approvals; P1 llm/tool/file/shell; P2 verbose) so the SDK can drop low priority first.
- **D15 Package layout**: `packages/event-schema` is its own uv project (`abb-event-schema`, import
  `abb_event_schema`, src layout), own venv, included in `scripts/quality.sh`, the Makefile and CI.

## Proposed Design

```text
packages/event-schema/
  pyproject.toml  README.md
  src/abb_event_schema/
    ids.py          ULID, prefixes, parse/validate, UUID conversion
    enums.py        EventStatus, SpanKind, RunStatus (+ allowed transitions), EventClass, Priority
    limits.py       size and count limits (single source of numbers)
    versioning.py   SCHEMA_VERSION, accept/reject rules
    registry.py     known event types: class, priority, span role, required/typed attributes
    event.py        EventIn, Event, field validators, attribute rules
    errors.py       EventValidationError, issue conversion without echoing values
    parse.py        parse_event(raw) -> Event/EventIn with size and version checks
    ordering.py     event_sort_key, sort_events
    dedup.py        canonical_json, canonical_hash
    spans.py        Span model, derive_spans(events), check_span_relationships(events)
    jsonschema.py   export of schemas + registry
  schemas/1.0/      event.json, event-in.json, event-types.json   (generated, committed)
  ts/               generated TypeScript types (generated, committed)
  examples/         valid/*.json (one per registered type), invalid/*.json (+ expected code)
  tests/
docs/architecture/events.md        reference for humans
docs/decisions/ADR-001, ADR-006, ADR-007
```

Registry (dot names; grouped by family, mapped from §15): `run.{started,completed,failed,cancelled}`,
`agent.{started,completed,state_changed,spawned}`, `llm.request.{started,completed,failed}`,
`tool.call.{started,completed,failed}`, `file.{read,created,modified,deleted}`,
`git.{diff,commit,branch_created,push}`, `shell.command.{started,completed,failed}`,
`db.query.{started,completed,failed}`, `http.{request,response}`,
`retry.attempted`, `timeout.occurred`, `loop.detected`, `rate_limit.hit`,
`policy.{warning}`, `policy.action.blocked`, `secret.detected`,
`approval.{requested,granted,denied}`, `span.{started,completed,failed}`, plus `custom.*`.

## Affected Files

New: everything under `packages/event-schema/`, `docs/architecture/events.md`, three ADRs.
Changed: `Makefile`, `scripts/quality.sh`, `.github/workflows/ci.yml` (package gate and schema
check), `docs/DECISIONS.md`, `docs/TESTING.md`, `README.md`, `ARCHITECTURE.md`, `.gitignore`.
No change to `apps/*`.

## Acceptance Criteria (commands)

1. `cd packages/event-schema && uv run pytest` passes, with tests for: valid events (every registered
   type via `examples/valid`), malformed events (wrong types, bad IDs, bad names, naive timestamps,
   NUL bytes, nested attributes, oversize), missing required fields (envelope and per-type attributes),
   unknown attributes preserved, unknown top-level fields ignored, schema versions (1.0, 1.7 accepted;
   2.0, garbage rejected with `EVENT_SCHEMA_UNSUPPORTED`), parent/child relationships (self-parent,
   cycles, conflicting parents, late-arriving parent not an error), duplicate event IDs (same content
   vs different content hash), out-of-order sequences (shuffled input sorts identically; seeded random).
2. `make schema-check` regenerates `schemas/1.0/*` and `ts/` and `git diff --exit-code` is clean;
   `invalid/` examples fail with the expected code.
3. Independence: a test imports the package in a subprocess and asserts none of `fastapi`,
   `sqlalchemy`, `asyncpg`, `langgraph`, `openai`, `anthropic` are in `sys.modules`; the package's
   only runtime dependency is `pydantic` (checked from package metadata).
4. A documented example (README snippet) that builds, serializes, validates and sorts an event runs
   as a test with no framework imports.
5. `ruff`, `mypy --strict` clean for the package; `scripts/quality.sh full` includes it and passes;
   CI green on the PR.
6. ADR-001, ADR-006, ADR-007 exist and `docs/DECISIONS.md` index is updated; `docs/architecture/events.md`
   documents the envelope, families, attribute rules, ordering semantics and limits.
7. Error output never echoes submitted values (test with a secret-looking value).

## Verification Plan

Run each command from a fresh state; review the generated JSON Schema by eye against §64.1; run the
generated schema through an independent validator (`jsonschema` library, dev dependency) against
every valid/invalid example so the schema and the Pydantic models agree; `verify-change` and
`review-change` passes before completion; CI run linked in the completion report.

## Risks

- **R1 Multi-emitter ordering**: per-run `sequence` collides when child agents run in other processes.
  Mitigation: adding an optional `emitter_id` later is backward compatible; revisit in Phase 9 with an ADR.
- **R2 Contract churn**: this is the most expensive thing to change; hence examples, registry coverage
  tests and golden schema files.
- **R3 pydantic in the SDK** (D1) - decided in Phase 3.
- **R4 Strictness drift** between Pydantic and JSON Schema: cross-validated in the verification plan.
- **R5 Attribute-name alignment with OpenTelemetry gen_ai conventions** (unstable): names follow the
  spec (`llm.*`); mapping lives behind the adapter (ADR-007).

## Ordered Steps (one commit each)

1. [ ] Scaffold `packages/event-schema` (pyproject, tooling, empty package, one test), wire Makefile,
   `quality.sh`, CI, `.gitignore`.
2. [ ] `ids.py`, `limits.py`, `versioning.py`, `enums.py` with tests.
3. [ ] `registry.py`, `event.py`, `errors.py`, `parse.py` with tests (valid/malformed/missing/unknown/version).
4. [ ] `ordering.py`, `dedup.py`, `spans.py` with tests.
5. [ ] Examples, JSON Schema + TS generation, `make schema`/`schema-check`, cross-validation tests, independence test.
6. [ ] Docs: `events.md`, ADR-001/006/007, DECISIONS, TESTING, README, ARCHITECTURE.
7. [ ] `verify-change`, `review-change`, `harden-change`, `complete-phase` (move plan to completed, update state).
