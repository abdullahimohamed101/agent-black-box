# Phase 22 code review: run-page redesign ("Flight recorder trace")

Reviewer: independent adversarial pass (skill `review-change`). Branch `feature/phase-22-run-redesign` at `45556af`,
diffed against `origin/main` (`git diff origin/main...HEAD`, 268 files). Nothing was modified; this file is the only
output. Evidence vocabulary: VERIFIED = run here; READ = established by reading the code; ASSUMED = reasoned.

Focused tests run here (VERIFIED, all green): `pnpm exec vitest run tests/motion-contract.test.ts tests/tokens-use.test.ts
tests/tokens-contrast.test.ts tests/run-headline.test.tsx tests/runs-list-redesign.test.tsx` (152 tests). The API suite was
not run (the author's full run was in progress; findings below are from reading `waste.py`, `summary.py`, `service.py`
and the tests).

## Summary

| Severity | Count |
| --- | --- |
| P0 | 0 |
| P1 | 3 |
| P2 | 6 |
| P3 | 12 |

Top three: (1) `design.spec` "no route animates on first paint" was loosened to "eventually zero", which the plan's own
acceptance 12 forbids; (2) the headline's "how long" clock never ticks for `WAITING_FOR_APPROVAL` and `QUEUED`, so the
waiting state (D4) shows a frozen duration; (3) BLOCKED runs can never have a first error under the shared rule, so the
runs list prints "first error not recorded for this run yet" forever and the headline, fault line and D4's "blocked
(first error and its step)" are all missing for exactly the status the sentence is written for.

Areas with **no substantive issues** (stated explicitly, as asked):

- **`GET /v1/analytics/waste` security and tenancy (explicit pass).** Every statement starts from
  `R.workspace_id == scope.tenant.workspace_id` (`waste.py:48-56`), the project filter only narrows, the route goes
  through the same `Reader` principal and `AnalyticsService.scope()` as the other analytics routes (`router.py:143-168`),
  `top` is bounded by `TopParam` (1..`MAX_TOP`, the 422 for `top=0` is tested), it runs in the store's read-only
  statement-timed transaction (`postgres.py:701-706`), there is no raw SQL in the router (INV-7), no string
  interpolation (SQLAlchemy Core throughout), the JSONB read is a cast of a server-written number, and the cross-workspace,
  cross-project, 401/403, registry, role-matrix and OpenAPI-count tests were all extended (`tests/authz/registry.py`,
  `test_role_matrix.py`, `test_registry_is_complete.py`, `test_openapi.py`, `test_analytics_waste.py`). Response size is
  bounded by `top` and the `_other` fold.
- **First-error fields for viewers.** `FirstErrorOut` carries an event id, a sequence, two counts, seconds and USD
  (`runs/schemas.py:59-77`); nothing derived from an attribute value (`summary.py:213-252`). The parity with the web
  `isError` rule is real (`is_error_event` vs `timeline.ts:57-58`, shared cases in
  `packages/event-schema/tests/data/first-error-cases.json`, tested on both sides). The authz test asserts viewer == owner
  on list and detail. What the headline renders for the subject uses `tool.name`, `llm.model`, `tool.error_type`,
  `llm.error_type`, all in `REVIEWED_METADATA` (`tests/authz/test_content_withheld.py:30-41`). See P2-1 for the two
  attributes that are not.
- **Search as an oracle (ADR-100/101).** The top bar sends the same `q` to the same `/v1/runs`; the API's refusal is
  shown as a fixed sentence that never repeats the value (`TopbarSearch.tsx:37-53`); inline results are run name, id,
  status and agent (metadata). The fixture search now refuses content fields for a viewer with the same 403 whatever the
  value and never indexes content attributes (`fixtures/search.ts:33-45,126-131,166-174`). Facet counts come from the
  existing `facets` API, unchanged.
- **Compare divergence line.** Built only from `StepOut.label` / `label_withheld` (`comparison.ts:131-199`); the viewer
  path prints "details withheld for your role"; names are rendered as text in `<code>` (`CompareView.tsx:185-199`),
  hostile-label test present.
- **SetupGuide.** Renders no key, copies only the install line and a snippet with no secret, links to the API-keys page,
  nothing in URLs (`SetupGuide.tsx`). The clipboard failure path is handled.
- **Login / invite.** `return_to` still goes through `safeReturnTo` (`server/config.ts:60-63`); the invite token is taken
  from the fragment, moved to `sessionStorage`, and `history.replaceState` clears it before any render
  (`lib/invite.ts:9-21`, pre-existing); the e2e asserts the URL no longer carries it.
- **`error.tsx` / `not-found.tsx`.** Only `error.digest` is printed, never `error.message` or a stack (`error.tsx:21-25`);
  the unit test asserts the message is absent.
- **XSS.** No `dangerouslySetInnerHTML`, `innerHTML`, `localStorage` or `sessionStorage` added in `apps/web/src` (grep over
  the diff). The new inline SVGs are static JSX with no data-driven attributes (`illustrations/index.tsx`,
  `shell/Brand.tsx`). Every payload-derived string is a React text node.
- **Dependencies, fonts, stray files.** No `package.json`, lockfile or `pyproject` change. The two Plex Serif woff2 files
  sit beside the existing `OFL.txt` (ADR-110 route). No `.env`, no generated `apps/web/AGENTS.md`/`CLAUDE.md`, no
  screenshot churn in `docs/screenshots/phase-{4,9,10,11,15,16}` (all 141 new images are under `phase-22/`).
- **Derived-state rebuild.** `search-rebuild` selects every run whose search document is below `SUMMARY_VERSION`
  (`search/repository.py:103-127`) and the summarizer writes the current version into that document (`handlers.py:83`),
  so v4 runs are reselected as ADR-112 says; `rebuild-costs` queues by range. The rebuild test
  (`test_summarizer.py:115-154`) exercises the real path. Duplicate and out-of-order arrival are covered by the shuffled,
  duplicated ingestion test (`test_summarizer.py:82-112`) and the pure-derivation shuffle test.

## P1: must fix before merge

### P1-1 `e2e/design.spec.ts:304-323`: "no route animates on first paint" was weakened, against acceptance 12

- **Problem.** Before: `expect(await animations(page), url).toBe(0)` right after `settled(page)` on every route. After:
  `waitForFunction(() => document.getAnimations().length === 0, {timeout: 2000})` then only `infinite === 0`. Any
  finite animation on any route now passes. The plan (Non-goals: "No weakened tests"; acceptance 12: "every modified
  existing test ... keeps or strengthens its assertions") and `ui-direction.md` ("Nothing animates on first paint apart
  from a failed run's fault line") both promise the stronger property. The separate "a run that opened clean ... plays
  nothing" test covers one route (`RUN(1)`), not the list, dashboard, compare, analytics, settings, login routes.
- **Failure scenario.** A future `.hl[data-fade]`, `.drawer` or `.tl-row[data-arrived]` rule that fires on first paint on
  the runs list or analytics page (for example a `data-fade` set on mount) passes this suite.
- **Correction.** Keep the `recordAnimations` hook and assert per route:
  `expect(await started(page), url).toEqual(url.includes(RUN(2)) ? ["fault-draw-x"] : [])`, and keep the original
  `animations(page) === 0` after the fault line has finished. Also list this spec change in the plan (acceptance 12
  requires every modified test to be listed with its reason; the plan has no such list today).

### P1-2 `components/RunDetail.tsx:439-445` + `lib/runHeadline.ts:277-291`: the waiting headline's clock never ticks

- **Problem.** `ticking = status === "RUNNING" || status === "WAITING"`. The `waiting` kind is produced for both
  `WAITING` and `WAITING_FOR_APPROVAL` (`headlineKind`), and `QUEUED` is `running` with `live: true`. For
  `WAITING_FOR_APPROVAL` (the fixture, the e2e and D4's "awaiting approval (what and how long)") `now` is set once at
  mount, so the detail reads "waiting 4m 00s" at page open and still "waiting 4m 00s" ten minutes later; the run query's
  own refetch (`queries.ts:89`, `isActive`) re-renders with the same `now`. Same for a queued run's "last event N ago".
- **Failure scenario.** An approver opens a run awaiting approval, leaves the tab open, comes back: the headline says the
  agent has been waiting 12 s when it has been waiting 20 min. The number is the one fact that state exists to show.
- **Correction.** `const ticking = !!run.data && isActive(run.data.status)` (or `model.live || model.kind === "waiting"`).
  Add a component test that advances fake timers on `approvalRun()` and expects the detail to change.

### P1-3 `lib/runsListRow.ts:35-36`, `lib/runHeadline.ts:216-231`, `runs/summary.py:209-211`: BLOCKED runs never get a first error, and the list says "yet" forever

- **Problem.** A run becomes BLOCKED through `run.completed` with status `blocked` (`summary.py:37-42,61-67`).
  `is_error_event` is `status in {error, timeout} or type.endswith(".failed")`, so neither that event nor
  `policy.action.blocked` (a point event, `registry.py:246`) is an error. Consequently for a BLOCKED run with no earlier
  tool/model failure: `first_error` is null at version 5 (READ), the headline falls through to `Blocked after 12 s`
  with no subject, no step, no Go and no fault line, and `failureLine()` prints
  `Blocked · first error not recorded for this run yet`. The word "yet" is wrong for every v5 run (nothing is pending);
  it is also printed for a v4 run, where it would be true, because the client cannot tell the two apart
  (`first_error` is null in both; `summary_state` is "current" in both since it reflects the job queue,
  `runs/queries.py:324-327`, not the version). D4 promises "blocked (first error and its step)" and D8 a failure sentence
  for blocked runs; the fixtures have no BLOCKED run, so no test sees this (the runs-list spec's `/^(Failed|Timed
  out|Blocked)( at step \d+)?/` accepts the bare word).
- **Failure scenario.** A policy blocks `git push --force`; the run is BLOCKED; the engineer opens the list and reads
  that the first error "is not recorded yet", waits, and nothing ever arrives. On the run page nothing names the policy.
- **Correction.** Two parts. (a) Text: branch on `run.summary_version >= 5` (the number is in `RunOut`): v5 and null ->
  no detail line or "no error event recorded"; below 5 -> "first-error facts not rebuilt yet". (b) Product: decide in
  ADR-112 whether `policy.action.blocked` (or `run.completed` with status `blocked`) counts as the first error for BLOCKED
  runs, mirror it in `isError`/`is_error_event` and the shared cases, and add a BLOCKED fixture run so the list, the
  headline and the fault line are exercised. If (b) is deferred, say so in the plan and KNOWN_ISSUES.

## P2: should fix

### P2-1 `lib/runHeadline.ts:184-194,277-285`: the headline renders two unclassified free-text attributes for every role

- **Problem.** `cancelledBy()` prints `approval.action` and `error.summary`; the waiting state prints `approval.action`.
  Neither key exists in the event-schema registry, the SDK or the API (grep: only the web fixtures define
  `approval.action`), so they are neither in `CONTENT_ATTRIBUTES` nor in `REVIEWED_METADATA`; the API passes unknown
  string attributes through to viewers untouched (`content.py:24-25`). The file's own header says the words come "never
  from content attributes", and ADR-112 says the headline facts are "never from an attribute value, payload, path,
  command or name". An agent that puts the command it wants approved into `approval.action` ("run `rm -rf build/`") gets
  it into the most prominent line of the page for a VIEWER. The viewer headline test only checks `file.path`,
  `shell.command`, `shell.cwd` (`tests/run-headline.test.tsx:268-272`), so it cannot catch this.
- **Correction.** Either register the two keys and classify them (content, most likely, for `approval.action`), or drop
  the attribute text from the headline and name only the kind ("Awaiting approval", "Cancelled after an approval was
  denied"). Extend the withheld test to assert no attribute value outside `REVIEWED_METADATA` appears in the headline.

### P2-2 `tests/tokens-use.test.ts:7-18`: the "red only for failure" test only looks at a hand-picked file list

- **Problem.** `FILES` omits `analytics.css`, `illustrations.css`, `login.css`, `setup-guide.css`, `settings.css`
  (all new in this phase). Today none of them read `--fail`/`--bad` (grep VERIFIED), so the test passes, but the test
  is described as "the review point" for ADR-111 and it cannot see a violation in exactly the files this phase added.
  Mentally mutating `login.css` with `.notice { color: var(--fail) }` passes the suite.
- **Correction.** `readdirSync(DIR).filter(f => f.endsWith(".css"))` as `motion-contract.test.ts` already does, and keep
  `ALLOWED` as the single allowlist.

### P2-3 `lib/timeline.ts:217-222` + `components/Timeline.tsx:88,933-944,970`: the fault line sits on the group header even when the group is expanded, so a pre-error row is tinted "after the fault"

- **Problem.** `faultRowIndex` returns the first row whose `errorEventId` matches, and a group header always precedes its
  members, so with the default (expanded) grouping the line is drawn on the header row and `data-after-fault` is set on
  every row below it, including the group's `tool.call.started` row that happened before the error. The label says
  "step 7" on the row of step 6; the `data-first-error` tint is one row lower than the line. `fault-line.test.tsx:47-64`
  asserts this ("the line sits on the group's row and the error row is below it"), so it is intended, but it contradicts
  D5/acceptance 4 ("rows above it are quiet, rows below it carry a faint tint": the started row is neither above nor
  after the error). The collapsed case is right.
- **Correction.** Anchor on the group header only when the group is collapsed:
  `r.kind === "group" ? r.collapsed && r.errorEventId === id : r.event.event_id === id`, and update the test.

### P2-4 Plan and state documents do not meet their own gate

- `docs/plans/completed/phase-22-run-page-redesign.md` has no "modified tests" list (acceptance 12), no closing evidence
  per criterion, and still reads "Status: Planned"; `docs/PROJECT_STATE.md` and `docs/KNOWN_ISSUES.md` are untouched on
  the branch although the plan pulls in KI-119, KI-120, KI-124, KI-125, KI-126, KI-127 (the code cites KI-125/126 as done)
  and the session protocol requires the state file to be updated. If step 11 is still to come, fine, but the review
  cannot evidence acceptance 12/13 today.
- `docs/architecture/api-v1.md:154` and `runs/schemas.py:107-110` say `first_error` is "null when the run has no error or
  its summary predates version 5" without saying how a client tells them apart; ADR-112 says "read `summary_version`" but
  the two web consumers do not (see P1-3).

### P2-5 `analytics/schemas.py:286-288` and `waste.py:104-106,221`: `unrebuilt_runs` says one thing and counts another

- **Problem.** The field is documented as "Runs summarised before cost lines existed; `rebuild-costs` fills them in",
  but the query counts `0 < summary_version < SUMMARY_VERSION`, i.e. every run below 5 (cost lines arrived at 2). The
  OpenAPI text is therefore wrong for the common case (v4 runs with cost lines) and the web panel does not show the
  field at all, so an operator cannot see it. ADR-113 says analytics "counts them".
- **Correction.** Document it as "runs summarised below the current summary version (first-error facts missing)", or
  split into the two counts; show it in `WastePanel` beside `after_first_error_unknown_runs` (which the panel does show).

### P2-6 `components/RunsList.tsx:346-353` + `components/Facets.tsx:85-93`: two status mechanisms silently AND to nothing

- **Problem.** "Needs attention" sets the legacy `status=` parameter (a disjunction); the Status facet appends
  `status:failed` to `q`. Both are on the same page. With `status:success` in `q`, pressing "Needs attention" yields an
  empty list with "No runs match these filters" and no hint why; the facet buttons do not reflect the legacy parameter
  and the attention button does not reflect the facet. The comment acknowledges the grammar cannot express OR.
- **Correction.** Short term: when toggling attention on, remove any `status:` term from `q` (and vice versa), or render
  the attention state as a disabled-looking facet row. Longer term: a `needs_attention` grammar term (server side).

## P3: optional

- P3-1 `runHeadline.ts:63-69,216-231`: when the first error is the terminal event itself (TIMED_OUT via
  `run.completed`/`timeout`, or FAILED via `run.failed` with no earlier error) the lead reads
  "Timed out at step 12 (+30 s): run.completed, timeout" and the list "Timed out at step 12 · no retries". Map the
  lifecycle types to words ("the run's end") rather than printing the event type and lower-case status.
- P3-2 `RunDetail.tsx:278,411-413,662,748`: `jumpToError`, `firstErrorId` and `faultAtMs` use the client's
  `firstError(events)` while the headline uses the server's event id. Until the page holding the error has loaded
  (pages autoload, `RunDetail.tsx:198`), the Go button is inert and there is no line; the headline reads "...: an event".
  Prefer `model.failure?.eventId` and let `jumpTo` fetch the unloaded event (it already can).
- P3-3 `Timeline.tsx:60-86`: `atEdge` is measured only on scroll and mount; a window or inspector resize that changes
  `clientHeight` leaves it stale, and "N new events" counts events a class filter hides. Measure in the arrival effect
  and count only visible rows.
- P3-4 `RunsList.tsx:105-117`: `guideShown` never resets, so after the first run arrives, adding then clearing a filter
  brings the "Your first run arrived" guide back over a populated list. Reset it when `runs.length > 0` was seen without
  the guide.
- P3-5 `waste.py:88,110-120` vs `96`: agents whose failed runs cost $0 (all unpriced) are dropped from `by_agent` by
  `HAVING wasted > 0` while `failed.runs` counts them, so `sum(by_agent.failed_runs) < failed.runs`. Either keep zero
  rows or document the difference. ADR-113's "indexed by `ix_cost_project_started`" is optimistic: the model queries
  filter on `runs.started_at`/`runs.project_id` after the join, not on the cost lines' own columns.
- P3-6 `fixtures/builder.ts:131-154`: the fixture's `seconds_after` measures to the last event rather than
  `completed_at`, and `summary_version` stays 1 while `first_error` is populated; harmless for fixtures, but the web
  fallback branch (`summary_version < 5`) is then never exercised by fixtures once P1-3 is fixed. Set it to 5.
- P3-7 `runs-list.css:107-117`: `.row-more` is roughly 20 x 24 px (font 0.85rem, no vertical padding), under the
  WCAG 2.2 AA 24 x 24 px target minimum and well under the plan's touch-target intent; `.facet-button` on phones is
  similar. Give both `min-height: 24px`/`min-width: 24px`.
- P3-8 `tests/motion-contract.test.ts:71-72,157-162`: `animation-iteration-count` is not in `MOTION_PROPS`, so
  `.x { animation-name: spin; animation-iteration-count: infinite }` escapes both the "stray" and the "nothing loops"
  checks. Add it (and `animation-delay` is harmless). Otherwise the mutations I tried mentally (an ungated `transition:
  opacity 120ms`, an ungated keyframe used by a gated rule) do fail the test as intended.
- P3-9 `components/RunHeadline.tsx:106-113`: `key={changes}` remounts the headline on a state change, so a focused Go
  button loses focus when a live run flips to failed. Fade via a class toggle instead of a remount.
- P3-10 `public/brand/option-b.svg`, `option-c.svg`: unused design options are shipped as public assets; keep them in
  `docs/design/` only.
- P3-11 `RunsListPage.tsx:33-44`: the URL-adoption logic compares `sp.toString()` with the string the page wrote; it
  works because both come from `URLSearchParams` (READ). A comment that this equality is load-bearing (encoding of `,`
  and spaces) would stop a future `encodeURIComponent` from breaking back/forward adoption.
- P3-12 `error.tsx:17`: `role="alert"` on `<main>` that contains the brand link and two actions; an alert region with
  interactive content is read in full by screen readers on every mount. Put the role on the paragraph.

## Checks made with no finding (for the record)

- SUMMARY_VERSION bump effects: list and detail carry `first_error` identically (test); `WastePanel`, `failureLine` and
  `deriveFailure` all handle `null`; analytics counts v<5 failed runs as `after_first_error_unknown_runs` rather than
  zero (`waste.py:99-100`, tested with a forced v4 row).
- `cost_after` with unpriced calls: counted, priced at 0, reported (`summary.py:241-251`, tests); `seconds_after` for
  running runs measures to the last event and is clamped at 0 (tests).
- Multi-agent runs and `sequence: null`: canonical order via `sort_events`; shuffled-arrival tests pass; "after" is
  canonical order by design (ADR-112).
- Waste window: `started_at >= UTC midnight(start) and < UTC midnight(end)`, the same predicate the rollups use
  (`rollup.py:80,109`); the reconciliation test compares against the cost report.
- `/` shortcut: ignored in inputs, textareas, selects, contenteditable and `role=textbox|combobox`
  (`TopbarSearch.tsx:18-26`); no other `/` handler exists in `apps/web/src` (grep).
- Hydration: `useSyncExternalStore` server snapshots are `false`; `Date.now()` is in `useState` initialisers on client
  components that render a skeleton on the server; `useId` for ids. No React #418 vector found.
- Keyboard: FilterMenu returns focus on Escape and closes on outside pointer/blur; the combobox has
  `aria-activedescendant`, `aria-controls`, an `Escape` that closes then clears; EventDrawer is `inert` while leaving.
- Accessible name "Show it" vs visible "Go" (`RunHeadline.tsx:42-55`): WCAG 2.5.3 wants the visible label contained in
  the accessible name; "Go" is not in "Show it". Low severity because the `title` carries both words, but a name like
  "Go to the first error" would satisfy both the tests (`getByRole("button", {name: /Show it|Go/})`) and the rule.
- Contrast: `tokens-contrast.test.ts` resolves aliases and checks all semantic pairs in both themes plus the filled
  FAILED badge; the walker reads the first `:root` block and the first dark block (READ: order in `globals.css` holds).
- `diff-del` and `button.danger` moved from red to amber (`globals.css` diff); the token-use test asserts it.
