# Project State

Last updated: 2026-10-13 (Phase 14 implemented on `feature/phase-14-policy-and-approvals`, review fixed, push pending; Phase 13 merged, PR #143)

## Current phase
**Phase 14 (policy engine and approvals)** is implemented on `feature/phase-14-policy-and-approvals` (worktree `abb-worktrees/phase-14`) and has had its adversarial review (`docs/plans/active/phase-14-code-review.md`: P1 1, P2 5, P3 10) and every finding is fixed or dispositioned with fail-before tests (plan: 'Code review dispositions'; migration 0094, ADR-141 Amendment 2, ADR-142 Amendment 1); what remains is the push and the PR (not pushed: needs approval). Plan with closing evidence for all eight gates, the findings of the real stack and the modified-tests table: `docs/plans/active/phase-14-policy-and-approvals.md`. Built: ADR-140 (policy model, versions, dry run; migration 0092), ADR-141 with Amendment 1 (decisions, approvals, single-use intent binding enforced in the database, long-poll waits, expiry job, server-written events; migration 0093), ADR-142 (the SDK guard contract); SDK 0.4.0 (`guard`, `@guarded`, fail modes, standard library only), MCP adapter 0.2.0, the coding helpers and `examples/coding-agent --policy-demo` with `policies/starter.yaml`; the Policies settings page, the Approvals page with the rail count, decisions on the run page; `scripts/policy-e2e.sh` (`make policy-e2e`, CI job `policy-e2e`); runbook `docs/runbooks/policy-and-approvals.md`; benchmark `docs/benchmarks/phase-14-policy-e2e.md`. Enforcement is **cooperative** (it stops an agent that asks through the SDK, not one that bypasses it). KI-140..142, KI-145..151 and KI-153..159 are open as "to file"; KI-143, KI-144 and KI-152 are resolved. Step 5 found and fixed three defects the unit tests could not see (the SDK's policy transport closed its socket before reading the body, a run that ended ahead of the server's late approval event stayed RUNNING, the expiry job stamped the database clock).

**Phase 13 (security observability and redaction modes)** is merged (PR #143), see below.

Earlier:
**Phase 13 (security observability and redaction modes)** is merged (PR #143): ADR-130 (server-side redaction before storage, per-project capture modes, default METADATA_ONLY), ADR-131 (six `sec.*` detectors, content-free findings, mutes and allowlist, Amendment 1: a settings change re-analyses recent runs), migrations 0090-0091, `GET /v1/security/findings`, `/v1/security/detectors`, `GET|PUT /v1/projects/{id}/security-settings`, the web run-page chip and Security group, the workspace Security page and settings. Step 4 evidence (all seven gates, with numbers) is in the plan `docs/plans/completed/phase-13-security-observability.md`; the end-to-end run is `make security-e2e` (CI job `security-e2e`); benchmark `docs/benchmarks/phase-13-security.md`; operator notes `docs/runbooks/security-observability.md`. Step 5 done: the independent review (`docs/plans/completed/phase-13-code-review.md`: P1 1, P2 4, P3 10) is fixed or dispositioned finding by finding in the plan ("Code review dispositions"; ADR-130 and ADR-131 Amendment 2; new KI-135 to KI-139): the server and SDK patterns redact only `user:password` arguments and names that end in a secret word, a long line is cut only where no secret straddles the cut, look-alike characters are folded for detection, settings-triggered re-analysis has a per-project cooldown, the findings job reads artifacts through `ArtifactRepository`, and the settings copy says what METADATA_ONLY keeps. Gate evidence of the fix round is in the plan. KI-032, KI-042 and KI-133 resolved; KI-134 to KI-139 open (S3, to file). **Next: push and open the PR (needs the user's approval)**, then Phase 14 (policy engine and approvals). Known: `tests/test_stream_reauth.py::test_a_revoked_or_expired_session_ends_its_stream` fails on any run after 2026-10-13 12:00 UTC because it revokes sessions by `expires_at > now() + 1 day` while its fake clock is 2026-10-07 (7-day sessions): a date-dependent test from an earlier phase, not caused by this branch.

Before that (merged): Phases 0-12, 15, 16, 21 and 22 are merged to `main` (Phase 12, visual replay level 1, is PR #136: plan with closing evidence `docs/plans/completed/phase-12-visual-replay.md`, review `docs/plans/completed/phase-12-code-review.md`, ADR-120 and ADR-121; Phase 22, the run-page redesign "Flight recorder trace", is PR #132; plan with closing evidence, modified-test table and review dispositions: `docs/plans/completed/phase-22-run-page-redesign.md`; review: `docs/plans/completed/phase-22-code-review.md`). The MVP gate was accepted on 2026-10-12. 

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
- Phase 10: reliability findings. Eight pure detectors (repeated sequence, identical request, retry storm, context growth, cost spike,
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

- Phase 16 (branch, merged with Phase 10, awaiting its PR): `GET /v1/runs?q=` (§152 grammar, `docs/architecture/api-v1.md` "Search"), the derived `run_search` table (migration 0080, `SUMMARY_VERSION` 4, written by the summarizer in the run's
  transaction, hash-skipped, `search-rebuild` CLI, `docs/runbooks/search.md`), permission-safe content search for actors with `payload.read` (ADR-100, ADR-101: viewers get `403` for `file:`/`command:`, and
  nothing else differs from a nonsense word), bounded reads (4 permits per process, 5 s budget, two statements), the web search box with chips from the server's parse, matched column, facets and the unindexed
  notice (`apps/web/e2e/search.spec.ts` in `auth-e2e`), the rule-based `QueryCompiler` fake; `scripts/bench_search.py`; KI-080..083 filed as "to file". Measured: no D7 trigger, no `pg_trgm` need at 100,000 runs
  (`docs/benchmarks/phase-16-regression-check.md`).

## In-progress work
Phase 11 is merged (PR #104): 15 operations, migrations 0070-0071 (head), the `evaluate_run` job, comparison, experiments, the run-page Evaluations section, the compare and experiments pages, `scripts/eval-e2e.sh`
(CI job `eval-e2e`), `docs/runbooks/evaluations.md`; the independent review found no P0/P1 and two P2 defects, fixed (`docs/plans/completed/phase-11-code-review.md`). KI-100..109 and KI-116 are GitHub issues #106-#116.
A second KI-050 batch (more models, batch/flex/fast/regional multipliers, cache writes) is being built on `feature/ki-050-batch2`.

Phase 21 (web only; no API, schema or SDK change): rail, top bar, full-width pages, instrument-style run header, a viewport-filling run work area (context column, timeline / waterfall / evidence pane, draggable inspector), the Evidence view, the
runs list and every document page restyled on shared tokens (IBM Plex self-hosted, ADR-110), one status grammar, a motion layer that is zero under reduced motion. Status, evidence per acceptance criterion and the deviations are in
`docs/plans/completed/phase-21-ui-shell-and-design-pass.md`; screenshots in `docs/screenshots/phase-21/` (before/after, with a critique); what landed in `docs/design/ui-direction.md`. No existing spec or vitest file was edited (additions only).
Code review (`docs/plans/completed/phase-21-code-review.md`): 0 P0, 1 P1 (React #418 hydration mismatch from the breadcrumb), 6 P2, 7 P3; all fixed except what is listed as deferred in the plan's "Code review dispositions" (KI-123..126). Web gates after the fixes: eslint, prettier, tsc, vitest 755, `next build`, fixtures Playwright 136 passed / 56 skipped, the eight API-backed E2E scripts (several needed a re-run under load; see the plan). `scripts/quality.sh quick` (Python steps) was not completed
here (KI-122). The reviewer did the keyboard walk, 200% zoom and 1024x640; screen-reader announcement of the radio-tab view switch and the folded strip is still judged from the accessibility tree only.

## Blocked work
None for the code. Verification: after the review fixes the full API suite and the API-backed E2E scripts were re-run and pass (see the plan).

## Next actions (exact)
1. Phase 14 step 6: run the independent review (`review-change`, security pass: approvals, tenant scoping, redaction, policy), fix, then ask to push and open the PR. **Deploy notes for Phase 14:** migrations 0092 (policy versions) and 0093 (approvals) apply with `alembic upgrade head`; agents that enforce need SDK 0.4.0 (the MCP adapter 0.2.0 requires it); the default is `policy_mode` off, so deploying changes nothing until a project publishes a policy AND an agent sets `BLACKBOX_POLICY=enforce`; create agent keys as a signed-in person if self-approval should apply (a CLI-made key has no creator); limits are per process (KI-141, KI-147). After the merge: Phase 17 (alerting and integrations; approvals notifications belong there). **Deploy note for Phase 13:** a project with no security settings row is now `METADATA_ONLY`, so existing projects stop storing payloads and artifact bodies until set to `FULL` (`set-capture-mode`, or Settings, Security; runbook `docs/runbooks/security-observability.md`). After deploying Phase 12, run `search-rebuild` is not needed for replay, but events' `cost_usd` appears only for runs summarised since ADR-121 (re-summarise older runs with `rebuild-costs` if replay cost should show for them).
2. After deploying Phase 22 (and Phase 16): run `python -m abb_api.cli search-rebuild` so runs summarised below `SUMMARY_VERSION` 6 get first-error facts and are searchable (ADR-112, KI-081); until then their rows say "first-error facts not rebuilt yet" and the waste report counts them in `unrebuilt_runs`.
3. Done 2026-10-12: the real OIDC sign-in with Google (KI-067 resolved). Still open for the user: a compose runtime check of the demo login (`make up && make seed`, sign in as `owner@local.test`, `make smoke && make sdk-e2e`; KI-074).
4. KI-050 is closed for what the vendors' pages state; the OpenAI deprecations page is due a re-read on 2026-10-23.
5. Then Phases 12, 13 and 14, 17, 19, 20, the hosting guide, and the demo video (the brand mark now exists; the video is recorded after the redesign, ffmpeg needs approval).
6. After deploying Phase 16, run `search-rebuild` for existing workspaces so older runs become searchable (KI-081, issue #94).

## Open decisions
- MVP acceptance (after the gate items above).
- Remove the `Co-Authored-By` trailers from the 5 earliest commits (needs a force-push; not done).
- The shared web read key is gone (Phase 15), but sign-in has only run against the fake provider (KI-067): configure a real provider and run the manual check before exposing the app.
- Whether to stamp `received_at` with the database clock (multi-instance clock skew vs the stream overlap, ADR-022).

## Known technical debt
`docs/KNOWN_ISSUES.md` (severity, target and GitHub issue per item): notably (Phase 15 additions KI-067..078: real-provider login unverified, no session list, no `email_verified`-less providers, audit retention, trusted proxies, compose login unverified) full recomputation of very large active runs (KI-016), no quotas (KI-018), no failed-auth throttling (KI-019).

## Last verified test status
2026-10-13, Phase 14 branch (own databases `abb_p14_*`, `abb_test_p14`): see the plan's closing evidence. API ruff/format/mypy (343 files)/openapi clean; full pytest 3,019 passed (27 min); web tsc/eslint/prettier clean, vitest 1,351, `pnpm build`, fixtures Playwright 303 passed / 97 skipped; event-schema 391, sdk 1,201 (coverage 94.64%), examples 22+1 skipped and 10, integrations 20/53/75/79/66; `auth`, `findings`, `multiagent`, `analytics`, `integrations`, `security` and `policy-e2e` passed, `coding`, `eval` and `stream` passed on a single re-run after load-induced failures. UNVERIFIED (env): the CI job itself, `make smoke`, `sdk-e2e`, adapters on Python 3.12. Previous:
2026-10-12, Phase 22 branch after the review fixes (own databases `abb_p22_*`): web tsc, eslint and prettier clean, vitest 994 (61 files), `next build`, `pnpm gen:api:check`, fixtures Playwright 197 passed / 60 skipped; API ruff, ruff format, `mypy` (263 files) clean, `openapi --check` clean, focused API tests (`test_first_error`, `test_run_summary`, `test_analytics_waste`) 141 passed; `auth-e2e` 13 passed. Before the fixes (lead, merged branch): API 1,839 passed in 925 s, web vitest 970, fixtures Playwright 195 passed. After the fixes the lead re-ran the full API suite (1,847 passed in 854 s) and the `auth`, `findings`, `multiagent`, `eval`, `stream`, `analytics` and `integrations` E2E scripts (exit 0) and `coding-e2e` (6 passed after a spec fix). Previous:
2026-10-11, Phase 11 branch (final tree, own databases `abb_test_p11`/`abb_p11_*`, dropped afterwards): event-schema 351, sdk 893 (coverage 94.27%), examples 16 + 10, integrations 17/74/66/61/44, api 1,699 passed (authz 151, search 359, rest 742 + 447),
web vitest 626, eslint/prettier/tsc/`next build` clean, openapi/schema/gen:api checks clean, migrations head 0071 <-> base and 0071 -> 0080 -> head. Fixtures Playwright 21. E2E: `auth-e2e` 13, `findings-e2e` 6, `multiagent-e2e` 6, `coding-e2e` 6, `eval-e2e` 7.
Not run: `make smoke`, `sdk-e2e`, `stream-e2e`, `analytics-e2e`, compose jobs (UNVERIFIED (env)). Previous:
2026-10-11, Phase 16 branch merged with `origin/main` (Phase 10), own databases `abb_test_p16`/`abb_p16`: event-schema 351, sdk 893 (coverage 94.24%), examples 12 + 10, integrations 17/74/66/61/44,
api 1,411 passed (authz 121, search 359, rest 931), web vitest 567, eslint/prettier/tsc/`next build` clean, openapi/gen:api checks clean, migrations head 0080 <-> base and 0080 -> 0060 -> head. E2E: `auth-e2e` 13 (8 auth + 5 search),
`findings-e2e` 6 (its runs-list test now searches `status:failed` and still sees the findings column), `multiagent-e2e` 6, `coding-e2e` 6, fixtures Playwright 17. Not run: compose-based jobs and `make smoke`/`sdk-e2e`/`up` (UNVERIFIED (env)).
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
