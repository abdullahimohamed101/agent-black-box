# Project State

Last updated: 2026-10-11

## Current phase
Phases 0-9 and 15 are merged to `main` (CI green; Phase 15 is PR #58, Phase 9 is PR #73). The MVP gate (spec §49, §138) is reached but **not accepted**: the user decides
after reviewing the items under "Next actions". Phases 10, 16 and 11 are being built on their own branches (worktrees), merge order 10, 16, 11; Phase 10 is built and
reviewed and its review findings are being fixed. **Phase 16 (search and investigation) is implemented on `feature/phase-16-search-investigation`; its independent security review
found no P0/P1; its P2/P3 findings are fixed or filed** (plan status "Implemented; pending security review"; closing evidence for AC-1..14 is in the plan). Phase 11 starts after 16.

## Current milestone
M0-M3 complete (foundation, ingestion, SDK, web, live streaming, coding-agent demo, cost and analytics). Phase 8 (integrations) is
post-MVP and also done. Phase 15 (auth, RBAC) is done. M4 (multi-agent tracing, Phase 9) is done.

## Completed work
- Phases 0-2: foundation, event contract, ingestion/outbox/query API (`docs/plans/completed/`).
- Phase 3: `packages/sdk-python` (stdlib only, ADR-013); KI-020 fixed (`abb_runtime` role, migrations 0007-0008).
- Phase 4: `apps/web` (dashboard, runs list, run detail, typed client, fixtures, server-side read proxy, ADR-020/021).
- Phase 5: SSE live streaming (`GET /v1/runs/{id}/stream`, arrival-time resume, NOTIFY wake-up, ADR-022), polling fallback.
- Phase 6: artifact store (ADR-030, migration 0044), coding telemetry, command risk classifier and secret-safe capture (ADR-031; capture is
  best-effort and opt-in, accepted gaps are listed there), local syntax highlighting (ADR-032), `blackbox.coding`, web story/diff/shell panels,
  `examples/coding-agent`, `scripts/coding-e2e.sh`.
- Phase 7: CostEngine, analytics rollups (migrations 0040-0043), `/v1/analytics/*`, analytics page (ADR-040..043).
- Phase 8: adapters for LangGraph, OpenAI, Anthropic and MCP, conformance suite, SDK 0.2.0 (ADR-050..052).
- Phase 10 (implemented on its branch, pending review): reliability findings. Eight pure detectors (repeated sequence, identical request, retry storm, context growth, cost spike,
  tool-failure cascade, failure chain with probable causes, reported loop), the §81.2 finding contract, explained health dimensions with no score (ADR-080/081, migration 0060,
  `GET /v1/runs/{id}/findings`, `RunOut.findings` badge, web panel with "Show evidence", `rebuild-findings` CLI, `docs/runbooks/findings.md`). Evidence is metadata only, so viewers see what owners see.
- Phase 15: OIDC sign-in with API-owned sessions (ADR-060), one `authorize()` path and a role/scope matrix with a route-walking test registry (ADR-061),
  invitations, members, API-key and pricing-override management, append-only audit log (ADR-062, migrations 0045-0047), `payload.read` (VIEWER/BILLING never see content),
  stream re-authentication, web sign-in/switcher/settings, the shared web key removed; resolves KI-029, KI-027, KI-051, KI-033; viewers also never see command text or file paths. Closing evidence is in the plan; the independent security
  review (26 mutation checks, none survived) and its fixes are in `docs/plans/completed/phase-15-security-review.md`.

- Phase 9: `run.agent()`/`agent.spawned`, trace-context propagation across threads, asyncio tasks, forks and processes (SDK 0.3.0, ADR-070),
  per-agent run summary and root identity from `run.started` (`SUMMARY_VERSION` 3, **the next bump is 4**), browser-derived span tree and waterfall with lanes, zoom,
  critical path and agent tree (ADR-071), multi-agent fixture, `examples/multi-agent`, `scripts/multiagent-e2e.sh` (CI job `multiagent-e2e`); no migration, no new
  route. KI-063 spike measured and not shipped (`docs/benchmarks/phase-9-ki063-langgraph-threads.md`); KI-084..086 are GitHub issues. An independent security review (13 mutation checks; one P1, the SDK stripped NUL bytes after redaction, now fixed with regression tests) is in `docs/plans/completed/phase-9-security-review.md`.

- Phase 16 (branch, not merged): `GET /v1/runs?q=` (§152 grammar, `docs/architecture/api-v1.md` "Search"), the derived `run_search` table (migration 0080, `SUMMARY_VERSION` 4, written by the summarizer in the run's
  transaction, hash-skipped, `search-rebuild` CLI, `docs/runbooks/search.md`), permission-safe content search for actors with `payload.read` (ADR-100, ADR-101: viewers get `403` for `file:`/`command:`, and
  nothing else differs from a nonsense word), bounded reads (4 permits per process, 5 s budget, two statements), the web search box with chips from the server's parse, matched column, facets and the unindexed
  notice (`apps/web/e2e/search.spec.ts` in `auth-e2e`), the rule-based `QueryCompiler` fake; `scripts/bench_search.py`; KI-080..083 filed as "to file". Measured: no D7 trigger, no `pg_trgm` need at 100,000 runs
  (`docs/benchmarks/phase-16-regression-check.md`).

## In-progress work
Phase 16 (search and investigation) is implemented, independently reviewed and its findings fixed (`docs/plans/active/phase-16-security-review.md`; KI-110..115 filed); it is merged with Phase 10 on this branch and awaits its PR, then `complete-phase`.
Phase 10 (PR #79) is on main; its `complete-phase` close-out (plan and reviews to `completed/`, KI-090..099 as GitHub issues) is still to do.
Phase 11 is built in its own worktree under `../abb-worktrees/`; nothing is pushed from the worktrees until a phase is fully ready.

## Blocked work
None.

## Next actions (exact)
1. User: the one manual sign-in with a real OIDC provider (AC-14, KI-067; needs your own client id and secret, see `docs/runbooks/auth-and-access.md`), and a compose
   runtime check of the demo login (`make up && make seed`, sign in as `owner@local.test`, `make smoke && make sdk-e2e`; KI-074).
2. User: decide MVP gate acceptance. Remaining gate item: KI-050 (OpenAI prices and tiered models are not in the built-in table; the engine has no price tiers).
3. Finish and merge Phase 10, then 16 and 11 (rebase notes in each plan's "Merge notes"; Phase 16 must use `SUMMARY_VERSION` 4); then 13 and 14 in sequence.
4. File GitHub issues for the Phase 16 known issues (KI-080..083 and KI-110.., placeholders say "to file"). After deploying Phase 16, run `search-rebuild` for existing workspaces (KI-081).

## Open decisions
- MVP acceptance (after the gate items above).
- Remove the `Co-Authored-By` trailers from the 5 earliest commits (needs a force-push; not done).
- The shared web read key is gone (Phase 15), but sign-in has only run against the fake provider (KI-067): configure a real provider and run the manual check before exposing the app.
- Whether to stamp `received_at` with the database clock (multi-instance clock skew vs the stream overlap, ADR-022).

## Known technical debt
`docs/KNOWN_ISSUES.md` (severity, target and GitHub issue per item): notably (Phase 15 additions KI-067..078: real-provider login unverified, no session list, no `email_verified`-less providers, audit retention, trusted proxies, compose login unverified) full recomputation of very large active runs (KI-016), no quotas (KI-018), no failed-auth throttling (KI-019).

## Last verified test status
TODO_VERIFIED
Previous (Phase 10 branch):
2026-10-11, Phase 10 branch (run as parts of `scripts/quality.sh full`, own databases): event-schema 348, sdk 806 (coverage 94.06%), examples 12, integrations 17/74/66/61/44, api 1002 (901 + authz 101),
web vitest 431, lint/format/mypy/tsc clean, `schema-check`/openapi/web client current, migrations up/down/up to 0060, `next build`. E2E: `findings-e2e` passed (6 browser tests), `fixtures.spec.ts` 12 passed.
`coding-e2e`, `stream-e2e`, `analytics-e2e`, `e2e-web-real`, `auth-e2e` not run on this branch (UNVERIFIED (env): ports reserved for parallel worktrees). Benchmark: `docs/benchmarks/phase-10-regression-check.md`.
2026-10-11, Phase 9 branch (parts of `scripts/quality.sh full`, own databases `abb_test_p9`/`abb_p9`): event-schema 351, sdk 883 (coverage 94.18%), examples 20 (12 coding-agent, 8 multi-agent),
integrations 17/74/66/61/44, api 860 passed, 1 failed (environment-dependent: `test_summarizer.py::test_a_burst_of_batches...` fails on `c212058`'s code too because the shared Docker
Postgres clock runs about 2.2 s ahead of the host; `test_hostile_numbers` flaked once for the same reason), web vitest 517, eslint/prettier/tsc/`next build` clean, migrations up/down/up (head 0047), openapi/schema checks clean
(no OpenAPI or migration drift). E2E: `multiagent-e2e` 5, fixtures.spec 13, `stream-e2e` 5, `coding-e2e` 6, `analytics-e2e` 4, `auth-e2e` 8 (one Chrome/axe infrastructure failure on the first run, green on re-run).
Previous: 2026-10-09, Phase 15 branch: `scripts/quality.sh full` run as its parts, all green: event-schema 348, sdk 806 (coverage 94%), examples 12, integrations 17/74/66/61/44, api 799, web 396; migrations 0045-0047 up/down/up; `next build`.
E2E (own databases): `auth-e2e` 8, `e2e-web-real` 2, `coding-e2e` 6, `stream-e2e` 5, `analytics-e2e` 4, `integrations-e2e` passed. Not run: `make smoke`, `make sdk-e2e`, `make up` (compose ports busy; UNVERIFIED (env), KI-074).
Previous (main): 2026-10-09 (Phase 6 branch merged with Phases 7 and 8): `scripts/quality.sh full` exit 0, api 535, web 311, plus event-schema, SDK, example and integration suites; GitHub CI green on PR #49 (18 checks incl. `coding-e2e`, `integrations-e2e`, `stream-e2e`). A manual browser run of the demo (runs list, run page, diff panel, analytics) worked; findings are KI-064..066.

## Last verified build status
Compose stack: last fully verified at Phase 2 (images build; clean-slate `docker compose up --wait` reached alembic 0006 with 15 tables;
`scripts/smoke.sh` passed; 858-request hostile probe: zero 5xx). **UNVERIFIED (env)** since: the compose stack was not rebuilt after Phase 2
(migrations 0007-0044, the `abb_runtime` role, streaming, artifacts, analytics). CI's `containers` job covered Phase 3's compose run; run `make up && make smoke
&& make sdk-e2e` as part of the MVP gate. Later phases were verified outside compose by their own E2E scripts (own API, worker, web and database).

## Commands to verify environment
```bash
colima status || colima start --cpu 2 --memory 4
cp -n .env.example .env && make setup
scripts/quality.sh full
make up && make smoke        # then: make down
```
