# Independent review of the Phase 16 plan (search and investigation)

Reviewed: `docs/plans/active/phase-16-search-investigation.md` on `feature/phase-16-search-investigation` at 4d4075e (main c212058).
Stance: adversarial, against the plan text and the code it claims to describe; nothing in `apps/`, `packages/` or `scripts/` was changed.
No database and no dependencies exist in this worktree, so every claim about Postgres behaviour is **UNVERIFIED (env)** and marked so;
code claims were verified by reading the files at the stated lines.

Severity: **P0** critical, **P1** must fix before implementation, **P2** should fix, **P3** optional.
Evidence: **VERIFIED** (lines read here), **UNVERIFIED (env)** (needs Postgres or installed packages), **ASSUMED** (reasoned).

Summary: no P0. Four P1: the plan's one ASSUMED claim (Postgres does not re-parse pre-tokenised text) is wrong and the text-search
semantics, phrase search and prefix search rest on it (F1); the per-request cost bound and the "ingest still accepted" claim do not hold
with per-statement timeouts over a shared 15-connection pool (F2); `SUMMARY_VERSION` collides with Phase 9 (both 2 -> 3) and the merge
rule "take the higher" silently marks Phase 9's runs as searchable when they are not (F3); and step 2 compiles against a table that does
not exist until step 3, so `scripts/quality.sh full` cannot be green after step 2 as written (F4). Eleven P2, fourteen P3. The oracle
design (D5) is sound in its core: I could not construct a query that makes a VIEWER's result set, facets, cursor, error or echo depend on
withheld content, with one exception that depends on content *volume* rather than content (F5, `truncated`) and one ambiguity in what
"span.name of custom spans" means (F13).

---

## Verified correct (file:line)

- `GET /v1/runs` parameters, `limit` 1-200, cursor `max_length=512`, `require(actions.RUN_READ)`: `runs/router.py:85-107`, `:33`.
- `RunQueries._base()` puts `workspace_id` and the key's `project_scope` on every statement: `runs/queries.py:71-75`; `page()` keyset on
  `(started_at, id)`: `:105-118`; `summary_states` from the outbox: `:122-152`.
- `list_runs` -> `authorise_project` -> `RunQueries(conn, tenant, principal.project_id)`; `holds()` at `runs/service.py:184-185, 249`.
- `RunOut` carries `summary` and `metadata` to every reader; nothing content-bearing is in `derive_run`'s summary (counts, `models`,
  timestamps): `runs/summary.py:152-172`; `files_modified` is a count only (`:168`). `run.name` comes from the `run.started` attribute (`:102-109`).
- `runs` table and its three B-tree indexes; no `error_count` column, no GIN, no tsvector: `db/tables.py:284-325` (the plan says
  `267-316`; wrong lines, right content). `events` has no attributes index: `:386-390`.
- Summarizer: lock, `load_run`, `derive_run`, `apply_derivation`, cost lines, analytics refresh, one transaction: `jobs/handlers.py:35-67`.
- `POST /v1/cost/rebuild` re-enqueues `summarize_run` for up to `limit` runs: `cost/router.py:232-246` (requires `PRICING_WRITE`, `:29`).
- Withholding: `CONTENT_ATTRIBUTES` (8 keys) `packages/event-schema/.../registry.py:150-161`; the leaf heuristic and
  `_CONTENT_SPAN_KINDS = {None, shell, file, git}`: `runs/content.py:17-39`. Span display names come from `span.name`, `tool.name`,
  `shell.command`, `llm.model`, `file.path` in that order: `packages/event-schema/.../spans.py:42-47`; `tool.name` is required on
  `tool.call.*` (`registry.py:205-207`), so a non-content-kind span is never named by a command (see F13 for the remaining ambiguity).
- `REVIEWED_METADATA` and the fail-closed classification test: `tests/authz/test_content_withheld.py:29-57`; the per-route canary loop: `:65-87`.
- Matrix: VIEWER lacks `payload.read`, BILLING lacks `run.read`, `runs:read` keys hold `payload.read`: `authz/matrix.py:31-59`;
  `holds()`: `authz/service.py:63-65`; `PermissionDenied` shapes per actor kind: `:17-44`.
- `EXPECTED_V1_OPERATIONS = 31` (`tests/authz/test_registry_is_complete.py:12`), `seen == 36` (`tests/test_openapi.py:55`), revisions through
  `0047` (`tests/test_migrations.py:58-59`), drift test (`:86-94`), `0044 -> head -> 0044` (`:97`).
- Canaries: `COMMAND_CANARY`, `PATH_CANARY`, `CUSTOM_CANARY`, `CWDCANARY` (in `shell.cwd = /srv/CWDCANARY`): `tests/authz/world.py:31-40, 139-152`;
  `assert_clean`: `tests/authz/test_cross_workspace.py:46-50`.
- Engine has no global `command_timeout`, `pool_timeout=5`: `db/engine.py:12-19`; the pool is SQLAlchemy's default 5 + 10 overflow
  (`tests/test_runs_api.py:751`). Analytics `SET TRANSACTION READ ONLY` + `SET LOCAL statement_timeout` -> `503 ANALYTICS_TIMEOUT`:
  `analytics/postgres.py:86-93, 229-246`; the `pg_sleep` test: `tests/test_analytics_api.py:243-251`.
- Statement counter technique: `tests/test_runs_api.py:500-523`; pool-exhaustion 503: `:744-756`; hostile filters 422: `:652-660`.
- Runtime role default privileges (new tables get DML): `migrations/versions/0007_runtime_role.py:36-39`; `test_runtime_role.py:99-118`
  checks every non-append-only table is writable and `APPEND_ONLY_TABLES == {events, audit_log}`.
- Web: `upstream.ts:28-41` allowlist admits `GET v1/runs` with any query; `MAX_QUERY_CHARS = 2048` -> `414 QUERY_TOO_LONG` (`:43, :115-116`);
  `RunsListPage.tsx:10-34` keeps `status|agent|range` in the URL; `RunsList.tsx:47-51` turns `range` into an absolute `startedAfter` once per
  mount; `queries.ts:24-49` `useRuns`. `lib/withheld.ts`, `fixtures/runs.ts`, `scripts/auth-e2e.sh`, `scripts/analytics_seed.py`,
  `scripts/bench_ingest.py` (with a freshness case, `:173-179`) exist. Postgres is `postgres:16-alpine` (`docker-compose.yml:27`).
- Event-schema limits (4,096-char strings, 64 attributes, 16 tags of 64, name 256): `limits.py`.
- uvicorn's access log is disabled (`core/logging.py:44-45`), so `q` does not reach the API log through the request line.
- Spec: §77.1 "fast, explainable, and permission-safe" and §77.2 (PostgreSQL FTS for selected metadata, NL compiles to structured filters):
  spec lines 5709-5731; §152 examples: 8318-8335; §34 examples: 3127-3145; §10.10: 1496-1506.
- Sibling plans (read only): Phase 9 `SUMMARY_VERSION = 3`, `summary["agents"]` (max 200), no migrations, does not touch `openapi.json`;
  Phase 10 migration `0060`, `seen == 37`, `RunOut.findings`, `summary_states(job_type=...)`, `summarize_run` enqueues `detect_findings`,
  `SUMMARY_VERSION` stays 2; Phase 11 migrations `0070/0071`, `seen == 50`, no `summarize_run` hook, adds
  `test_a_database_at_0047_upgrades_to_head_and_back_to_0047`.

## Unverified

- **UNVERIFIED (env)** `to_tsvector('simple', <space-joined tokens>)` "yields exactly our lexemes" (plan line 83-84). From the Postgres
  documentation (text search parser, chapter 12.5) this is false; see F1 for the reasoning and a check that needs no database.
- **UNVERIFIED (env)** the bench numbers and the noise band (AC-11, AC-12); **ASSUMED** FastAPI resolves the `require()` dependency before
  query-parameter validation, so BILLING with a malformed `q` gets 403 not 422 (plan security note 4). The plan's own test covers it; keep it.
- **UNVERIFIED (env)** Postgres `LIKE`/`ILIKE` cost with many `%` wildcards over 4,096-byte strings (F2 relies on the statement timeout, not
  on this).
- **ASSUMED** that `pg_trgm` is not needed (plan line 82): consistent with D6, nothing to verify.

---

## Findings

### F1 (P1) The one ASSUMED claim is wrong: Postgres re-parses "pre-tokenised" text, so index and query lexemes diverge

Location: plan lines 83-84 (ASSUMED), D4 lines 138-141 ("so Postgres does no parsing of its own"), D8 lines 170-172 (`to_tsquery`/`plainto_tsquery`),
AC-1/AC-3 phrase and prefix semantics, Risk 3.

Problem. `to_tsvector('simple', text)` always runs the default text-search parser before the `simple` dictionary; `simple` only skips
stemming. The parser classifies `src/auth.ts` as a `file` token, `auth.ts` as `host`, `v1.2` as `version`, `3.14` as `float`, and splits
`foo_bar` into `foo` and `bar` and `claude-3-5-sonnet` into the hyphenated word plus every part (`hword`/`hword_part`). The plan's tokeniser
keeps `_`, `.`, `-`, `/` inside tokens, so the lexemes stored are not the tokens emitted. The same applies to `to_tsquery` and
`plainto_tsquery`; a quoted lexeme passed to `to_tsquery` is still parsed and turned into a `<->` phrase. Consequences the tests in
AC-1/AC-3 will hit (UNVERIFIED (env), reasoned from the documented parser):

- Prefix: `foo_b*` -> `to_tsquery('simple','foo_b:*')` becomes `'foo' & 'b':*` and matches any document with `foo` and any `b...` word.
- Phrase (D12 "adjacent"): the tokeniser emits `src/auth.ts src auth.ts auth ts` for one path, so positions are shifted and
  `"auth.ts now"` (adjacent in the command) is not adjacent in the document; with the parser's own splitting the shift differs again.
- The 16,383 position cap: a 64 KiB document has roughly 10,000 tokens plus segment expansions; positions past the cap collapse and phrase
  adjacency becomes true for any two tail lexemes.
- Risk 3's mitigation ("one shared function") does not help when Postgres applies a second, different tokeniser after it.

Failure scenario: an OWNER searches `"npm test"` (phrase) to find runs where `npm test` failed (§10.10): the document for a run whose
command was `npm test -- src/auth.test.ts` stores `npm test -- src/auth.test.ts src auth.test.ts auth test ts` through two tokenisers
with different positions; the test corpus in AC-3 expects exact run ids and gets either misses or extra hits depending on the parser's
view of `auth.test.ts`. Prefix `model:claude-3*` (an array field, unaffected) works while the free-text `claude-3*` does not.

Suggested plan change. Do not let Postgres parse at all: build the `tsvector` **literal** in Python from the tokeniser's output
(`'lexeme':pos 'lexeme':pos ...`, quotes doubled, backslashes doubled, lexemes <= 2,047 bytes, positions <= 16,383) and bind it as a
parameter cast with `CAST(:doc AS tsvector)`; build the query the same way (`'lex' & 'lex2'`, `'lex':*`, `'a' <-> 'b'`) and bind it as
`CAST(:q AS tsquery)`. The `tsvector`/`tsquery` input functions do no normalisation and no parsing, which is the property D4 wants. Then the
check that can run **without a database** is a unit test of the literal serialiser (escaping, bounds, round trip through a small parser of
the literal syntax in the test), and the one Postgres-dependent fact left is the input syntax, pinned by a single AC-3 test. Decide phrase
semantics explicitly: either phrases are matched on the un-expanded token stream (emit segment expansions at the *same* position as the
parent token, which the literal syntax allows: `'src/auth.ts':7 'src':7 'auth.ts':7 'auth':7 'ts':7`), or define a phrase as "all lexemes
present" and drop `<->`. The same-position trick also fixes the prefix case (`auth*` matches at position 7 whichever segment it hits).
Replace the ASSUMED line with this decision and remove "Postgres does no parsing of its own".

### F2 (P1) The cost bound is per statement, the pool is shared, and "ingest still accepted" does not follow

Location: D8 lines 168-173, D9 line 175-177, security note 6 (lines 359-361), KI-082.

Problem. `SET LOCAL statement_timeout` bounds one statement. A `q` request runs one page statement, one `matched` statement and one
grouped statement per facet (five: `status`, `agents`, `tools`, `models`, `event_types`), so the budget per request is up to 7 x 5 s = 35 s,
and the connection is held for the whole transaction. The engine pool is 5 + 10 overflow per process with `pool_timeout=5`
(`db/engine.py:18`, `tests/test_runs_api.py:751`), shared with ingestion. Fifteen concurrent slow searches (any VIEWER; `name:%a%b%c%d%e%f%g%h%`
is allowed to everyone and `ILIKE` with eight `%` over a workspace's names has no index) hold every connection; the sixteenth caller of
*any* route, including `POST /v1/events`, waits 5 s and gets `503 DEPENDENCY_UNAVAILABLE`. The plan's security item 6 expects "ingest
still accepted"; with this design it is not, and the test as written would fail or, worse, pass only because the test process has a
fresh pool.

Failure scenario: a VIEWER session runs `for i in 1..20: GET /v1/runs?q=name:%a%b%c%d%e%f%g%h%` concurrently against a workspace with
100,000 runs; each request holds a connection for up to 35 s; the worker's `summarize_run` and the SDK's batches are refused for the duration.

Suggested plan change. (a) One budget per request: `SET LOCAL statement_timeout` stays, but the service also enforces a wall-clock budget
(`ABB_SEARCH_TIMEOUT_SECONDS` total, facets skipped with `facets: null` and a `facets_skipped` reason when the page has used most of it).
(b) A per-process concurrency cap for `q` requests (an `asyncio.Semaphore`, e.g. 4, `503 SEARCH_BUSY` retryable when full, acquired before
a connection is taken) so search can never take more than N of the 15 connections; record it as the interim for KI-082. (c) Run `matched`
inside the page statement (select the array columns for the page's rows and compute the intersection in Python) and fold the five facet
queries into one statement over a CTE of the 1,000 newest matches (one `UNNEST` per array, `UNION ALL`, `GROUP BY`), so a request is two
statements, not seven. (d) Rewrite security item 6 to assert the cap (N+1 concurrent slow searches: N time out, one is `SEARCH_BUSY`, and a
`POST /v1/events` during the flood is 202) and keep the `pg_stat_activity` check.

### F3 (P1) `SUMMARY_VERSION` collides with Phase 9 and "take the higher number" is the wrong merge rule

Location: D4 line 146, proposed design line 229, KI-081 (line 102), Risk 5 and Merge notes line 413; Phase 9 plan lines 199-205 and 372.

Problem. Phase 9 also bumps `SUMMARY_VERSION` from 2 to 3 (per-agent summary). Phase 16 uses "summarised at `SUMMARY_VERSION >= 3`" as the
definition of "has a search document" (KI-081) and the version as the signal that drives `unrebuilt_runs` in analytics
(`analytics/rollup.py:104`, the only consumer of the constant outside the writer). With the merge order 9, 10, 16, 11 and the rule "take the
higher number", both phases end at 3: every run summarised between Phase 9's merge and Phase 16's merge is at version 3 with no
`run_search` row, KI-081's documented command (`rebuild` for runs below 3) finds nothing to do, analytics reports zero unrebuilt runs, and
the runs are silently unsearchable (the inner join drops them, F8).

Suggested plan change. Replace the merge rule with: "the phase that merges second sets `SUMMARY_VERSION` to the merged value + 1 and
updates `test_run_summary.py:199` and KI-081's text". For the stated order, Phase 16 lands at 4. Independently of the version, make the
presence of the `run_search` row the operational signal: add `RunQueries.unindexed_count(project_id)` (or reuse the existing
`unrebuilt_runs` figure with the new version) and show it next to the search box ("N runs not yet indexed; rebuild"), so the UI does not
depend on a constant that three phases are editing.

### F4 (P1) Step 2 compiles against `run_search` before step 3 creates it; `quality.sh full` cannot be green after step 2 as written

Location: Ordered steps 2 and 3 (lines 389-393); AC-2.

Problem. Step 2 adds `compiler.py` with "SQL text assertions" that reference `content_doc`, `file_paths`, `commands`, and
"`RunQueries.page` accepts a compiled search" with a join on `run_search`. The table object does not exist in `db/tables.py` until step 3,
and adding it to `tables.py` in step 2 without the migration fails the drift test (`test_migrations.py:86-94`, `compare_metadata` reports the
missing table). Either step 2's tests cannot be written or the step is red.

Suggested plan change. Swap steps 2 and 3 (migration + `tables.py` + `document.py` first, then the compiler), or merge them into one step
with two commits in that order. Also add `tests/test_run_summary.py` (the version pin at `:199`) to the step's affected files; it is missing
from the "Edited" list (line 265-270) and would fail the step that bumps the constant.

### F5 (P2) `truncated` is one flag for both documents, so a VIEWER learns about content volume

Location: D4 lines 137-138, D9 line 248-249 (`matched.truncated`, "index truncated" badge), AC-8.

Problem. `truncated` is set when *any* array exceeds 2,000 values or the *document* exceeds 64 KiB. For a run with ordinary metadata, the
only way to trip it is content (more than 2,000 distinct file paths or commands, or more than 64 KiB of command and path text). The flag is
returned in `matched` for every actor, so a VIEWER reads "this run touched more than 2,000 distinct paths or ran more than 64 KiB of
commands", which no existing route tells them (the events route tells them counts of events, not distinct paths). It is a volume bit, not
a word match, so D5's letter holds, but ADR-101's rule ("an actor may search a field only if ... receives that field's values") is
violated for the *derived* field.

Failure scenario: VIEWER lists runs with `q=` (empty: `matched` present); runs whose documents were truncated by content carry
`truncated: true`; the viewer sorts out which runs had very large change sets.

Suggested plan change. Two flags, `metadata_truncated` and `content_truncated`, in the row; the response carries `content_truncated` only
when `content_allowed`. Add a canary-style assertion to AC-4(e): for actors in `WITHHELD_FROM`, no `truncated` key derived from content
appears.

### F6 (P2) Facets and `matched` run on every poll because the web always sends `q`

Location: Web lines 250-251 ("`useRuns` sends `q` (always, possibly empty, so `query.fields` arrives)"), D2 line 114 ("present whenever `q`
is sent (even empty)"), D9.

Problem. `useRuns` refetches every `POLL_MS * 5` (`queries.ts:47`). With `q` always present, the default runs page costs the page statement
plus facets over the 1,000 newest runs plus `matched` on every poll, for every open tab, and the facets are meaningless for an empty query.
This multiplies the baseline read load of the most-visited page by the number of statements (F2) with no user benefit.

Suggested plan change. Compute `facets` and `matched` only when `q` has at least one term; return `query` (the catalogue) for an empty `q`
cheaply, or move the catalogue to `GET /v1/me`-style static data (it is per-actor only through `content_allowed`). State in AC-7 that an
empty `q` executes exactly the statements the `q`-less path does (statement counter).

### F7 (P2) `file:` glob semantics are undefined where the tests need them to be exact

Location: D6 lines 158-160, D12 `file` row, AC-3 ("glob ... return exactly the expected run ids").

Problem. `*` and `**` both become `%`, so `*` crosses `/` (not glob semantics), and the pattern is anchored at both ends by `ILIKE`
unless the plan says otherwise. `file:"src/auth/**"` then matches only paths that *start* with `src/auth/`; the demo and the SDK capture
absolute paths (`PATH_CANARY = /home/PATHCANARY/.ssh/id_rsa`, `world.py:38`), so the spec's own example matches nothing. `?` -> `_` is
fine. AC-3 cannot be written until anchoring and `/`-crossing are decided.

Suggested plan change. Define: a pattern without a leading `/` or `**/` is unanchored at the start (`%src/auth/%`); `*` and `**` both
match across `/` (document it; true single-segment globbing needs `SIMILAR TO` or regex and is out of scope); a pattern with no wildcard is
a substring match. Put three examples in the catalogue row and in `api-v1.md`.

### F8 (P2) The inner join silently drops unindexed runs and inverts negation; "summary_state tells the user" is not true

Location: proposed design lines 219-221, Risk 4 ("the UI says processing for a run without a document"), KI-081.

Problem. `summary_state` is computed only for the rows returned (`runs/queries.py:122`, `service.py:197`); a run excluded by the inner join
is not in the page, so there is nothing to label. Two consequences: (1) `status:failed` (run-row only, no join) and `status:failed -tool:x`
(join) return different populations for reasons unrelated to `tool:x`; (2) `-tool:github` means "runs that did not use github" but
returns only *indexed* runs that did not, which is the opposite of what a negation user expects for queued runs (no events, no document)
and old runs.

Failure scenario: after deployment, before the backfill, `q=-tool:github` returns zero rows for a workspace of 50,000 old runs; the UI
says "No runs match" and nothing says why.

Suggested plan change. Use a LEFT JOIN with `COALESCE` semantics per field class (an unindexed run fails positive index predicates and
passes negated ones), or keep the inner join and return `unindexed_count` for the same predicate on the run row (F3) with a banner.
Pin the chosen behaviour in AC-3 with a queued run and an un-backfilled run in the corpus.

### F9 (P2) A text term can tokenise to nothing; the plan does not say what it matches

Location: D3 limits, D4 tokeniser (drop tokens > 256 bytes; split on punctuation), D12 free-text row.

Problem. `"!!!"`, `@@`, a 300-byte token, or a value consisting only of characters the tokeniser splits on yields zero lexemes. An empty
tsquery is an error in Postgres (`NOTICE: text-search query doesn't contain lexemes` and the predicate is false), a negated empty term
would then match everything, and `len(lexemes) <= 16` is unenforceable on an empty list. Hostile input in AC-6 (`& | ! ( ) : * <->` as text)
is exactly this case and the plan expects "a literal (escaped) match", which cannot exist for a term with no lexemes.

Suggested plan change. A text term with no lexemes after tokenisation is `422 QUERY_INVALID` with `details.reason = "no searchable
characters"` and the term's position. State it in D3 and in AC-6's expected results.

### F10 (P2) AC-11's trigger compares against a historical file inside its own noise band

Location: AC-11, D7, Verification 4.

Problem. The Phase 7 file shows run-to-run p95 of 150.3 / 150.6 / 172.0 ms (14%) on the same day and machine; the D7 trigger is 10% on p95.
Comparing three new runs against numbers recorded on another day of the same VM makes the trigger fire or pass by chance, and "the split
into `index_run` is done in this phase" is a large piece of work hanging on a coin flip.

Suggested plan change. Measure A/B in one session: `main` (or the branch with the upsert disabled by a flag) and the branch, interleaved,
three runs each, and compare medians of p95; state the noise band observed and require the regression to exceed it. Record the method in
`docs/benchmarks/phase-16-search.md` as `phase-7-regression-check.md` does.

### F11 (P2) The legacy `range` conversion freezes a relative range into bookmarks

Location: Web lines 243-244 and 250 (`range -> started:>=<absolute> computed once per mount`), AC-10 (legacy URL conversion), KI-080.

Problem. Today `?range=24h` is relative at every visit (`RunsList.tsx:47-51` recomputes from `now`). Writing `started:>=2026-10-10T08:00Z`
into `q` in the URL turns a bookmarked "last 24 hours" into a fixed window that shows fewer runs every day. KI-080 defers relative times in
the grammar, which is fine, but the URL must not lose the intent.

Suggested plan change. Keep `range` as a URL parameter beside `q` (the UI converts to an absolute `started:` term at request time, as today),
or add `started:>=-24h` to the grammar now (one token form, one compiler case, an injected clock; it is smaller than the conversion code).

### F12 (P2) The content-field 403 is built outside `authorize()` and bypasses denial logging

Location: D2 line 116, D5 (a) line 149, `search/errors.py::content_field_forbidden` (line 218), security note 4.

Problem. ADR-061: "one `authorize()` path". A second hand-built `403 PERMISSION_DENIED` in `search/errors.py` duplicates the actor-kind
branching (`authz/service.py:17-44`) and skips `note_denial` (`authz/audit.py:46-60`): a VIEWER probing `file:` a thousand times leaves no
log line and no audit row, unlike every other denial of a person. The key branch (`INSUFFICIENT_SCOPE required_scope: runs:read`) is also
unreachable: a key without `runs:read` lacks `run.read` and is refused by `require()` first, and one with it holds `payload.read`.

Suggested plan change. In the compiler, call `authorize(principal, actions.PAYLOAD_READ)` (or `authorize_audited` from the service, which
has the request) and attach `details.field` to the raised `PermissionDenied`; delete `content_field_forbidden`. Keep `details.field` the
catalogue's canonical name, never the user's spelling (`FILE:`, confusables), so the error never echoes input (also closes the XSS note 9).

### F13 (P2) "span.name of custom spans" must mean the attribute, not the derived span name

Location: D4 line 134 ("`span.name` of custom spans"), ADR-101 (7).

Problem. Two readings: the `span.name` *attribute* (reviewed metadata, `test_content_withheld.py:31`) or the derived `Span.name`
(`spans.py:42-47`), which for a span of kind `None`/`shell`/`file`/`git` is a command or a path and is withheld by `_span_out`
(`service.py:92`). The document builder has the event list, not the derived spans, so the natural implementation indexes the attribute;
but if someone "improves" it to the derived names for "custom" spans without `span_name_is_content`, commands enter `metadata_doc`.

Suggested plan change. Write D4 as: "the `span.name` attribute of any event (it is reviewed metadata); never the derived `Span.name`";
add a mutation check "index derived span names" that AC-4(b) must kill (it will, because `CMDCANARY` is a shell span's derived name).

### F14 (P2) `agent:` is defined on `runs.agent_slug` while the index has `agents`; Phase 9 makes a run multi-agent

Location: D12 `agent` row (`runs.agent_slug`, equals), D4 `agents` array, D9 facets over `agents`; Phase 9 plan line 199-202.

Problem. After Phase 9 a run has a root `agent_slug` and up to 200 agents in `summary["agents"]`. `agent:worker-3` on `runs.agent_slug`
finds nothing for a child agent, while the `agents` facet counts it; clicking the facet appends a term that returns zero rows.

Suggested plan change. `agent:` = contains in `run_search.agents` (any agent of the run), with `root_agent:` as the run-row field if
needed; facets and field agree. State in the Merge notes that this is the row to rebase against Phase 9.

### F15 (P2) GIN write amplification on active runs; skip the upsert when the document is unchanged

Location: D4 (five indexes, two GIN on tsvector, two on arrays), D7 (same transaction, every summarisation), AC-11.

Problem. Summarisation is debounced per second (`summary_debounce_seconds = 1.0`, `config.py:33`); a 30,000-event active run is
re-summarised many times, and each upsert rewrites a row of up to 64 KiB of tsvector plus 2,000-element arrays and touches four GIN
indexes (delete + insert into pending lists), producing dead tuples and GIN pending-list churn proportional to the run's lifetime, not to
its change. This is also what AC-11's 1,000-event case will not show and the KI-016 tail will.

Suggested plan change. Store a `document_hash` and `UPDATE ... WHERE document_hash IS DISTINCT FROM :hash` (or compare before writing);
add `pg_stat_user_tables.n_dead_tup` and GIN sizes after the 30,000-event bench run to AC-11's report; make `ix_run_search_project_started`
the ORDER BY source when the join is present (otherwise the planner sorts on `runs.started_at` and the copied column is dead weight).

### F16 (P3) The grammar's `-` is ambiguous with values that start with `-`

Location: D3 (`term := '-'? (...)`).

`--force`, `-rf`, `cost:>-0` and `started:>=-24h` (if F11 is taken) all begin with `-`. Define: a leading `-` negates once; a second `-`
starts the value; a quoted value is never negation. Add `-"--force"` and `--force` to AC-1.

### F17 (P3) `+` in RFC 3339 offsets is a space in a GET query string

Location: D12 `started` row, AC-6 (`started:>=0001-01-01T00:00:00+14:00`).

`q=started:>=2026-10-01T00:00:00+00:00` arrives as `started:>=2026-10-01T00:00:00 00:00` (two terms) unless the client encodes `+` as
`%2B`. Accept `Z` and recommend it; have the UI encode with `encodeURIComponent`; mention it in `api-v1.md`.

### F18 (P3) AC-4(b) "same body" cannot hold because the `query` echo differs

Location: AC-4(b) line 293-294.

The echo carries the parsed terms and spans of the query, which differ between `CMDCANARY` and `NOSUCHTERM-x9`. Compare `items`, `facets`
and `next_cursor` (the plan says so in the same sentence); drop "same body".

### F19 (P3) AC-4(a) "no SQL executed" must exclude the statements authentication runs

Location: AC-4(a).

`require()` executes session and membership lookups before the service runs. Count statements that mention `runs` or `run_search` only.

### F20 (P3) The catalogue cannot express two of the spec's §10.10 examples

Location: D12, "Not fields (deliberately)" line 208-209; spec 1496-1506.

"all database write operations" needs `db.operation`; `tool.operation` and `http.method` are reviewed metadata too
(`test_content_withheld.py:32-37`) and are not indexed. They can be added to `metadata_doc` under D5's rule with no oracle cost. Either
add them or list them as deliberate omissions with the one-row recipe.

### F21 (P3) Null semantics of negated comparisons are undefined

Location: D12 (`duration`: "running runs (null) never match"), AC-3 negation.

`-duration:>60s` is `NOT (duration_ms > 60000)`, which is NULL for running runs, so they also never match the negation; the same for
`-name:x` on a null name and `cost` on unpriced runs. State it ("a null never matches, negated or not") and pin it in AC-3.

### F22 (P3) The 414 from the web proxy can hit a legal 512-character `q`

Location: current architecture line 74-75, security note 6.

`MAX_QUERY_CHARS = 2048` is measured on the encoded query (`upstream.ts:115`); non-ASCII and punctuation expand 3-9x, so a 512-char `q`
of quoted paths or non-Latin text is refused with `414 QUERY_TOO_LONG` before the API's 422. Either lower the `q` limit to what always fits
(about 300 characters) or have the UI handle 414 like 422 (no caret). Add the case to AC-10.

### F23 (P3) `runs` and `search` import each other

Location: proposed design (`document.py` uses `runs/content.py::is_content_attribute`; `runs/queries.py` and `runs/service.py` import
`search.*`).

Not a Python cycle today, but a package cycle against the "routers -> services -> repositories -> db" direction. Move
`is_content_attribute` and `span_name_is_content` to a neutral module (`abb_api/content.py`) or have `search` depend on `runs` only.

### F24 (P3) The backfill reuses `cost.rebuild`: `PRICING_WRITE`, audited as cost, and it churns analytics

Location: proposed design line 231-233, KI-081, Risk 4.

A search backfill needs a BILLING/OWNER/ADMIN actor (`cost/router.py:29`), is logged as `cost.rebuild`, rewrites cost lines and enqueues
a `refresh_analytics_day` per touched day for every run. For a 50,000-run workspace that is a day of rollup rewrites. Prefer the
`search-rebuild` CLI (bounded batches, audited as `search.rebuild`) over documenting the cost route, and decide whether a re-summarisation
whose derivation hash is unchanged should skip the cost and analytics writes.

### F25 (P3) The FK from `run_search` to `runs` takes a lock on `runs` during migration 0080

Location: Migration 0080 (line 236-239, "no long locks").

`ADD FOREIGN KEY` acquires `SHARE ROW EXCLUSIVE` on the referenced table for the validation (fast, the new table is empty) but it waits
behind any long transaction on `runs` and blocks writers while it waits. Note it in the runbook sentence ("brief lock on `runs`; run outside
a bulk rebuild"); the same is true of every FK migration in this repository.

### F26 (P3) The plan hard-codes "31/36" where it means "unchanged"

Location: D2 line 118, Merge notes line 419-420, AC-5.

After Phase 10 merges first, the counts are 32/37 (Phase 10 plan line 105). Write "unchanged by this phase" and drop the literals so the
rebase needs no plan edit.

### F27 (P3) `q` with content terms lives in URLs: browser history, bookmarks, referrers and any reverse proxy's access log

Location: D2 (grammar in a GET parameter), D10 ("a bookmark of `?q=` is the saved search"), security note 8.

The API's access log is off (`core/logging.py:45`, VERIFIED), so note 8's grep is sound for the API, but an OWNER's
`command:"Bearer s3cr3t"` is stored by the browser and by anything in front of the API or the web server. Accept it explicitly in ADR-101
("search terms are the searcher's own input; they are not withheld from the searcher's own logs") and add `Referrer-Policy: no-referrer`
on the runs page if the web app does not already set it.

### F28 (P3) Prefix on an expanded token: which segment gets the `*`

Location: D3 trailing `*`, D4 tokeniser segment expansion.

`src/au*` tokenises to `src/au`, `src`, `au`; applying `:*` to all three is "src/au* AND src* AND au*", which is right only by accident.
Define: prefix applies to the last lexeme of the term only, the rest are exact. (Moot if F1's same-position encoding is adopted.)

### F29 (P3) `started:<date>` with equals is a day, not an instant

Location: D12 `started` row ("a date means midnight UTC").

`started:2026-10-01` as equality on a timestamp matches nothing. Define equals-on-date as the whole UTC day (or reject equals for `started`).

---

## Oracle analysis (item 2 of the brief): what I tried and why it holds

For an actor without `payload.read` the plan's VIEWER statement references only `runs` columns and the metadata columns of `run_search`
(D5-c, asserted on SQL text). Each channel:

- Match/no-match on content words: free text compiles to `metadata_doc` only; `metadata_doc` is built from fields a VIEWER already reads on
  `GET /v1/runs` (`name`, `metadata` leaves, `summary.models`) and `GET .../events` (`event_type`, `agent_id`, `tags`, `tool.name`,
  `llm.*`, `span.name` attribute). No content attribute (`CONTENT_ATTRIBUTES` plus the leaf heuristic) is in it, and the derived
  `Span.name` is excluded if F13 is written as recommended. Query sequence tried: `CMDCANARY`, `cmdcanary*`, `"CMDCANARY-curl"`,
  `-CMDCANARY`, `id_rsa`, `ssh`, `srv`, `ｃｕｒｌ`, `s3cr3t`: all compile to a `metadata_doc` predicate that is false for every run of the
  world (identical items to `NOSUCHTERM-x9`); negations are true for every run (identical items to `-NOSUCHTERM-x9`).
- Field combinations: `-file:x`, `file:x -tool:y`, `tool:y file:*`: all `403` at compile time before `authorise_project` and before any SQL;
  the body depends on the field name only. `fiel:x` is `422` (catalogue, not data).
- Count, order, pagination: no total, newest-first keyset on `(started_at, id)` that is identical for every actor; `next_cursor` encodes
  the last row's key only; facets are grouped over the VIEWER's own predicate.
- `matched`: subsets of `tools`, `models`, `event_types`, `agents`, `tags` named by the query: all VIEWER-visible.
- Error messages/codes: `422` positions are a function of the query text; `403` is a function of the field name.
- Timing: no content predicate is in the statement, so no content-dependent work happens.
- Derived fields: `summary.*` has no content strings (`summary.py:152-172`); `files_modified` is a count the viewer already sees;
  `error_count`/`security_events` are counts of visible event types. `truncated` is the exception (F5). `run.name` and `metadata` are
  client-controlled free text visible to everyone today (KI-078); search makes scanning them across runs cheaper but reveals no new value.
  Note that the SDK's own redaction set lists `run.name` beside `shell.command` (`packages/sdk-python/src/blackbox/redaction.py:175-183`):
  the SDK already treats names as secret-bearing, which supports keeping KI-078 open rather than widening what is indexed.
- Inner join: a run absent for lack of a document is absent for every actor alike (F8 is a usability defect, not an oracle).

## Cross-plan items (merge order 9, 10, 16, 11): what Phase 16 must change

1. **`SUMMARY_VERSION`**: Phase 9 lands 3; Phase 16 sets **4** on rebase and updates `test_run_summary.py:199`, KI-081 and the Merge note
   (replace "take the higher number" with "merged value + 1"). (F3)
2. **Migration chain**: after Phase 10, head is `0060`; Phase 16 sets `0080.down_revision = "0060"` and inserts `"0080"` after `"0060"` in
   `test_migrations.py`; Phase 11 then rebases `0070` onto `0080`. Phase 16 must **not** add a `0047 -> head -> 0047` test (Phase 11 adds one;
   two tests of the same name conflict).
3. **`jobs/handlers.py::summarize_run`**: Phase 10 enqueues `detect_findings` after `apply_derivation`; Phase 16 adds the `run_search` upsert
   in the same place. Both are additive; order them "upsert, then enqueue" so a document exists before detectors (which may later add a
   findings array) run. Phase 11 adds a handler, not a hook.
4. **`runs/queries.py` and `runs/service.py`**: Phase 10 changes `summary_states(job_type=...)` and adds the findings badge lookup in
   `list_runs`; Phase 16 adds `search=` to `page()` and the `q` branch in `list_runs`. Phase 16 should call `summary_states` with the default
   argument and keep its `q` branch after the badge lookup, so the rebase is a block insert.
5. **`RunOut`/`RunPage`**: Phase 10 adds `RunOut.findings` (placed last); Phase 16 adds `RunOut.matched`, `RunPage.query`, `RunPage.facets`.
   Append after `findings`. Regenerate `openapi.json` and `schema.d.ts` after rebase (do not merge them).
6. **Route counts**: Phase 16 asserts "unchanged", not 31/36 (F26); after Phase 10 they are 32/37.
7. **Field catalogue**: (a) `agent:` becomes "contains in `run_search.agents`" (F14) to agree with Phase 9's multi-agent runs; the `agents`
   array in `run_search` should be built the same way as Phase 9's `summary["agents"]` (one source: pass the derivation's agent list to
   `build_document`, not a second walk). (b) Phase 10's findings: do **not** index findings in Phase 16 (they are written by a different job
   at a different time, so a `run_search` column would be stale between the two jobs). Reserve the field name `finding:` in the catalogue
   doc as "Phase 10 follow-up: the compiler joins `run_findings`/`findings` on `(workspace_id, run_id)`, a metadata field (Phase 10's
   evidence is content-free by construction)". Phase 11's evaluations likewise stay out (`verdict:` reserved).
8. **`globals.css`, `KNOWN_ISSUES.md`, `DECISIONS.md`, `PROJECT_STATE.md`, `IMPLEMENTATION_PLAN.md`, `README.md`**: append-only in the last
   commit, as all four plans say; KI numbers: Phase 16 takes KI-080..083, so Phases 9-11 must not (check their plans' numbering on rebase).

## Mutation checks to add (each must fail at least one test)

- Build the tsvector with `to_tsvector('simple', ...)` instead of the literal (F1): the prefix test `foo_b*` must fail.
- Return `truncated` to a VIEWER when only content was truncated (F5).
- Index the derived `Span.name` (F13): AC-4(b) must fail on `CMDCANARY`.
- Remove the concurrency cap (F2): the N+1 flood test must fail.
- Compute facets for an empty `q` (F6): the statement-count test must fail.
- Construct the 403 without `authorize()` (F12): the denial-log test must fail.

## What the plan gets right (keep)

The permission split as two columns with a compile-time gate; no counts and no ranking; the SQL-text assertion; identical-body probing
with canaries in every syntactic form; the statement counter for refused fields; the explicit, written triggers for trigram, external
search and the job split; a derived table instead of columns on `runs`; `q` on the existing route with no matrix change (correct: content
access is already the `payload.read` action, and `run.read` already gates the route; verified against `matrix.py` and `registry.py:207`);
the empty-table migration; the fuzzer; the hostile corpus; the NL compiler as a grammar-string protocol only.
