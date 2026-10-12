# Phase 22: Run-page redesign, "Flight recorder trace"

Status: Implemented; pending merge (PR open for review, 2026-10-12). Adversarial review done, every finding fixed or dispositioned below. Decisions D1-D12 were settled with the user one question at a time (grill, 2026-10-12).
Owner: web + api. Branch: `feature/phase-22-run-redesign` (from `main` `c7bf3e8`).
Depends on: Phase 21 (merged, PR #119). Phase 12 (visual replay) builds on this page next.
Spec refs: §62 invariants (INV-2 derived state rebuildable, INV-5 withheld content), `docs/design/ui-direction.md`, ADR-110.

## Outcome

An engineer whose agent just failed opens the run page and, within five seconds and without scrolling or clicking, can say where it went wrong, how many retries it caused, what they cost, and where the run diverged. Colour only ever means a problem. The same language runs through the runs list, compare and analytics.

## Non-goals

- No change to what the data says: same events, same viewer withholding, same role matrix (INV-5).
- No replay (Phase 12), no policies or approvals (Phase 14), no alerts (Phase 17).
- No landing or marketing page (Phase 20).
- No weakened tests. Existing specs may change only where the redesign legitimately moves a selector; each change is listed in the plan with the reason and keeps or strengthens its assertions.

## Decisions (settled with the user)

- D1 Primary user: an engineer whose agent just failed or misbehaved.
- D2 Success metric: the five-second first-error test on the fixture run at 1440 px and 375 px, plus a Playwright assertion that the first-error marker and the retry and cost figures are visible in the first viewport.
- D3 Run-page hierarchy: failure line, cost of the failure, quiet timeline, identity line, findings out of the main view (one line per finding in the inspector, two clicks).
- D4 The headline occupies one fixed place and adapts: failed / timed out / blocked (first error and its step), running (step, last event age, live dot), waiting or awaiting approval (what and how long), recovered (succeeded after N retries), clean success (completed in X, cost), cancelled (by whom, at which step). Skeleton, error and withheld variants for each.
- D5 Direction "Flight recorder trace": a fault line marks the first error on the timeline and the waterfall; rows above it are quiet, rows below it carry a faint tint; red is reserved for failure; amber for retry and waiting; a steel-blue interaction accent; status is a glyph plus word with no outline, only FAILED is filled.
- D6 First-error facts are computed on the server and stored with the run summary: `first_error_event_id`, `first_error_sequence`, `retries_after`, `seconds_after`, `cost_after`. Derived from events, rebuildable (INV-2), metadata so viewers see them. Needs an ADR, an API contract change and a rebuild path.
- D7 Search: a global top-bar search opened with `/` using the existing query grammar, with inline top results; a facet column on the runs list. Both edit the one query string, which stays the single source of truth and the URL. Viewer rules unchanged (ADR-101).
- D8 Runs list: its job is to show which runs need you. Failed, timed-out and blocked runs get a second line with the failure sentence; clean runs stay one quiet line; a "Needs attention" facet; the compare checkboxes appear only when comparing; events and errors move into row detail.
- D9 Illustration and empty states: the first-run empty state is a working setup guide (API key, SDK install, three-line snippet with copy, live switch to the first run). One small inline-SVG trace system (trace line, fault-line cut, recorder tape), token colours, used only on empty, error and not-found states. No hero art, stock images or mascot.
- D10 Typeface: add IBM Plex Serif medium (self-hosted woff2, OFL, `next/font/local`, same route as ADR-110), used only for the failure line and empty-state headlines.
- D11 Login and invite are rebuilt inside the product (tokens, type, brand mark, one sentence, side gutter at 375 px, keyboard path). The landing page waits for Phase 20.
- D12 Compare opens with a divergence line and the same fault-line cut over the aligned steps, with the summary tiles collapsed into one mono line of deltas; analytics opens with "where the money was wasted" (spend on failed runs and retries by agent and model, from the cost engine). Everything else on those pages only inherits the styling.

Also settled earlier: scope may include new features, decorative-but-purposeful motion, new fonts, dependencies and illustration, provided each new feature was grilled (done above). Motion language: 120-200 ms, ease-out `cubic-bezier(0.2, 0, 0, 1)`, transform and opacity only, no springs or bounce, reduced motion gives a static complete page.

## Design notes

- Palette (light default, dark follows system; every pair verified at 4.5:1 or better when the tokens are built): bg `#eef1f4`/`#12161b`, surface `#ffffff`/`#1a2027`, ink `#171b21`/`#e8ecf1`, muted `#566272`/`#a3adb9`, accent `#2a5f8f`/`#7db3e6`, fail `#b3261e`/`#ff8a80`, retry and wait `#8a5a00`/`#ffcb6b`. Old token names stay as aliases.
- Removed accessories: the seven-box metric strip (duration, cost and tokens become one mono line), outlined pills, most upper-case micro-labels, the always-open filter chip row (collapsed into one menu with a count).
- Motion: fault line scales in over 280 ms once on opening a failed run; headline crossfade 120 ms on state change; jump-to-error scrolls then fades a 600 ms outline; inspector 160 ms slide of 8 px with fade; live rows fade in over 180 ms with a 4 px rise and never stagger; row hover is an instant background; the recording dot loses its pulse (reduced motion off anyway).
- Brand mark: three SVG options drawn for the user to choose from before it ships.

## Known issues considered

Pulled in: KI-119, KI-120, KI-124, KI-125, KI-126 (shell debt the redesign touches), KI-127 (replace the fixed wait in the 220 KB log case and diagnose the human-verdict spec when specs may change). Stays deferred: KI-117, KI-118, KI-121, KI-122, KI-123 (test-infrastructure items with no UI effect); KI-121 gets its screenshots as part of step 8.

## Acceptance criteria

1. Five-second test: on the failed fixture run at 1440 px and 375 px the first-error marker, retry count and cost-after figures are visible in the first viewport (Playwright assertion).
2. First-error fields are returned by the run summary, equal a from-scratch recomputation over the events (rebuild test), and are identical for owner and viewer.
3. All six headline states render with skeleton, error and withheld variants (component tests).
4. The fault line appears on timeline and waterfall at the first error; rows above are quiet; red is used only for failure (token-use test).
5. Findings and evaluations reachable within two clicks from the run page.
6. Top-bar search opens with `/`, shows inline results, matches the query grammar, and a viewer's search never matches content (extends ADR-101 tests); facets edit the query string and the URL.
7. Runs list shows the failure line for failed, timed-out and blocked runs; "Needs attention" facet works; compare checkboxes appear only when comparing.
8. First-run empty state shows the setup guide and switches to the run when the first event arrives (E2E).
9. Compare opens with the divergence line; analytics opens with wasted spend, reconciled with the cost engine (test).
10. Login and invite have a side gutter at 375 px, a keyboard path and axe clean.
11. Axe serious/critical violations are zero on every page; every text pair is at least 4.5:1; reduced motion produces a static complete page (zero-motion contract extended).
12. Existing tests pass; every modified existing test is listed in the plan with its reason and keeps or strengthens its assertions.
13. `scripts/quality.sh full` green in CI; adversarial review has no open P0/P1.

## Verification plan

Focused unit and component tests per step; Playwright (fixtures and API-backed) for the five-second test, the search and the empty state; axe and before/after screenshots at 1440 px and 375 px; migration check (empty to head and previous head to new head) if the summary fields are stored; my own gate run after every step, then Fable review.

## Risks

- Moving findings changes where people look; covered by acceptance 5.
- Server-computed fields add a contract, a migration (or projection) and a rebuild path; covered by acceptance 2 and an ADR.
- Large scope: build in the order below and keep each step shippable; if review shows it too large, step 8-10 (list, search, compare, analytics, login) can split into a follow-up PR without breaking the core.

## Ordered steps (one commit each)

1. Tokens, status grammar, type scale, Plex Serif, brand-mark options (ADR for the direction).
2. API: first-error fields in the run summary, rebuild path, ADR, contract and `schema.d.ts`.
3. Run header: failure line, cost-of-failure pair, six states, identity line.
4. Timeline and waterfall fault line; quiet and tinted rows.
5. Findings and evaluations moved into the inspector; context column collapsed by default.
6. Motion layer per the spec above; zero-motion contract extended.
7. Runs list redesign with failure lines, facet column and top-bar search.
8. Setup-guide empty state and the illustration system.
9. Compare and analytics headlines; styling propagation to experiments and settings.
10. Login and invite rebuilt.
11. Verification, screenshots, KI updates, closing evidence, review, fixes, push and PR.

## Closing evidence

Evidence vocabulary: VERIFIED (run in this environment, output observed), UNVERIFIED (env), ASSUMED. Commands run from the worktree; web in `apps/web`, API in `apps/api` with `ABB_TEST_DATABASE_URL=postgresql+asyncpg://abb:...@localhost:5433/abb_p22_*`.

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | five-second test | VERIFIED `pnpm exec playwright test e2e/five-second.spec.ts`: both viewports pass (inside the fixtures run: 197 passed, 60 skipped). At 375 px the fault label first fell 4 px below the fold after the review fix that moved the line onto the error row; the phone lead size (`run-header.css`, max-width 30 rem) fixed it |
| 2 | first-error facts equal a recomputation, identical per role | VERIFIED `pytest tests/test_first_error.py tests/test_run_summary.py` (with `test_analytics_waste.py`: 141 passed); the rebuild test `test_summarizer.py::...rebuild...` and `tests/authz/test_content_withheld.py::test_first_error_facts_are_metadata_and_identical_for_every_role` passed in the full API run before the review fixes (1,839 passed, lead) and again up to 66 % of the re-run; the blocked cases (`first-error-cases.json`) failed before `first_error_index` and pass after |
| 3 | six headline states x skeleton, error, withheld | VERIFIED `pnpm exec vitest run tests/run-headline.test.tsx`: states x variants, the shared parity cases, the sentinel test for owner and viewer (994 tests in the whole web run) |
| 4 | fault line on timeline and waterfall; red only for failure | VERIFIED `tests/fault-line.test.tsx` (17 tests; the expanded-group case failed first), `tests/tokens-use.test.ts` now over every stylesheet (a `var(--fail)` rule in `login.css` makes it fail: observed and reverted) |
| 5 | findings and evaluations within two clicks | VERIFIED `e2e/context-findings.spec.ts` and `fixtures.spec.ts` in the fixtures run (197 passed) |
| 6 | `/` search, inline results, grammar, viewer never matches content; facets edit q and URL | VERIFIED `tests/search.test.tsx`, `tests/runs-list-redesign.test.tsx`, `e2e/runs-list-redesign.spec.ts`, `e2e/search.spec.ts` (fixtures) and `auth-e2e` (13 passed: includes the search spec as OWNER and VIEWER) |
| 7 | failure lines, Needs attention, compare tick boxes only when comparing | VERIFIED `e2e/runs-list-redesign.spec.ts` (every failure line says "at step N", including the blocked fixture run) and the two new mutual-exclusion tests |
| 8 | setup guide switches to the first run | VERIFIED `tests/empty-states.test.tsx` and `e2e/empty-states.spec.ts` (fixtures run) |
| 9 | compare divergence line; analytics wasted spend reconciled | VERIFIED `tests/compare.test.tsx`, `e2e/compare-analytics.spec.ts`; `pytest tests/test_analytics_waste.py` reconciles with the cost engine (11 passed) |
| 10 | login and invite: gutter at 375 px, keyboard, axe | VERIFIED `e2e/login.spec.ts` (fixtures run) and `auth-e2e` 13 passed |
| 11 | axe zero, contrast, reduced motion static | VERIFIED `e2e/design.spec.ts` (axe on 11 routes, both colour schemes; the first-paint motion test in its strong form), `tests/tokens-contrast.test.ts`, `tests/motion-contract.test.ts` (also fails on an infinite `animation-iteration-count`) |
| 12 | existing tests pass; modified ones listed | VERIFIED: the table above; web vitest 994/994, fixtures Playwright 197 passed / 60 skipped (`git checkout -- docs/screenshots/phase-{4,9,10,11,15,16}` afterwards) |
| 13 | `scripts/quality.sh full` green in CI; no open P0/P1 | Review: no P0; P1-1..P1-3 fixed (above). VERIFIED locally: web `tsc`, `eslint`, `prettier --check`, `vitest`, `pnpm build`, `pnpm gen:api:check`; API `ruff check`, `ruff format --check`, `mypy` (263 files), `openapi --check`. `scripts/quality.sh full` itself and the CI run: UNVERIFIED (env) here, left to the PR |

UNVERIFIED (env) after the review fixes (KI-130): the full API suite (a re-run reached 66 % with no failure, then the Docker VM disk filled and PostgreSQL could not write); the `findings`, `multiagent`, `coding`, `eval`, `stream`, `analytics` and `integrations` E2E scripts (each failed at once on the full disk, not on a test). `auth-e2e` ran before the disk filled and passed. Before the review fixes the lead verified the full API suite (1,839 passed in 925 s) and the fixtures suite.

## Modified existing tests

Acceptance 12: every existing test or spec this branch changed (`git diff origin/main...HEAD --name-status` over `apps/web/tests`, `apps/web/e2e`,
`apps/api/tests`, `scripts`, `packages`), with the reason. None was weakened, skipped or deleted; where an assertion moved it names the same fact in the new words.

| File | Why it changed |
| --- | --- |
| `apps/api/tests/analytics_fixtures.py` | the seeder gained `tools_first` (a failing tool before the spend) so the waste report has spend after a first error; existing callers unchanged |
| `apps/api/tests/authz/registry.py`, `test_registry_is_complete.py`, `test_role_matrix.py`, `test_openapi.py` | the new `GET /v1/analytics/waste` route is registered in the route-walking registry, the role matrix and the operation counts (47 + 1) |
| `apps/api/tests/authz/test_content_withheld.py` | additions: first-error facts are metadata and identical for every role |
| `apps/api/tests/test_run_summary.py`, `test_summarizer.py`, `test_runs_api.py`, `scripts/multiagent-e2e.sh` | `SUMMARY_VERSION` 4 to 5 (ADR-112) and then 6 (blocked-run rule, review P1-3): the version pins now say 6 |
| `apps/web/e2e/design.spec.ts` | skip-link order includes the new top-bar search; the annunciator test reads the failure line (red) and counts; settled animations before axe; the first-paint motion test was loosened in the first pass and is restored to the strong form (started animations per route are `[]` except a failed run's own fault line; review P1-1); the spinner `spin` is excluded as the allowed loading indicator |
| `apps/web/e2e/fixtures.spec.ts` | headline words ("Failed at step N" + a detail line with the retries, "Completed in", "Running", "Awaiting approval"); the context column is opened with the Context button (closed by default, D3); findings sit behind Details / Health disclosures; the run count is 13 (the blocked fixture run) |
| `apps/web/e2e/auth.spec.ts`, `real.spec.ts`, `stream.spec.ts` | the same headline words; settle animations before axe |
| `apps/web/e2e/coding.spec.ts` | open the context column; the first error is read from the recovered headline's detail line; the 220 KB log case waits for the rendered chunk instead of a fixed 100 ms pause (KI-127) |
| `apps/web/e2e/eval.spec.ts`, `multiagent.spec.ts`, `findings.spec.ts`, `viewer-withheld.spec.ts`, `shots.spec.ts` | open the context column (closed by default); findings wording ("Nothing wrong found") and the one-line-per-finding Details disclosure |
| `apps/web/e2e/review-fixes.spec.ts` | KI-126: an open context stays open at 1280 and the inspector narrows (folds below 1280); the class chips moved into the Filter popover, so the toolbar test checks every toolbar control and the popover |
| `apps/web/tests/run-detail.test.tsx`, `run-header.test.tsx`, `runs-header.test.tsx` | headline words and component (`headlineModel`); class filters live in the Filter popover; the compare bar is behind the Compare button |
| `apps/web/tests/runs-list.test.tsx` | fixture count is now odd: the page size is `ceil(N/2)`; the status text list includes Blocked |
| `apps/web/tests/runs-pick-hint.test.tsx`, `evaluations.test.tsx` | tick boxes and the compare bar appear only after pressing Compare (D8) |
| `apps/web/tests/fixtures.test.ts` | the fixture set now includes a BLOCKED run with a first error and `summary_version` 6; asserts that |
| `apps/web/tests/context-focus.test.tsx`, `context-fold.test.tsx`, `run-layout.test.tsx` | the context column starts closed (D3); the fold thresholds (KI-126); the Context button names what is in the column |
| `apps/web/tests/findings.test.tsx` | the empty findings sentence |
| `apps/web/tests/compare.test.tsx`, `analytics.test.tsx` | the divergence line replaces "first diverge at step"; the wasted-spend panel is added above the cost section |
| `apps/web/tests/tokens.test.ts` | the old token names are aliases (`var(--x)`), so values are resolved before the contrast checks |
| `apps/web/tests/tokens-use.test.ts`, `motion-contract.test.ts` (review fixes) | `tokens-use` now reads every stylesheet in `src/app` (a mutation in `login.css` fails it); `motion-contract` also checks `animation-iteration-count` |
| `apps/web/tests/run-headline.test.tsx`, `fault-line.test.tsx`, `runs-list-redesign.test.tsx` (review fixes; new in this branch) | the "falls back to the bare word" case was split into current / older / catching-up wording; the fault-line case that asserted the line on an expanded group's header (the wrong behaviour, review P2-3) now asserts the line on the error event's own row |
| `packages/event-schema/tests/data/first-error-cases.json` | blocked cases added (parity, read by the API and the web) |

## Code review dispositions

Review: `docs/plans/active/phase-22-code-review.md` (0 P0, 3 P1, 6 P2, 12 P3). Each finding has its fail-before test (the test was run against the old code and failed, or, where noted, the failure is by reasoning).

| Finding | Disposition | Fix and test |
| --- | --- | --- |
| P1-1 first-paint motion weakened | Fixed | `design.spec.ts` asserts `started` animations per route (`[]`, except `fault-draw-x` / `fault-draw-y` on routes that open a run with a first error) and zero after settle. It failed first on the overview (`spin`): the loading spinner is excluded with a comment |
| P1-2 waiting headline never ticks | Fixed | `ticking = isActive(status)`; `run-header.test.tsx` "an approval wait keeps counting" (fake timers; failed first: stayed "4m 00s") |
| P1-3 blocked runs have no first error | Fixed (a) and (b) | (a) `missingFirstErrorText`: current version "no error event recorded", older "first-error facts not rebuilt yet", catching up "summary still updating"; (b) `SUMMARY_VERSION` 6, `first_error_index` in `summary.py`, `firstErrorFor` in `timeline.ts`, shared cases with `run_status`, a BLOCKED fixture run, ADR-112 and `api-v1.md`. Tests: `test_first_error.py` (4 failed first), `run-headline.test.tsx`, `runs-list-redesign.test.tsx`, `fault-line.test.tsx`, `runs-list-redesign.spec.ts` (every failure line now needs "at step N") |
| P2-1 free-text attributes in the headline | Fixed | `approval.action` and `error.summary` are no longer read; the headline names kinds only; `run-headline.test.tsx` sets a sentinel in every unreviewed attribute, for owner and viewer, across nine states (failed first). The waiting-on subject uses the event kind, not `describe()` |
| P2-2 tokens-use file list | Fixed | `readdirSync` over `src/app`; mutation checks: a `var(--fail)` rule in `login.css` fails `tokens-use`, an infinite `animation-iteration-count` fails `motion-contract` |
| P2-3 fault line on an expanded group header | Fixed | anchor on the group only when collapsed (`faultRowIndex`, `GroupRow.eventIds`). The old `fault-line.test.tsx` case encoded the wrong behaviour (it asserted the line on the group's row with the error row below it) and was rewritten, not weakened: it now asserts the line, the tint and the quiet rows against the error row |
| P2-4 docs | Fixed | this plan, `PROJECT_STATE.md`, `KNOWN_ISSUES.md`, `IMPLEMENTATION_PLAN.md`, README, `api-v1.md` |
| P2-5 `unrebuilt_runs` | Fixed | documented as runs below the current summary version; shown in `WastePanel` (`waste-unrebuilt`); `openapi.json` and `schema.d.ts` regenerated; `test_analytics_waste.py` (a v5 failed run is unknown, the description) and `analytics.test.tsx` |
| P2-6 attention vs status | Fixed | turning Needs attention on drops `status:` terms from `q`; a Status facet turns it off; both orders tested in `runs-list-redesign.test.tsx` |
| P3-1 lifecycle words | Fixed | "the run's end", event type in words, no repeated status (`run-headline.test.tsx`) |
| P3-2 jump before the page loads | Fixed | the headline's and the Go control's id come from the stored first error; `jumpTo` fetches the event; `run-header.test.tsx` "Show it works before the page holding the first error has loaded" |
| P3-3 live edge | Fixed (resize); count of visible rows implemented without its own test | `ResizeObserver` re-measures the edge (`run-motion.test.tsx`, failed first); the "N new events" count passes the fresh events through the active filters (ASSUMED: no component test, the path needs a live stream) |
| P3-4 guide returns | Fixed | `guideShown` resets once the person filters (`empty-states.test.tsx`, failed first) |
| P3-5 by-agent vs failed runs | Fixed by documenting | the field description and ADR-113 say agents with $0 waste are not listed; the "indexed by `ix_cost_project_started`" claim in ADR-113 is corrected |
| P3-6 fixtures | Partly fixed; partly not a defect | fixtures claim `summary_version` 6 and the older-version branch is tested explicitly (`runs-list-redesign.test.tsx`, `run-headline.test.tsx` with `summary_version: 5`). `seconds_after` to the last event equals `completed_at` in the builder (it sets `completed_at` to the last event), so there is nothing to fix |
| P3-7 target size | Fixed | `.row-more` and `.facet-button` are at least 24 px; `runs-list-redesign.spec.ts` measures them at 375 px (new) |
| P3-8 `animation-iteration-count` | Fixed | added to `MOTION_PROPS`; the mutation fails the contract |
| P3-9 headline remount | Fixed | no `key` remount; `data-fade` alternates `a`/`b`; `run-motion.test.tsx` (focus kept) |
| P3-10 unused brand options | Fixed | `option-b.svg` and `option-c.svg` moved to `docs/design/brand-mark-options/`; docs and ADR-111 updated |
| P3-11 URL adoption comment | Fixed | comment in `RunsListPage.tsx` |
| P3-12 `role="alert"` on `<main>` | Fixed | the role is on the sentence (`empty-states.test.tsx`, failed first) |
| "Show it" vs visible "Go" (the review's no-finding note) | Not changed | WCAG 2.5.3 is met by the `title` and the accessible name pattern the whole suite reads; renaming it touches 12 specs for a low-severity naming point; recorded here |
