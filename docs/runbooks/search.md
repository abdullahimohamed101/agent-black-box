# Runbook: search (`GET /v1/runs?q=`)

Search reads a derived table, `run_search`, written by the summarizer in the same transaction as the run row (ADR-100). Nothing here is a source of truth:
everything is rebuilt from events.

## Runs are "not yet indexed"

The web page and `query.unindexed` say how many runs in the searched scope have no document. They match no *index* term (`tool:`, `model:`, `agent:`, `event:`,
`tag:`, `errors:`, `severity:`, `security:`, `file:`, `command:`, free text), neither positively nor negated; run-row terms (`status:`, `cost:`, `retries:`, `duration:`,
`started:`, `name:`, `root_agent:`) still see them.

- New runs are indexed by the summarizer within the usual debounce (about 1 s). A run that stays unindexed while `summary_state` is `processing` or `failed` is a worker
  problem: see `worker-lag.md` and `dead-letters.md`.
- Runs summarised before `SUMMARY_VERSION` 4 (Phase 16) need one rebuild (KI-081):
  `python -m abb_api.cli search-rebuild --workspace W [--project P] [--since 2026-10-01] [--limit 10000]`. It enqueues `summarize_run` for runs that have no document (or an older
  version), in batches, and writes an audit row `search.rebuild` with actor `cli`. It re-summarises, so cost lines are rewritten and analytics refreshes are queued: run it
  off-peak and in slices (`--since`, `--limit`).

## Searches fail with 503

- `SEARCH_BUSY` (retryable, `Retry-After: 1`): all `ABB_SEARCH_MAX_CONCURRENT` (default 4) permits of this API process are in use. Searches never queue and never take a
  database connection before a permit, so ingestion and the plain list are not affected. Raise the cap only with the connection pool in mind (the cap is validated at 1 to 14).
- `SEARCH_TIMEOUT` (retryable): the page statement ran past `ABB_SEARCH_TIMEOUT_SECONDS` (default 5). Narrow the time range or the project. A search whose *facets* statement
  runs out of budget still succeeds with `facets: null, facets_skipped: "budget"`.
- If many users see timeouts: check that the GIN indexes exist and are not bloated (`\di+ ix_run_search_*`), then the measured triggers in ADR-100 (`pg_trgm` for `command:` and `file:`
  past 1 s at your volume, `docs/benchmarks/phase-16-regression-check.md`).

## Migration 0080

It creates an empty table and its indexes; no backfill. Its foreign key briefly takes a `SHARE ROW EXCLUSIVE` lock on `runs` that waits behind any long transaction (KI-056):
run it outside bulk rebuilds (`rebuild-costs`, `search-rebuild`) and long analytics sessions. Downgrade drops the table (derived data).

## Retention and privacy

`run_search` holds command lines and file paths (`commands`, `file_paths`, `content_doc`) of runs: it is as sensitive as the events, cascades with `runs` and is reachable by no route
except through the search compiler. A retention job that deletes runs removes it with them (Phase 19). The API logs no query text; the audit log records `search.rebuild`, never `q`.
