# Phase 2 - Data Model, Ingestion, Run Querying

Status: Completed on branch `feature/phase-2-ingestion`; pending merge (PR needs user approval) and a CI run on GitHub
Owner: coding agent
Depends on: Phase 1 (event contract)
Spec: §19, §36, §60.1-60.2, §61.3, §65-66, §71-73, §78, §92, §101, §110, §130, §149, §153, §155; ADR-001, ADR-003, ADR-006, ADR-011 (written/finalised: ADR-002, ADR-012)

## Outcome

A synthetic run POSTed to the public ingestion API is reconstructed by the query API with correct ordering,
span parentage, status and summary, no matter how the events were split, ordered, duplicated or delayed.
Tenants are isolated structurally (composite keys) and by tests. This is the data plane every later phase builds on.

```text
SDK/curl --Bearer key--> POST /v1/events[/batch]  -> validate (event-schema) -> one transaction:
        upsert agents/runs, INSERT events ON CONFLICT DO NOTHING, enqueue coalesced outbox job -> 202
worker (SKIP LOCKED) -> rebuild run: spans, status, summary (pure function of the events)
GET /v1/runs, /v1/runs/{id}, /events, /events/{event_id}, /spans   (key with runs:read)
```

## Non-Goals

- Dashboard users, OAuth, sessions, RBAC, key-management endpoints (Phase 15; keys are created by CLI now). The
  `users` and `workspace_members` tables exist but are unused.
- Artifact upload/storage (Phase 6), SSE/live streaming (Phase 5), cost engine and pricing (Phase 7: the summary only
  sums `cost.estimated_usd` if present), analytics endpoints, evaluation/policy/approval logic or endpoints.
- Retention jobs, row-level security, table partitioning, Redis/Kafka/ClickHouse (no measured trigger), distributed
  rate limiting, API key cache.
- SDKs. Tests and examples post raw JSON.

## Current Architecture

`apps/api` has `core/` (config, logging, request IDs, typed errors, middleware), `db.py` (engine, `check_database`),
`health/`. Alembic baseline `0001`. `packages/event-schema` provides `EventIn`/`Event`, `parse_event_in`, `finalize`,
`content_hash`, `sort_events`, `derive_spans`, ids (`to_uuid`/`from_uuid`), `RunStatus` and `can_transition`. The API
does not depend on the package yet and its Docker build context is `apps/api` only. Tests share one `abb_test`
database (KI-011); `docker compose` does not run migrations (KI-012).

## Decisions (confirm before implementation; ADR-002 and ADR-012 capture the contestable ones)

- **D1 SQLAlchemy Core, not the ORM.** Tables are `Table` objects in one `MetaData` (Alembic autogenerate works), queries
  are explicit statements inside repositories, rows become Pydantic/domain objects at the repository boundary. This keeps
  ORM models from leaking as API contracts, gives direct access to `INSERT .. ON CONFLICT`, `FOR UPDATE SKIP LOCKED`, and
  keeps SQL out of routers (spec §129). (ADR-002)
- **D2 Tenancy is structural.** Every tenant table has `workspace_id` and its primary key is `(workspace_id, id)`, so two
  tenants can never collide or observe each other's ids; child tables reference `(workspace_id, parent_id)` with composite
  foreign keys so a row cannot point at another tenant's parent. Repositories are constructed with a `TenantContext`
  and add `workspace_id = :tenant` to every statement; no repository method accepts a workspace argument (INV-3). Lookups of
  another tenant's id return not found (404), never 403.
- **D3 Ids are stored as `uuid`** (`ids.to_uuid`) and rendered back as prefixed ULIDs at the API boundary.
- **D4 API keys**: `abb_live_<key_id>.<secret>`; `key_id` is 12 random base32 chars (public, unique, indexed), `secret` is 32
  random bytes (url-safe base64). Only `sha256(secret)` is stored (the secret has 256 bits of entropy, so a slow hash adds
  nothing); comparison uses `hmac.compare_digest`; an unknown `key_id` still performs a dummy compare. Scopes:
  `events:write`, `runs:read`. `project_id` is nullable: ingestion requires a project-bound key; a workspace-wide key may read
  across the workspace's projects (spec §63.2). Unknown, malformed, revoked and expired keys all return the same `401
  API_KEY_INVALID`; a valid key lacking a scope gets `403 INSUFFICIENT_SCOPE`. `last_used_at` is updated at most once a minute.
  Keys are created only via CLI (`python -m abb_api.cli`); the secret is printed once.
- **D5 Runs are created implicitly.** The first accepted event for an unseen `run_id` upserts the run (project from the key,
  `started_at` from the event) so arrival order never matters; `POST /v1/runs` also exists for explicit, idempotent creation.
  A run belongs to exactly one project; events for a run owned by another project are rejected per event (`RUN_PROJECT_MISMATCH`).
- **D6 Run state is derived, not written by ingestion.** Ingestion only inserts events and enqueues work. The summarizer
  recomputes a run from all of its events (a pure function in `runs/summary.py`): status folded over the canonical order using
  the §153 state machine (invalid transitions are ignored, not errors; late events can change the result), spans via
  `derive_spans`, and the §78 summary with `summary_version = 1`. Full recompute per job is O(events in run) and idempotent;
  incremental update waits for a measured need (R2). (ADR-012)
- **D7 Run status mapping**: `run.started` -> RUNNING; `run.completed` by `status` (success/none -> SUCCESS, error -> FAILED,
  timeout -> TIMED_OUT, blocked -> BLOCKED, cancelled -> CANCELLED); `run.failed` -> FAILED; `run.cancelled` -> CANCELLED;
  `approval.requested` -> WAITING_FOR_APPROVAL; `approval.granted/denied` -> RUNNING. A run with events but no lifecycle event
  is RUNNING; one created by `POST /v1/runs` without events is QUEUED.
- **D8 Summary fields** (all derived from events, documented in `docs/architecture/api-v1.md`): `event_count`, `duration_ms`,
  `llm_calls` (`llm.request.completed|failed`), `tool_calls` (`tool.call.completed|failed`), `input_tokens`, `output_tokens`,
  `estimated_cost_usd` (sum of `cost.estimated_usd`), `retry_count` (`retry.attempted`), `error_count` (events with status
  error/timeout or a `*.failed` type, each event counted once), `files_modified` (distinct `file.path` of created/modified/
  deleted), `models` (distinct `llm.model`), `first_event_at`, `last_event_at`. No security or evaluation fields until those phases.
- **D9 Ordering on read**: `GET .../events` follows Phase 1's rule. The run records `ordering_mode` (`sequence` when every event
  has a `sequence`, else `time`) and the listing sorts by `(sequence, occurred_at, received_at, event_id)` or `(occurred_at,
  sequence, received_at, event_id)` accordingly, with a keyset cursor that embeds the mode; if the mode flips mid-pagination
  (a late unsequenced event) the API answers `409 CURSOR_STALE` and the client restarts. Partial data is normal; the response
  includes `run.summary_state` (`current` or `processing` when a summarize job is pending).
- **D10 Ingestion semantics**: `POST /v1/events/batch` accepts `{batch_id?, sent_at?, events[]}` (<= 1000 events), optionally
  gzip-compressed; 5 MiB limit on the compressed body and on the decompressed bytes (streamed with a hard cap, so a zip bomb
  cannot expand), 256 KB per event. Each event is validated independently with `parse_event_in` then `finalize` (tenant mismatch
  rejects that event). One transaction inserts the valid events; `202` always means committed. Response: `{batch_id, accepted,
  duplicates, conflicts, rejected, errors[{index, event_id?, code, issues}], server_time, request_id}`. A malformed envelope,
  oversize body, or an empty batch is a typed 4xx for the whole request. `POST /v1/events` is the single-event form (422 when invalid).
  Duplicates (same id and content hash) count as success; same id with different content is kept-first and counted in
  `conflicts` and logged (ADR-006). `received_at` is taken once per batch from an injected clock.
- **D11 Rate limiting** (FR-ING-007): in-memory token bucket per project (events/s and bytes/s), `429` + `Retry-After`, behind a
  `RateLimiter` protocol; defaults are generous and configurable. Per-process only; distributed limiting is Phase 19.
- **D12 Outbox + worker** (§72): `outbox_jobs` with `dedupe_key`; a partial unique index on `(job_type, dedupe_key) WHERE
  status = 'pending'` coalesces many events of one run into one pending job. The worker (`python -m abb_api.worker`, same image)
  claims with `FOR UPDATE SKIP LOCKED`, takes a 60 s lease, retries with exponential backoff, dead-letters after 5 attempts with
  the last error, and shuts down cleanly on SIGTERM. A `run_once()` function drives it in tests without sleeping.
- **D13 Minimal skeleton tables** for `artifacts`, `evaluations`, `policies` (per §20; the build prompt lists them) with no
  endpoints; later phases extend them with additive migrations. `approvals`, `security_findings`, `alerts` are not created yet.
- **D14 Phase 1 carry-over: no raw JSON retention.** Only validated, normalised fields are stored plus `schema_version`; unknown
  top-level fields are dropped, unknown attributes are preserved. Rationale: storage cost, a second copy of potentially sensitive
  text, and no consumer needs the raw form yet. Revisit when an adapter needs lossless re-processing.
- **D15 Test isolation (resolves KI-011)**: a session fixture creates a throwaway database, runs `alembic upgrade head`, and
  truncates tables between tests; migration tests each create their own throwaway database. **Compose (resolves KI-012)**: a
  one-shot `migrate` service, then `api` and `worker`; the api Docker build context moves to the repo root to include
  `packages/event-schema`; `abb-event-schema` is a path dependency of `apps/api`.
- **D16 Clock and randomness are injected** (`Clock` callable) so `received_at`, key expiry and lease timing are testable.
- **D17 Error codes** (all in the existing envelope): `API_KEY_INVALID` 401, `INSUFFICIENT_SCOPE` 403, `RUN_NOT_FOUND` 404,
  `EVENT_NOT_FOUND` 404, `EVENT_SCHEMA_UNSUPPORTED` 400, `EVENT_INVALID` 422, `BATCH_INVALID` 400, `PAYLOAD_TOO_LARGE` 413,
  `RATE_LIMITED` 429 (retryable, `Retry-After`), `CURSOR_INVALID` 400, `CURSOR_STALE` 409, `RUN_PROJECT_MISMATCH` (per-event).
- **D18 OpenAPI is a committed artifact** (`apps/api/openapi.json`, `make openapi` / `openapi-check`), the basis of the typed
  web client in Phase 4 and the §111 API-compatibility gate.
- **D19 Read auth for the web app is deferred.** Until Phase 15 the read API is reachable with a `runs:read` API key; how the
  Phase 4 UI obtains one (server-side key in the Next.js backend) is decided in the Phase 4 plan.

## Proposed Design

```text
apps/api/src/abb_api/
  tenancy.py          TenantContext, Principal (workspace_id, project_id|None, scopes, key_id)
  clock.py            Clock protocol, system clock
  db/                 engine, session factory, tables.py (MetaData), id/uuid helpers
  auth/               keys.py (generate/hash/parse/verify), repository.py, dependencies.py (principal, require_scope)
  ingestion/          router.py, service.py, store.py (EventStore protocol + Postgres impl), body.py (limits, gzip), ratelimit.py, schemas.py
  runs/               router.py, service.py, repository.py (RunRepository), summary.py (pure derivation), cursors.py, schemas.py
  traces/             repository.py (SpanRepository), router parts for /spans
  jobs/               outbox.py (OutboxRepository), worker.py (loop, run_once, handlers), handlers/summarize_run.py
  cli.py              create-workspace | create-project | create-key | seed | jobs list/retry
apps/api/migrations/versions/  0002_tenancy  0003_runs_spans_events  0004_outbox  0005_skeleton_tables
```

Table sketch (all ids `uuid`; `(workspace_id, id)` primary keys unless noted; timestamps `timestamptz`):
`workspaces(id)`, `users`, `workspace_members(workspace_id, user_id, role)`, `projects(workspace_id, id, name, slug)` unique
`(workspace_id, slug)`, `agents(workspace_id, id, project_id, slug, name)` unique `(workspace_id, project_id, slug)`,
`agent_versions(workspace_id, id, agent_id, fingerprint)`, `api_keys(key_id text unique, workspace_id, project_id null,
secret_hash bytea, scopes text[], created_by, created_at, last_used_at, expires_at, revoked_at)` (the only table looked up
before the tenant is known), `runs(workspace_id, id, project_id, agent_slug, trace_id, name, status, ordering_mode, started_at,
completed_at, duration_ms, summary jsonb, summary_version, metadata jsonb, created_at, updated_at)`, `spans(workspace_id, id,
run_id, trace_id, parent_span_id, name, kind, agent_slug, status, started_at, ended_at, duration_ms)`, `events` per §149
plus `agent_id`, `agent_version`, `duration_ms`, `tags text[]`, `payload jsonb`, `sdk jsonb`, `content_hash bytea`, PK
`(workspace_id, event_id)`, `outbox_jobs(id, job_type, workspace_id, dedupe_key, payload, status, attempt_count, available_at,
lease_owner, lease_expires_at, last_error, ...)`, skeletons `artifacts`, `evaluations`, `policies`.
Indexes (spec §73.2, measured later): `runs(workspace_id, project_id, started_at desc, id)`, `runs(workspace_id, status,
started_at desc, id)`, `events(workspace_id, run_id, sequence, event_id)`, `events(workspace_id, run_id, occurred_at, event_id)`,
`events(workspace_id, event_type, occurred_at desc)`, `spans(workspace_id, run_id, parent_span_id)`, the outbox pending index.

Immutability (INV-1): the application never issues UPDATE/DELETE on `events`; a migration revokes nothing yet (single DB role)
but a test asserts no repository statement targets `events` except INSERT and SELECT. Derived tables (`runs`, `spans`) are
rewritten only by the summarizer (INV-2).

## Affected Files

New: everything listed above; `docs/architecture/api-v1.md`, `docs/benchmarks/phase-2-ingestion.md`, ADR-002, ADR-012.
Changed: `apps/api/pyproject.toml` + lock (path dependency), `apps/api/Dockerfile` (root context), `docker-compose.yml`,
`.github/workflows/ci.yml` (container job, `openapi-check`), `Makefile`, `scripts/quality.sh`, `.env.example`
(`ABB_*` limits), `apps/api/src/abb_api/{main,core/config,core/errors,core/middleware}.py`, docs (SECURITY, OPERATIONS,
TESTING, ARCHITECTURE, KNOWN_ISSUES, DECISIONS, IMPLEMENTATION_PLAN, PROJECT_STATE).

## Acceptance Criteria (commands)

1. `alembic upgrade head` on an empty database; for every revision `upgrade` then `downgrade -1` then `upgrade` (per-revision
   migration test, each in its own throwaway database); `head-1 -> head` applies; `make openapi-check` clean.
2. Repository tests on real PostgreSQL: every repository method scoped to the tenant (a two-workspace matrix proves reads and
   writes never cross); composite foreign keys reject a cross-tenant parent; `(workspace_id, event_id)` dedup holds under 50
   concurrent identical batches (exactly one row per event); cursor pagination is stable while rows are inserted; shuffled
   ingestion yields the canonical order.
3. Public API end-to-end (ASGI client, real PostgreSQL): a synthetic run with an agent span, LLM span, tool span, file and
   shell events, a failed test, a retry and a late event after `run.completed` is sent in shuffled, gzip-compressed batches
   with duplicates; `GET /v1/runs/{id}`, `/events`, `/spans` return correct order, parent/child links, status, summary.
4. Summary equivalence: for N seeded shuffles/batch splits (with the worker running between batches in some), the final
   `runs` row (status, summary, spans) equals a from-scratch rebuild from the stored events.
5. Auth and limits matrix: no key / malformed / unknown / revoked / expired -> 401 identical body; wrong scope -> 403; other
   workspace's run id on every `GET` -> 404; project-bound key reading another project -> 404; oversize body (compressed and
   decompressed), >1000 events, invalid gzip, wrong content type -> typed 4xx; rate limit -> 429 with `Retry-After`; no key,
   secret or payload text appears in logs (asserted by capturing log output).
6. Worker: two workers never process the same job (`SKIP LOCKED`); a failing handler retries with backoff then dead-letters
   with the error; an expired lease is reclaimed; jobs coalesce (1000 events of one run -> 1 pending job).
7. Error contract: every non-2xx response matches the envelope and carries `X-Request-ID`; no stack traces.
8. Manual evidence: `make up` (migrate, api, worker, web healthy); `make seed`; `curl` posts a batch with the seeded key and a
   `GET /v1/runs` shows the run; the same flow with the key removed fails with 401. Recorded in the completion report.
9. `scripts/quality.sh full` passes; CI green on the PR.
10. ADR-002 and ADR-012 written; `docs/architecture/api-v1.md` documents endpoints, limits, errors, ordering and summary rules;
    SECURITY.md describes the real key design. An ingestion benchmark (100-event batches, local, recorded method and numbers
    in `docs/benchmarks/phase-2-ingestion.md`; no claim beyond what was measured; targets p50 < 50 ms, p95 < 150 ms).

## Verification Plan

Run each acceptance command from a fresh clone (`make setup` then the checks). Fuzz the batch endpoint with the Phase 1
mutation generator wrapped in batches (never a 500). Review explicitly for tenancy, auth timing, gzip handling, SQL injection
surface (parameters only), log hygiene. `verify-change`, then `review-change` with a dedicated security pass (AGENTS.md:
auth, API keys and tenant scoping are security-sensitive), then `harden-change`, then `complete-phase`.

## Risks

- **R1 Cursor staleness** when `ordering_mode` flips during pagination (D9). Mitigation: explicit `409 CURSOR_STALE`; rare in practice.
- **R2 Full recompute cost** per summarizer job on very long runs. Mitigation: coalescing, measured in the benchmark note; incremental
  summarization only if a measured budget (run detail < 500 ms, §61.2) is missed.
- **R3 Auth is one indexed query per request.** Acceptable for MVP; a short TTL cache (§121) only after measuring; revocation latency must stay small.
- **R4 Unpartitioned `events`** (§73.3): fine at MVP volume; partitioning by month is an additive later migration with a trigger.
- **R5 Rate limiter is per process**, so N API processes allow N times the rate. Documented; Phase 19.
- **R6 Dev key handling**: the seeded key is written to a gitignored `.local/` file and printed once; never committed or logged.
- **R7 Docker context change** touches both images and CI; verified with `make up` and the CI container job.
- **R8 asyncpg/pgbouncer**: no prepared-statement pooling assumptions yet; revisit with the production pooler (Phase 19).

## Ordered Steps (one commit each)

1. [x] Wire `abb-event-schema` into `apps/api` (path dependency, Dockerfile root context, compose, CI), settings for limits; import test.
2. [x] DB layer: `tables.py`, migrations 0002-0005, throwaway-database test fixtures, per-revision migration tests (KI-011).
3. [x] Tenancy + clock; workspace/project/agent repositories; API key generation, hashing, verification; CLI (`create-*`, `seed`); tests.
4. [x] `EventStore` ingest transaction (run/agent upserts, `ON CONFLICT DO NOTHING`, duplicate vs conflict classification, outbox enqueue); concurrency tests.
5. [x] HTTP ingestion: body limits and gzip, auth dependency, rate limiter, `/v1/events`, `/v1/events/batch`, error codes; API tests.
6. [x] Summary derivation (pure), span persistence, outbox worker (claim, lease, retry, dead-letter), `summarize_run`; equivalence tests.
7. [x] Query API: `POST/GET /v1/runs`, run detail, events (cursor, order mode) and event detail, spans; cross-workspace matrix.
8. [x] OpenAPI export/check, compose `migrate` + `worker`, `make seed`/`make up` flow, benchmark note, manual evidence.
9. [x] Docs and ADRs (002, 012), SECURITY/OPERATIONS/TESTING/ARCHITECTURE, KNOWN_ISSUES (close KI-011/012).
10. [x] `verify-change`, `review-change` (+ security pass), `harden-change`, `complete-phase` (move plan to `completed/`).

## Evidence (2026-10-07)

1. **PASS** Migrations: `0001` to `0006` apply to an empty database; each revision upgrades, downgrades and upgrades again in its own
   throwaway database; `head-1` to `head`; downgrade to base leaves no tables; `tables.py` and migrations are checked for drift;
   `openapi.json` is current (`make openapi-check`, part of the gate). Clean-slate stack (`docker compose down -v`, `up --build --wait`):
   `alembic_version = 0006`, 15 tables, `ix_outbox_dedupe` present.
2. **PASS** Repository and store tests on real PostgreSQL: cross-tenant references rejected by composite keys; two workspaces may share
   ids; 50 concurrent identical batches store each event once with one pending job; opposite-order overlapping batches do not deadlock
   (proved deterministically by capturing the SQL write order); cursor pagination stays stable while runs arrive; shuffled ingestion yields
   canonical order (`tests/test_event_store.py`, `test_schema_constraints.py`, `test_runs_api.py`).
3. **PASS** Public-API acceptance: a 13-event run sent in shuffled, gzip-compressed, overlapping batches is reconstructed by
   `GET /v1/runs/{id}`, `/events`, `/spans` with exactly the canonical order, parent/child spans, status `SUCCESS` and the derived summary
   (`test_a_run_sent_in_shuffled_gzip_batches_is_reconstructed_exactly`). Same flow on real containers: `scripts/smoke.sh`.
4. **PASS** Summary equivalence: for 12 seeds of random batching, order, duplication and worker timing the final run row and spans equal a
   from-scratch derivation, and a replayed job changes nothing (`test_summarizer.py`).
5. **PASS** Auth and limits matrix: 401 (identical body) for missing, malformed, unknown, wrong-secret, revoked and expired keys; 403 wrong
   scope and workspace-wide key ingesting; 404 for other workspace or project on every read route; 413 for oversized bodies (compressed,
   decompressed, streamed, zip bomb bounded by peak memory); 415, 400, 429 with `Retry-After`; no key, secret or payload text in logs.
6. **PASS** Worker: two concurrent claimers receive disjoint jobs (`SKIP LOCKED`); failures retry with backoff then dead-letter; expired leases
   are reclaimed; lost leases roll back; leases are kept alive for slow jobs; a job that kills its worker is dead-lettered, not looped;
   1000 events of a run coalesce into one pending job; the real worker process starts, works and exits 0 on SIGTERM.
7. **PASS** Error contract: every non-2xx response matches the envelope with `X-Request-ID`; OpenAPI documents the same envelope for every
   error; 858 hostile requests (forged cursors, extreme timestamps, NUL bytes, odd paths, nested and oversized bodies) against the live API
   give zero 5xx.
8. **PASS** Manual evidence: `docker compose --profile app up --build --wait` (migrate, api, worker, web healthy); `make seed`; `scripts/smoke.sh`
   provisions two workspaces, gzip-ingests a run, confirms a retry is all duplicates, waits for the worker, reads status, summary, ordered
   pages and spans, and checks 401/401/404. The same script fails (as it should) with the worker stopped.
9. **PASS (local)** `scripts/quality.sh full` exits 0: event-schema 337, api 329 (real PostgreSQL, 98% statement coverage with greenlet
   tracing), web 7; a pristine clone passes it too. **UNVERIFIED (env)**: GitHub CI for this branch has not yet run on the final commit.
10. **PASS** ADR-002 and ADR-012 written; `docs/architecture/api-v1.md`, three runbooks, SECURITY, OPERATIONS and TESTING updated;
    benchmark recorded in `docs/benchmarks/phase-2-ingestion.md` (single exporter p50 27 ms, p95 44 ms on a 2-vCPU VM, within the 50/150 ms
    targets; 8 concurrent clients saturate one API process; the large-run tail is documented as KI-016). A mechanical check found every
    `make` target, link and CLI command in the docs exists, except deliberate references to future work.

### Independent verification and review: defects found and fixed

Benchmark (step 8): spans upsert exceeded the 32,767 bind-parameter limit for runs over about 2,500 spans (dead-lettered while the API said
`current`); job scheduling mixed host and database clocks; recomputing after every batch starved ingestion (debounce added).

`verify-change` (858-request hostile probe, repeated runs, coverage): `occurred_at` such as `0001-01-01T00:00:00+14:00` overflowed UTC
conversion and made a poison event; a crash in one event could 500 the whole batch; forged or out-of-range cursor elements, extreme time
filters and NUL bytes in filters produced 500s. Fixed at the root, with per-event isolation and a logged 422 safety net for database data errors.

`review-change` incl. security pass (three hypotheses tested as experiments before classification): reclaimed jobs ignored `max_attempts`
(a 2.5 s job under a 1 s lease ran 8 times; a worker-killing job would crash-loop forever); long jobs lost their own results to lease expiry
(heartbeat added); SQLAlchemy pool exhaustion surfaced as 500 instead of a retryable 503; run listings scanned the whole outbox history
(index added); responses lacked `nosniff` and `no-store`. Deferred with owners and triggers: KI-016 and KI-017-KI-026.

Process notes: three times a fix was lost or a bad commit pushed because of my own tooling (a `git checkout` restoring uncommitted work;
a chained commit that ignored a failing gate); mutation checks now run only on committed code and the gate's exit code is checked first.
