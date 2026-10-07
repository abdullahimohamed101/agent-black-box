# Phase 3 - Python SDK (and KI-020: database roles)

Status: Completed on branch `feature/phase-3-python-sdk` (not pushed; PR needs user approval). Built in parallel with Phase 4; `docs/PROJECT_STATE.md` and `docs/IMPLEMENTATION_PLAN.md` are reconciled by the coordinator at merge.
Owner: coding agent
Depends on: Phase 2 (ingestion API merged to `main`)
Spec: §67 (buffering, priorities, redaction pipeline), §68 (public API), §70 (modes), §71 (ingestion), §156 (overhead target); INV-4, INV-5; ADR-006, ADR-010 (realised here), ADR-013 (new)

## Outcome

A handful of lines trace an agent through the real API, and telemetry can never harm the agent: no exception escapes, memory and retries are
bounded, the application thread never does I/O. Also (pulled forward from KI-020): the API's runtime database role cannot rewrite history.

```python
bb = BlackBox(api_key=..., project="demo")
with bb.run("fix bug") as run:
    with run.span("search", kind="tool") as s:
        s.set_attribute("tool.result_count", 3)
bb.shutdown()
```

## Non-Goals

TypeScript SDK (Phase 17), framework adapters / auto-instrumentation (Phase 17, `integrations/`), artifact upload / `payload_ref` (Phase 6), pricing
(Phase 7: the SDK only records usage and an optional caller-supplied `cost.estimated_usd`), OpenTelemetry export, multi-process run propagation, async HTTP
transport, PyPI publishing (needs approval), row-level security (Phase 19).

## Known issues considered

- **KI-020 (S1)**: pulled in, step 2. Acceptance A1-A3.
- KI-018 / KI-019 (S1, Phase 19): stay deferred; the SDK's bounded queue does not affect them.
- KI-016, KI-014/015: not touched.

## Decisions

- D1 (ADR-013): stdlib only, no pydantic/httpx; contract tests against `abb_event_schema` (dev dependency) prevent drift.
- D2 (ADR-013): retry policy honours `Retry-After` on 429/503, splits on 413, drops non-retryable 4xx with counters.
- D3 (ADR-013): batch endpoint with stable `batch_id`, gzip > 1 KiB, <= 100 events (config, max 1000), <= 4 MiB.
- D4 Import name `blackbox`, distribution `agent-black-box`, path `packages/sdk-python` (spec §68). Version 0.1.0.
- D5 Redaction (ADR-010 realised): `Redactor` pipeline in the spec's order: key deny/allow rules -> secret-pattern detector -> user callback -> payload mode.
  Default payload mode is `METADATA_ONLY` (INV-5: payload capture is opt-in); `FULL` and `DISABLED` selectable. Redaction is deterministic
  (`[REDACTED:<kind>]`), applied to attributes, payload and tags before serialization; a failing user callback drops the payload (fail closed), counted.
- D6 Modes: `enabled` (HTTP), `local` (append JSON lines to a file), `offline` (queue only, nothing sent, bounded; tests/air-gap), `disabled` (every call a no-op).
  A missing API key in HTTP mode downgrades to `disabled` with one warning (never raises).
- D7 Sequence numbers are allocated per run, in call order, under a lock; ids come from a process-wide monotonic ULID generator. Defaults: batch 100, flush 250 ms,
  queue 10,000, HTTP timeout 2 s (spec §67.3).
- D8 Priority drop policy: P0 is never dropped intentionally (admitted until a hard cap of 2x the queue bound, then dropped and counted as `dropped_p0`); when full, an arriving
  event evicts the oldest queued event of strictly lower priority (P2 before P1), else is dropped itself. Priorities come from the event type (spec §67.4), mapped in the SDK.
- D9 DB roles (KI-020): migration `0007` creates `abb_runtime` (NOLOGIN until given a password) with DML on every table except `events` (SELECT, INSERT only), plus default
  privileges for future tables; Alembic reads `ABB_MIGRATION_DATABASE_URL` (falls back to `ABB_DATABASE_URL`) and sets the runtime role's password from
  `ABB_RUNTIME_DB_PASSWORD` on every `upgrade`. Compose `migrate` uses the owner; `api`/`worker` use `abb_runtime`.

## Proposed design (SDK)

```text
packages/sdk-python/src/blackbox/
  __init__.py   public API: BlackBox, Run, Span, PayloadMode, ...; version
  client.py     BlackBox (modes, lifecycle, atexit), Run, Span, LlmCall, observe
  context.py    contextvars: current run/span
  events.py     envelope builder + priority map + limits (stdlib)
  ids.py        ULID generator
  redaction.py  Redactor pipeline, secret patterns, payload modes
  buffer.py     bounded priority buffer, counters
  exporter.py   background thread: batching, gzip, retry policy, HTTP / local-file / offline sinks
  config.py     Config + defaults
```

Hot path (application thread): build dict -> redact -> `buffer.put` (lock + deque). The exporter thread owns all I/O. Every public method wraps its body so an internal
error is counted and swallowed (INV-4); user exceptions inside `with` blocks always propagate unchanged and mark the span `failed`.

## Affected files

`apps/api/migrations/versions/0007_runtime_role.py`, `apps/api/migrations/env.py`, `docker-compose.yml`, `infrastructure/docker/`, `.env.example`, `apps/api/tests/`,
`docs/SECURITY.md`, `docs/development/setup.md`, `docs/KNOWN_ISSUES.md`; `packages/sdk-python/**`, `examples/`, `scripts/quality.sh`, `Makefile`, `.github/workflows/ci.yml`, ADR-013.
Not touched: `apps/web`, `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md` (coordinator reconciles).

## Acceptance criteria

KI-020
- A1 Migration 0007 up/down/up works; as `abb_runtime`, UPDATE/DELETE/TRUNCATE on `events` fail with insufficient privilege, INSERT/SELECT work, DML works on other tables, DDL fails.
- A2 The ingestion + worker + query flow passes with the application connected as `abb_runtime` (tests run the API against the runtime role).
- A3 `docker compose --profile app` clean start (separate project name): api/worker connect as `abb_runtime`; `scripts/smoke.sh` passes.

SDK
- B1 Unit tests: sync, async, nested spans, parent/child ids, sequence allocation, attributes; errors mark spans failed and re-raise.
- B2 Context propagation across asyncio tasks and threads; no leakage between concurrent runs.
- B3 Contract tests (ADR-013): all emitted event types validate via `EventIn` and the JSON Schema; ids, priority map and limits match the contract.
- B4 Redaction: allow/deny, secret patterns, callback, payload modes, determinism, callback failure fails closed.
- B5 Buffer: bounded; priority eviction order; P0 hard cap; counters; thread-safe under concurrent producers.
- B6 Exporter against a stub HTTP server: batching by size/time, gzip, `batch_id` stable across retries, bounded backoff, `Retry-After` honoured, 413 split, non-retryable 4xx dropped, partial rejection counted.
- B7 Backend offline/hanging/garbage: the agent continues, nothing raises, queue stays bounded, `shutdown(timeout)` returns on time.
- B8 `flush()`/`shutdown()` idempotent; atexit flush; disabled / local / offline modes.
- B9 Benchmark recorded: application-thread cost per event (target < 1 ms typical) in `docs/benchmarks/phase-3-sdk.md`.
- B10 Example script (< 10 lines of SDK use) produces a full trace through the real API; `GET /v1/runs/{id}` and `/events` show correct order and status.
- B11 `scripts/quality.sh full` exit 0 with the SDK in the gate; mutation test of committed SDK core logic.

## Verification plan

Focused pytest per module; `scripts/quality.sh full`; temp-database fixtures only (shared Postgres :5433 is used by another agent; never `down -v`); A3 uses a distinct
compose project name and ports; benchmark run on this machine; example run against a throwaway API process.

## Risks

- Role change breaks a code path that did UPDATE on `events` (mitigated: A2 and the source-scan test). Existing deployments must set `ABB_RUNTIME_DB_PASSWORD` and switch URLs (documented).
- A second ULID/envelope implementation drifts (mitigated: B3 in the gate).
- The daemon exporter thread loses queued events if the process is killed (documented; `local` mode for durability).
- Fork safety: the exporter thread does not survive `os.fork`; the SDK restarts it lazily in the child.
- Secret detection is best-effort (documented, not a guarantee).

## Ordered steps (one commit each)

1. Plan + ADR-013.
2. KI-020: migration 0007, env.py, compose/init/.env.example, tests, docs, KNOWN_ISSUES.
3. SDK scaffold, ids, events builder, config, contract tests.
4. Redaction.
5. Buffer + priorities.
6. Exporter (HTTP, retry, local, offline).
7. Client API, context, observe, lifecycle.
8. Example, benchmark, CI/quality/Makefile wiring.
9. Review + hardening; mutation test; completion evidence and plan status.

## Evidence (2026-10-07; VERIFIED = command run here, output observed)

| Criterion | Status | Command / result |
| --- | --- | --- |
| A1 migration + privileges | VERIFIED | `cd apps/api && uv run pytest -q tests/test_runtime_role.py tests/test_migrations.py`: 11 role tests (connects as `abb_runtime`: UPDATE/DELETE/TRUNCATE/DROP/ALTER events, CREATE TABLE, `alembic_version`, `ALTER ROLE ... SUPERUSER` all `permission denied`; INSERT/SELECT and DML on other tables work; every non-events table fully writable) and per-revision up/down/up for 0007 pass |
| A2 flow as runtime role | VERIFIED | the `api` fixture now runs the app and worker as `abb_runtime`; the whole API suite (342 tests) passes |
| A3 containers | VERIFIED | separate compose project `abb-p3` (ports 5533/8100, own image tag): `migrate` exit 0, api/worker healthy, `SELECT current_user` = `abb_runtime`, `DELETE FROM events` = `permission denied for table events`, `scripts/smoke.sh`: SMOKE PASSED. Project and volume removed afterwards |
| B1-B8 | VERIFIED | `cd packages/sdk-python && uv run pytest -q --cov`: 106 passed, coverage 94% (gate 90%), Python 3.10.22 (the minimum supported) |
| B3 contract | VERIFIED | `tests/test_contract.py`: all emitted types validate through `EventIn` and the JSON Schema; limits, priority map, id patterns, event-type regex equal the contract's |
| B9 benchmark | VERIFIED | `docs/benchmarks/phase-3-sdk.md`: 7-23 us mean per operation, p99 13-40 us, target 1 ms |
| B10 example through the real API | VERIFIED | `scripts/sdk-e2e.sh` against the container stack: SDK example, 6 events, status SUCCESS, `summary_state` current, ordered events, 2 spans: SDK E2E PASSED |
| B11 gate | VERIFIED | `scripts/quality.sh full` exit 0 (event-schema 337, sdk 106, api 342, web 7) |
| Mutation test | VERIFIED | hand-rolled mutants over committed SDK logic (buffer eviction, retry/backoff/Retry-After/413 split, redaction rules, status mapping, bounds): first pass 11 of 44 survived; added tests for each real gap; final 1 of 44 survives and is behaviourally equivalent (`shutdown()` idempotence guard) |

Defects found by the tests while building (fixed): a malformed endpoint (`http://[::1`) raised from the constructor (INV-4); payload strings were silently
truncated to 4096 characters in `FULL` mode; a stale keep-alive connection cost a retry attempt and a random delay; `Config.__repr__` showed the API key; forked
children could deadlock on a lock held by a vanished thread (now `os.register_at_fork`).

## Review notes (self-review, `review-change` checklist)

Security-sensitive parts: privileges (migration 0007), redaction. Checked: runtime role cannot create objects or change roles; password comes only from
the environment and is applied by Alembic's connection (never stored in a migration); `NOLOGIN` without it. Redaction runs before buffering so a later
serialization bug cannot leak; default mode drops payloads; API key never logged or in `repr`. Tenant scoping is unchanged (INV-3). Residual risks are in
KNOWN_ISSUES KI-030..029.

Decisions beyond the plan: `project=` is informational (the key decides the project); `llm_call` takes `temperature`/`max_tokens` named arguments; run `metadata`
becomes flat `metadata.<key>` attributes (scalars only) so it survives the default payload mode; a failing redaction callback drops the event (P0: keeps structural attributes only).
