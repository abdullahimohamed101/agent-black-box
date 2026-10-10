# Phase 16 - Search and investigation

Status: Planned 2026-10-09 (awaiting the user's answers below; "go with your recommendation" suffices)
Owner: implementer agent
Branch: `feature/phase-16-search-investigation` (from `main` c212058; worktree `../abb-worktrees/phase-16`; Phases 9, 10 and 11 are planned in parallel)
Depends on: Phase 2 (runs query API, cursors, summarizer), Phase 7 (typed run columns `cost_usd`, `retry_count`, ADR-043), Phase 15 (`authorize()`, `payload.read`, read-time withholding in `runs/content.py`, the route registry and canary tests)
Spec refs: §77 (query and search architecture), §152 (search grammar), §34 (search and investigation examples), §10.10 (search use cases), §77.1 "fast, explainable, and permission-safe", §93 (data classes: paths, commands, prompts are Class 2), §90.3 (cross-tenant access), §62 INV-2/3/7, §57.2 (deferred stores need a measured trigger)
ADRs: ADR-002 (structural tenancy), ADR-012 (derived state through the outbox), ADR-041/043 (typed derived columns; aggregates only where measured slow), ADR-061 (one `authorize()` path; command text and file paths are content). New: ADR-100, ADR-101 (drafted in the appendix; written at step 9).
Migration ids: **0080** only (chain `0047 -> 0080`; see "Merge notes" for how the later-merging phase rebases `down_revision`). Reserved for this phase: 0080-0089.
Known issues: none closed; adds KI-080..083 (listed under "Known issues considered").

## Decisions needed from the user (each with a recommended default)

1. **Endpoint shape.** Recommended: extend `GET /v1/runs` with a `q` parameter (the §152 grammar) and return the parsed query and facets alongside the page. No new route, so no matrix, registry, web-proxy or OpenAPI-count change (fewer merge conflicts with Phases 9-11). Alternative: a new `GET /v1/runs/search` (needs a RouteCase, an allowlist entry and two test-count bumps; planned in D2 if chosen).
2. **Saved searches.** Recommended: out of scope. The URL carries `q` (bookmarkable, shareable); a saved search is the natural shape of a Phase 17 alert rule and gets its table then.
3. **Natural language.** Recommended: in this phase only the `QueryCompiler` interface and a deterministic rule-based fake used by tests; no endpoint, no UI, no vendor dependency. The real compiler is a later ADR.
4. **Content search for actors with `payload.read`.** Recommended: yes, in this phase: `file:` and `command:` fields and free text over captured content, built from the same attributes `runs/content.py` already withholds. Alternative: metadata-only search now and content search with Phase 13 (server-side redaction). The oracle protection (D5) is required either way.
5. **Where the search document is built.** Recommended: inside the summarizer transaction (the events are already in memory), with `make bench` deciding whether it moves to its own outbox job (D7 states the measured trigger).

## Outcome

A developer types `status:failed tool:github retries:>=2 started:>=2026-10-01 "npm test"` on the runs page and sees, within a second, the
runs of their workspace (and only their workspace) that match, why each matched (the tools, models and event types the query named), and a
facet strip to narrow further with one click. The query is a typed AST compiled to one bounded SQL statement; the UI never builds SQL-like
strings (§152). A VIEWER searches the same box over everything they can already read, and nothing else: no query, count, facet or ordering
ever tells them whether a withheld command, path or payload contains a word (§77.1 "permission-safe", ADR-061). The index is derived state
rebuilt from events (INV-2), ingest cost is re-measured, and every heavier store keeps a written, measured trigger.

## Non-goals

Event-level search across runs as its own result list (the run is the result; the run page has the timeline filters), relevance ranking
(results are newest first), `OR` and parentheses (flat conjunction with negation; D3 says why), regular expressions, substring search of
payload bodies or artifacts (prompts, diffs, stdout: Class 2 bodies stay behind the detail and artifact endpoints), search over audit, members
or keys, saved searches and alert rules (Phase 17), a natural-language endpoint (interface and fake only), trigram or external search
indexes (triggers in D6), server-side redaction (Phase 13; KI-078 stays), per-project roles, quotas (KI-018) and a shared rate limiter
(KI-017/019: Phase 19). Nothing here needs Kafka, ClickHouse, Redis, OpenSearch or Kubernetes; the triggers for a dedicated index are written in D6.

## Current architecture (verified by reading the code unless marked ASSUMED)

- **Run listing** (`apps/api/src/abb_api/runs/router.py:85-107`, `service.py:169-207`, `queries.py:81-120`): `GET /v1/runs` filters by
  `status` (repeatable), `agent_id` (slug pattern), `project_id` (through `projects/access.py::authorise_project`, the one place a project
  filter is authorised), `started_after`/`started_before` (aware timestamps), `sort`, `limit` 1-200, keyset `cursor` on `(started_at, id)`
  encoded by `runs/cursors.py` (versioned, kind-checked, width-checked, untrusted). `RunQueries._base()` puts `workspace_id = tenant` on every
  statement and a project-bound key's `project_scope` on top (INV-3). `summary_states` adds `current|processing|failed` per page.
- **Runs table** (`db/tables.py:267-316`): typed derived columns written by the summarizer with the summary (`cost_usd`, `retry_cost_usd`,
  `llm_calls`, `tool_calls`, `retry_count`, `files_modified`, ...; ADR-043), `summary` JSONB (has `models`, `error_count`, `event_count`),
  `metadata` JSONB (client-controlled, 16 KB, from `POST /v1/runs`), `name`, `agent_slug`, `status`, `started_at`, `duration_ms`. Indexes:
  `ix_runs_project_started (workspace_id, project_id, started_at DESC, id)`, `ix_runs_status_started`, `ix_runs_workspace_started`. There is
  **no** `error_count` column, no GIN index anywhere, no `tsvector`, no `pg_trgm`; `events.attributes` JSONB has no index (`ix_events_type_time`
  is `(workspace_id, event_type, occurred_at DESC)`).
- **Summarizer** (`jobs/handlers.py:35-66`): `summarize_run` takes the run lock, loads every event (KI-022), calls `derive_run`
  (`runs/summary.py`, a pure function), `apply_derivation` (run row + spans), writes cost lines and queues an analytics refresh, in one
  transaction. `POST /v1/cost/rebuild` (`cost/router.py:232-246`) re-enqueues `summarize_run` for up to `limit` runs of a project: that is the
  existing "rebuild derived state" path (INV-2) and is what a search backfill reuses.
- **Content withholding** (`runs/content.py`, ADR-061 amendment): for an actor without `payload.read` the attributes in
  `abb_event_schema.registry.CONTENT_ATTRIBUTES` (`shell.command`, `shell.cwd`, `file.path`, `git.repo`, `git.branch`, `git.push_target`,
  `http.url`, `test.failing`) and any unregistered attribute whose last segment is `command|cmd|argv|args|path|paths|filename|file|cwd|url|uri`
  are replaced by `[withheld]`; span names of `shell|file|git|null` kinds are nulled; payload bodies are dropped. The classification is
  fail-closed by `tests/authz/test_content_withheld.py::REVIEWED_METADATA` (every string attribute is content or reviewed metadata). The
  reviewed-metadata set includes `run.name`, `tool.name`, `llm.model`, `llm.provider`, `span.name`, `tool.operation`, `policy.reason`,
  `retry.reason`; tags and run `metadata` are visible to every reader (KI-078).
- **Authorization** (`authz/`): `require(action)` per route; `RUN_READ` for the run routes; `PAYLOAD_READ` is held by OWNER, ADMIN, DEVELOPER,
  SECURITY and by `runs:read` keys; VIEWER lacks it; BILLING cannot read runs at all. `holds(principal, action)` is how a service varies a
  response by permission (`service.py:249`). `tests/authz/registry.py` has 31 `/v1` cases (`EXPECTED_V1_OPERATIONS = 31`), `test_openapi.py`
  asserts 36 `/v1` operations, `test_migrations.py` lists revisions through `0047`. The cross-workspace world seeds `CANARY-GLOBEX-7f3e` in
  every free-text field of globex and `COMMAND_CANARY`, `PATH_CANARY`, `CWDCANARY`, `CUSTOM_CANARY` in acme's first run (`tests/authz/world.py`).
- **Query cost bounds today**: the engine has deliberately no global `command_timeout` (`db/engine.py:14-17`); analytics applies
  `SET TRANSACTION READ ONLY` + `SET LOCAL statement_timeout` per read and maps a cancel to `503 ANALYTICS_TIMEOUT` (retryable)
  (`analytics/postgres.py:86-93, 229-246`, tested at `tests/test_analytics_api.py:243-251` with `timeout_seconds=0.05`). Query parameters that
  reach SQL are pattern-constrained in the router (`EventTypeParam`, agent slug, id patterns); hostile filters are `422`
  (`tests/test_runs_api.py:652`).
- **Web** (`apps/web`): `RunsListPage.tsx` keeps filters in the URL (`status`, `agent`, `range`) and local state; `RunsList.tsx` renders status
  chips, an agent input and a range select and calls `useRuns` (`lib/queries.ts`, TanStack infinite query over `GET /v1/runs`); the proxy
  allowlist in `server/upstream.ts:30-41` admits `GET v1/runs` with any query string up to 2,048 chars (`MAX_QUERY_CHARS`), so a `q` parameter
  needs no proxy change. `lib/withheld.ts` renders withheld attributes. Types come from `lib/api/schema.d.ts` (generated by `make web-client`
  from `apps/api/openapi.json`). Fixture mode (`src/fixtures/runs.ts`) serves canned pages. Playwright specs live in `apps/web/e2e`;
  `scripts/auth-e2e.sh` runs `e2e/auth.spec.ts` with real sessions for several roles (the right harness for an owner-vs-viewer browser check).
- **Event schema limits** (`abb_event_schema/limits.py`): attribute string 4,096 chars, 64 attributes, 16 tags of 64 chars, `run.name` 256:
  every indexed value is bounded at ingestion.
- **Benchmarks**: `make bench` (`scripts/bench_ingest.py`) measures ingest p50/p95/p99 and summary freshness; the Phase 7 regression check
  (`docs/benchmarks/phase-7-regression-check.md`) is the baseline (1,000-event runs: 27.8 / 44.6 / 53.7 ms; 30,000-event run p99 2.4 s = KI-016).
- **Environment**: Postgres 16 (`postgres:16-alpine` in compose; `pg_trgm` is a trusted extension there but is not needed by this plan).
  **UNVERIFIED (env)**: this worktree has no `.venv` or `node_modules`; step 1 starts with `uv sync` and `pnpm install`. **ASSUMED**: Postgres
  `to_tsvector('simple', ...)` with our own pre-tokenised text yields exactly our lexemes (checked by a test at step 2 before anything depends on it).

## Known issues considered

- **KI-078 (S3, target Phase 13)**: stays open. Search inherits its boundary exactly: a VIEWER can search `run.name`, tags, `tool.name`,
  `llm.model`, custom `span.name` because they can already read them; nothing a VIEWER cannot read becomes searchable (D5). The plan does not
  widen the leak: unregistered non-content attributes are not indexed at all (fail closed).
- **KI-018 (S1), KI-022 (S2), KI-016 (S2)**: the search document is built from the events the summarizer already holds in memory and is
  bounded (D4: at most 2,000 distinct terms per array, 64 KiB of text per document), so it adds no new unbounded cardinality and no second
  pass over events. The KI-016 tail (30,000-event run) is re-measured (AC-11).
- **KI-019 (S1), KI-017 (S2)**: a search is a read with a statement timeout (D8); it adds no limiter and relies on the same future shared
  limiter. Noted as KI-082 (search cost per request is bounded but not rate-limited beyond the pool).
- **KI-055 (S2), KI-040, KI-021, KI-072 (retention)**: `run_search` rows cascade with runs and are rebuilt by the existing rebuild path; a
  retention job that deletes runs removes them (Phase 19).
- **KI-056 (S2, long-locking migration)**: migration 0080 creates an empty table and indexes only (no backfill in the migration); the backfill
  is the bounded `POST /v1/cost/rebuild`/CLI path, so no long lock. Recorded in the runbook note (step 9).
- **KI-050, KI-067..077, KI-052, KI-057, KI-041..044, KI-030..035, KI-060..063, KI-008/010/013/023/024/026/031**: unrelated, stay deferred as listed.
- New items filed at completion: **KI-080** (S3) `started:`/`duration:` take absolute values and units only; relative times (`-7d`) are a UI
  conversion; **KI-081** (S3) search covers runs summarised at `SUMMARY_VERSION >= 3` only until the one-off backfill is run (documented command);
  **KI-082** (S3) no per-actor rate limit on search (Phase 19 with KI-017/019); **KI-083** (S3) the natural-language compiler is a fake.

## Decisions (D1..D12; ADR-100 and ADR-101 drafted in the appendix)

- **D1 Search is a typed AST, parsed and compiled server-side; the UI only sends `q` and renders what the server parsed.** `search/ast.py`
  (frozen dataclasses: `Query(terms)`, `FieldTerm(field, op, value, negated, span)`, `TextTerm(text, phrase, prefix, negated, span)`),
  `search/parser.py` (hand-written scanner, no regex backtracking, O(n)), `search/fields.py` (the field catalogue as data: name, kind,
  storage column, `needs_content`), `search/compiler.py` (AST + `Principal` -> SQLAlchemy predicates over `runs` and `run_search`),
  `search/document.py` (events -> `SearchDocument`, pure). Rationale: §152; one grammar in one language; the compiler is the only thing that
  knows SQL (INV-7, no SQL in routers); the UI gets the parsed terms back (`query.terms[].span`) so chips, errors and removal are exact.
- **D2 (user decision 1) The grammar rides on `GET /v1/runs?q=`.** `q` combines by AND with the existing parameters (they stay for SDK and
  URL compatibility; the web now writes `q` only). The response gains two optional, additive fields present whenever `q` is sent (even empty):
  `query` (`{terms, fields, content_allowed}`) and `facets` (D9). Errors: `422 QUERY_INVALID` with `details.position` and `details.reason`;
  `403 PERMISSION_DENIED` with `details.field` when a content field is used without `payload.read` (keys: `403 INSUFFICIENT_SCOPE`,
  `required_scope: runs:read`, as ADR-061 prescribes per actor kind); `503 SEARCH_TIMEOUT` (retryable) on the statement timeout. No new route:
  the route's action stays `run.read`, the registry and OpenAPI counts stay 31/36, the web proxy allowlist is unchanged. If the user prefers a
  dedicated route: `GET /v1/runs/search` with `require(actions.RUN_READ)`, `RouteCase("GET", "/v1/runs/search", RUN_READ, _get(...), 200,
  ("project_id",))`, `EXPECTED_V1_OPERATIONS = 32`, `seen == 37`, `upstream.ts` pattern `runs/search`, all in one commit.
- **D3 Grammar (§152 made precise).** `query := term*` separated by whitespace; `term := '-'? (field ':' op? value | value)`;
  `op := '>=' | '<=' | '>' | '<'` (numeric and time fields only); `value := '"' chars '"' | bare` where a quoted value may escape `\"` and
  `\\`, a bare value runs to whitespace; a bare or quoted value without `field:` is free text (quoted = phrase); a trailing `*` on an
  enumerated or text value means prefix. Flat conjunction with per-term negation, no `OR`, no parentheses, no `NOT` keyword (a bare `NOT` is a
  word). Field names are case-insensitive; enum values case-insensitive; text is case-folded (`str.casefold`) and NFKC-normalised. Limits
  (`422` beyond): `q` <= 512 characters, <= 16 terms, <= 8 text terms, a value <= 256 characters, a text term <= 16 lexemes; control characters,
  NUL and unpaired surrogates are `422`. Rationale: every §152/§34 example parses; no nesting means no depth to bound and no exponential plans;
  `OR` is the first thing to add when a real query needs it (one AST node, one compiler case).
- **D4 The index is a derived per-run document in Postgres, written by the summarizer, rebuildable from events (INV-2).** Table `run_search`
  (migration 0080): `(workspace_id, run_id)` PK with a composite FK to `runs` `ON DELETE CASCADE`, `project_id`, `started_at` (copied for the
  keyset), `summary_version`, `error_count int`, `security_events int`, metadata arrays `event_types`, `tools`, `models`, `providers`,
  `agents`, `tags` (`text[]`), `metadata_doc tsvector`, content columns `file_paths text[]`, `commands text[]`, `content_doc tsvector`,
  `truncated bool`, `updated_at`. Built by `search/document.py::build_document(events, run_name, run_metadata)` from: event types; `tool.name`;
  `llm.model`/`llm.provider`; `agent_id` of every event; tags; `run.name`/the run row's name; `span.name` of custom spans; string leaves of the
  run's `metadata` (metadata doc); and `CONTENT_ATTRIBUTES` values plus unregistered content leaves (`content.py::is_content_attribute`) into
  `file_paths` (`file.path`), `commands` (`shell.command`) and `content_doc` (all of them, including `shell.cwd`, `git.*`, `http.url`,
  `test.failing`). Unregistered non-content attributes are not indexed. Bounds: each array keeps the first 2,000 distinct values in canonical
  event order, each document at most 64 KiB of tokenised text; beyond that `truncated = true` (shown in the UI as "index truncated"). Tokeniser
  (`search/tokens.py`, shared by document and query): NFKC, casefold, split on anything that is not a letter, digit, `_`, `.`, `-`, `/`; then
  also emit each `/`-segment and `.`-part of a token (`src/auth.ts` -> `src/auth.ts`, `src`, `auth.ts`, `auth`, `ts`), drop tokens longer than
  256 bytes. The tsvector is `to_tsvector('simple', <space-joined tokens>)` so Postgres does no parsing of its own (the `simple` config does not
  stem). Indexes: `ix_run_search_project_started (workspace_id, project_id, started_at DESC, run_id)`, `ix_run_search_meta GIN(metadata_doc)`,
  `ix_run_search_content GIN(content_doc)`, `ix_run_search_event_types GIN(event_types)`, `ix_run_search_tools GIN(tools)`. The runtime role gets
  DML through the default privileges of migration 0007 (the table is derived, not append-only). Rationale: a separate table keeps the hot `runs`
  row and its three B-tree indexes small; arrays give exact-match and prefix semantics with no parser surprises; tsvector gives bounded,
  indexed word search; everything is rebuilt by re-summarising (`SUMMARY_VERSION` 2 -> 3 so a rebuild is forced where needed).
- **D5 (ADR-101) Search is never an oracle for withheld content.** The rule: *an actor may search a field only if the same actor receives that
  field's values on the existing read routes.* Mechanically: `fields.py` marks `file`, `command` as `needs_content`; the compiler takes the
  `Principal` and (a) refuses a `needs_content` field without `payload.read` at validation time with `403` (before any SQL is built; an error
  reveals nothing about data), (b) compiles free text to `metadata_doc @@ q` only, and to `(metadata_doc @@ q OR content_doc @@ q)` when
  `payload.read` is held, (c) never emits `content_doc`, `file_paths` or `commands` in a statement for an actor without `payload.read`
  (asserted on the compiled SQL text by a unit test), (d) facets (D9) are computed only over `status`, `agents`, `tools`, `models`,
  `event_types`, never over content columns, (e) there is no total count and no relevance ranking (results are newest first), so neither a count
  nor an order can encode a content match, (f) `RunOut` rows are the same projection as `GET /v1/runs` (which already withholds nothing of the run
  row; event-level content is withheld by the event routes). A BILLING person is refused at `run.read` before any of this. Keys with `runs:read`
  imply `payload.read` (ADR-061) and search content. Rationale: §77.1 "permission-safe"; ADR-061 made command text and paths content; a
  match/no-match on a secret in a command is the cheapest exfiltration channel a search feature can open.
- **D6 Postgres FTS and arrays now; trigram and external indexes have measured triggers.** `command:` and `file:` are substring/glob matches
  (`ILIKE` with `\`, `%`, `_` escaped; glob `*`/`**` -> `%`, `?` -> `_`) over the bounded arrays via `EXISTS (SELECT 1 FROM unnest(...) v WHERE
  v ILIKE :p)`, evaluated after the tenant/project/time predicates, inside the statement timeout. Trigger for `pg_trgm` GIN on `commands`/
  `file_paths` (migration 0081, an ADR): p95 of a `command:` or `file:` search > 1 s on the measured dataset (AC-12). Trigger for a dedicated
  search service (OpenSearch/Meilisearch; spec §77.2 "only when necessary"): metadata free-text p95 > 1.5 s with GIN in place at the Stage A
  scale (100,000 runs/day, 90 days), or a product need for ranking/typo tolerance. ClickHouse/Kafka are untouched (§57.2).
- **D7 (user decision 5) The document is built inside `summarize_run`, same transaction as `apply_derivation`.** The events are already in
  memory; one upsert per summarisation. Trigger to split it into its own coalesced outbox job (`index_run`, one row per run, debounced like
  summarise): `make bench` shows > 10% p95 regression on the 1,000-event-run case or > 20% on summary freshness (AC-11). KI-016's tail is
  reported, not a trigger (the 30,000-event run is already past its own trigger).
- **D8 Every search statement is bounded.** `SET TRANSACTION READ ONLY` + `SET LOCAL statement_timeout` (`ABB_SEARCH_TIMEOUT_SECONDS`, default
  5) exactly like analytics; `503 SEARCH_TIMEOUT` retryable with "narrow the time range or project"; `limit` <= 200 as today; facets bounded to
  the newest 1,000 matches (`LIMIT 1000` subquery) and the top 10 values per facet; parser limits per D3; patterns bound as parameters only (never
  formatted into SQL); `to_tsquery` input is built from our lexemes with quotes and backslashes escaped (or `plainto_tsquery` when no prefix is
  used), never from raw user text; a glob with more than 8 wildcards is `422`. The existing `/v1/runs` path without `q` is untouched (no timeout
  added to it: behaviour-preserving for SDK clients).
- **D9 Investigation view = results + "why it matched" + facets.** Each `RunOut` in a `q` page carries `matched` (the subset of the run's
  `tools`, `models`, `event_types`, `agents`, `tags` that the query's terms named; empty for pure run-row queries). `facets` = counts of
  `status`, `agents`, `tools`, `models`, `event_types` over the newest 1,000 matching runs (one grouped query per facet over the same compiled
  predicate, `unnest` on the arrays, top 10). Clicking a facet appends a term. No per-row content (files, commands) in the list: the run page
  already shows them with the existing withholding. Rationale: spec §34 "investigation" needs a narrowing loop; counts over viewer-visible
  fields of a viewer-compiled predicate reveal nothing new (D5-d).
- **D10 (user decision 2) Saved searches are out.** A bookmark of `?q=` is the saved search; Phase 17's alert rule table (`condition` + `query`)
  is where persistence belongs. Nothing in this phase is harder to add later because of this.
- **D11 (user decision 3) Natural language: interface and a deterministic fake only.** `search/natural.py`: `class QueryCompiler(Protocol):
  def compile(self, text: str, *, fields: FieldCatalogue) -> str` returns a **grammar string** (never SQL, never an AST with hidden fields), which
  then goes through the same parser and the same permission gate as a typed query (§77.2: "compile into structured filters"). `RuleBasedCompiler`
  maps a small phrase table ("failed" -> `status:failed`, "more than $N" -> `cost:>N`, "retried more than N times" -> `retries:>N`, "last week" ->
  `started:>=<date>` from an injected clock, "touching X" -> `file:X` which the gate then refuses for a viewer). No endpoint, no setting, no UI,
  no dependency. A vendor-backed compiler is its own later ADR (prompt-injection and data-egress review, §77.2 "never unrestricted raw database
  access").
- **D12 Field catalogue (data in `search/fields.py`; the test holds a second literal copy, like the matrix).**

  | Field | Kind | Storage | Needs `payload.read` | Semantics |
  | --- | --- | --- | --- | --- |
  | `status` | enum (`RUN_STATUSES`) | `runs.status` | no | equals (case-insensitive) |
  | `agent` | slug | `runs.agent_slug` | no | equals; `*` prefix |
  | `name` | text | `runs.name` | no | case-insensitive substring (escaped `ILIKE`) |
  | `cost` | decimal USD | `runs.cost_usd` | no | `>=`, `<=`, `>`, `<`, equals |
  | `retries` | int | `runs.retry_count` | no | comparisons |
  | `errors` | int | `run_search.error_count` | no | comparisons; `severity:error` is an alias of `errors:>0` (other `severity` values `422`) |
  | `duration` | number + unit (`ms`, `s`, `m`, `h`) | `runs.duration_ms` | no | comparisons; running runs (null) never match |
  | `started` | date or RFC 3339 with offset | `runs.started_at` | no | comparisons (a date means midnight UTC) |
  | `event` | event type (`EVENT_TYPE_PATTERN`) | `run_search.event_types` | no | contains; `*` prefix (`event:shell.*`) |
  | `tool`, `model`, `provider`, `tag` | token | arrays | no | contains; `*` prefix |
  | `security` | `true|false` | `run_search.security_events` | no | `> 0` / `= 0` (events of class SECURITY: `policy.*`, `secret.detected`) |
  | `file` | glob | `run_search.file_paths` | **yes** | glob over paths (`src/auth/**`) |
  | `command` | text | `run_search.commands` | **yes** | case-insensitive substring over command lines |
  | free text | words/phrase | `metadata_doc` (+ `content_doc` with `payload.read`) | content part only | all lexemes present (phrase: adjacent) |

  Not fields (deliberately): `project` (a query parameter resolved by `authorise_project`), `url`/`repo`/`branch` (reachable as free text for
  content holders), `tokens`, `llm_calls` (add as one row each when asked).

## Proposed design

### API (`apps/api/src/abb_api/search/`, new package; `runs/` edits are small)

- `tokens.py` (tokeniser), `ast.py`, `fields.py`, `parser.py` (`parse(q) -> Query`, raises `QueryError(position, reason)`), `compiler.py`
  (`compile(query, principal, *, scope) -> CompiledSearch(predicates, needs_index, matched_columns, facet_columns)`), `document.py`
  (`build_document(...) -> SearchDocument`), `repository.py` (`RunSearchRepository.upsert(run_id, project_id, started_at, document)` and
  `delete`), `natural.py`, `errors.py` (`query_invalid`, `content_field_forbidden`, `search_timeout`).
- `runs/queries.py::RunQueries.page` gains `search: CompiledSearch | None`: when `needs_index`, the statement joins `run_search` on
  `(workspace_id, run_id)` (inner join: a run not yet summarised at version 3 is not searchable; `summary_state` tells the user) and applies the
  predicates; order and keyset unchanged. `facets(search, limit=1000)` and `matched(run_ids, search)` are two further methods, all under
  `_base()` (INV-3). The read runs under the D8 timeout via a small `search/reads.py::bounded_read(engine, timeout)` helper that mirrors
  `PostgresAnalyticsStore._read` (shared later if a third caller appears; not abstracted now).
- `runs/service.py::list_runs` takes `q: str | None`; when present: parse (422), compile with the principal (403), run page + matched + facets,
  attach `query` echo (`terms` with `span`, `fields` catalogue with `needs_content`, `content_allowed = holds(principal, PAYLOAD_READ)`).
- `runs/schemas.py`: `RunOut.matched: MatchedOut | None`, `RunPage.query: QueryOut | None`, `RunPage.facets: FacetsOut | None` (all optional and
  absent without `q`: additive for every existing client and fixture).
- `jobs/handlers.py::summarize_run`: after `apply_derivation`, `await RunSearchRepository(conn, tenant).upsert(...)` with the document built from
  the same `events` list; `SUMMARY_VERSION = 3` (`runs/summary.py`) so the existing rebuild semantics cover the backfill.
- Settings: `search_timeout_seconds: float = 5.0` (`core/config.py`).
- CLI: no new command; backfill = `POST /v1/cost/rebuild` per project (documented) or `python -m abb_api.cli jobs-...` as today. If a
  workspace-wide one-liner is wanted, step 3 adds `search-rebuild --workspace <id> [--project]` reusing `enqueue_summarize` in batches of 1,000
  (bounded; ASSUMED acceptable; otherwise documented SQL in the runbook).
- Docs: `docs/architecture/api-v1.md` "Runs" gains a "Search" subsection with the grammar, the catalogue, the permission rule and the error codes.

### Migration 0080 (`0080_run_search.py`, `down_revision = "0047"`)

Creates `run_search` and its five indexes; `downgrade` drops the table. No data migration, no long locks (KI-056). `tests/test_migrations.py`
revision list gains `"0080"`. `db/tables.py` gains the table (the drift test keeps them equal).

### Web (`apps/web`)

- `lib/search.ts`: `qFromFilters(ListFilters)`/`filtersFromTerms(terms)` so the existing status chips, agent input and range select write
  terms into `q` (range -> `started:>=<absolute>` computed once per mount as today); `removeTerm(q, span)`; `appendTerm(q, term)`.
- `components/SearchBox.tsx`: input with submit on Enter, inline `422` error with the caret position, `403` message naming the field ("`file:`
  needs the content permission"), a help popover listing `query.fields` (content fields marked, hidden entirely when `content_allowed` is false).
- `components/QueryChips.tsx`: one chip per `query.terms[]` with a remove control; `components/Facets.tsx`: the strip, each value a button that
  appends a term; `RunsTable.tsx` gains a "Matched" column rendered only when any row has `matched`; "index truncated" badge when a run's
  document was truncated (from `matched.truncated`).
- `RunsListPage.tsx`: `q` in the URL replaces `status|agent|range` (old URLs are converted once on load, so bookmarks keep working);
  `queries.ts::useRuns` sends `q` (always, possibly empty, so `query.fields` arrives).
- Fixtures: `src/fixtures/runs.ts` answers `q` with a canned echo (terms parsed by a tiny fixture-only splitter, no grammar logic) so the
  component and Playwright fixture specs are deterministic. No new CSS variables; the chip and filter classes in `globals.css` are reused (one
  `.facets` rule added at the end of the file).
- Tests: vitest for `search.ts`, `SearchBox`, `QueryChips`, `Facets`, the runs page states (parse error, permission error, timeout, empty,
  truncated); Playwright `e2e/search.spec.ts` run by `scripts/auth-e2e.sh` (owner finds the content canary by `command:`, viewer sees the
  permission message and no result for free text; screenshots saved).

### Affected files

- New: `apps/api/src/abb_api/search/{__init__,tokens,ast,fields,parser,compiler,document,repository,reads,natural,errors}.py`;
  `apps/api/migrations/versions/0080_run_search.py`; `apps/api/tests/search/{test_tokens,test_parser,test_compiler,test_document,test_search_api,
  test_search_oracle,test_search_hostile,test_natural}.py`; `apps/web/src/lib/search.ts`, `components/{SearchBox,QueryChips,Facets}.tsx`,
  `tests/search.test.tsx`, `e2e/search.spec.ts`; `docs/decisions/ADR-100-*.md`, `ADR-101-*.md`; `docs/benchmarks/phase-16-search.md`.
- Edited: `runs/{queries,service,schemas,router}.py`, `runs/summary.py` (version), `jobs/handlers.py` (one call), `core/config.py`,
  `db/tables.py`, `tests/test_migrations.py` (one list entry), `tests/authz/{test_content_withheld,test_cross_workspace}.py` (search probes added
  to the existing loops), `tests/test_runs_api.py` (the `q`-less behaviour is pinned), `apps/web/src/components/{RunsList,RunsListPage,RunsTable}.tsx`,
  `lib/queries.ts`, `src/fixtures/runs.ts`, `app/globals.css` (append), `playwright`/`scripts/auth-e2e.sh` (one spec added),
  `docs/architecture/api-v1.md`, `docs/TESTING.md`, `docs/KNOWN_ISSUES.md`, `docs/DECISIONS.md`, `docs/PROJECT_STATE.md`,
  `docs/IMPLEMENTATION_PLAN.md`, `README.md` (one line), and the generated `apps/api/openapi.json` + `apps/web/src/lib/api/schema.d.ts`.
- Not touched: `main.py`, `authz/*`, `tests/authz/registry.py`, `test_openapi.py`, `server/upstream.ts`, `AppShell.tsx`, `Makefile`,
  `scripts/quality.sh`, `.github/workflows/ci.yml`, `packages/*`, `integrations/*` (under decision 1's recommendation).

## Acceptance criteria (each command-checkable; run from `apps/api` with `uv run pytest` unless stated)

- **AC-1 Grammar.** `tests/search/test_parser.py`: every example in §152 and §34 (`status:failed`, `cost:>1.00`, `retries:>=3`,
  `model:"provider/model"`, `tool:github`, `file:"src/auth/**"`, `event:shell.command.completed`, `severity:error`, `security:true`,
  `started:>=2026-10-01`, `duration:>60s`, `model:"claude*"`) parses to the expected AST with spans; negation, phrases, escapes and prefixes;
  every limit in D3 yields `QueryError` with the right position; a seeded in-test fuzzer (10,000 random strings from a hostile alphabet, no new
  dependency) never raises anything but `QueryError` and never exceeds 10 ms per parse.
- **AC-2 Compiler gate.** `tests/search/test_compiler.py`: for a principal without `payload.read`, `file:` and `command:` raise
  `content_field_forbidden` before compilation; for every query in a corpus (fields x text x negation), the compiled SQL (`str(stmt.compile(
  dialect=postgresql.dialect()))`) contains none of `content_doc`, `file_paths`, `commands`; with `payload.read` the same corpus does reference
  `content_doc` for text terms; every statement contains `runs.workspace_id = ` as a bound parameter; no statement contains a literal from the
  query (`literal_binds` off; user strings only ever appear as parameters).
- **AC-3 Semantics on Postgres.** `tests/search/test_search_api.py` (real database, `api` fixture): seeded runs cover every field in D12; each
  field, each operator, negation, prefix, glob, phrase and a 6-term combination return exactly the expected run ids; a run not yet summarised is
  absent while `summary_state=processing`, present after the worker; the keyset cursor pages a 120-run result set without gaps or repeats while
  new matching runs arrive; `q` combined with legacy `status`/`agent_id`/`started_after` ANDs.
- **AC-4 Oracle (viewer).** `tests/search/test_search_oracle.py` using `tests/authz/world.py`: for `viewer` and `downgraded`: (a) `q=file:x`,
  `q=command:x`, `q=-file:x` -> `403 PERMISSION_DENIED` `details.field`, no SQL executed (statement counter on the engine, as
  `test_event_lists_never_select_the_payload_column` does); (b) free text `CMDCANARY`, `s3cr3t`, `PATHCANARY`, `id_rsa`, `CWDCANARY`,
  `CUSTOMCANARY`, each with and without `*`, each negated, each as a phrase, return the **same body** (modulo request id) as `q=NOSUCHTERM-x9` and
  as the negation of a nonexistent term: same items, same `facets`, same `next_cursor`; (c) `owner` gets acme's first run for each of them;
  (d) `billing` gets `403` at `run.read`; (e) no facet key or value and no `matched` value equals any `CONTENT_CANARIES` substring for any
  actor in `WITHHELD_FROM`; (f) the registry loop in `test_content_withheld.py` is extended with `?q=<canary>` variants of `GET /v1/runs`.
- **AC-5 Tenancy.** `tests/authz/test_cross_workspace.py` gains `GET /v1/runs?q=CANARY-GLOBEX-7f3e`, `q=tool:canary-globex-7f3e-tool`,
  `q=model:canary-globex-7f3e`, `q=name:CANARY` for every acme actor: empty items, empty facets, `assert_clean` passes; a dual member with
  `X-ABB-Workspace: acme` searching globex's run name finds nothing; `tests/authz/test_registry_is_complete.py` and `test_openapi.py` are
  unchanged and green (no new route).
- **AC-6 Hostile input.** `tests/search/test_search_hostile.py`: 513 and 10,000-char `q`, 17 terms, 9 text terms, a 257-char value, NUL, `\x1f`,
  unpaired surrogate (`%ED%A0%80`), unbalanced quotes, `cost:>1e400`, `cost:>NaN`, `cost:>-0`, `retries:>=9223372036854775808`,
  `started:9999-99-99`, `started:>=0001-01-01T00:00:00+14:00`, `duration:>1y`, `event:../../etc`, `status:drop`, `file:` with 9 wildcards (for an
  owner), `name:%`, `name:_`, `name:\`, `'`, `;--`, tsquery operators `& | ! ( ) : * <->` as text, RTL and combining marks, emoji: each is `422
  QUERY_INVALID` or a literal (escaped) match, never `500`; the API log has no ERROR line (caplog), and `name:%` matches only a name containing `%`.
- **AC-7 Cost bounds.** `tests/search/test_search_api.py::test_a_slow_search_is_a_retryable_503`: with `search_timeout_seconds=0.05` and a
  `pg_sleep`-wrapped read (same technique as `tests/test_analytics_api.py:243`), `503 SEARCH_TIMEOUT` `retryable: true`; a page is at most 200
  rows; facets are at most 10 values per key and are computed over at most 1,000 runs (asserted with 1,200 seeded matches: counts sum to <= 1,000).
- **AC-8 Derived and rebuildable (INV-2).** `tests/search/test_document.py`: `build_document` is order-independent (shuffled events give an
  equal document), bounded (2,001 distinct tools -> 2,000 and `truncated`; a 100 KiB metadata -> 64 KiB text and `truncated`), and the runtime
  role can write `run_search` (`test_runtime_role.py` already asserts DML on every non-append-only table); `test_search_api.py`: delete the
  `run_search` row, `POST /v1/cost/rebuild`, the row is back and equal; `SUMMARY_VERSION == 3`.
- **AC-9 Migration.** `scripts/quality.sh full`: `0080` upgrades, downgrades and upgrades again from `0047`; `test_migrations.py` lists it; the
  drift test passes.
- **AC-10 Web.** `pnpm --filter @abb/web test`: chips render from `query.terms`, removing a chip rewrites `q` by span, the legacy URL
  `?status=FAILED&agent=x&range=24h` converts to `q`, parse error shows the caret, `403` shows the field message, timeout shows retry, viewer
  help omits content fields; `make auth-e2e` runs `e2e/search.spec.ts`: owner searches `command:curl` and sees the run, viewer searches `curl`
  and sees "No runs match" with the same page as a nonsense word, viewer typing `file:x` sees the permission message; screenshots in
  `docs/screenshots/phase-16-*.png`; `pnpm --filter @abb/web build` green.
- **AC-11 Ingest and summarizer cost (D7 trigger).** `make seed && make bench` three times, recorded in `docs/benchmarks/phase-16-search.md`
  against `phase-7-regression-check.md`: 1,000-event-run p95 within 10% and 2,000-event summary freshness within 20% of the baseline, else the
  split into `index_run` is done in this phase (step 3b). `UNVERIFIED (env)` if Colima cannot run here; stated as such, the gate stays open.
- **AC-12 Search latency (D6 triggers).** `scripts/bench_search.py` (new, reuses `analytics_seed.py` to seed 100,000 runs with documents, then
  issues 200 queries of each class: run-row only, array contains, metadata text, content text, `command:` substring, `file:` glob): p95 per class
  recorded; metadata text and array classes < 500 ms, content substring < 1 s, else the trigram index (0081) or the fallback is scheduled and an
  ADR written. Also recorded: `run_search` size and GIN index sizes per 100,000 runs.
- **AC-13 Natural language fake.** `tests/search/test_natural.py`: the rule-based compiler's output for each phrase in D11 parses, compiles for an
  owner, and is refused for a viewer when it names a content field; the `QueryCompiler` protocol is the only public name; `rg -n "anthropic|openai"
  apps/api/src/abb_api/search` finds nothing.
- **AC-14 Quality gate.** `scripts/quality.sh full` exit 0; `python -m abb_api.openapi --check`; `make web-client` leaves no diff; all CI jobs
  green on the PR.

## Verification plan

1. Per step: focused tests, then `scripts/quality.sh quick`; `full` before each commit; `make auth-e2e` at steps 7-8 (quality.sh does not run E2E).
2. Mutation checks during review (each must fail at least one test): drop the `payload.read` check in the compiler; let the viewer branch add
   `content_doc`; compute facets over `file_paths`; return a `total` count; order by `ts_rank`; drop `workspace_id` from the join; format a
   pattern into SQL instead of binding; remove the `statement_timeout`; raise the term limit; skip NFKC in the query tokeniser only.
3. Browser: `make auth-e2e` plus a manual pass on `make dev` with the demo (owner and viewer), screenshots kept.
4. Benchmarks: AC-11 and AC-12 on the Colima VM used for Phase 7; results and dataset caveats written as in `phase-7-regression-check.md`.
5. Environment: Docker/Colima, Postgres 5433, Node 22, Chrome for Playwright (setup.md); step 1 starts with `uv sync` and `pnpm install`. Only
   AC-11/AC-12 can end `UNVERIFIED (env)` (VM busy or absent), and then D6/D7 keep their fallbacks open as KI items.

## Security review note (mandatory adversarial pass; what to attack, expected result)

1. **Oracle by match.** As VIEWER: free text, prefix, phrase and negated forms of every content canary (AC-4b); `name:` with a canary (name is
   metadata: matches only if the name holds it); a field the catalogue does not list (`422`, not a hidden content field); mixed case and NFKC
   confusables (`ｃｕｒｌ`) of a canary; a term that is a substring of a path segment (`id_rsa`, `ssh`); `event:` with a content-looking value (`422`
   by pattern). Expected: identical bodies to a nonsense term.
2. **Oracle by count or order.** Facet counts with and without a content word; `next_cursor` presence; item order; response size and timing
   (a content predicate must not even run for a viewer: AC-4a statement counter; timing is not asserted but the absence of the predicate is).
3. **Oracle by error.** `403` for `file:` must not depend on whether a match exists (same body for `file:/home/PATHCANARY*` and `file:/nothing`);
   `422` positions must not depend on data.
4. **Downgrade and keys.** An OWNER demoted to VIEWER mid-session (`downgraded`), a `runs:read` key (content allowed, ADR-061), an
   `events:write`-only key (`403 INSUFFICIENT_SCOPE run.read`), BILLING (`403` before parsing: a parse error for BILLING must still be `403`,
   never `422`, so `require(run.read)` precedes parsing, which the dependency order guarantees).
5. **Tenant.** Every AC-5 probe; a cursor from an acme search replayed with `X-ABB-Workspace: globex` by a dual member (empty or 404, no
   acme row); `project_id` of the other workspace with `q` (404 as today).
6. **Cost.** 16 terms each with 8 wildcards against 100,000 runs (timeout `503`, the connection returned to the pool, no lingering backend: check
   `pg_stat_activity`); 50 concurrent slow searches (pool timeout `503`, ingest still accepted: `test_connection_pool_exhaustion_is_a_retryable_503`
   pattern); a 2,048-char URL through the proxy with a 513-char `q` (`422` from the API, not `414`).
7. **Injection.** Every string in AC-6 through the compiler with `literal_binds` inspection; tsquery syntax in free text; glob to LIKE escaping
   (`\` first, then `%`, `_`); `ILIKE` with `name:` containing `\`.
8. **Index content.** `run_search.content_doc` holds commands and paths: confirm the table is not reachable by any route except through the
   compiler, that `test_runtime_role` treats it as writable derived state, that the runbook's retention note lists it, and that the audit log never
   records `q` (search is a read; nothing is audited, and `set_caller` logs no query text: grep the log capture for a canary after a search).
9. **Web.** `q` never reaches `window.location` unencoded; the help popover for a viewer never lists `file`/`command`; the `403` message does not
   echo the value; chips render text, never HTML.

## Risks

1. **Oracle subtlety.** The split is only as good as the attribute classification (KI-078). Mitigation: fail-closed catalogue, AC-2's SQL
   assertion, AC-4's identical-body check, the mutation checks, and no counts or ranking by design (D5-e).
2. **Summarizer cost and the KI-016 tail.** Mitigation: bounded document, one upsert, AC-11 with a written trigger and a ready fallback (3b).
3. **Tokeniser mismatch** between document and query (prefix on `src/auth.ts`). Mitigation: one shared function, a property test that any token of a
   document matches itself as a query, the `simple` config, step 2's check of the ASSUMED `to_tsvector` behaviour.
4. **Backfill.** Existing runs are unsearchable until re-summarised. Mitigation: `SUMMARY_VERSION` 3, documented `POST /v1/cost/rebuild` per
   project (bounded), KI-081 until done; the UI says "processing" for a run without a document.
5. **Merge conflicts with Phases 9-11** on `jobs/handlers.py`, `runs/summary.py` (`SUMMARY_VERSION`), `openapi.json`, `schema.d.ts`, the docs
   indexes. Mitigation: the "Merge notes" below; generated artifacts only in the last commit of a step; `SUMMARY_VERSION` bumps are additive
   (take the max on merge).
6. **Query UX.** A grammar box can be opaque. Mitigation: chips from the server parse, quick-add controls stay, help popover, exact error positions.
7. **Facet cost** on broad queries. Mitigation: the 1,000-row subquery, the same timeout, facets skipped (`null`) when the page itself timed out.

## Ordered steps (one commit each; `scripts/quality.sh full` green after every step; generated artifacts in the last commit of a step)

1. `feat(search): tokeniser, AST, field catalogue and parser for the §152 grammar` - `search/{tokens,ast,fields,parser,errors}.py`, AC-1
   tests incl. the fuzzer; starts with `uv sync`/`pnpm install` and records the two UNVERIFIED (env) items as verified.
2. `feat(search): compiler with the content gate; RunQueries.page accepts a compiled search` - `compiler.py`, `reads.py`, AC-2 tests (SQL
   text assertions, workspace predicate, no literals), the `to_tsvector('simple')` check (ASSUMED -> VERIFIED), no route change yet.
3. `feat(db): migration 0080 run_search; document builder; summarizer writes the document (SUMMARY_VERSION 3)` - `document.py`,
   `repository.py`, `db/tables.py`, `test_migrations.py` entry, AC-8 tests; `jobs/handlers.py` one call. **3b (only if AC-11 trips):**
   `feat(jobs): index_run job` moving the write to its own coalesced job.
4. `feat(runs): GET /v1/runs?q= with query echo, matched and facets; errors QUERY_INVALID, PERMISSION_DENIED(field), SEARCH_TIMEOUT` -
   `service.py`, `schemas.py`, `router.py`, `config.py`; AC-3, AC-6, AC-7 tests; `test_runs_api.py` pins the `q`-less path; **last commit of the
   step regenerates** `openapi.json` and `schema.d.ts` (`make openapi web-client`).
5. `test(authz): search oracle and cross-workspace probes` - AC-4, AC-5 (extends `test_content_withheld.py`, `test_cross_workspace.py`,
   `tests/search/test_search_oracle.py`); registry and OpenAPI counts asserted unchanged.
6. `feat(search): QueryCompiler interface and rule-based fake` - `natural.py`, AC-13.
7. `feat(web): search box, chips from the server parse, legacy filter URLs converted to q, fixtures` - `lib/search.ts`, `SearchBox`,
   `QueryChips`, `RunsListPage`/`RunsList`/`queries.ts`, fixture echo, vitest (AC-10 unit part).
8. `feat(web): investigation view: matched column, facet strip; Playwright search.spec in auth-e2e; screenshots` - `Facets.tsx`, `RunsTable`,
   `globals.css` (append), `e2e/search.spec.ts`, `scripts/auth-e2e.sh` (one line), `docs/screenshots/`.
9. `docs(phase-16): ADR-100/101, api-v1 search section, TESTING, benchmarks (AC-11, AC-12), runbook note, KNOWN_ISSUES KI-080..083, DECISIONS,
   PROJECT_STATE, IMPLEMENTATION_PLAN, README line` - `scripts/bench_search.py` lands here with its results; then `complete-phase`.

## Merge notes (parallel phases 9, 10, 11)

- **Migration chain.** This phase adds exactly one revision, `0080`, with `down_revision = "0047"`. Whichever phase merges to `main` second
  (or later) rebases its first migration's `down_revision` onto the then-current head (e.g. `0080 -> down_revision "0067"` if Phase 10's chain
  ended at 0067, or Phase 10's `0060 -> "0080"` if this phase merged first), keeps its own revision ids (the numbers are labels, not order), and
  inserts the ids into `tests/test_migrations.py` after the new head. Unlike Phase 6 (0030 renumbered to 0044 because ids were sequential),
  no file is renamed. `SUMMARY_VERSION`: if another phase also bumps it, the merge takes the higher number and both plans' notes say so.
- **Hot files touched (minimally, in dedicated commits):** `apps/api/openapi.json` and `apps/web/src/lib/api/schema.d.ts` (regenerated: last commit
  of step 4; on rebase, regenerate rather than merge), `apps/api/src/abb_api/jobs/handlers.py` (one call in `summarize_run`, step 3),
  `apps/api/src/abb_api/runs/summary.py` (one constant, step 3), `apps/api/tests/test_migrations.py` (one list entry, step 3),
  `apps/web/src/app/globals.css` (append-only, step 8), `docs/KNOWN_ISSUES.md` (append four rows, step 9), `docs/DECISIONS.md` (append two rows),
  `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md` (the Phase 16 status cell), `README.md` (one line) - all step 9.
- **Hot files not touched:** `main.py`, `authz/matrix.py`, `authz/actions.py`, `tests/authz/registry.py`, `tests/authz/test_registry_is_complete.py`
  (count 31), `tests/test_openapi.py` (count 36), `apps/web/src/server/upstream.ts`, `AppShell.tsx`, `Makefile`, `scripts/quality.sh`,
  `.github/workflows/ci.yml`. (Under decision 1's alternative, the registry, both counts and `upstream.ts` change in step 4's last commit.)

## Appendix: ADR drafts (written as files at step 9; these drafts are the plan's record)

### ADR-100: Search is a typed filter grammar compiled server-side over a derived per-run Postgres search document

Status: Proposed. Phase: 16.

**Context.** Spec §152 asks for a token grammar parsed into a typed AST compiled to storage queries so the UI never builds query strings; §77.1
wants structured search fast, explainable and permission-safe; §77.2 allows PostgreSQL full-text search for selected textual metadata and defers a
dedicated index until necessary; §57.2 defers ClickHouse-class stores to measured triggers. Runs already carry typed derived columns (ADR-043) and a
summarizer that rebuilds derived state from events (ADR-012, INV-2).

**Decision.** (1) One grammar (flat conjunction of `field:op?value` terms, free text, per-term negation, quoted phrases, trailing-`*` prefix),
parsed by a hand-written scanner with hard limits (512 chars, 16 terms, 8 text terms, 256-char values) into frozen AST nodes; the field catalogue
is data (`search/fields.py`). (2) The AST compiles to SQLAlchemy predicates over `runs` and a derived table `run_search` written by the summarizer
in the same transaction as the run row: `text[]` arrays (`event_types`, `tools`, `models`, `providers`, `agents`, `tags`, `file_paths`,
`commands`), two `tsvector` documents (`metadata_doc`, `content_doc`) in the `simple` configuration built from our own tokeniser (NFKC, casefold,
path and dotted-name segments), and `error_count`/`security_events`; GIN indexes on the documents and the two most selective arrays; bounded
(2,000 values per array, 64 KiB of text, `truncated` flag). (3) Results are newest first by the existing keyset cursor; there is no relevance
ranking and no total count. (4) Every search statement is read-only under `statement_timeout` (`ABB_SEARCH_TIMEOUT_SECONDS`, 5 s ->
`503 SEARCH_TIMEOUT`); user strings are only ever bound parameters; globs become escaped `LIKE` patterns with at most 8 wildcards. (5) `GET
/v1/runs?q=` carries the grammar; the response echoes the parsed terms with spans, the catalogue and facets so the UI renders chips from the
server's parse. (6) Natural language, when it comes, implements `QueryCompiler.compile(text) -> grammar string` and goes through the same parser
and gate; this phase ships only the protocol and a rule-based fake. (7) Triggers: `pg_trgm` GIN on `commands`/`file_paths` when a substring/glob
search p95 exceeds 1 s on the measured dataset; a dedicated search service when metadata text p95 exceeds 1.5 s at Stage A scale with GIN in
place or when ranking/typo tolerance becomes a product need; the summarizer write moves to its own coalesced outbox job when `make bench` shows
> 10% p95 regression on 1,000-event runs or > 20% on summary freshness.

**Alternatives.** Querying `events.attributes` directly with a JSONB GIN (every search a scan over the biggest table; no run-level semantics;
withholding would have to be re-implemented in SQL). Columns on `runs` (bloats the hot row and its indexes). Postgres' own text parser on raw
attribute text (surprising tokens for paths and URLs; stemming). Trigram everywhere (2-3x index size before a need is measured). An external
index now (a second system of record for Class 2 content with its own tenancy to prove). `OR`/grouping in v1 (unbounded plan shapes before a
measured need). Relevance ranking (an ordering oracle, see ADR-101).

**Consequences.** Search is derived, rebuildable and tenant-keyed like everything else; existing runs need one re-summarisation to become
searchable (`SUMMARY_VERSION` 3, `POST /v1/cost/rebuild`); the summarizer does slightly more per run (measured); adding a field is one catalogue row,
one document line and one test row; the UI has no grammar logic.

### ADR-101: Search never reveals withheld content: permission-split documents, a compile-time gate, no counts or ranking

Status: Proposed. Phase: 16. Amends ADR-061.

**Context.** ADR-061 made command text and file paths captured content, withheld at read time from actors without `payload.read`. A search
feature can leak the same content by match/no-match, by counts, by ordering, by error differences and by timing: a VIEWER asking
`command:"Bearer s3cr3t"` learns whether a secret exists in any run. Spec §77.1 requires permission-safe search; §93 classifies paths and commands
as Class 2.

**Decision.** (1) The rule: an actor may search a field only if the same actor receives that field's values on the existing read routes.
(2) The search document is split into a metadata document and a content document (and content arrays) in separate columns; the catalogue marks
`file` and `command` as content fields. (3) The compiler takes the `Principal`: a content field without `payload.read` is refused with
`403 PERMISSION_DENIED` (`details.field`) before any SQL is built, independent of data; free text compiles to the metadata document only unless
`payload.read` is held; a statement for an actor without `payload.read` never references a content column (asserted on the compiled SQL by a
test). (4) No total count, no relevance ranking, facets only over metadata fields and only for the actor's own compiled predicate, so neither a
number nor an order can encode a content match. (5) Error positions and bodies depend only on the query text, never on data. (6) Keys with
`runs:read` hold `payload.read` (ADR-061) and search content. (7) Unregistered attributes that are not content by name are not indexed at all
(fail closed; KI-078's heuristic boundary is inherited, not widened). (8) Tests: identical bodies for a viewer between a content canary and a
nonsense term in every syntactic form; a statement counter proving no query runs for a refused field; canary scans of facets and `matched`;
mutation checks that removing any of (3)-(5) fails a test.

**Alternatives.** Filtering results after a content match (the count and the emptiness still leak). Returning `[withheld]` matches (tells the
viewer a secret exists). Hashing content terms (equality on a hash is still an oracle). Searching content for everyone and redacting later
(Phase 13's redaction does not exist yet, and a redacted document is still an oracle for the unredacted part).

**Consequences.** A VIEWER's search is strictly weaker than an OWNER's and the UI says so (the help lists only allowed fields; a refused field
names the permission). Server-side redaction (Phase 13) can later move individual attributes between the two documents by changing the
classification in one place. A future ranking feature must rank on metadata only or be gated by `payload.read` as a whole.
