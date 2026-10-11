# ADR-100: Search Is a Typed Filter Grammar Compiled Server-Side Over a Derived Per-Run Postgres Search Document

Status: Accepted (Phase 16; the independent security review is pending)
Date: 2026-10-11
Phase: 16

## Context
Spec §152 asks for a token grammar parsed into a typed AST compiled to storage queries so the UI never builds query strings; §77.1
wants structured search fast, explainable and permission-safe; §77.2 allows PostgreSQL full-text search for selected textual metadata and defers a
dedicated index until necessary; §57.2 defers ClickHouse-class stores to measured triggers. Runs already carry typed derived columns (ADR-043) and a
summarizer that rebuilds derived state from events (ADR-012, INV-2). Postgres' text-search functions always run their own parser, which splits
and classifies identifiers and paths differently from any tokeniser we control.

## Decision
(1) One grammar (flat conjunction of `field:op?value` terms, free text, per-term negation, quoted phrases, trailing-`*` prefix on
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

## Alternatives
Querying `events.attributes` with a JSONB GIN (every search a scan of the largest table; withholding re-implemented in SQL).
Columns on `runs` (bloats the hot row). `to_tsvector('simple')` on pre-tokenised text (re-parsed: `foo_bar` splits, `src/auth.ts` becomes a
file token, phrases and prefixes break). Trigram everywhere (2-3x index size before a need is measured). An external index now (a second
system of record for Class 2 content with its own tenancy to prove). `OR`/grouping in v1. Relevance ranking (an ordering oracle, ADR-101).
Per-statement timeouts alone (seven statements per request could hold a pooled connection for 35 s and starve ingestion).

## Consequences
Search is derived, rebuildable and tenant-keyed like everything else; runs summarised before `SUMMARY_VERSION` 4 need one
re-summarisation (bounded `search-rebuild`, which also rewrites cost lines); the summarizer does slightly more per changed run (measured A/B);
adding a field is one catalogue row, one document line and one test row; the UI has no grammar logic; search can never take more than four
of a process's fifteen connections.

## As built (deviations from the plan's draft)
- Array values (`agents`, `tools`, `models`, `providers`, `tags`) are stored NFKC + case-folded, so those fields are case-insensitive and `matched` shows the
  normalised spelling. A document value longer than 256 characters (4,096 for content) is dropped and sets that half's truncation flag; arrays also stop at 64 KiB.
- `document_hash` covers the document plus project, start time and `SUMMARY_VERSION`, so a version bump or a moved start rewrites the row.
- `agents` is read from Phase 9's `summary["agents"]` (at most 50 entries plus `(other)`): one source list for the run page and `agent:`.
- A query with no index term left-joins `run_search` in the facets statement (the status facet counts every run); the unindexed count also applies the query's
  run-row predicates. A facets statement cancelled by the budget degrades to `facets: null, facets_skipped: "budget"` (200) rather than failing the search.
- The timeout code is `503 SEARCH_TIMEOUT`, as drafted (not 504). The gate is created lazily on `app.state` from the router.
- Measured (docs/benchmarks/phase-16-regression-check.md): the summarizer write did not trip the D7 trigger; no `pg_trgm` index is needed at 100,000 runs.
