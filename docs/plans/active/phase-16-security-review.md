# Independent security review of the Phase 16 implementation (search and investigation)

Reviewed: `feature/phase-16-search-investigation` at c933770 (20 commits on `origin/main` merge-base 80595bd), in the worktree
`abb-worktrees/phase-16`, on 2026-10-11. Stance: adversarial, against the running code; the implementer's plan, plan review (d7f805f),
ADR-100/101, the Phase 15 security review and KI-078 were read first. Nothing in `apps/`, `packages/` or `scripts/` was changed by this
review (every mutation was reverted with `git checkout -- <file>` inside the same command; `git status` is clean but for this file).

Evidence vocabulary: **VERIFIED** (run here, or read at the cited file:line), **UNVERIFIED (env)**, **ASSUMED** (reasoned).
Severity: **P0** critical, **P1** must fix before merge, **P2** should fix, **P3** optional.

## Summary

**No P0, no P1.** The core question (can an actor without `payload.read` learn anything about withheld content through search?) holds:
I could not construct a query sequence, in any syntactic, Unicode or legacy-parameter form, whose items, facets, `matched`, flags,
cursor, status code, error body, response length or statement set depends on withheld content (section "Oracle analysis", all VERIFIED
with a throwaway probe against the Phase 15 world plus the implementer's own oracle suite). The gate is complete, the statements a viewer
triggers never name a content column, tenancy is bound on every statement, the literals are parameters, the read bounds are real.

Two P2 (a surviving mutation that leaves the content index's freshness unpinned by any test, and a grammar/URL regression for agent ids
with `.` or `_`), nine P3. Two further mutations survived; both are unpinned *claims* in ADR-101 "As built" rather than defects.

## Scope and method

- Read: `AGENTS.md`, the plan and its review dispositions, ADR-100, ADR-101, ADR-060..062, the Phase 15 review, KI-078, spec §77/§152;
  every file under `apps/api/src/abb_api/search/`, `abb_api/content.py`, `runs/{content,service,queries,search_view,router,schemas}.py`,
  `jobs/handlers.py`, `runs/summary.py`, `cli.py`, migration 0080, `db/tables.py`; `tests/search/*`, `tests/authz/{world,test_content_withheld,
  test_cross_workspace}.py`, `tests/test_migrations.py`; `apps/web/src/{lib/search.ts,components/{SearchBox,QueryChips,Facets,UnindexedNotice,
  RunsList,RunsListPage,RunsTable}.tsx,lib/queries.ts,fixtures/{api,search}.ts,server/upstream.ts}`, `e2e/search.spec.ts`,
  `scripts/auth-e2e.sh`, `scripts/bench_search.py`, `docs/benchmarks/phase-16-regression-check.md`, `docs/runbooks/search.md`.
- Ran (VERIFIED): `uv run pytest tests/search` (345 passed, 121 s), `uv run pytest tests/authz` (119 passed, 149 s),
  `pnpm exec vitest run tests/search.test.tsx tests/upstream.test.ts` (30 passed); eleven mutation checks (table at the end); a throwaway
  probe test (`tests/search/test_zz_review_probe.py`, written, run and deleted; never committed) sending 20 canary/nonsense pairs x 4
  parameter variants to `viewer` and `downgraded`; `git merge-tree` dry merges against `origin/main` and `origin/feature/phase-10-reliability-intelligence`.
- Not run: `scripts/auth-e2e.sh` (Playwright; the committed screenshots were inspected instead: `docs/screenshots/phase-16/viewer-refused.png`
  shows the canonical field name only and no value), the benchmarks (UNVERIFIED (env) as the implementer also states for the Colima VM).

## Verified correct (file:line)

- **Gate placement and completeness.** `compile_query` reports every `FieldTerm` whose catalogue row has `needs_content` before building any
  predicate and returns an empty `CompiledSearch` for a refused actor (`search/compiler.py:208-219`); `refused` is a property of the
  content list and `holds(PAYLOAD_READ)` (`:122-125`); `RunQueries.page`/`facets_and_unindexed` raise on a refused search as a second line
  (`runs/queries.py:161-162, 207-208`). The service parses (422), compiles, refuses through `authorize_audited(request, principal, PAYLOAD_READ)`
  and attaches `details.field = content_fields[0]` (canonical name, first use order) **before** the cursor is decoded, before `authorise_project`,
  before a permit or a connection (`runs/service.py:208-219, 294-307`). Field names reach the catalogue only through `_FIELD_NAME`
  (`[A-Za-z_][A-Za-z0-9_]*:`, ASCII) and `.lower()` (`search/parser.py:34, 141-143`): `FILE:`, `Command:` are refused; ligature `ﬁle:`,
  fullwidth `ｆｉｌｅ:`, `file​:`, `file :`, `"file:x"`, `--file:x`, `-"-file:x"` all fall through to a *text* term that compiles to
  `metadata_doc` only (probe: identical bodies to their nonsense twins, 200). `severity:` is rewritten to `errors` (`:225-228`), never to a
  content field. BILLING is refused by `require(RUN_READ)` before parsing (oracle test `test_billing_is_refused_before_the_query_is_read`,
  VERIFIED green); a key without `runs:read` likewise (`INSUFFICIENT_SCOPE`); a `runs:read` key holds `payload.read` (ADR-061).
- **Viewer statements never name a content column.** `page_columns()` adds `content_truncated` only with `content_allowed`
  (`compiler.py:127-133`); `_record` reads it only then (`queries.py:99-107`); `_text_predicate` ORs `content_doc` only with `content=True`
  (`compiler.py:199-205`); facets unnest `FACET_ARRAYS = (agents, tools, models, event_types)` (`:74`, `queries.py:239-245`); the unindexed
  count applies `row_predicates` (run-row columns) only (`queries.py:257-269`). The statement-text corpus test is **not vacuous**: the owner
  guard `test_the_same_check_would_catch_a_content_statement_for_an_owner` (`tests/search/test_search_oracle.py:229-238`) proves the assertion
  can fail, and mutations M1/M3/M13 below were killed by it.
- **Document split.** `build_document` routes every `is_content_attribute` key (registry `CONTENT_ATTRIBUTES` plus the leaf heuristic,
  `abb_api/content.py:24-25`) to the content side and only `tool.name`/`llm.model`/`llm.provider` (arrays) and `tool.operation`/`db.operation`/
  `http.method`/`span.name` (words) to metadata (`search/document.py:40-43, 161-174`); unregistered non-content attributes are dropped
  (`test_unregistered_non_content_attributes_are_not_indexed`). `metadata_truncated` depends only on metadata inputs (`:180-194`); `content_truncated`
  only on content (`:209-212`). The derived `Span.name` is never seen by the builder (it takes events, not spans). The SDK emits `shell.command`
  for shell spans and `span.name` only for other kinds (`packages/sdk-python/src/blackbox/client.py:137-141`), so the metadata document does
  not receive commands by that path (see F3 for a third-party client).
- **No count, no ranking, keyset only.** `RunPage` has `items`, `next_cursor`, `query`, `facets`, `facets_skipped` (`runs/schemas.py`);
  ordering is `(started_at, id)` of the joined index or of `runs` (`queries.py:165-181`); `test_a_search_page_has_no_total_count_and_no_relevance_field`.
  `MatchedOut` drops the `content_truncated` key entirely for a viewer (`schemas.py:38-46`).
- **Tenancy (INV-3).** `_base()` binds `workspace_id` and the key's `project_scope` (`queries.py:118-122`); `index_join` states the tenant on
  the joined side as a bound parameter (`compiler.py:256-265`); the facets CTE and the NOT EXISTS anti-join both repeat it (`queries.py:211-216,
  221-228, 258-267`); `RunSearchRepository` and `search-rebuild` are tenant-scoped (`search/repository.py:31-39, 112-133`; `cli.py` resolves
  the workspace slug first). Cross-workspace probes, dual-member header switch and cursor replay are green (`tests/authz/test_cross_workspace.py`).
- **Injection.** User strings are bound parameters only (`test_user_strings_are_bound_parameters_only`); `tsvector`/`tsquery` are literals built in
  Python with `'`/`\` escaping, NUL and length guards, cast with `CAST(:x AS tsquery)` (`search/tsliteral.py:15-44`; `compiler.py:200-201`);
  `to_tsquery`/`to_tsvector` never appear (`test_tsquery_is_cast_from_a_literal_and_to_tsquery_is_never_used`); LIKE escaping is `\`, `%`, `_` in
  that order with `ESCAPE '\'` (`compiler.py:78-80`); tsquery operators in free text become separators (`test_tsquery_syntax_in_free_text_is_a_separator_never_an_operator`);
  the hostile corpus (44 inputs) is 422 or a literal match, never 500 (`tests/search/test_search_hostile.py`). Error positions and reasons are
  functions of the text (`parser.py` raises only on grammar; probe: `file:x cost:>abc` is the same 422 at position 13 for viewer and owner).
- **Bounds (review F2).** The permit is taken before the connection (`service.py:242`: `async with gate.request() as budget, self._engine.connect()`),
  `locked()`/`acquire()` have no await between them (`search/reads.py:78-88`), each statement runs under `SET LOCAL statement_timeout = remaining`
  and `SET TRANSACTION READ ONLY` (`:48-68`), facets run only with >= 40% budget left and degrade to `facets_skipped: "budget"` (`service.py:259-275`),
  the page statement selects the arrays so `matched` is computed in Python (one statement), facets + unindexed are one CTE statement
  (`queries.py:188-291`). The flood test is truthful: three tasks, `search_max_concurrent=2`, an ingest `202` and a plain list `200` during the
  flood, `pg_stat_activity` and `pool.checkedout()` back to zero (`tests/search/test_search_budget.py:81-104`; mutation M11 killed).
- **Hash-skipped upsert.** `SearchDocument.hash` is the SHA-256 of a canonical JSON of **all 13 fields** (`document.py:63-70`) and the stored digest
  adds project, start time and version (`repository.py:55-64`); the upsert's `WHERE document_hash != excluded.document_hash` writes nothing
  when unchanged (`:94-101`; `test_an_unchanged_resummarisation_writes_nothing_and_a_change_does`). Complete by reading; see F1 for the test gap.
- **INV-2.** `search-rebuild` enqueues `summarize_run` for runs with `summary_version > 0` and no row (or an older version), in batches, audited
  as `search.rebuild` with no `q` (`cli.py`; `test_search_rebuild_restores_missing_documents_and_is_audited`); migration 0080 is an empty table
  plus five indexes with a cascading FK (`migrations/versions/0080_run_search.py`); `SUMMARY_VERSION = 4` with the pin updated (`runs/summary.py:28`,
  `tests/test_run_summary.py:209`); the runtime role's append-only set is unchanged (`tests/test_runtime_role.py:117`).
- **Logs and URLs.** The middleware logs `path` only, never the query string (`core/middleware.py:42, 49`); denial logging carries `actor_id` and
  the permission (`authz/audit.py:46-50`); `test_the_query_text_never_reaches_the_logs` and `test_the_refusal_is_logged_and_audited_without_the_query`
  are green. `Referrer-Policy: no-referrer` is set globally (`apps/web/next.config.ts:35`). The web proxy allowlist is unchanged (`server/upstream.ts:28-41`)
  and the 414 at 2,048 encoded characters is handled like a 422 (`lib/search.ts:135`; `tests/upstream.test.ts`).
- **Web.** Chips, the caret and the problem notice render text nodes only (`QueryChips.tsx`, `SearchBox.tsx:8-17, 43-53`); the 403 notice names
  `problem.field` only after a `^[a-z_]{1,32}$` check (`lib/search.ts:115, 128-134`) and never the value (screenshot VERIFIED); a viewer's help
  list is the API's `query.fields` (`RunsList.tsx`, `queries.ts::useSearchCatalogue`), so content fields are never offered; `convertLegacy` only
  rewrites `status`/`agent` into `q` and performs no navigation (no open redirect; `lib/search.ts:81-96`); `q` reaches the URL through
  `URLSearchParams` (`RunsListPage.tsx`). Facet values containing `*` are not clickable (`Facets.tsx:40-45`).
- **Plan-review P1 fixes landed.** F1: literals + same-position expansion (`tokens.py:67-91`, `tsliteral.py`, pinned on Postgres by
  `test_postgres_accepts_the_literal_syntax_we_build`). F2: permit-before-connection, one budget, two statements, 503 codes (above). F3:
  `SUMMARY_VERSION` 4, `query.unindexed` (`queries.py:257-278`, `search_view.py:77-83`). F4: migration/table/document commit (af1d3d3) precedes
  the compiler commit (eb16193).

## Oracle analysis (the core question)

Channels tried for `viewer` and `downgraded` (an OWNER demoted mid-session; its principal is rebuilt from the current role, so `holds()` is false):

| Channel | Result |
| --- | --- |
| match/no-match, items, order, `next_cursor`, facets, `matched`, flags, response length | Identical to the nonsense twin for every content canary (`CMDCANARY-curl-H-Bearer-s3cr3t`, `/home/PATHCANARY/.ssh/id_rsa`, `/srv/CWDCANARY`, `CUSTOMCANARY-rm-rf`, substrings `id_rsa`, `ssh`, `home`, `rm`, `s3cr3t`, `curl`, `src/au*`) in bare, prefix, phrase, negated, multi-negated, AND-with-allowed, AND-with-negated-allowed, NFKC confusable, zero-width, NBSP, quoted-field and escaped-field forms, each with `limit=1`, legacy `agent_id`, `started_after`, a junk `cursor` and a second page (implementer's oracle suite + my probe: 20 pairs x 4 variants x 2 actors, all `same=True`, `leak=False`). VERIFIED |
| `unindexed` count | Over run-row scope and run-row predicates only (`queries.py:257-269`); whether a row exists is a property of summarisation, not of content (no content-dependent failure path exists in `build_document`: tokens are <= 256 bytes, positions capped at 8,000, NUL cannot be stored). VERIFIED by reading |
| `metadata_truncated` vs `content_truncated` | Separate inputs (`document.py:180-212`); the content key is absent (not null) for a viewer. VERIFIED |
| error codes, positions, bodies | 422 before the gate (grammar only), 403 with the canonical field regardless of the value or of whether a match exists (`REFUSED_PAIRS`), 400 for a junk cursor only after the gate. Positions are text offsets. VERIFIED |
| statements executed, timing | A refused query executes no statement naming `runs`/`run_search`/`events`/`spans` (listener); a viewer's statements never reference a content column, so no content-dependent work happens. VERIFIED |
| `query.fields`, `content_allowed` | Catalogue filtered by permission; the existence of `file`/`command` is revealed by a 403 (by design, documented). VERIFIED |
| derived strings | `run.name`, tags, `metadata` leaves, `tool.name`, `span.name`, `tool.operation`, `db.operation`, `http.method`, agents, event types: all visible to a viewer on the existing routes (KI-078 boundary, accepted by the plan). `error_count`, `security_events` are counts of visible events. No summary string is built from commands (`runs/summary.py` builds counts, models, timestamps). VERIFIED |
| audit/log rows | No `q` anywhere (above). VERIFIED |

## Findings

### F1 (P2) A content-only change is not pinned to rewrite the index: the "content fields in the hash" mutation survives every test

Location: `search/document.py:63-70` (`SearchDocument.hash`), `tests/search/test_document.py:140-145`
(`test_the_hash_changes_with_any_searchable_fact`), `tests/search/test_search_index.py:84-141`.

Problem. The hash is complete today (all 13 dataclass fields), but every test that exercises "the hash changes" or "a changed document is written"
changes a **metadata** fact (run name, metadata leaf, error count, a new tool). Removing `commands`, `file_paths`, `content_doc` and
`content_truncated` from the hash leaves `tests/search/test_document.py` and `tests/search/test_search_index.py` green (mutation table, M-hash:
16 passed). The property that matters for an OWNER's search (a run that executes a new command whose event type and tool vocabulary are already
in the document gets a rewritten `content_doc`) is therefore unprotected against a refactor of the hash, the `_Bounded` collectors or the
upsert's `WHERE`.

Failure scenario (after such a refactor): an active run whose first summarisation held `npm install` later runs `curl -H "Authorization: Bearer ..."`;
every later debounced summarisation skips the write; `command:curl` never finds the run; the investigation view says "no runs match" while the
events page shows the command. A staleness bug in the sensitive half of the index with no failing test.

Suggested fix. Add to `test_the_hash_changes_with_any_searchable_fact` three cases that change only content (a second `shell.command.completed`
with a new command and the same tool/event vocabulary; a new `file.path`; a content value that trips `content_truncated`) and one end-to-end case in
`test_an_unchanged_resummarisation_writes_nothing_and_a_change_does` where the new event is `shell.command.completed` only, asserting `commands` and
`document_hash` changed. Five lines of test.

### F2 (P2) `agent:` and `root_agent:` reject agent ids the API accepts (`.` and `_`), so facets and converted bookmarks can produce 422

Location: `search/parser.py:35` (`_SLUG = [a-z0-9][a-z0-9-]{0,62}`) and `:185-189`; `runs/router.py:108` (`agent_id` pattern
`^[a-z0-9][a-z0-9._-]{0,63}$`); `apps/web/src/lib/search.ts:10` (`AGENT_SLUG` with `._`), `:81-96` (`convertLegacy`), `components/Facets.tsx:40-55`.

Problem. The grammar's slug is narrower than the API's own `agent_id` parameter and the web's `AGENT_SLUG`. VERIFIED on the world:
`agent:my_agent`, `agent:v1.2-bot`, `root_agent:my_agent` and `agent:"(other)"` are `422 not a valid agent slug` for an owner while
`GET /v1/runs?agent_id=my_agent` is `200`. Consequences: (a) an old bookmark `?agent=my_agent` is converted by `convertLegacy` into
`q=agent:my_agent` and now fails with "This search could not be understood" where it listed runs before this phase (a user-visible regression
of a URL contract the plan promised to keep, AC-10); (b) the `agents` facet lists every agent of the run (`summary["agents"]`, including
`(other)` and ids with `.`/`_`), and clicking one appends `agent:<value>`, which is a 422 for those values (`Facets.tsx` only guards `*`).

Suggested fix. Make `_SLUG` the API's pattern (`[a-z0-9][a-z0-9._-]{0,63}`) and treat `(other)` as unsearchable in the facet (not clickable,
like `*`), or drop it from the `agents` array at document time. Add `agent:my_agent` and `?agent=my_agent` to `test_parser.py` and
`tests/search.test.tsx` (legacy conversion). Not a security issue: nothing leaks, the term is simply refused.

### F3 (P3) The `span.name` attribute is indexed for every span kind, although the spans route withholds derived names of shell/file/git spans

Location: `search/document.py:43` (`_TEXT_ATTRIBUTES` includes `span.name`), `abb_api/content.py:21, 28-29` (`_CONTENT_SPAN_KINDS`),
`packages/event-schema/src/abb_event_schema/spans.py:44`, `runs/service.py:110-111`.

Problem. Phase 15 withholds the *derived* span name for kinds `None|shell|file|git` because `Span.name` falls back to the command or path.
`spans.py:44` takes the `span.name` attribute first; a third-party client (the SDK does not, `client.py:137-141`) that sends
`span.started {span.kind: "shell", span.name: "<command>"}` gets its name withheld on `GET .../spans` but shown on `GET .../events` (the attribute
is reviewed metadata) and, now, searchable across every run of the workspace by a VIEWER. ADR-101's rule holds (the events route already
shows it), so this is the KI-078 boundary, inherited exactly as the plan says, but search makes the inconsistency between the two routes
cheaper to exploit (one query over all runs instead of paging events per run).

Suggested fix. Either skip `span.name` when the event's `span.kind` is a content kind (one condition in `build_document`), or record the
case explicitly in KI-078/ADR-101 (7). Add a document test for a `span.started` with `span.kind: shell`.

### F4 (P3) Two ADR-101 "As built" claims are not pinned: the gate runs before the cursor is decoded, and the search transaction is read-only

Location: `runs/service.py:214-219`, `search/reads.py:58-60`; ADR-101 "As built" first bullet, `reads.py` docstring.

Problem. Mutation M7 (decode the cursor before the gate) and M16 (drop `SET TRANSACTION READ ONLY`) survive `tests/search` (35 and 21 tests
respectively). Neither is an oracle today (a junk cursor is a 400 whose body is data-independent; the statements are SELECTs), but both are
stated guarantees. The first would turn a viewer's `file:x` + stale cursor into `400 CURSOR_INVALID` instead of `403`, which the web would
explain as a paging problem rather than a permission one.

Suggested fix. In the oracle refusal test add a junk and a well-formed foreign cursor (`403` either way); in `test_search_budget.py` assert
`SET TRANSACTION READ ONLY` is among the captured statements of a search (the listener already exists) or that a monkeypatched page statement
that writes fails with `read-only transaction`.

### F5 (P3) The rebase notes understate the Phase 10 merge: a dry merge conflicts in 12 files

Location: plan "Merge notes"; `git merge-tree --write-tree origin/feature/phase-10-reliability-intelligence HEAD` (VERIFIED).

Content conflicts: `cli.py`, `db/tables.py`, `runs/queries.py`, `runs/schemas.py`, `runs/service.py`, `tests/test_migrations.py`,
`apps/web/src/components/RunsTable.tsx`, `apps/web/src/lib/api/types.ts`, `docs/DECISIONS.md`, `docs/PROJECT_STATE.md`, `docs/TESTING.md`,
`docs/architecture/api-v1.md`. All are "both appended at the same spot" conflicts and the notes' resolutions are right (`0080.down_revision = "0060"`
plus the list entry; `RunOut.findings` before `matched`; `summary_states` default; the `run_search` upsert before `_enqueue_detect`, which
`jobs/handlers.py` auto-merges in that order because Phase 10's enqueue sits after the analytics loop). `SUMMARY_VERSION` auto-merges to 4
(Phase 10 keeps 3). `openapi.json`/`schema.d.ts` must be regenerated, as the notes say. Not a defect; the PR description should list the twelve
files so the merge is not treated as a "block insert". Likewise the branch is behind `origin/main` (Phase 9 closed: plan docs moved to
`completed/`, KI-084..086 filed): a merge conflicts in `docs/KNOWN_ISSUES.md` and `docs/PROJECT_STATE.md` and relocates the Phase 16 plan
files (VERIFIED with `merge-tree`); the branch did not regress these, it just needs the merge.

### F6 (P3) The unindexed count is an anti-join over the whole scope once nothing is unindexed

Location: `runs/queries.py:257-278`.

`SELECT count(*) FROM (... NOT EXISTS run_search ... LIMIT 1001)` can stop early only while unindexed runs exist; in the steady state (every run
indexed) it visits every run of the workspace/project/time scope on each search with an index term. Bounded by the budget (facets are skipped,
`200`), and 100,000 runs measured fine, but at Stage A scale the facets of every search are the first thing to disappear. ASSUMED from the
plan shape (no EXPLAIN run). Suggested: compute the count only when the run-row scope is time-bounded or project-bounded, or cache per
(workspace, project) with the summarizer decrementing it; at least document it in the runbook's "timeouts" section. Related: a run created with
`POST /v1/runs` that never receives events is counted as "not yet indexed" forever (`summary_version = 0`, no row) and `search-rebuild` cannot
index it or a run whose summarisation dead-lettered (`repository.py:124` requires `summary_version > 0`): the runbook should say so.

### F7 (P3) The range control turns every polled runs page into a search with facets

Location: `apps/web/src/lib/search.ts:98-107` (`effectiveQuery`), `components/RunsList.tsx` (`q: effective`), `lib/queries.ts:50`
(`refetchInterval: POLL_MS * 5`).

Review F6 moved facets and `matched` behind "at least one term". The range control now adds `started:>=-24h` to `q`, so a page with any range
selected is a search (permit, budget, facets CTE over the newest 1,000 runs with a LEFT JOIN) every 15 s per open tab. Functionally right
(bookmarks stay relative) and bounded, but it re-creates the load F6 removed for the most common view. Suggested: when `q` has only the range
term, send `started_after` (the legacy parameter) instead, and keep `q` for typed terms.

### F8 (P3) `search_max_concurrent` permits are held while waiting for a pooled connection, outside the budget

Location: `runs/service.py:242`, `db/engine.py` (`pool_timeout=5`), `search/reads.py:36`.

The budget's clock starts when the permit is granted; `engine.connect()` may then wait up to `pool_timeout` (5 s) if ingestion holds the pool,
and `authorise_project` runs before the first `statement_timeout`. A search can therefore hold a permit for budget + 5 s and return
`503 DEPENDENCY_UNAVAILABLE` rather than `SEARCH_TIMEOUT`. Harmless for ingestion (the point of the cap); worth a sentence in KI-082.

### F9 (P3) `convertLegacy` keeps a `status` list beside `q`, so the two filter systems coexist in one URL

Location: `apps/web/src/lib/search.ts:81-96`, `components/RunsList.tsx` (legacy status checkboxes and the Agent input still send
`status`/`agent_id`).

Two statuses stay the legacy OR parameter while one becomes a term; the Agent input stays a legacy parameter while a facet click writes a term.
Documented as a deviation and ANDed correctly by the API, but a user can now hold `status:failed` in `q` and tick "Success" in the checkboxes and
get an empty page with no explanation. Suggested: show the legacy parameters as chips too (removable), or fold single-status checkboxes into `q`
as the plan originally said.

### F10 (P3) Fixture-mode search re-implements a grammar subset in TypeScript

Location: `apps/web/src/fixtures/search.ts` (a 200-line scanner), `fixtures/api.ts`.

The plan's D1 says the UI never parses `q`; the fixtures do, for demo mode. It answers a subset and returns 422 for the rest, so it cannot
mislead the Playwright fixture spec about content search, and `search.spec.ts` runs against the real API. Risk is drift (the fixture accepts
`agent:my_agent`, the API does not: F2). Suggested: keep the fixture table-driven over canned `(q -> response)` pairs instead of a scanner, or
mark the module "demo only; not the grammar" in the file header (it already says so) and add F2's case to its tests.

### F11 (P3) Benchmark caveats to carry into the PR

Location: `docs/benchmarks/phase-16-regression-check.md`.

The A/B is honest about its limits (native processes, not the Colima VM the AC named; three rounds; medians; the 30,000-event p95 declared
bimodal; the 4-client +18% traced to one 215 ms run). Two numbers sit close to the trigger: summary freshness +13.1% (2,000 events) against a
20% threshold with a 14% noise band, from three samples with one 1.77 s outlier, and the 4-client case I p95 +18%. The conclusion "trigger not
tripped" is defensible under the plan's own rule (band **and** threshold), but the VM re-measurement should be a listed follow-up with a date,
and `scripts/bench_search.py` seeds `to_tsvector` corpora (its own header says so), so the recorded GIN sizes are not the production
builder's. Nothing to change in the code.

## Attack list disposition (brief items 1-9)

1. Gate completeness: no bypass found (case, Unicode, escaping, aliases, reserved words, quoted forms, `field:` in phrases; keys, users, downgraded; BILLING before parse). VERIFIED.
2. Content columns in viewer statements: none; corpus assertion non-vacuous (owner guard; M1/M3/M13 killed). VERIFIED.
3. Classification: registered content → content; unregistered content-named → content (M9 killed); unregistered other → dropped; viewer-matchable metadata values are those already visible (list in "Oracle analysis"); `span.name` of content-kind spans is the one soft spot (F3).
4. Tenancy: bound on page, facets, unindexed and the join; cursor replay, project-bound keys, foreign ids identical 404s; `search-rebuild` scoped. VERIFIED (suite + M4/M15 killed).
5. Injection/parsing: literals, binding, escaping order, limits, hostile corpus, Cs/Cc refused, `%2B`, `-` rules, `started:<date>` as a day. VERIFIED. ReDoS: every regex is linear (`fullmatch` on bounded alternations, no nested quantifiers) - ASSUMED by reading.
6. Bounds: permit before connection (no await between check and acquire), `SET LOCAL statement_timeout`, read-only (unpinned, F4), flood test truthful (M11 killed), facets degrade, hash skip complete by reading (test gap F1), bench honest (F11).
7. INV-2: rebuildable, `unindexed` behaviour pinned, old runs after version 4 handled by `search-rebuild`, deletes cascade; Phase 10 notes workable but twelve conflicts (F5).
8. Web: text-only rendering, canonical field only, viewer never offered content fields, `Referrer-Policy`, 414 handled, no redirect in legacy conversion, `q` in history accepted (ADR-101 (8)), fixtures (F10).
9. Tests: oracle canaries are really indexed (owner finds them: `test_actors_with_payload_read_find_the_content`), guards exist for the statement assertion, the `total` mutation was caught and pinned; remaining gaps F1/F4; no flaky gate observed in two full runs of `tests/search` and `tests/authz`.

## Mutation checks (each applied, run, reverted; `git status` clean afterwards)

| # | Mutation | Tests run | Result |
| --- | --- | --- | --- |
| M-hash | `SearchDocument.hash` excludes `commands`, `file_paths`, `content_doc`, `content_truncated` | `tests/search/test_document.py`, `test_search_index.py` | **SURVIVED** (16 passed) → F1 |
| M1 | viewer free text compiled with `content=True` | `test_compiler.py` | killed: `test_no_statement_for_a_viewer_ever_mentions_a_content_column` |
| M2 | `CompiledSearch.refused` returns `False` | `test_compiler.py` | killed: `test_content_fields_are_reported_and_nothing_is_built_for_a_viewer` |
| M3 | `page_columns()` selects `content_truncated` for everyone | `test_compiler.py` | killed: `test_no_statement_for_a_viewer_ever_mentions_a_content_column` |
| M4 | `runs.workspace_id` predicate dropped from the facets CTE | `test_compiler.py`, `tests/authz/test_cross_workspace.py` | killed: `test_every_statement_binds_the_workspace` |
| M7 | cursor decoded before the content gate | `test_search_oracle.py`, `test_search_http.py` | **SURVIVED** (35 passed) → F4 |
| M9 | leaf-name heuristic removed from `is_content_attribute` | `test_document.py` | killed: `test_content_side_holds_commands_paths_and_unregistered_content_leaves` |
| M11 | semaphore refusal removed (searches queue) | `test_search_budget.py` | killed: `test_a_flood_gets_timeouts_and_one_busy_...` |
| M13 | tools facet unnests `file_paths` | `test_search_oracle.py` | killed: `test_every_form_of_a_content_word_returns_what_a_nonsense_word_returns[viewer]` |
| M15 | tenant bound dropped from the join's ON | `test_compiler.py` | killed: `test_every_statement_binds_the_workspace` |
| M16 | `SET TRANSACTION READ ONLY` removed | `test_search_budget.py`, `test_search_http.py` | **SURVIVED** (21 passed) → F4 |

Not re-run (the implementer's table, consistent with the code read): `to_tsvector` instead of the literal, ranking by `cardinality(tools)`,
`statement_timeout` removed, `MAX_TERMS` raised, `authorize_audited` removed, the `total` count.

## Verdict

Mergeable from a security standpoint once F1 (test) and F2 (grammar/URL regression) are addressed; F3-F11 can follow as KI entries or small
commits. The oracle protection is real, tested in a way that can fail, and survived every spelling I could think of.
