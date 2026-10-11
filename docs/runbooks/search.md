# Runbook: search (`GET /v1/runs?q=`)

Search reads a derived table, `run_search`, written by the summarizer in the same transaction as the run row (ADR-100). Nothing here is a source of truth:
everything is rebuilt from events.

## Runs are "not yet indexed"

The web page and `query.unindexed` say how many runs in the searched scope have no document. They match no *index* term (`tool:`, `model:`, `agent:`, `event:`,
`tag:`, `errors:`, `severity:`, `security:`, `file:`, `command:`, free text), neither positively nor negated; run-row terms (`status:`, `cost:`, `retries:`, `duration:`,
`started:`, `name:`, `root_agent:`) still see them.

- New runs are indexed by the summarizer within the usual debounce (about 1 s). A run that stays unindexed while `summary_state` is `processing` or `failed` is a worker
  problem: see `worker-lag.md` and `dead-letters.md`.
- A run created with `POST /v1/runs` that never receives events, and a run whose summarisation dead-lettered, have no document and stay in the "not yet indexed" count:
  `search-rebuild` selects runs with `summary_version > 0`, so it does not index them (KI-111). Re-enqueue a dead-lettered summary as in `dead-letters.md`.
- The count itself is an anti-join over the whole scope once nothing is unindexed; under load it is the first thing the budget drops (KI-110).
- Runs summarised before `SUMMARY_VERSION` 4 (Phase 16) need one rebuild (KI-081):
  `python -m abb_api.cli search-rebuild --workspace W [--project P] [--since 2026-10-01] [--limit 10000]`. It enqueues `summarize_run` for runs that have no document (or an older
  version), in batches, and writes an audit row `search.rebuild` with actor `cli`. It re-summarises, so cost lines are rewritten and analytics refreshes are queued: run it
  off-peak and in slices (`--since`, `--limit`).

## Searches fail with 503

- `SEARCH_BUSY` (retryable, `Retry-After: 1`): all `ABB_SEARCH_MAX_CONCURRENT` (default 4) permits of this API process are in use. Searches never queue and never take a
  database connection before a permit, so ingestion and the plain list are not affected. Raise the cap only with the connection pool in mind (the cap is validated at 1 to 14).
- The budget starts when the permit is granted, but a request can still wait for a pooled connection (`pool_timeout`, 5 s) and the project check before its first statement, so a
  permit can be held for the budget plus up to 5 s while ingestion holds the pool, and that search answers `503 DEPENDENCY_UNAVAILABLE` rather than `SEARCH_TIMEOUT` (KI-082).
  Harmless for ingestion (that is what the cap is for); if it shows up, look at pool saturation, not at the search statements.
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
