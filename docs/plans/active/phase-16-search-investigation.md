# Phase 16 - Search and investigation

Status: Implemented; pending security review (2026-10-11). Decisions 1-5 were taken at their recommended defaults. Evidence for every AC is in "Closing evidence" at the end. History: planned 2026-10-09 (4d4075e); independent review `phase-16-plan-review.md` (d7f805f) addressed below, see "Review dispositions".
Owner: implementer agent
Branch: `feature/phase-16-search-investigation` (from `main` c212058; worktree `../abb-worktrees/phase-16`; Phases 9, 10 and 11 are planned in parallel; merge order 9, 10, 16, 11)
Depends on: Phase 2 (runs query API, cursors, summarizer), Phase 7 (typed run columns `cost_usd`, `retry_count`, ADR-043), Phase 15 (`authorize()`, `payload.read`, read-time withholding in `runs/content.py`, the route registry and canary tests); on rebase: Phase 9 (`SUMMARY_VERSION` 3, `summary["agents"]`), Phase 10 (migration 0060, `summary_states(job_type=...)`, `RunOut.findings`)
Spec refs: §77 (query and search architecture), §152 (search grammar), §34 (search and investigation examples), §10.10 (search use cases), §77.1 "fast, explainable, and permission-safe", §93 (data classes: paths, commands, prompts are Class 2), §90.3 (cross-tenant access), §62 INV-2/3/7, §57.2 (deferred stores need a measured trigger)
ADRs: ADR-002 (structural tenancy), ADR-012 (derived state through the outbox), ADR-041/043 (typed derived columns; aggregates only where measured slow), ADR-061 (one `authorize()` path; command text and file paths are content). New: ADR-100, ADR-101 (drafted in the appendix; written at step 9).
Migration ids: **0080** only. Chain: `0047 -> 0060 -> 0080` (Phase 10 merged; `0080.down_revision = "0060"`, see "Merge notes"). Reserved for this phase: 0080-0089.
Known issues: none closed; adds KI-080..083 (Phases 9-11 use KI-084..086, 090..099, 100..109).

## Decisions needed from the user (each with a recommended default)

1. **Endpoint shape.** Recommended: extend `GET /v1/runs` with a `q` parameter (the §152 grammar) and return the parsed query and facets alongside the page. No new route, so no matrix, registry, web-proxy or OpenAPI-count change (fewer merge conflicts with Phases 9-11). Alternative: a new `GET /v1/runs/search` (needs a RouteCase, an allowlist entry and two test-count bumps; planned in D2 if chosen).
2. **Saved searches.** Recommended: out of scope. The URL carries `q` (bookmarkable, shareable); a saved search is the natural shape of a Phase 17 alert rule and gets its table then.
3. **Natural language.** Recommended: in this phase only the `QueryCompiler` interface and a deterministic rule-based fake used by tests; no endpoint, no UI, no vendor dependency. The real compiler is a later ADR.
4. **Content search for actors with `payload.read`.** Recommended: yes, in this phase: `file:` and `command:` fields and free text over captured content, built from the same attributes `runs/content.py` already withholds. Alternative: metadata-only search now and content search with Phase 13 (server-side redaction). The oracle protection (D5) is required either way.
5. **Where the search document is built.** Recommended: inside the summarizer transaction (the events are already in memory), skipped when the document hash is unchanged, with an interleaved A/B `make bench` (AC-11) deciding whether it moves to its own outbox job (D7 states the measured trigger).

## Outcome

A developer types `status:failed tool:github retries:>=2 started:>=-7d "npm test"` on the runs page and sees, within a second, the runs of
their workspace (and only their workspace) that match, why each matched (the tools, models, agents and event types the query named), a
facet strip to narrow further with one click, and a notice if some runs are not yet indexed. The query is a typed AST compiled to at most
two bounded SQL statements under a per-request budget and a per-process concurrency cap; the UI never builds SQL-like strings (§152). A
VIEWER searches the same box over everything they can already read, and nothing else: no query, count, facet, flag, ordering or error ever
tells them whether a withheld command, path or payload contains a word or how much of it there is (§77.1 "permission-safe", ADR-061). The
index is derived state rebuilt from events (INV-2), ingest cost is re-measured A/B, and every heavier store keeps a written, measured trigger.

## Non-goals

Event-level search across runs as its own result list (the run is the result; the run page has the timeline filters), relevance ranking
(results are newest first), `OR` and parentheses (flat conjunction with negation; D3 says why), regular expressions and single-segment globbing,
substring search of payload bodies or artifacts (prompts, diffs, stdout: Class 2 bodies stay behind the detail and artifact endpoints), search
over audit, members, keys, findings (Phase 10; `finding:` reserved) or evaluations (Phase 11; `verdict:` reserved), saved searches and alert
rules (Phase 17), a natural-language endpoint (interface and fake only), trigram or external search indexes (triggers in D6), server-side
redaction (Phase 13; KI-078 stays), per-project roles, quotas (KI-018) and a shared rate limiter (KI-017/019: Phase 19; this phase adds a
per-process cap only). Nothing here needs Kafka, ClickHouse, Redis, OpenSearch or Kubernetes.

## Current architecture (verified by reading the code unless marked ASSUMED)

- **Run listing** (`apps/api/src/abb_api/runs/router.py:85-107`, `service.py:169-207`, `queries.py:81-120`): `GET /v1/runs` filters by
  `status` (repeatable), `agent_id` (slug pattern), `project_id` (through `projects/access.py::authorise_project`, the one place a project
  filter is authorised), `started_after`/`started_before` (aware timestamps), `sort`, `limit` 1-200, keyset `cursor` on `(started_at, id)`
  encoded by `runs/cursors.py` (versioned, kind-checked, width-checked, untrusted). `RunQueries._base()` puts `workspace_id = tenant` on every
  statement and a project-bound key's `project_scope` on top (INV-3). `summary_states` adds `current|processing|failed` per page
  (`queries.py:122-152`; Phase 10 generalises it with a `job_type` argument whose default stays `SUMMARIZE_RUN`).
- **Runs table** (`db/tables.py:284-325`): typed derived columns written by the summarizer with the summary (`cost_usd`, `retry_cost_usd`,
  `llm_calls`, `tool_calls`, `retry_count`, `files_modified`, ...; ADR-043), `summary` JSONB (has `models`, `error_count`, `event_count`),
  `metadata` JSONB (client-controlled, 16 KB, from `POST /v1/runs`), `name`, `agent_slug`, `status`, `started_at`, `duration_ms`. Indexes:
  `ix_runs_project_started (workspace_id, project_id, started_at DESC, id)`, `ix_runs_status_started`, `ix_runs_workspace_started`. There is
  **no** `error_count` column, no GIN index anywhere, no `tsvector`, no `pg_trgm`; `events.attributes` JSONB has no index (`:386-390`).
- **Summarizer** (`jobs/handlers.py:35-67`): `summarize_run` takes the run lock, loads every event (KI-022), calls `derive_run`
  (`runs/summary.py`, a pure function; `SUMMARY_VERSION = 2`, pinned by `tests/test_run_summary.py:199`), `apply_derivation` (run row + spans),
  writes cost lines and queues an analytics refresh, in one transaction; it is debounced per second (`core/config.py:33`
  `summary_debounce_seconds = 1.0`), so an active run is re-summarised many times. `analytics/rollup.py:104` counts runs with
  `summary_version < SUMMARY_VERSION` as `unrebuilt_runs`. `POST /v1/cost/rebuild` (`cost/router.py:232-246`, requires `pricing.write`,
  audited as `cost.rebuild`) re-enqueues `summarize_run` for up to `limit` runs of a project.
- **Content withholding** (`runs/content.py`, ADR-061 amendment): for an actor without `payload.read` the attributes in
  `abb_event_schema.registry.CONTENT_ATTRIBUTES` (`shell.command`, `shell.cwd`, `file.path`, `git.repo`, `git.branch`, `git.push_target`,
  `http.url`, `test.failing`) and any unregistered attribute whose last segment is `command|cmd|argv|args|path|paths|filename|file|cwd|url|uri`
  are replaced by `[withheld]`; span names of `shell|file|git|null` kinds are nulled (the derived `Span.name` falls back to `shell.command` and
  `file.path`: `packages/event-schema/.../spans.py:42-47`); payload bodies are dropped. The classification is fail-closed by
  `tests/authz/test_content_withheld.py::REVIEWED_METADATA` (every string attribute is content or reviewed metadata). The reviewed set includes
  `run.name`, `tool.name`, `tool.operation`, `llm.model`, `llm.provider`, `span.name`, `db.operation`, `http.method`, `policy.reason`,
  `retry.reason`; tags and run `metadata` are visible to every reader (KI-078).
- **Authorization** (`authz/`): `require(action)` per route; `RUN_READ` for the run routes; `PAYLOAD_READ` is held by OWNER, ADMIN, DEVELOPER,
  SECURITY and by `runs:read` keys; VIEWER lacks it; BILLING cannot read runs at all. `holds()` varies a response by permission
  (`authz/service.py:63-65`); `authorize()` raises `PermissionDenied` with the per-actor-kind shape (`:17-44`); `authorize_audited(request, ...)`
  also logs the denial and, for people, writes a rate-bounded audit row (`authz/audit.py:40-76`). `tests/authz/registry.py` has one case per
  route; `test_registry_is_complete.py` and `test_openapi.py` pin the operation counts (31 `/v1` cases, 36 operations today; Phase 10 adds one
  each). `test_migrations.py` lists revisions through `0047`; its drift test compares `db/tables.py` with the migrated schema (`:86-94`). The
  cross-workspace world seeds `CANARY-GLOBEX-7f3e` (tag, model, tool `canary-globex-7f3e-tool`) in globex and `COMMAND_CANARY`,
  `PATH_CANARY`, `CWDCANARY`, `CUSTOM_CANARY` in acme's first run (`tests/authz/world.py:31-40, 133-152`).
- **Query cost bounds today**: the engine has no global `command_timeout`, `pool_timeout=5`, and SQLAlchemy's default pool of 5 + 10 overflow
  per process shared by every route including ingestion (`db/engine.py:12-19`; `tests/test_runs_api.py:744-756` proves exhaustion is a `503`).
  Analytics applies `SET TRANSACTION READ ONLY` + `SET LOCAL statement_timeout` per read and maps a cancel to `503 ANALYTICS_TIMEOUT` (retryable)
  (`analytics/postgres.py:86-93, 229-246`, tested at `tests/test_analytics_api.py:243-251`). Query parameters that reach SQL are
  pattern-constrained in the router; hostile filters are `422` (`tests/test_runs_api.py:652`). uvicorn's access log is off (`core/logging.py:44-45`).
- **Web** (`apps/web`): `RunsListPage.tsx` keeps filters in the URL (`status`, `agent`, `range`) and local state; `RunsList.tsx:47-51` turns
  `range` into an absolute `startedAfter` once per mount; `useRuns` (`lib/queries.ts:24-49`) polls every `POLL_MS * 5`; the proxy allowlist in
  `server/upstream.ts:28-41` admits `GET v1/runs` with any query string, refusing encoded query strings over 2,048 chars with
  `414 QUERY_TOO_LONG` (`:43, :115-116`). `lib/withheld.ts` renders withheld attributes. Types come from `lib/api/schema.d.ts` (`make
  web-client` from `apps/api/openapi.json`). Fixture mode (`src/fixtures/runs.ts`) serves canned pages. `next.config.ts:35` already sends
  `Referrer-Policy: no-referrer`. Playwright specs live in `apps/web/e2e`; `scripts/auth-e2e.sh:128` runs `e2e/auth.spec.ts` with real sessions
  for several roles.
- **Event-schema limits** (`abb_event_schema/limits.py`): attribute string 4,096 chars, 64 attributes, 16 tags of 64 chars, `run.name` 256:
  every indexed value is bounded at ingestion.
- **Benchmarks**: `make bench` (`scripts/bench_ingest.py`, with a freshness case) measures ingest p50/p95/p99; the Phase 7 regression check
  (`docs/benchmarks/phase-7-regression-check.md`) shows a 14% run-to-run noise band on p95 for the 4-client case, which is why AC-11 is A/B.
- **Postgres text search (corrected after review F1).** `to_tsvector('simple', text)` always runs the default text-search parser (`simple`
  only skips stemming): it classifies `src/auth.ts` as a `file` token, splits `foo_bar` on `_` and emits hyphenated words plus their parts, and
  the same happens inside `to_tsquery`/`plainto_tsquery`. So pre-tokenised text cannot be handed to those functions. The `tsvector` and
  `tsquery` **input functions** (`CAST(:x AS tsvector)`, `'lexeme':pos` syntax) do no parsing and no normalisation; that is what D4 uses.
  **UNVERIFIED (env)** here (no database); the input syntax is pinned by one AC-3 test, and everything else is a DB-less serialiser test.
- **Environment**: Postgres 16 (`postgres:16-alpine`). **UNVERIFIED (env)**: this worktree has no `.venv` or `node_modules`; step 1 starts
  with `uv sync` and `pnpm install`. **ASSUMED**: FastAPI resolves the `require()` dependency before query-parameter validation, so BILLING
  with a malformed `q` gets `403`, not `422` (pinned by a test in AC-4).

## Known issues considered

- **KI-078 (S3, target Phase 13)**: stays open. Search inherits its boundary exactly: a VIEWER can search `run.name`, tags, `tool.name`,
  `llm.model`, the `span.name` attribute because they can already read them; nothing a VIEWER cannot read becomes searchable (D5). The plan does
  not widen the leak: unregistered non-content attributes are not indexed at all (fail closed; KI-080).
- **KI-018 (S1), KI-022 (S2), KI-016 (S2)**: the search document is built from the events the summarizer already holds in memory and is
  bounded (D4: 2,000 distinct values per array, 8,000 token positions per document), the write is skipped when the document hash is unchanged
  (D7), so it adds no new unbounded cardinality, no second pass over events and no churn proportional to a run's lifetime. The KI-016 tail
  (30,000-event run) is re-measured with dead-tuple and GIN-size figures (AC-11).
- **KI-019 (S1), KI-017 (S2)**: a search is a read under a per-request budget and a per-process concurrency cap (D8); the cap is per process
  like every limiter today and is the interim for KI-082 until Phase 19's shared limiter.
- **KI-055 (S2), KI-040, KI-021, KI-072 (retention)**: `run_search` rows cascade with runs and are rebuilt by the summarizer; a retention job
  that deletes runs removes them (Phase 19).
- **KI-056 (S2, long-locking migration)**: migration 0080 creates an empty table and indexes only (no backfill in the migration). Its
  foreign key takes a brief `SHARE ROW EXCLUSIVE` lock on `runs` that waits behind any long transaction; the runbook note says "run outside a
  bulk rebuild" (step 9).
- **KI-050, KI-067..077, KI-052, KI-057, KI-041..044, KI-030..035, KI-060..063, KI-008/010/013/023/024/026/031**: unrelated, stay deferred as listed.
- New items filed at completion: **KI-080** (S3) unregistered attributes that are not content by name are not searchable (fail closed; Phase 13
  may classify them); **KI-081** (S3) search covers runs summarised at `SUMMARY_VERSION >= 4` (the value after rebasing on Phase 9); older runs
  show as "not yet indexed" until `search-rebuild` is run, which re-summarises them (and so rewrites cost lines and queues analytics refreshes:
  a one-off, documented); **KI-082** (S3) the search concurrency cap and budget are per process, not per actor (Phase 19 with KI-017/019);
  **KI-083** (S3) the natural-language compiler is a rule-based fake.

## Decisions (D1..D12; ADR-100 and ADR-101 drafted in the appendix)

- **D1 Search is a typed AST, parsed and compiled server-side; the UI only sends `q` and renders what the server parsed.** `search/ast.py`
  (frozen dataclasses: `Query(terms)`, `FieldTerm(field, op, value, negated, span)`, `TextTerm(lexemes, phrase, prefix, negated, span)`),
  `search/parser.py` (hand-written scanner, no regex backtracking, O(n)), `search/fields.py` (the field catalogue as data: name, kind, storage
  column, `needs_content`), `search/compiler.py` (AST + `Principal` -> SQLAlchemy predicates over `runs` and `run_search`), `search/document.py`
  (events -> `SearchDocument`, pure), `search/tsliteral.py` (tsvector/tsquery literal serialisers). Rationale: §152; one grammar in one language;
  the compiler is the only thing that knows SQL (INV-7); the UI gets the parsed terms back (`query.terms[].span`) so chips, errors and removal
  are exact.
- **D2 (user decision 1) The grammar rides on `GET /v1/runs?q=`.** `q` combines by AND with the existing parameters (they stay for SDK and
  URL compatibility; the web now writes `q` plus `range`). The response gains optional, additive fields present whenever `q` is sent:
  `query` (`{terms, fields, content_allowed, unindexed}`) always; `facets` and per-run `matched` only when `q` has at least one term (an empty
  `q` executes exactly the statements the `q`-less path does: AC-7). Errors: `422 QUERY_INVALID` with `details.position` and `details.reason`;
  `403 PERMISSION_DENIED` raised by `authorize_audited(request, principal, PAYLOAD_READ)` with `details.field` = the catalogue's canonical field
  name when a content field is used without `payload.read` (the key branch is unreachable: a key with `run.read` holds `payload.read`,
  ADR-061); `503 SEARCH_TIMEOUT` and `503 SEARCH_BUSY` (both retryable). No new route: the route's action stays `run.read`; the registry and
  OpenAPI operation counts are **unchanged by this phase**; the web proxy allowlist is unchanged. If the user prefers a dedicated route:
  `GET /v1/runs/search` with `require(actions.RUN_READ)`, `RouteCase("GET", "/v1/runs/search", RUN_READ, _get(...), 200, ("project_id",))`,
  both counts + 1, `upstream.ts` pattern `runs/search`, all in one commit.
- **D3 Grammar (§152 made precise).** `query := term*` separated by whitespace; `term := '-'? (field ':' op? value | value)`;
  `op := '>=' | '<=' | '>' | '<'` (numeric and time fields only); `value := '"' chars '"' | bare` where a quoted value may escape `\"` and
  `\\`, a bare value runs to whitespace; a bare or quoted value without `field:` is free text (quoted = phrase); a trailing `*` on an
  enumerated value or on the last lexeme of a text term means prefix. Negation (review F16): one leading `-` negates the term once and a
  second `-` starts the value (`--force` = NOT text `-force`, which tokenises to `force`); a quoted value is never negation (`-"--force"` = NOT
  phrase `--force`); after an operator `-` is part of the value (`cost:>-0`, `started:>=-24h`). Flat conjunction, no `OR`, no parentheses, no
  `NOT` keyword (a bare `NOT` is a word). Field names and enum values are case-insensitive; text is NFKC-normalised and case-folded. Time
  values: a date (`2026-10-01`: the whole UTC day for equals, midnight for comparisons), an RFC 3339 instant (`Z` recommended; a `+` offset
  must be sent as `%2B`), or a relative form `-<n><unit>` with units `m|h|d|w` resolved against the server clock at request time (so a
  bookmarked `started:>=-24h` stays relative; the echo shows the resolved instant). Limits (`422` beyond): `q` <= 512 characters, <= 16 terms,
  <= 8 text terms, a value <= 256 characters, a text term <= 16 lexemes, a glob <= 8 wildcards, a relative time <= 3,660 days; control
  characters, NUL and unpaired surrogates are `422`; a text term that tokenises to no lexeme is `422 QUERY_INVALID` with `reason = "no
  searchable characters"` at the term's position. Rationale: every §152/§34 example parses; no nesting means no depth to bound and no
  exponential plans; `OR` is the first thing to add when a real query needs it (one AST node, one compiler case).
- **D4 The index is a derived per-run document in Postgres, written by the summarizer, rebuildable from events (INV-2).** Table `run_search`
  (migration 0080): `(workspace_id, run_id)` PK with a composite FK to `runs` `ON DELETE CASCADE`, `project_id`, `started_at` (copied for the
  keyset; the ORDER BY uses this table's index when the join is present), `summary_version`, `document_hash bytea` (SHA-256 of the canonical
  document; the summarizer skips the write when unchanged), `error_count int`, `security_events int`, metadata arrays `event_types`, `tools`,
  `models`, `providers`, `agents`, `tags` (`text[]`), `metadata_doc tsvector`, `metadata_truncated bool`, content columns `file_paths text[]`,
  `commands text[]`, `content_doc tsvector`, `content_truncated bool`, `updated_at`. Built by `search/document.py::build_document(events,
  run_name, run_metadata, agents)` from: event types; `tool.name`, `tool.operation`; `llm.model`/`llm.provider`; `db.operation`, `http.method`;
  the run's agent list (`agents` = the same list Phase 9's `summary["agents"]` carries, passed in from the derivation, not re-walked; before the
  rebase, the distinct `agent_id`s of the events in canonical order); tags; `run.name`/the run row's name; the **`span.name` attribute** of any
  event (reviewed metadata; never the derived `Span.name`, which falls back to commands and paths); string leaves of the run's `metadata`
  (metadata doc); and `CONTENT_ATTRIBUTES` values plus unregistered content leaves (`is_content_attribute`) into `file_paths` (`file.path`),
  `commands` (`shell.command`) and `content_doc` (all of them, including `shell.cwd`, `git.*`, `http.url`, `test.failing`). Unregistered
  non-content attributes are not indexed (KI-080). Bounds: each array keeps the first 2,000 distinct values in canonical event order; each
  document keeps the first 8,000 token positions (the `tsvector` position cap is 16,383 and lexemes <= 2,047 bytes); beyond that the
  **per-document** flag (`metadata_truncated` or `content_truncated`) is set. Tokeniser (`search/tokens.py`, shared by document and query):
  NFKC, casefold, split on anything that is not a letter, digit, `_`, `.`, `-`, `/`, drop tokens longer than 256 bytes. **Document side only**:
  each token is a lexeme at position *p*, and its `/`-segments and `.`-parts are emitted as further lexemes at the **same position** *p*
  (`'src/auth.ts':7 'src':7 'auth.ts':7 'auth':7 'ts':7`), so phrase adjacency and prefix matching work on the un-expanded stream. **Query
  side**: no expansion; each query token is one lexeme, a phrase is `'npm' <-> 'test'`, a prefix is `'src/au':*` on the last lexeme only. The
  tsvector and tsquery are **literals built in Python** by `search/tsliteral.py` (quotes and backslashes doubled, bounds enforced) and bound as
  parameters with `CAST(:doc AS tsvector)` / `CAST(:q AS tsquery)`; `to_tsvector`/`to_tsquery` are never used (review F1). Indexes:
  `ix_run_search_project_started (workspace_id, project_id, started_at DESC, run_id)`, `ix_run_search_meta GIN(metadata_doc)`,
  `ix_run_search_content GIN(content_doc)`, `ix_run_search_event_types GIN(event_types)`, `ix_run_search_tools GIN(tools)`. The runtime role
  gets DML through the default privileges of migration 0007 (derived, not append-only). Rationale: a separate table keeps the hot `runs` row
  and its B-tree indexes small; arrays give exact-match and prefix semantics; literal tsvectors give bounded, indexed word search with exactly
  our lexemes; everything is rebuilt by re-summarising.
- **D5 (ADR-101) Search is never an oracle for withheld content.** The rule: *an actor may search a field, or receive a derived flag or
  count, only if the same actor receives that field's values on the existing read routes.* Mechanically: `fields.py` marks `file`, `command`
  as `needs_content`; the compiler takes the `Principal` and (a) reports any `needs_content` field in the AST before building SQL, and the
  service calls `authorize_audited(request, principal, PAYLOAD_READ)` (one `authorize()` path, denial logged and audited like every other) and
  attaches `details.field` (canonical name, never the user's spelling), (b) compiles free text to `metadata_doc @@ q` only, and to
  `(metadata_doc @@ q OR content_doc @@ q)` when `payload.read` is held, (c) never emits `content_doc`, `file_paths`, `commands` or
  `content_truncated` in a statement for an actor without `payload.read` (asserted on the compiled SQL text), (d) facets are computed only
  over `status`, `agents`, `tools`, `models`, `event_types`, (e) there is no total count and no relevance ranking (newest first), (f) the
  response carries `metadata_truncated` for everyone and `content_truncated` only when `content_allowed` (review F5), (g) `unindexed` (D9) is
  a count over the run-row scope, independent of any content. A BILLING person is refused at `run.read` before parsing. Keys with `runs:read`
  imply `payload.read` (ADR-061) and search content. Search terms are the searcher's own input and may appear in their browser history or a
  reverse proxy's log; the API logs no query text and the web app sends `Referrer-Policy: no-referrer` (verified).
- **D6 Postgres FTS and arrays now; trigram and external indexes have measured triggers.** `command:` and `file:` are substring/glob matches
  over the bounded arrays via `EXISTS (SELECT 1 FROM unnest(...) v WHERE v ILIKE :p)` after the tenant/project/time predicates, inside the
  budget. Glob semantics (review F7): a value with no wildcard is a case-insensitive substring match; with wildcards, `*` and `**` both match
  any characters **including `/`** (documented; single-segment globbing is out of scope), `?` matches one character, the pattern is anchored at
  the end and unanchored at the start unless it begins with `/` (so `file:src/auth/**` matches `/home/x/src/auth/login.ts`, `file:*.ts` matches
  every TypeScript path, `file:/etc/passwd` matches exactly that path); `\`, `%`, `_` are escaped in that order. Trigger for `pg_trgm` GIN on
  `commands`/`file_paths` (migration 0081, an ADR): p95 of a `command:` or `file:` search > 1 s on the measured dataset (AC-12). Trigger for a
  dedicated search service (spec §77.2 "only when necessary"): metadata free-text p95 > 1.5 s with GIN in place at Stage A scale (100,000
  runs/day, 90 days), or a product need for ranking/typo tolerance. ClickHouse/Kafka are untouched (§57.2).
- **D7 (user decision 5) The document is built inside `summarize_run`, same transaction as `apply_derivation`, written only when changed.**
  The events are already in memory; the upsert runs `WHERE run_search.document_hash IS DISTINCT FROM :hash`, so a debounced re-summarisation
  of an active run whose searchable facts did not change touches no GIN index (review F15). Order in the handler: `apply_derivation`, cost
  lines, **`run_search` upsert, then** Phase 10's `detect_findings` enqueue, then the analytics refresh. Trigger to split it into its own
  coalesced outbox job (`index_run`): the interleaved A/B bench (AC-11) shows a p95 regression on the 1,000-event-run case that exceeds the
  observed noise band and 10%, or summary freshness worse by more than the band and 20%.
- **D8 Every search request is bounded: budget, cap, two statements.** (a) A per-process `asyncio.Semaphore(ABB_SEARCH_MAX_CONCURRENT,
  default 4)` is acquired **before** a connection is taken; when full the request is `503 SEARCH_BUSY` (retryable, `Retry-After: 1`), so
  search can hold at most 4 of the 15 pooled connections and ingestion is never starved by search (review F2). (b) One wall-clock budget per
  request, `ABB_SEARCH_TIMEOUT_SECONDS` (default 5): the page statement runs under `SET TRANSACTION READ ONLY` + `SET LOCAL statement_timeout`
  = the remaining budget; the facets statement runs only if at least 40% of the budget remains, else `facets: null` with
  `facets_skipped: "budget"`; a cancelled page is `503 SEARCH_TIMEOUT` (retryable; "narrow the time range or project"). (c) At most two
  statements per request: the page statement also selects the array columns of the page's rows, and `matched` is the intersection computed in
  Python; the facets statement is one CTE over the newest 1,000 matches with one `UNNEST` per array, `UNION ALL`, `GROUP BY`, top 10 per
  facet, plus the bounded `unindexed` count (`SELECT count(*) FROM (... NOT EXISTS run_search ... LIMIT 1001)`) in the same statement. (d)
  `limit` <= 200 as today; parser limits per D3; patterns, tsvector and tsquery literals are bound parameters only; the existing `/v1/runs`
  path without `q` (and with an empty `q`) is untouched: no semaphore, no timeout (behaviour-preserving for SDK clients).
- **D9 Investigation view = results + "why it matched" + facets + "not yet indexed".** Each `RunOut` in a page with terms carries `matched`
  (the subset of the run's `tools`, `models`, `event_types`, `agents`, `tags` that the query's terms named, plus the truncation flags per D5-f).
  `facets` = counts of `status`, `agents`, `tools`, `models`, `event_types` over the newest 1,000 matching runs. `query.unindexed` = how many
  runs in the request's scope (workspace, project, legacy time filters) have no `run_search` row, capped at 1,000 (`"1000+"`), computed only
  when the query needs the index; the UI shows "N runs are not yet indexed; `search-rebuild` adds them" (review F3/F8). When any index field
  or text term is present the statement uses an **inner join**: an unindexed run (queued, or summarised before this phase) matches neither a
  positive nor a negated index term; a query with only run-row fields does not join and sees every run. This asymmetry is documented in
  `api-v1.md` and pinned by AC-3. Clicking a facet appends a term. No per-row content in the list.
- **D10 (user decision 2) Saved searches are out.** A bookmark of `?q=` (with relative times, D3) is the saved search; Phase 17's alert rule
  table is where persistence belongs.
- **D11 (user decision 3) Natural language: interface and a deterministic fake only.** `search/natural.py`: `class QueryCompiler(Protocol):
  def compile(self, text: str, *, fields: FieldCatalogue) -> str` returns a **grammar string** (never SQL, never an AST with hidden fields), which
  then goes through the same parser and the same permission gate as a typed query (§77.2). `RuleBasedCompiler` maps a small phrase table
  ("failed" -> `status:failed`, "more than $N" -> `cost:>N`, "retried more than N times" -> `retries:>N`, "last week" -> `started:>=-7d`,
  "touching X" -> `file:X` which the gate then refuses for a viewer). No endpoint, no setting, no UI, no dependency.
- **D12 Field catalogue (data in `search/fields.py`; the test holds a second literal copy, like the matrix).** Null semantics: a null column
  value never matches, negated or not (`-duration:>60s` does not return running runs; `-name:x` does not return unnamed runs).

  | Field | Kind | Storage | Needs `payload.read` | Semantics |
  | --- | --- | --- | --- | --- |
  | `status` | enum (`RUN_STATUSES`) | `runs.status` | no | equals (case-insensitive) |
  | `agent` | slug | `run_search.agents` | no | contains (any agent of the run, incl. Phase 9 children); `*` prefix |
  | `root_agent` | slug | `runs.agent_slug` | no | equals; `*` prefix |
  | `name` | text | `runs.name` | no | case-insensitive substring (escaped `ILIKE`) |
  | `cost` | decimal USD | `runs.cost_usd` | no | `>=`, `<=`, `>`, `<`, equals |
  | `retries` | int | `runs.retry_count` | no | comparisons |
  | `errors` | int | `run_search.error_count` | no | comparisons; `severity:error` is an alias of `errors:>0` (other values `422`) |
  | `duration` | number + unit (`ms`, `s`, `m`, `h`) | `runs.duration_ms` | no | comparisons |
  | `started` | date, RFC 3339, or `-<n><m|h|d|w>` | `runs.started_at` | no | comparisons; equals on a date = that UTC day |
  | `event` | event type (`EVENT_TYPE_PATTERN`) | `run_search.event_types` | no | contains; `*` prefix (`event:shell.*`) |
  | `tool`, `model`, `provider`, `tag` | token | arrays | no | contains; `*` prefix |
  | `security` | `true|false` | `run_search.security_events` | no | `> 0` / `= 0` (events of class SECURITY) |
  | `file` | glob | `run_search.file_paths` | **yes** | glob per D6 (`src/auth/**`, `*.ts`, `/etc/passwd`) |
  | `command` | text | `run_search.commands` | **yes** | case-insensitive substring over command lines |
  | free text | words/phrase | `metadata_doc` (+ `content_doc` with `payload.read`) | content part only | all lexemes present; phrase = adjacent; `*` on the last lexeme |

  Reserved, not implemented: `finding:` (Phase 10 follow-up: a join on `run_findings` by `(workspace_id, run_id)`, metadata by construction; not
  indexed here because a different job writes it at a different time), `verdict:` (Phase 11 evaluations). Not fields (deliberately): `project`
  (a query parameter resolved by `authorise_project`), `url`/`repo`/`branch` (free text for content holders), `operation` (free text over
  `db.operation`/`tool.operation`/`http.method`, which are indexed in `metadata_doc` so "all database write operations" is `event:db.query.*
  write`), `tokens`, `llm_calls` (one catalogue row each when asked).

## Proposed design

### API (`apps/api/src/abb_api/search/`, new package; `runs/` edits are small)

- `tokens.py`, `tsliteral.py` (`tsvector_literal(positions) -> str`, `tsquery_literal(lexemes, phrase, prefix) -> str`, with a small literal
  parser used only by tests for round trips), `ast.py`, `fields.py`, `parser.py` (`parse(q, clock) -> Query`, raises `QueryError(position,
  reason)`), `compiler.py` (`compile(query, principal, *, scope) -> CompiledSearch(predicates, needs_index, content_fields, matched_columns,
  facet_columns)`), `document.py` (`build_document(...) -> SearchDocument` with `hash`), `repository.py` (`RunSearchRepository.upsert_if_changed(
  run_id, project_id, started_at, version, document) -> bool`), `reads.py` (`SearchBudget`: semaphore + wall clock + `bounded_statement(conn,
  remaining)`), `natural.py`, `errors.py` (`query_invalid`, `search_timeout`, `search_busy`; **no** permission error here, review F12).
- `abb_api/content.py` (new, neutral): `is_content_attribute`, `span_name_is_content` and `WITHHELD` move here; `runs/content.py` re-exports
  them (one-line change) so `search` never imports `runs` (dependency direction, review F23).
- `runs/queries.py::RunQueries.page` gains `search: CompiledSearch | None`: when `needs_index`, inner join `run_search` on
  `(workspace_id, run_id)`, order by the joined table's `started_at, run_id` (its index), select the matched columns; keyset unchanged.
  `facets_and_unindexed(search, scope)` is the one CTE statement. `summary_states` is called with its default argument (Phase 10 adds `job_type`).
- `runs/service.py::list_runs(..., q: str | None, request: Request | None)`: when `q` is not None: parse (422), compile with the principal,
  `authorize_audited(request, principal, PAYLOAD_READ)` if `content_fields` (403 with `details.field`), then if the query has no term: the
  `q`-less path plus `query` (catalogue, `content_allowed`, `unindexed = null`); else `SearchBudget` -> page (+matched in Python) -> facets if
  budget remains. The `q` branch sits **after** Phase 10's findings-badge lookup in the function body, so the rebase is a block insert.
- `runs/schemas.py`: `RunOut.matched: MatchedOut | None = None` (after Phase 10's `findings`), `RunPage.query: QueryOut | None = None`,
  `RunPage.facets: FacetsOut | None = None`, `RunPage.facets_skipped: str | None = None` (all additive).
- `jobs/handlers.py::summarize_run`: after `apply_derivation` and the cost lines, `await RunSearchRepository(conn, tenant).upsert_if_changed(...)`
  with the document built from the same `events` list and the derivation's agent list; then Phase 10's enqueue (rebase), then the analytics
  refresh. `runs/summary.py`: `SUMMARY_VERSION = 4` after rebasing on Phase 9 (3); `tests/test_run_summary.py:199` updated in the same commit.
- Settings: `search_timeout_seconds: float = 5.0`, `search_max_concurrent: int = 4` (`core/config.py`).
- CLI: `search-rebuild --workspace W [--project P] [--since T] [--limit N<=10000]` enqueues `summarize_run` for runs without a `run_search` row
  (or below the version), in batches of 1,000, audited as `search.rebuild` with actor `cli` (the `rebuild-costs`/Phase 10 `rebuild-findings`
  pattern; review F24). It re-summarises, so cost lines are rewritten and analytics refreshes queued: documented as a one-off cost (KI-081);
  skipping those writes when a derivation hash is unchanged is a follow-up for the summarizer, not this phase.
- Docs: `docs/architecture/api-v1.md` "Runs" gains a "Search" subsection with the grammar, the catalogue with three glob examples, relative
  times, `%2B` for offsets, the inner-join rule, the permission rule and the error codes.

### Migration 0080 (`0080_run_search.py`; `down_revision = "0047"` now, `"0060"` after rebasing on Phase 10)

Creates `run_search` and its five indexes; `downgrade` drops the table. No data migration. `tests/test_migrations.py` revision list gains
`"0080"` (after `"0060"` once rebased). This phase does **not** add a `0047 -> head -> 0047` test (Phase 11 adds it). `db/tables.py` gains
the table (append; the drift test keeps them equal).

### Web (`apps/web`)

- `lib/search.ts`: `qFromFilters`/`termsToFilters` so the status chips and agent input write terms into `q`; `range` stays a URL parameter
  beside `q` and becomes `started:>=-24h`-style terms at request time (review F11; relative times are in the grammar); `removeTerm(q, span)`;
  `appendTerm(q, term)`; `encodeURIComponent` on `q`.
- `components/SearchBox.tsx`: input with submit on Enter, inline `422` error with the caret position, `403` message naming the field ("`file:`
  needs the content permission"; the value is never echoed), `414` from the proxy shown like a `422` without a caret, `503 SEARCH_BUSY|TIMEOUT`
  with a retry button, a help popover listing `query.fields` (content fields hidden when `content_allowed` is false).
- `components/QueryChips.tsx`, `components/Facets.tsx`, `components/UnindexedNotice.tsx` ("N runs are not yet indexed"), `RunsTable.tsx` gains
  a "Matched" column when any row has `matched`; "index truncated" badge from the flags the API returned.
- `RunsListPage.tsx`: `q` and `range` in the URL; the old `status|agent` parameters are converted to `q` once on load. `queries.ts::useRuns`
  sends `q` only when it has at least one term or the help needs the catalogue (first load); the poll refetch of a termless page is the
  `q`-less request.
- Fixtures: `src/fixtures/runs.ts` answers `q` with a canned echo for a fixed set of queries. One `.facets`/`.unindexed` rule appended to
  `globals.css` in the last commit of the step.
- Tests: vitest for `search.ts` and the components and page states (parse error, permission error, 414, busy, timeout, empty, truncated,
  unindexed); Playwright `e2e/search.spec.ts` run by `scripts/auth-e2e.sh` (owner finds the content canary by `command:`; viewer sees the
  permission message for `file:` and the same empty page for `curl` as for a nonsense word; screenshots saved).

### Affected files

- New: `apps/api/src/abb_api/search/{__init__,tokens,tsliteral,ast,fields,parser,compiler,document,repository,reads,natural,errors}.py`;
  `apps/api/src/abb_api/content.py`; `apps/api/migrations/versions/0080_run_search.py`; `apps/api/tests/search/{test_tokens,test_tsliteral,
  test_parser,test_compiler,test_document,test_search_api,test_search_oracle,test_search_hostile,test_search_budget,test_natural}.py`;
  `apps/web/src/lib/search.ts`, `components/{SearchBox,QueryChips,Facets,UnindexedNotice}.tsx`, `tests/search.test.tsx`, `e2e/search.spec.ts`;
  `docs/decisions/ADR-100-*.md`, `ADR-101-*.md`; `docs/benchmarks/phase-16-search.md`; `scripts/bench_search.py`.
- Edited: `runs/{queries,service,schemas,router,content}.py`, `runs/summary.py` (`SUMMARY_VERSION`), `tests/test_run_summary.py` (the pin at
  `:199`), `jobs/handlers.py` (one call), `core/config.py`, `cli.py` (+ `tests/test_cli.py`), `db/tables.py` (append), `tests/test_migrations.py`
  (one list entry), `tests/authz/{test_content_withheld,test_cross_workspace}.py` (search probes in the existing loops), `tests/test_runs_api.py`
  (the `q`-less behaviour pinned), `apps/web/src/components/{RunsList,RunsListPage,RunsTable}.tsx`, `lib/queries.ts`, `src/fixtures/runs.ts`,
  `app/globals.css` (append), `scripts/auth-e2e.sh` (one line), `docs/architecture/api-v1.md`, `docs/TESTING.md`, `docs/runbooks/` (one note),
  `docs/KNOWN_ISSUES.md`, `docs/DECISIONS.md`, `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md`, `README.md` (one line), and the
  generated `apps/api/openapi.json` + `apps/web/src/lib/api/schema.d.ts`.
- Not touched: `main.py`, `authz/*`, `tests/authz/registry.py`, `tests/authz/test_registry_is_complete.py`, `tests/test_openapi.py`,
  `apps/web/src/server/upstream.ts`, `AppShell.tsx`, `Makefile`, `scripts/quality.sh`, `.github/workflows/ci.yml`, `packages/*`,
  `integrations/*` (under decision 1's recommendation).

## Acceptance criteria (each command-checkable; run from `apps/api` with `uv run pytest` unless stated)

- **AC-1 Grammar.** `tests/search/test_parser.py`: every example in §152 and §34 (`status:failed`, `cost:>1.00`, `retries:>=3`,
  `model:"provider/model"`, `tool:github`, `file:"src/auth/**"`, `event:shell.command.completed`, `severity:error`, `security:true`,
  `started:>=2026-10-01`, `started:>=-24h`, `duration:>60s`, `model:"claude*"`) parses to the expected AST with spans; negation incl. `--force`,
  `-"--force"`, `cost:>-0`; phrases, escapes, prefixes; every limit in D3 yields `QueryError` with the right position; a term with no lexemes is
  `QueryError("no searchable characters")`; a seeded in-test fuzzer (10,000 random strings from a hostile alphabet, no new dependency) never
  raises anything but `QueryError` and never exceeds 10 ms per parse.
- **AC-2 Literals and compiler gate.** `tests/search/test_tsliteral.py` (no database): serialiser escaping (`'`, `\`, spaces, `:`), position
  and lexeme bounds, same-position segment expansion, round trip through the test's literal parser, and the property "every document token
  matches itself as a query, as a prefix of itself, and as a phrase with its neighbour". `tests/search/test_compiler.py`: for a principal
  without `payload.read`, `file:`/`command:` are reported as `content_fields` and no SQL is built; for every query in a corpus (fields x text x
  negation), the compiled SQL (`str(stmt.compile(dialect=postgresql.dialect()))`) contains none of `content_doc`, `file_paths`, `commands`,
  `content_truncated`; with `payload.read` the same corpus does reference `content_doc` for text terms; every statement binds `runs.workspace_id`;
  no statement contains a literal from the query (user strings only as parameters); `CAST(... AS tsquery)` appears and `to_tsquery` never does.
- **AC-3 Semantics on Postgres.** `tests/search/test_search_api.py` (real database): one test pins the tsvector/tsquery input syntax
  (`CAST('''a'':1 ''b'':2' AS tsvector) @@ CAST('''a'' <-> ''b''' AS tsquery)` is true; `'src/au':*` matches `'src/auth.ts':7`); seeded runs
  cover every field in D12; each field, operator, negation, prefix, glob (the three D6 examples), phrase (`"npm test"` matches `npm test --
  src/auth.test.ts`, not `npm run test`), relative time and a 6-term combination return exactly the expected run ids; nulls never match negated
  or not; a queued run and an un-backfilled run are absent from any query with an index term (positive or negated) and present for run-row
  queries, and `query.unindexed` counts them; the keyset cursor pages a 120-run result set without gaps or repeats while new matching runs
  arrive; `q` ANDs with legacy `status`/`agent_id`/`started_after`.
- **AC-4 Oracle (viewer).** `tests/search/test_search_oracle.py` using `tests/authz/world.py`: for `viewer` and `downgraded`: (a) `q=file:x`,
  `q=command:x`, `q=-file:x`, `q=FILE:x` -> `403 PERMISSION_DENIED`, `details.field == "file"|"command"`, same body for an existing and a
  nonexistent path, and no statement mentioning `runs` or `run_search` was executed (statement listener as in `test_runs_api.py:500-523`;
  authentication's statements excluded), and a `permission denied` log line plus an audit row (`authorize_audited`); (b) free text `CMDCANARY`,
  `s3cr3t`, `PATHCANARY`, `id_rsa`, `CWDCANARY`, `CUSTOMCANARY`, `ｃｕｒｌ`, each with and without `*`, each negated, each as a phrase, return the
  same `items`, `facets` and `next_cursor` as `q=NOSUCHTERM-x9` (resp. its negation); (c) `owner` gets acme's first run for each of them;
  (d) `billing` gets `403` for a valid and for a malformed `q` (never `422`); (e) no facet key or value, no `matched` value and no
  `content_truncated` key appears in any response to an actor in `WITHHELD_FROM`, while `metadata_truncated` may; (f) the registry loop in
  `test_content_withheld.py` is extended with `?q=<canary>` variants of `GET /v1/runs`.
- **AC-5 Tenancy.** `tests/authz/test_cross_workspace.py` gains `GET /v1/runs?q=CANARY-GLOBEX-7f3e`, `q=tool:canary-globex-7f3e-tool`,
  `q=model:canary-globex-7f3e`, `q=agent:canary-globex-7f3e`, `q=name:CANARY` for every acme actor: empty items, empty facets, `assert_clean`
  passes; a dual member with `X-ABB-Workspace: acme` searching globex's run name finds nothing; a cursor from an acme search replayed under
  the globex header is `400 CURSOR_INVALID` or an empty page with no acme row; `test_registry_is_complete.py` and `test_openapi.py` are
  unchanged by this phase and green.
- **AC-6 Hostile input.** `tests/search/test_search_hostile.py`: 513 and 10,000-char `q`, 17 terms, 9 text terms, a 257-char value, NUL, `\x1f`,
  unpaired surrogate (`%ED%A0%80`), unbalanced quotes, `cost:>1e400`, `cost:>NaN`, `retries:>=9223372036854775808`, `started:9999-99-99`,
  `started:>=-99999999d`, `started:>=0001-01-01T00:00:00%2B14:00`, `duration:>1y`, `event:../../etc`, `status:drop`, `file:` with 9 wildcards
  (owner), `name:%`, `name:_`, `name:\`, `'`, `;--`, `!!!`, `@@`, tsquery operators `& | ! ( ) : * <->` as text, RTL and combining marks,
  emoji: each is `422 QUERY_INVALID` or a literal (escaped) match, never `500`; the API log has no ERROR line; `name:%` matches only a name
  containing `%`.
- **AC-7 Cost bounds.** `tests/search/test_search_budget.py`: with `search_timeout_seconds=0.05` and a `pg_sleep` work function
  (`test_analytics_api.py:243` technique against `reads.py`), `503 SEARCH_TIMEOUT` `retryable: true`; with `search_max_concurrent=2`, three
  concurrent searches blocked on `pg_sleep` give two `SEARCH_TIMEOUT` and one `SEARCH_BUSY` while a `POST /v1/events` sent during the flood is
  `202` and `pg_stat_activity` shows no search backend afterwards; a request executes at most two statements mentioning `run_search`
  (listener); `q=` (empty) executes exactly the statements of the `q`-less request; facets are at most 10 values per key over at most 1,000
  runs (1,200 seeded matches: counts sum to <= 1,000); `facets_skipped: "budget"` when the page used more than 60% of the budget.
- **AC-8 Derived and rebuildable (INV-2).** `tests/search/test_document.py`: `build_document` is order-independent, bounded (2,001 distinct
  tools -> 2,000 and `metadata_truncated`; 9,000 content tokens -> 8,000 positions and `content_truncated` only), indexes the `span.name`
  attribute and never the derived span name (a shell span named by `CMDCANARY` leaves `metadata_doc` clean); `upsert_if_changed` returns
  `False` and writes nothing on an unchanged hash; `test_search_api.py`: delete the `run_search` row, run `search-rebuild`, the row is back
  and equal, audited `search.rebuild`; `SUMMARY_VERSION` equals the plan's value (4 after rebase; the test in `test_run_summary.py` is the pin).
- **AC-9 Migration.** `scripts/quality.sh full`: `0080` upgrades, downgrades and upgrades again from its parent; `test_migrations.py` lists it
  in chain order; the drift test passes.
- **AC-10 Web.** `pnpm --filter @abb/web test`: chips render from `query.terms`, removing a chip rewrites `q` by span, the legacy URL
  `?status=FAILED&agent=x&range=24h` converts to `q` + `range`, parse error shows the caret, `403` shows the field message without the value,
  `414` shows like `422`, busy/timeout show retry, the unindexed notice renders, viewer help omits content fields; `make auth-e2e` runs
  `e2e/search.spec.ts`: owner searches `command:curl` and sees the run, viewer searches `curl` and sees the same "No runs match" page as for a
  nonsense word, viewer typing `file:x` sees the permission message; screenshots in `docs/screenshots/phase-16-*.png`; `pnpm --filter @abb/web
  build` green.
- **AC-11 Ingest and summarizer cost (D7 trigger).** Interleaved A/B in one session on the Colima VM: the `main` image and the branch image,
  three `make seed && make bench` runs each, alternating; compare medians of p95 (1,000-event runs) and of summary freshness (2,000 and 10,000
  events); record the observed noise band; after the 30,000-event run record `pg_stat_user_tables.n_dead_tup` for `run_search` and the GIN index
  sizes. Written to `docs/benchmarks/phase-16-search.md` with the method. The split (2b) is done in this phase only if the regression exceeds
  both the band and the D7 thresholds. `UNVERIFIED (env)` if Colima cannot run here; the gate stays open.
- **AC-12 Search latency (D6 triggers).** `scripts/bench_search.py` (reuses `analytics_seed.py` to seed 100,000 runs with documents, then 200
  queries of each class: run-row only, array contains, metadata text, content text, `command:` substring, `file:` glob, each with facets): p95
  per class recorded; metadata text and array classes < 500 ms, content substring < 1 s, else the trigram index (0081) or the fallback is
  scheduled and an ADR written. Also recorded: `run_search` size and GIN index sizes per 100,000 runs.
- **AC-13 Natural language fake.** `tests/search/test_natural.py`: the rule-based compiler's output for each phrase in D11 parses, compiles for
  an owner, and is refused for a viewer when it names a content field; `QueryCompiler` is the only public name; `rg -n "anthropic|openai"
  apps/api/src/abb_api/search` finds nothing.
- **AC-14 Quality gate.** `scripts/quality.sh full` exit 0; `python -m abb_api.openapi --check`; `make web-client` leaves no diff; all CI jobs
  green on the PR.

## Verification plan

1. Per step: focused tests, then `scripts/quality.sh quick`; `full` before each commit; `make auth-e2e` at steps 7-8 (quality.sh does not run E2E).
2. Mutation checks during review (each must fail at least one test): build the tsvector with `to_tsvector('simple', ...)` instead of the
   literal (the `foo_b*` prefix and the `"npm test"` phrase tests fail); drop the `payload.read` report in the compiler; let the viewer branch
   add `content_doc`; compute facets over `file_paths`; return `content_truncated` to a viewer when only content was truncated; index the
   derived `Span.name` (AC-4b fails on `CMDCANARY`); return a `total` count; order by `ts_rank`; drop `workspace_id` from the join; format a
   pattern into SQL instead of binding; remove the `statement_timeout`; remove the semaphore (the flood test fails); compute facets for an
   empty `q` (the statement-count test fails); construct the 403 without `authorize_audited` (the denial-log test fails); raise the term limit;
   skip NFKC in the query tokeniser only; apply `*` to every lexeme of a text term.
3. Browser: `make auth-e2e` plus a manual pass on `make dev` with the demo (owner and viewer), screenshots kept.
4. Benchmarks: AC-11 (A/B, interleaved) and AC-12 on the Colima VM used for Phase 7; results and caveats written as in `phase-7-regression-check.md`.
5. Environment: Docker/Colima, Postgres 5433, Node 22, Chrome for Playwright (setup.md); step 1 starts with `uv sync` and `pnpm install`. Only
   AC-11/AC-12 can end `UNVERIFIED (env)` (VM busy or absent), and then D6/D7 keep their fallbacks open as KI items.

## Security review note (mandatory adversarial pass; what to attack, expected result)

1. **Oracle by match.** As VIEWER: free text, prefix, phrase and negated forms of every content canary (AC-4b); `name:` with a canary (name is
   metadata: matches only if the name holds it); a field the catalogue does not list (`422`, not a hidden content field); mixed case and NFKC
   confusables (`ｃｕｒｌ`) of a canary; a term that is a substring of a path segment (`id_rsa`, `ssh`); `event:` with a content-looking value (`422`
   by pattern). Expected: identical `items`, `facets`, `next_cursor` to a nonsense term.
2. **Oracle by count, order or flag.** Facet counts with and without a content word; `next_cursor` presence; item order; `unindexed`;
   `metadata_truncated` vs `content_truncated` (the latter never present for a viewer); response size.
3. **Oracle by error.** `403` for `file:` must not depend on whether a match exists (same body for `file:/home/PATHCANARY*` and `file:/nothing`);
   `details.field` is the canonical name, never the typed spelling; `422` positions must not depend on data.
4. **Downgrade and keys.** An OWNER demoted to VIEWER mid-session (`downgraded`), a `runs:read` key (content allowed, ADR-061), an
   `events:write`-only key (`403 INSUFFICIENT_SCOPE run.read`), BILLING (`403` before parsing, for a malformed `q` too).
5. **Tenant.** Every AC-5 probe; a cursor from an acme search replayed with `X-ABB-Workspace: globex` by a dual member; `project_id` of the
   other workspace with `q` (404 as today).
6. **Cost and starvation.** `ABB_SEARCH_MAX_CONCURRENT + 1` concurrent slow searches (`name:%a%b%c%d%e%f%g%h%` by a VIEWER against 100,000
   runs): N end as `SEARCH_TIMEOUT`, one is `SEARCH_BUSY` immediately, a `POST /v1/events` and a `GET /v1/runs` (no `q`) sent during the flood
   succeed, `pg_stat_activity` shows no search backend after the budget, every connection is back in the pool; a 2,048-char encoded URL through
   the proxy with a 512-char `q` (`414` from the proxy, handled by the UI; `422` from the API when sent directly).
7. **Injection.** Every string in AC-6 through the compiler with `literal_binds` inspection; tsquery syntax in free text (literal lexemes,
   never operators); glob to LIKE escaping (`\` first, then `%`, `_`); `ILIKE` with `name:` containing `\`; a lexeme containing `'` or `\` in the
   tsvector literal.
8. **Index content.** `run_search.content_doc`/`commands`/`file_paths` hold commands and paths: confirm the table is reachable by no route
   except through the compiler, that `test_runtime_role` treats it as writable derived state, that the runbook's retention note lists it, that
   the audit log records `search.rebuild` but never `q`, and that no API log line contains a canary after a search (log capture).
9. **Web.** `q` reaches the URL only through `encodeURIComponent`; the help popover for a viewer never lists `file`/`command`; the `403`
   message does not echo the value; chips render text, never HTML; `Referrer-Policy: no-referrer` is present on the runs page.

## Risks

1. **Oracle subtlety.** The split is only as good as the attribute classification (KI-078). Mitigation: fail-closed catalogue, AC-2's SQL
   assertion, AC-4's identical-items check incl. flags, the mutation checks, and no counts or ranking by design (D5-e).
2. **Summarizer cost and the KI-016 tail.** Mitigation: bounded document, hash-skipped upsert, A/B bench with dead-tuple and GIN figures (AC-11),
   a written trigger and a ready fallback (2b).
3. **Text-search semantics.** Postgres' own parser must never touch our lexemes. Mitigation: literals only (`CAST`), a DB-less serialiser test,
   one syntax-pinning Postgres test, the self-match property, and the `to_tsvector` mutation check.
4. **Backfill.** Existing runs are unsearchable until re-summarised. Mitigation: `unindexed` notice in the UI, `search-rebuild` (bounded, audited),
   KI-081; the inner-join asymmetry is documented and pinned.
5. **Merge conflicts with Phases 9-11** on `jobs/handlers.py`, `runs/summary.py` (`SUMMARY_VERSION`), `runs/{queries,service,schemas}.py`,
   `cli.py`, `openapi.json`, `schema.d.ts`, `test_migrations.py`, the docs indexes. Mitigation: the "Merge notes" below (block inserts after
   Phase 10's code, version 4, chain 0060 -> 0080); generated artifacts regenerated, never merged.
6. **Query UX.** Mitigation: chips from the server parse, quick-add controls stay, help popover, exact error positions, relative times.
7. **Facet cost** on broad queries. Mitigation: the 1,000-row CTE, the budget (facets skipped first), the cap.
8. **Pool starvation by search.** Mitigation: the semaphore is acquired before a connection; AC-7 and security item 6 prove ingest survives.

## Ordered steps (one commit each; `scripts/quality.sh full` green after every step; generated and append-only artifacts in the last commit of a step)

1. `feat(search): tokeniser, tsvector/tsquery literal serialisers, AST, field catalogue and parser for the §152 grammar` - `search/{tokens,
   tsliteral,ast,fields,parser,errors}.py`, `abb_api/content.py` (+ the `runs/content.py` re-export), AC-1 and the DB-less half of AC-2; starts
   with `uv sync`/`pnpm install` and records the two UNVERIFIED (env) items as verified.
2. `feat(db): migration 0080 run_search; document builder; summarizer writes the document when changed; search-rebuild CLI; SUMMARY_VERSION
   bump` - `document.py`, `repository.py`, `db/tables.py`, `test_migrations.py` entry, `runs/summary.py` + `tests/test_run_summary.py` pin,
   `jobs/handlers.py` (one call, before Phase 10's enqueue), `cli.py` + `tests/test_cli.py`, AC-8, AC-9 (review F4: the table exists before the
   compiler needs it). **2b (only if AC-11 trips at step 9):** `feat(jobs): index_run job`.
3. `feat(search): compiler with the content gate; bounded reads; RunQueries.page accepts a compiled search` - `compiler.py`, `reads.py`,
   `runs/queries.py` (`search=` on `page`, `facets_and_unindexed`), the rest of AC-2, the AC-3 syntax-pinning test; no route change yet.
4. `feat(runs): GET /v1/runs?q= with query echo, matched, facets, unindexed; QUERY_INVALID, PERMISSION_DENIED(field) via authorize_audited,
   SEARCH_TIMEOUT, SEARCH_BUSY` - `service.py`, `schemas.py`, `router.py`, `config.py`; AC-3, AC-6, AC-7 tests; `test_runs_api.py` pins the
   `q`-less path; **last commit of the step regenerates** `openapi.json` and `schema.d.ts` (`make openapi web-client`).
5. `test(authz): search oracle and cross-workspace probes` - AC-4, AC-5 (extends `test_content_withheld.py`, `test_cross_workspace.py`,
   `tests/search/test_search_oracle.py`); registry and OpenAPI counts asserted unchanged.
6. `feat(search): QueryCompiler interface and rule-based fake` - `natural.py`, AC-13.
7. `feat(web): search box, chips from the server parse, legacy filter URLs converted to q + range, fixtures` - `lib/search.ts`, `SearchBox`,
   `QueryChips`, `RunsListPage`/`RunsList`/`queries.ts`, fixture echo, vitest (AC-10 unit part).
8. `feat(web): investigation view: matched column, facet strip, unindexed notice; Playwright search.spec in auth-e2e; screenshots` -
   `Facets.tsx`, `UnindexedNotice.tsx`, `RunsTable`, `e2e/search.spec.ts`, `scripts/auth-e2e.sh` (one line), `docs/screenshots/`; `globals.css`
   appended in the last commit.
9. `docs(phase-16): ADR-100/101, api-v1 search section, TESTING, benchmarks (AC-11 A/B, AC-12), runbook note (FK lock, search-rebuild,
   retention), KNOWN_ISSUES KI-080..083, DECISIONS, PROJECT_STATE, IMPLEMENTATION_PLAN, README line` - `scripts/bench_search.py` lands here with
   its results; then `complete-phase`.

## Merge notes (merge order 9, 10, 16, 11; this phase rebases on 9 and 10 and is rebased on by 11)

- **Migration chain.** This phase adds exactly one revision, `0080`. On rebase after Phase 10: `0080.down_revision = "0060"` and `"0080"` is
  inserted after `"0060"` in `tests/test_migrations.py` (the list is chain order, not number order); the file is not renamed. Phase 11 then
  rebases `0070` onto `0080`. This phase adds **no** `0047 -> head -> 0047` test (Phase 11 adds it; two tests of one name would conflict).
- **`SUMMARY_VERSION`.** Phase 9 lands 3; this phase sets **4** on rebase (merged value + 1, never "take the higher"), updating
  `tests/test_run_summary.py:199`, KI-081 (`>= 4`) and the runbook in the same commit. `analytics/rollup.py`'s `unrebuilt_runs` then counts
  Phase 9-era runs (3) as unrebuilt, which is right: they have no document. The operational signal is the `run_search` row (`query.unindexed`),
  not the constant.
- **`jobs/handlers.py::summarize_run`.** Phase 10 enqueues `detect_findings` after `apply_derivation`; this phase's `run_search` upsert goes
  **before** that enqueue (both additive). Phase 11 adds a handler, not a hook.
- **`runs/queries.py`, `runs/service.py`.** Phase 10 changes `summary_states(job_type=...)` and adds the findings-badge lookup in `list_runs`;
  this phase calls `summary_states` with the default and places the `q` branch **after** the badge lookup (block insert on rebase).
- **`runs/schemas.py`.** Phase 10 appends `RunOut.findings`; this phase appends `RunOut.matched`, `RunPage.query`, `RunPage.facets`,
  `RunPage.facets_skipped` after it. `openapi.json` and `schema.d.ts` are regenerated after the rebase, never merged.
- **Route and operation counts.** Asserted "unchanged by this phase" (32/37 after Phase 10 under decision 1's recommendation).
- **Field catalogue.** `agent:` is "contains in `run_search.agents`" built from the derivation's agent list (Phase 9's `summary["agents"]`
  source; the same list, not a second walk); `root_agent:` is the run row. `finding:` (Phase 10) and `verdict:` (Phase 11) are reserved names,
  not implemented, and findings are not indexed here (different job, would be stale).
- **`cli.py`.** Phase 10 adds `rebuild-findings`; this phase adds `search-rebuild` after it (additive).
- **Append-only files in the last commit:** `globals.css`, `db/tables.py` (append the table), `docs/KNOWN_ISSUES.md` (KI-080..083; Phases 9-11
  use 084..086, 090..099, 100..109), `docs/DECISIONS.md` (ADR-100, 101), `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md` (status cell),
  `README.md` (one line).
- **Hot files not touched:** `main.py`, `authz/matrix.py`, `authz/actions.py`, `tests/authz/registry.py`, `tests/authz/test_registry_is_complete.py`,
  `tests/test_openapi.py`, `apps/web/src/server/upstream.ts`, `AppShell.tsx`, `Makefile`, `scripts/quality.sh`, `.github/workflows/ci.yml`.
  (Under decision 1's alternative, the registry, both counts and `upstream.ts` change in step 4's last commit.)

## Review dispositions (independent review d7f805f; 29 findings + 8 cross-plan items)

| Finding | Sev | Disposition | Where it landed |
| --- | --- | --- | --- |
| F1 Postgres re-parses pre-tokenised text | P1 | Accepted (re-checked against the Postgres parser's documented behaviour; UNVERIFIED (env) here) | Current architecture (corrected paragraph), D4 (literals via `CAST`, same-position expansion, query side unexpanded, prefix on last lexeme), `tsliteral.py`, AC-2 (DB-less serialiser + property), AC-3 (syntax pin, phrase example), Risk 3, mutation check, ADR-100 |
| F2 Per-statement bound, shared pool, ingest starvation | P1 | Accepted (pool 5+10, `pool_timeout=5` verified) | D8 (semaphore `SEARCH_BUSY`, per-request budget, two statements, matched in page, facets CTE), AC-7, security item 6, Risk 8, KI-082 |
| F3 `SUMMARY_VERSION` collides with Phase 9 | P1 | Accepted (Phase 9 plan L199 = 3; pin at `test_run_summary.py:199`; `rollup.py:104` verified) | Merge notes (4 = merged + 1), KI-081 (`>= 4`), D9/`query.unindexed`, AC-3, AC-8, step 2 |
| F4 Step 2 compiles before the table exists | P1 | Accepted (drift test verified) | Steps 2 and 3 swapped; `tests/test_run_summary.py` in Affected files and step 2 |
| F5 Shared `truncated` flag leaks content volume | P2 | Accepted | D4 (two flags), D5-f, D9, AC-4e, AC-8, security item 2, mutation check, ADR-101 |
| F6 Facets/matched on every poll | P2 | Accepted | D2 (terms required), D8-d, AC-7 (empty `q` = `q`-less statements), Web (`useRuns` sends `q` only with terms), mutation check |
| F7 Glob semantics undefined | P2 | Accepted | D6 (anchoring, `/`-crossing, no-wildcard = substring, three examples), D12, AC-3 |
| F8 Inner join drops unindexed runs | P2 | Accepted (inner join kept; the count-and-banner option) | D9 (`unindexed`, documented asymmetry), AC-3 (queued + un-backfilled runs pinned), Web `UnindexedNotice`, Risk 4 |
| F9 Text term with no lexemes | P2 | Accepted | D3, AC-1, AC-6 (`!!!`, `@@`, operators) |
| F10 Bench trigger inside the noise band | P2 | Accepted | AC-11 (interleaved A/B, medians, observed band), D7, Verification 4 |
| F11 Legacy `range` frozen into bookmarks | P2 | Accepted (relative times added to the grammar; `range` stays a URL parameter) | D3 (`-<n><unit>`), D10, D12, Web, AC-1, KI-080 repurposed |
| F12 403 built outside `authorize()` | P2 | Accepted | D2, D5-a (`authorize_audited`, canonical `details.field`), `errors.py` has no permission error, AC-4a (log + audit row), mutation check |
| F13 `span.name` attribute vs derived name | P2 | Accepted (`spans.py:42-47` verified) | D4, AC-8, mutation check, ADR-101 |
| F14 `agent:` on the run row vs `agents` array | P2 | Accepted (cross-plan g) | D12 (`agent` contains, `root_agent` row), D4 (one source list), AC-5, Merge notes |
| F15 GIN write amplification on active runs | P2 | Accepted | D4 (`document_hash`, ORDER BY from the joined index), D7 (`upsert_if_changed`), AC-8, AC-11 (dead tuples, GIN sizes) |
| F16 `-` ambiguity | P3 | Accepted | D3, AC-1 |
| F17 `+` in offsets | P3 | Accepted | D3 (`Z`, `%2B`), AC-6, api-v1 note, Web `encodeURIComponent` |
| F18 "same body" vs the echo | P3 | Accepted | AC-4b (items, facets, next_cursor) |
| F19 Authentication statements in the counter | P3 | Accepted | AC-4a |
| F20 §10.10 examples not expressible | P3 | Accepted (indexed as metadata free text, no new field) | D4, D12 ("Not fields") |
| F21 Null semantics of negation | P3 | Accepted | D12 preamble, AC-3 |
| F22 Proxy 414 on a legal `q` | P3 | Accepted (UI handles 414 like 422; limit stays 512) | Web, AC-10, security item 6 |
| F23 `runs` and `search` import cycle | P3 | Accepted | `abb_api/content.py` (new neutral module), step 1 |
| F24 Backfill via `cost.rebuild` | P3 | Accepted (`search-rebuild` CLI; skipping unchanged cost writes noted as a separate summarizer follow-up) | Proposed design (CLI), KI-081, AC-8 |
| F25 FK lock during 0080 | P3 | Accepted | Known issues (KI-056 note), step 9 runbook |
| F26 Literal counts | P3 | Accepted | D2, AC-5, Merge notes ("unchanged by this phase") |
| F27 `q` in URLs and proxy logs | P3 | Accepted (`Referrer-Policy: no-referrer` already set, verified `next.config.ts:35`) | D5, security item 9, ADR-101 |
| F28 Prefix on an expanded token | P3 | Accepted (moot with same-position encoding; still stated) | D3, D4 (query side unexpanded, `*` on the last lexeme), mutation check |
| F29 `started:<date>` equals | P3 | Accepted | D3, D12 |
| Cross-plan 1 `SUMMARY_VERSION` 4 | - | Applied | Merge notes, step 2 |
| Cross-plan 2 chain `0060 -> 0080`, no 0047 round-trip test | - | Applied | Migration section, Merge notes |
| Cross-plan 3 upsert before `detect_findings` enqueue | - | Applied | D7, Proposed design, Merge notes |
| Cross-plan 4 `summary_states` default; `q` branch after the badge | - | Applied | Proposed design, Merge notes |
| Cross-plan 5 schema fields after `findings`; regenerate artifacts | - | Applied | Proposed design, Merge notes |
| Cross-plan 6 counts "unchanged" | - | Applied | D2, AC-5, Merge notes |
| Cross-plan 7 `agent:` contains; `finding:`/`verdict:` reserved; findings not indexed | - | Applied | D12, Non-goals, Merge notes |
| Cross-plan 8 append-only files last; KI-080..083 | - | Applied | Merge notes, Known issues |

Counts: 29 findings accepted (4 P1, 11 P2, 14 P3), 0 rejected, 0 deferred; 8 cross-plan items applied. No new decision for the user arose
from the review.

## Appendix: ADR drafts (written as files at step 9; these drafts are the plan's record)

### ADR-100: Search is a typed filter grammar compiled server-side over a derived per-run Postgres search document

Status: Proposed. Phase: 16.

**Context.** Spec §152 asks for a token grammar parsed into a typed AST compiled to storage queries so the UI never builds query strings; §77.1
wants structured search fast, explainable and permission-safe; §77.2 allows PostgreSQL full-text search for selected textual metadata and defers a
dedicated index until necessary; §57.2 defers ClickHouse-class stores to measured triggers. Runs already carry typed derived columns (ADR-043) and a
summarizer that rebuilds derived state from events (ADR-012, INV-2). Postgres' text-search functions always run their own parser, which splits
and classifies identifiers and paths differently from any tokeniser we control.

**Decision.** (1) One grammar (flat conjunction of `field:op?value` terms, free text, per-term negation, quoted phrases, trailing-`*` prefix on
the last lexeme, relative times `-<n><m|h|d|w>`), parsed by a hand-written scanner with hard limits (512 chars, 16 terms, 8 text terms, 256-char
values, 16 lexemes, 8 wildcards; a term with no lexemes is an error) into frozen AST nodes; the field catalogue is data. (2) The AST compiles to
SQLAlchemy predicates over `runs` and a derived table `run_search` written by the summarizer in the same transaction as the run row and only
when its SHA-256 changed: `text[]` arrays (`event_types`, `tools`, `models`, `providers`, `agents`, `tags`, `file_paths`, `commands`), two
`tsvector` documents (`metadata_doc`, `content_doc`), two truncation flags, `error_count`/`security_events`; GIN indexes on the documents and the
two most selective arrays; bounded (2,000 values per array, 8,000 positions per document). (3) The tsvector and tsquery are **literals built in
Python** from one tokeniser (NFKC, casefold; document side emits path and dotted-name segments at the parent token's position; query side
unexpanded) and bound with `CAST(:x AS tsvector|tsquery)`; `to_tsvector`/`to_tsquery` are never called, so Postgres never re-parses our lexemes
and phrase/prefix semantics are ours. (4) Results are newest first by the existing keyset cursor; no ranking, no total count. (5) Every search
request holds one of `ABB_SEARCH_MAX_CONCURRENT` (4) per-process permits before taking a connection (`503 SEARCH_BUSY`), runs at most two
read-only statements under one wall-clock budget (`ABB_SEARCH_TIMEOUT_SECONDS`, 5 s; `503 SEARCH_TIMEOUT`; facets skipped first); user strings
are bound parameters only. (6) `GET /v1/runs?q=` carries the grammar; the response echoes the parsed terms with spans, the catalogue, the
unindexed count and facets. A query with an index term uses an inner join and does not see unindexed runs; the count says how many there are;
`search-rebuild` re-summarises them. (7) Natural language implements `QueryCompiler.compile(text) -> grammar string` through the same parser and
gate; this phase ships only the protocol and a rule-based fake. (8) Triggers: `pg_trgm` GIN when a substring/glob search p95 exceeds 1 s on the
measured dataset; a dedicated search service when metadata text p95 exceeds 1.5 s at Stage A scale with GIN in place or when ranking/typo
tolerance becomes a product need; the summarizer write moves to its own coalesced job when an interleaved A/B bench shows a regression beyond
the observed noise band and 10% on p95 (20% on freshness).

**Alternatives.** Querying `events.attributes` with a JSONB GIN (every search a scan of the largest table; withholding re-implemented in SQL).
Columns on `runs` (bloats the hot row). `to_tsvector('simple')` on pre-tokenised text (re-parsed: `foo_bar` splits, `src/auth.ts` becomes a
file token, phrases and prefixes break). Trigram everywhere (2-3x index size before a need is measured). An external index now (a second
system of record for Class 2 content with its own tenancy to prove). `OR`/grouping in v1. Relevance ranking (an ordering oracle, ADR-101).
Per-statement timeouts alone (seven statements per request could hold a pooled connection for 35 s and starve ingestion).

**Consequences.** Search is derived, rebuildable and tenant-keyed like everything else; runs summarised before `SUMMARY_VERSION` 4 need one
re-summarisation (bounded `search-rebuild`, which also rewrites cost lines); the summarizer does slightly more per changed run (measured A/B);
adding a field is one catalogue row, one document line and one test row; the UI has no grammar logic; search can never take more than four
of a process's fifteen connections.

### ADR-101: Search never reveals withheld content: permission-split documents, a compile-time gate, no counts, flags or ranking

Status: Proposed. Phase: 16. Amends ADR-061.

**Context.** ADR-061 made command text and file paths captured content, withheld at read time from actors without `payload.read`. A search
feature can leak the same content by match/no-match, by counts, by ordering, by derived flags (how much content there was), by error differences
and by timing: a VIEWER asking `command:"Bearer s3cr3t"` learns whether a secret exists in any run. Spec §77.1 requires permission-safe search;
§93 classifies paths and commands as Class 2.

**Decision.** (1) The rule: an actor may search a field, or receive a derived flag or count, only if the same actor receives that field's values
on the existing read routes. (2) The search document is split into a metadata document and a content document (and content arrays), each with
its own truncation flag, in separate columns; the catalogue marks `file` and `command` as content fields. (3) The compiler takes the `Principal`
and reports content fields before building SQL; the service refuses them through the one `authorize_audited(request, principal, payload.read)`
path (denial logged and audited like every other), with `details.field` = the canonical field name, independent of data; free text compiles to
the metadata document only unless `payload.read` is held; a statement for an actor without `payload.read` never references a content column or
the content truncation flag (asserted on the compiled SQL by a test). (4) No total count, no relevance ranking; facets only over metadata fields
and only for the actor's own compiled predicate; `content_truncated` is returned only with `payload.read`; the unindexed count is over the
run-row scope. (5) Error positions and bodies depend only on the query text, never on data. (6) Keys with `runs:read` hold `payload.read`
(ADR-061) and search content. (7) Indexed metadata is the `span.name` **attribute**, never the derived `Span.name` (which falls back to commands
and paths); unregistered attributes that are not content by name are not indexed at all (fail closed; KI-078's heuristic boundary is inherited,
not widened). (8) Search terms are the searcher's own input: they may sit in their browser history or a reverse proxy's log; the API logs no
query text and the web app sends `Referrer-Policy: no-referrer`. (9) Tests: identical items, facets and cursor for a viewer between a content
canary and a nonsense term in every syntactic form; a statement counter proving no run query runs for a refused field; canary scans of facets,
`matched` and flags; mutation checks that removing any of (3), (4) or (7) fails a test.

**Alternatives.** Filtering results after a content match (the count and the emptiness still leak). Returning `[withheld]` matches (tells the
viewer a secret exists). Hashing content terms (equality on a hash is still an oracle). One truncation flag (a volume bit about content).
Searching content for everyone and redacting later (Phase 13's redaction does not exist yet, and a redacted document is still an oracle for the
unredacted part).

**Consequences.** A VIEWER's search is strictly weaker than an OWNER's and the UI says so (the help lists only allowed fields; a refused field
names the permission, not the value). Server-side redaction (Phase 13) can later move individual attributes between the two documents by
changing the classification in one place (`abb_api/content.py`). A future ranking feature must rank on metadata only or be gated by
`payload.read` as a whole. Every denial of a content field is visible in the audit log like any other denial.

## Implementation notes (steps 1-3; deviations and choices, recorded as built)

- **Verified (was UNVERIFIED (env)).** `uv sync` works. Postgres 16 accepts the literal syntax of D4: `CAST(:d AS tsvector) @@ CAST(:q AS
  tsquery)` with quoted lexemes, `'it''s'`, doubled backslashes, `<->` and `:*` behaves as designed, `foo_bar` is not re-split, and
  same-position expansion is not adjacent (pinned by `test_search_api.py::test_postgres_accepts_the_literal_syntax_we_build`). Postgres keeps at most
  256 positions per lexeme and drops the rest silently (observed); with distinct values and the 8,000-position cap this is harmless. The
  asyncpg binding of the casts works (`CAST($1::VARCHAR AS TSVECTOR)`). `pnpm install` was not run (no web work in steps 1-3).
- **Normalisation.** Array values for `agents`, `tools`, `models`, `providers`, `tags` are stored NFKC + case-folded (`tokens.normalise`), and
  query values are normalised the same way, so those fields are case-insensitive; `matched` therefore shows the normalised spelling.
- **Tokeniser.** `.`, `-`, `/` are trimmed from both ends of a token (so `-force` and `--force` both tokenise to `force`); letters, marks,
  digits, `_ . - /` are word characters. Positions start at 1 and consecutive values are separated by one empty position; the 8,000 cap counts
  those gaps. Segment/part expansion is `tokens.expansions`.
- **Parser.** An unknown or reserved `name:` prefix is `422 unknown field` at the name (so `http://x` must be quoted); `<`/`>` are operators only on
  ordered fields (elsewhere they are part of the value); a relative time needs an operator; an RFC 3339 instant needs `Z` or an offset;
  accepted years are 1970-2999; `duration` needs a unit; integers are bounded to 2^31-1; a wildcard run (`**`) counts once toward the 8
  wildcard limit; `severity:error` is rewritten in the AST to `errors:>0`. Event prefixes keep the trailing dot (`event:shell.*` -> `shell.`).
- **Glob (D6).** The plan's "no wildcard = substring" and "`file:/etc/passwd` matches exactly" conflict; implemented: no wildcard and absolute
  path -> exact (case-insensitive); no wildcard otherwise -> substring; with wildcards -> end-anchored, start-anchored only if it begins with `/`.
- **Document.** `build_document(events, *, run_name, run_metadata, agents, error_count)`: `error_count` is the derivation's own figure (one rule
  for "error"). Values longer than 256 characters (4,096 for content) are dropped and set that half's truncation flag; arrays also stop at 64 KiB.
  The stored `document_hash` covers the document plus project, start time and `SUMMARY_VERSION`, so a version bump or a moved start rewrites the row.
- **`unindexed` and facets (D8/D9).** The unindexed count also applies the query's run-row-only predicates (`CompiledSearch.row_predicates`), so it
  counts runs that would otherwise have matched; a query without index terms left-joins `run_search` in the facets statement (the status facet
  counts every run, array facets count indexed runs) and reports no unindexed count.
- **Gate placement.** `SearchGate`/`SearchBudget` live in `search/reads.py`; the gate is created lazily per application in step 4 (so `main.py` stays
  untouched). `search_timeout_seconds` and `search_max_concurrent` (1-14) were added to `Settings` in step 3 because the budget tests need them.
- **Tables.** `run_search` uses the PostgreSQL `ARRAY` type (the generic one has no `contains`). `SUMMARY_VERSION` is 3 in this tree (comment in
  `runs/summary.py`); it becomes merged value + 1 on rebase.
- **Mutation checks run (each failed a test):** viewer text compiled against `content_doc`; content gate removed; `content_truncated` selected for
  everyone; tenant dropped from the join's ON (this one survived until the test asserted a bound parameter on the joined side; fixed).

### Merge of main (Phase 9) - done
- `SUMMARY_VERSION = 4` (Phase 9's 3 + 1); pin updated in `tests/test_run_summary.py`; KI-081 reads `>= 4` (plan text above).
- The search document's `agents` array now reads Phase 9's `summary["agents"]` (`search/document.py::summary_agents`, at most 50 entries plus
  `(other)`), replacing the earlier `distinct_agents(events)`: one source list for the run page and `agent:`.
- No 0047 round-trip test added; `openapi.json` and `schema.d.ts` unchanged by the merge (checks green).

### Implementation notes (steps 4-6)
- Wire shapes: `query` = `{terms[{kind,field,op,value,value_end,lexemes,phrase,prefix,negated,span}], fields, content_allowed, unindexed, unindexed_capped}`; `facets` = `{status,agents,tools,models,event_types}` of `{value,count}`; `matched` omits the `content_truncated` key (custom serializer) for actors without `payload.read`. Run-row-only queries have `matched = null` and `query.unindexed = null`.
- Refusal: parse (422) first, then the compile-time gate (`authorize_audited(PAYLOAD_READ)`, `details.field` = first content field in query order), both before the cursor is decoded, any connection is taken or the gate permit is acquired. An empty/whitespace `q` runs the plain statements (no permit, no timeout).
- Facets are the last statement; a facets statement cancelled by the budget degrades to `facets: null, facets_skipped: "budget"` (200) instead of failing the request. `summary_states` runs inside the same read-only transaction.
- Timeout code is `503 SEARCH_TIMEOUT` as in D8 (not 504).
- `SearchGate` is created lazily on `app.state.search_gate` from the router (`main.py` untouched).
- `RuleBasedCompiler` is exported beside `QueryCompiler` (tests need it); both carry no vendor, no SQL.
- Tests: `tests/search/conftest.py` re-exports the Phase 15 `world` fixture. Mutation checks (each failed a test, reverted): gate dropped (oracle refusal test), viewer text compiled against `content_doc` (content-word vs nonsense test), content column selected for viewers (viewer statement-text test), workspace predicate dropped from `_base` (cross-workspace test), content flag read inside the unindexed count (statement-text test), refusal error made data-dependent (refusal test).

### Implementation notes (steps 7-9)
- **Web (step 7).** The browser never parses `q`: `lib/search.ts` only edits the typed text by the spans the server echoed (`removeSpan`, `termText`), quotes facet values, converts legacy
  URLs and classifies the API's refusals. The status checkboxes and the Agent input stay as they were (legacy API parameters, ANDed with `q`; the existing tests and Playwright flow keep passing); an old
  bookmark `?status=FAILED&agent=x&range=24h` is converted once on load to `q=status:failed agent:x` plus `range`. **Deviation:** the grammar is a conjunction, so *several* statuses in an old URL stay the
  legacy `status` parameter (the API ORs them) instead of becoming terms. The range control becomes a `started:>=-24h` term added to the sent `q` (so the page is a search whenever a range is chosen, and its
  chip removes the range). The search help lists exactly `query.fields` from the API (`GET /v1/runs?q=&limit=1`, one light request), so a viewer is never offered `file:` or `command:` and the UI never
  guesses a role; `/v1/me` permissions were not needed. A busy or timed-out search is not auto-retried (`shouldRetry`), it shows a retry button. `414` (proxy) and a locally checked 512-character limit show
  the same "too long" message; the proxy test pins that 512 ASCII characters pass and 512 CJK characters exceed 2,048 encoded characters (F22). A new `app/search.css` (imported in `layout.tsx`) holds the styles; `globals.css`
  is untouched. Fixture mode answers a small subset (status, agent, tool, model, event, name, started, free words over the run name) and answers anything else with a `422` that says so.
- **E2E (step 8).** `scripts/auth-e2e.sh` provisions a second OWNER and a VIEWER (`searcher-*`, `viewer-*@auth.test`) with minted development sessions (so `auth.spec.ts`'s members, invitation and downgrade
  steps stay independent) and runs `auth.spec.ts` and `search.spec.ts` together. The fixtures spec gained one search test.
- **Test added in step 9:** `test_a_search_page_has_no_total_count_and_no_relevance_field` (no deferred mutation check killed "a total count added" until it existed).
- **Docs (step 9).** ADR-100/101, `docs/runbooks/search.md`, api-v1 "Search", TESTING, SECURITY, KI-080..083 (placeholder "to file"), README, ARCHITECTURE, IMPLEMENTATION_PLAN, PROJECT_STATE; benchmarks in
  `docs/benchmarks/phase-16-regression-check.md` (the file name replaces the plan's `phase-16-search.md`: one document for AC-11 and AC-12).

### Deferred mutation checks (steps 4-6), run in step 9 (each reverted at once; `git status` clean afterwards)
| Mutation | Result |
| --- | --- |
| tsvector built with `to_tsvector('simple', ...)` over the pre-tokenised words instead of the literal | killed: `test_search_api.py::test_free_text_reads_metadata_and_content_for_content_readers["deploy api"]` (phrase) |
| facets computed over `file_paths` (the tools facet reads it) | killed: `test_search_oracle.py::test_no_response_to_a_viewer_search_carries_content_or_content_flags[viewer]` |
| a `total` count added to the page | **survived** the existing suites; test added (`test_a_search_page_has_no_total_count_and_no_relevance_field`); killed after |
| ranking-style ordering (by `cardinality(tools)` ahead of time) | killed: `test_compiler.py::test_search_without_index_terms_does_not_join_and_facets_left_join` (statement text); the order is also asserted newest-first by the new test |
| the semaphore removed | killed: `test_search_budget.py::test_a_flood_gets_timeouts_and_one_busy_while_ingestion_and_plain_reads_are_fine` |
| `SET LOCAL statement_timeout` removed | killed: `test_search_budget.py::test_a_search_past_its_budget_is_a_retryable_503` |
| facets/search path for an empty `q` (the `if not query.terms: compiled = None` shortcut removed) | killed: `test_search_budget.py::test_the_plain_listing_takes_no_permit_and_has_no_timeout` (the request crashes instead of taking the plain path) |
| `authorize_audited` removed from the refusal | killed: `test_search_oracle.py::test_the_refusal_is_logged_and_audited_without_the_query[viewer]` |
| `MAX_TERMS` 16 -> 32 | killed: `test_parser.py::test_term_limits` |

### Closing evidence (command, observed output, verdict; 2026-10-11, own databases `abb_test_p16`, `abb_p16`, `abb_p16_base` (dropped), native processes)
| AC | Command | Observed | Verdict |
| --- | --- | --- | --- |
| AC-1 grammar | `uv run pytest tests/search/test_parser.py tests/search/test_tokens.py -q` (inside `tests/search`: 345 passed, 1:59) | 82 + 12 tests: every §152/§34 example, negation forms, limits with positions, 10,000-string fuzzer under 10 ms | PASS |
| AC-2 literals and compiler gate | `tests/search/test_tsliteral.py` (13), `test_compiler.py` (32) | serialiser, self-match property, compiled SQL has no content column for a viewer, workspace bound, `CAST(... AS tsquery)` only | PASS |
| AC-3 semantics on Postgres | `tests/search/test_search_api.py` (91), `test_search_http.py` (16), `test_search_index.py` (4) | every field, operator, negation, prefix, glob, phrase, relative time, null and unindexed behaviour, 120-run paging, `q` ANDed with legacy filters | PASS |
| AC-4 oracle (viewer) | `tests/search/test_search_oracle.py` (19) + `tests/authz/test_content_withheld.py` | identical items/facets/cursor for canaries vs a nonsense term; 403 with canonical field; no run statement; log + audit row; BILLING 403 before parsing; no content flag | PASS |
| AC-5 tenancy | `tests/authz` (119 passed, 2:21; cross-workspace file has the search probes); registry and OpenAPI counts unchanged | acme actors never see globex canaries; cursor replay refused | PASS |
| AC-6 hostile input | `tests/search/test_search_hostile.py` (44) | `422` or a literal match, never `500`; no ERROR log line | PASS |
| AC-7 cost bounds | `tests/search/test_search_budget.py` (5) + statement-count tests in `test_search_http.py` | 503 SEARCH_TIMEOUT/BUSY retryable; ingest `202` and plain list `200` during a flood; at most two statements; `q=` equals the plain request | PASS |
| AC-8 derived and rebuildable | `tests/search/test_document.py` (12), `test_search_index.py`, `tests/test_run_summary.py` (pins `SUMMARY_VERSION == 4`) | bounded, order-independent, `span.name` attribute only, unchanged hash writes nothing, `search-rebuild` restores the row and audits | PASS |
| AC-9 migration | `alembic downgrade 0047 && upgrade head` twice on a scratch DB; `downgrade base && upgrade head` on `abb_test_p16`; `tests/test_migrations.py` | `0080 (head)` -> `0047` -> `0080 (head)`; drift test passes | PASS |
| AC-10 web | `pnpm --filter @abb/web test` 539 passed (31 files); `scripts/auth-e2e.sh`: 13 passed (5 search + 8 auth); fixtures Playwright 14 passed (1 search); `next build` ok | chips, removal by span, legacy URL conversion, caret, 403 without echo or "no results", 414, busy/timeout retry, unindexed notice, hostile text as text, viewer help without content fields; axe clean on every search state; no horizontal scroll at 375 px; screenshots `docs/screenshots/phase-16/` | PASS |
| AC-11 ingest cost | A/B interleaved, native, three rounds (`docs/benchmarks/phase-16-regression-check.md`) | 1,000-event-run p95 +2.0%; freshness +13.1% (2,000) / +7.5% (10,000); D7 trigger not tripped, step 3b not needed; Colima VM numbers **UNVERIFIED (env)** (stack not rebuilt) | PASS (native); UNVERIFIED (env) on the VM |
| AC-12 search latency | `scripts/bench_search.py` at 10,000 and 100,000 runs (plan says 100,000: done, 100 variations per class there, 200 at 10,000) | worst p95 at 100,000: `file:` 520 ms, `command:` 449 ms (< 1 s), metadata text 242 ms, arrays 191 ms (< 500 ms): no `pg_trgm` index needed | PASS |
| AC-13 natural-language fake | `tests/search/test_natural.py` (15); `rg -n "anthropic|openai" apps/api/src/abb_api/search` | each phrase parses, compiles for an owner, refused for a viewer when it names a content field; `rg` exit 1 (no match) | PASS |
| AC-14 quality gate | piecewise (below); `python -m abb_api.openapi --check` exit 0; `gen:api:check` clean | all green; the compose-based CI jobs and a PR run were not available here | PASS locally; CI UNVERIFIED (env) |

Piecewise gate: event-schema 351 passed; sdk-python 893 passed (coverage 94.27%); examples 12 + 10; integrations 17 / 74 / 66 / 61 / 44; api ruff, format, mypy (192 files) clean, pytest `tests/authz` 119,
`tests/search` 345, the rest 745 passed (1,209 in all, no time-margin flakes this run); web eslint/prettier/tsc clean, vitest 539, `next build`; schema-check, openapi `--check`, `gen:api:check`; migrations 0047 <-> 0080 and
base <-> head; `uv build --wheel`.

Phase 10 had **not** merged when this was run (`origin/main` at `84491c6` has no migration 0060), so none of the rebase notes were applied: `0080.down_revision` is still `"0047"`, the `run_search` upsert has
no `_enqueue_detect` neighbour, and RunOut/RunPage field order is the Phase 9 one. They apply exactly as written under "Merge notes" when Phase 10 lands first.

### Security review fixes (2026-10-11; commits 594c2da F1, 87f3253 F2, e34bd50 F4, 5ddc897 docs/KI-110..115; see `phase-16-security-review.md` "Resolution")
| Row touched | Command | Observed | Verdict |
| --- | --- | --- | --- |
| AC-8 (hash, rebuild) | `uv run pytest tests/search` | 359 passed (2:08); two new tests fail when content fields are removed from `SearchDocument.hash` | PASS |
| AC-1 / AC-3 (grammar: agent ids) | `tests/search/test_parser.py`, `test_search_api.py` | `agent:my_agent`, `agent:v1.2-bot`, `root_agent:my_agent`, `agent:v1.*` parse and match; `_bad`, `.bad`, `"(other)"`, 65 characters are `422` | PASS |
| AC-4 / AC-7 (ADR-101 claims) | `test_search_oracle.py`, `test_search_budget.py` | refusal before cursor decode and read-only transaction are pinned (each fails when its guard is removed) | PASS |
| AC-5 | `uv run pytest tests/authz` | 119 passed (2:48) | PASS |
| AC-10 web | `pnpm exec vitest run` / eslint / prettier / tsc / `next build` / `gen:api:check` | 541 passed (31 files); the rest clean; fixtures Playwright 14 passed; `scripts/auth-e2e.sh` 13 passed | PASS |
| AC-14 quality gate | `uv run pytest --ignore=tests/authz --ignore=tests/search`; ruff, mypy (192 files), `openapi --check` | 745 passed (3:39); clean | PASS |

### Merge with Phase 10 (done, 2026-10-11)
`origin/main` (Phase 10) merged into this branch with `merge.directoryRenames=false`. Applied as written above: 0080 on 0060 (the branch's round-trip test downgrades 0080 to 0060), the `run_search`
upsert before `_enqueue_detect`, `findings` before `matched` in `RunOut`, `SUMMARY_VERSION` 4, route counts unchanged (37/32). Consequences of Phase 10's three new fixture runs (ten in all, two failed):
fixture-mode search tests now expect 11 rows (header + 10) and 3 for `status:failed`, the fixtures Playwright search test expects 2 rows, `scripts/multiagent-e2e.sh` pins `summary_version == 4`, and
`findings.spec.ts` now also searches (`status:failed`) and checks the findings column on the result rows.
