# Phase 12: Visual replay (level 1)

Status: Completed (merged 2026-10-12, PR #136). Decisions D1-D6 were settled with the user one question at a time (grill, 2026-10-12).
Owner: web, plus one additive API field pair added by the review (ADR-121: per-event settled cost, replay parity). Branch: `feature/phase-12-visual-replay` (from `main` `4941661`).
Depends on: Phase 22 (merged: run header, fault line, motion layer, context column), Phase 9 (waterfall, agent tree, skew detection), Phase 15 (roles, withholding).
Spec refs: §29, §87 level 1, §62 invariants (INV-5 withheld content), `docs/design/ui-direction.md`, ADR-111.

## Outcome

An engineer can replay a recorded run: a playhead moves along the existing run page and every panel shows the state as of that moment (events so far, cost so far, retries, errors, files changed, the diff and shell output that had happened, the fault line once reached). Nothing is executed and nothing reaches the original systems.

## Non-goals

- No execution of any kind: level 2 (mocked replay) and level 3 (live branch replay) stay deferred. Replay never calls a model, a tool or an external system.
- No new server state (saved replays, annotations, bookmarks) and no new API endpoints. The playhead is the URL. (The review added two additive read-only fields on the events the page already reads, ADR-121.)
- No video or GIF export (the demo video is a separate task).
- No replay inside compare; Evidence stays its own tab and is not replayed.
- No change to what events record. If an event lacks something replay would like, that becomes a known issue.

## Decisions (settled with the user)

- D1 Replay is a scrubbable reconstruction of the run page, not a video and not a separate list. A playhead sits on the existing timeline; the event list, header figures (cost so far, retries, errors, files changed), story, diff and shell panels show state as of the playhead; the Phase 22 fault line appears when the playhead reaches the first error; an option pauses automatically there.
- D2 Pacing: canonical order only (never the clock; multi-agent skew makes clock order unsafe). Each gap plays at its recorded length clamped to 80 ms-1.5 s at 1x; 2x and 5x divide the delay. A clamped gap is labelled ("40 s model call, skipped to 1.5 s") on the scrub bar and status line. Stepping jumps by event group (a request's started and completed events are one unit by default, with a "Group steps" toggle to step single events). The scrub bar shows true elapsed time with the fault line marked.
- D3 Data and the "no external contact" rule: replay is a client-side projection over events the run page already loaded through the existing read endpoints; artifact content (long shell logs) loads on demand through the existing artifact endpoint when the playhead reaches it. While replay runs every request must go to the app's own origin and be a GET; a Playwright test blocks every other origin and every non-GET and fails on any attempt, and asserts playing from loaded data makes zero requests. Roles are unchanged: a viewer's replay shows the metadata-only events of their run page with "Content withheld" panels, and the test covers it. The playhead lives in the URL (`?t=<event id>`); nothing is written to the server. A live run replays only the events received so far, with the existing "N new events" control.
- D4 Entry and controls: a Replay button in the run header (next to Context) toggles a mode and docks a transport bar under the header; Timeline and Waterfall follow the playhead. Transport: play/pause, step back/forward, 1x/2x/5x, scrub bar, elapsed time, "Pause at first error" (on by default), "Group steps". Keys: Space play/pause, Left/Right step, `[` and `]` speed, `r` enter or leave replay, Esc leave replay; they do not clash with `j k e`. Reduced motion: replay opens paused and every update is instant (no animated transitions). At 375 px the bar collapses to play/pause, step and the scrub bar, with speed in a menu. The scrub bar is a real slider with a text value ("event 7 of 15, 6.2 s"); stepping announces the current event politely, playing does not announce every tick.
- D5 Success and performance: on the failed fixture, a first-time user opens replay, presses play and it stops at the first error with headline, cost-so-far, retry count and fault line matching that moment, in three actions or fewer and under 15 s at 1x (Playwright at 1440 and 375, plus a unit test that the state at the pause equals the state computed independently from the events up to that event). The 10,000-event fixture replays at 5x and a scrub jump from start to end updates in under 100 ms (generous CI bound); the projection is incremental with checkpoints every N events, only visible rows re-render.
- D6 Multi-agent: one canonical order for every agent; on the Waterfall spans appear when the playhead reaches their start and grow until their end; the agent tree highlights the current event's agent; when clock skew was flagged the transport bar notes "timing across agents is approximate" and pacing uses only the clamped gaps.

## Known issues considered

No open item targets Phase 12. Considered and kept deferred: KI-118 (live-stream motion test), KI-126 (evidence focus), KI-127 (flaky E2E: replay tests wait on rendered state, never fixed timeouts).

## Acceptance criteria

1. The state at any playhead position equals an independent from-scratch computation over events up to that position (property test over the fixture runs: header figures, visible events, error count, retries, cost so far, files changed); at the last event it equals the run summary, for every role: cost is the server's settled per-call figure (ADR-121) and a viewer's withheld file paths are counted per event, flagged approximate, and replaced by the summary's exact count at the end. A figure the replay cannot state exactly says so ("at least", "about", "up to").
2. Pacing: gaps clamp to 80 ms-1.5 s at 1x and scale at 2x/5x; order is canonical; clamped gaps are labelled (unit tests, including a skewed multi-agent run).
3. Network isolation (Playwright): during replay of the failed fixture every request is same-origin GET; any other request fails the test; playing from loaded data makes zero requests.
4. Viewer replay shows only metadata and "Content withheld" panels; nothing a viewer could not already see is fetched (component test and API-backed spec).
5. Transport: all controls work by mouse and keyboard; Space/Left/Right/`[`/`]`/`r`/Esc do not clash with existing shortcuts; the scrub bar is an accessible slider; axe clean; 375 px layout works; replay always opens paused, and reduced motion also gets instant updates (review P3-2: the run page never autoplays, so "opens paused" is not specific to reduced motion).
6. The playhead round-trips through the URL (`?t=`) including reload and back/forward; an unknown or withheld id degrades to the start with a notice.
7. The success test of D5 passes at 1440 and 375 px.
8. Performance: the 10,000-event fixture scrubs start-to-end in under 100 ms per jump (bounded in a test) and plays at 5x without the page freezing.
9. Multi-agent: waterfall spans appear and grow with the playhead, the agent tree highlights the current agent, the skew note shows for a flagged run.
10. No new API endpoint, no migration; one additive response field pair (ADR-121); existing tests pass; every modified existing test is listed with its reason in this plan; `scripts/quality.sh full` green in CI; adversarial review has no open P0/P1.

## Verification plan

Unit tests for the projection, checkpoints and pacing; component tests for the transport; fixtures Playwright for isolation, success, keyboard, URL, reduced motion, 375 px and axe; the API-backed specs for the viewer case; screenshots; my own gate run, then Fable review.

## Risks

- Header, timeline, waterfall and panels must all derive from one playhead source of truth; a second copy of the "state so far" would drift (mitigated by one projection module and the parity property test).
- The 10,000-event case: virtualised lists plus incremental projection; the budget test guards it.
- Live runs: events arrive while replaying; the playhead must not jump (index stable, new events append).
- Phase 22 motion contract: replay adds animation-free updates only; the zero-motion contract test must stay green.

## Ordered steps (one commit each)

1. Replay core (pure library): event projection with checkpoints, state-at(index), pacing and group stepping, gap labels, tests including the 10,000-event bound and the parity property test.
2. Player and transport: `useReplay` hook (timer, play/pause, speed, URL `?t=`, reduced-motion default, keyboard), the transport bar component, accessible slider, 375 px layout.
3. Wire into the run page: header figures, timeline, waterfall, context/inspector, story/diff/shell panels follow the playhead; fault line at first error; pause at first error; multi-agent spans, agent tree and skew note; withheld behaviour for viewers.
4. Verification suite: network isolation, success test, viewer case, keyboard, URL, reduced motion, axe, performance; screenshots; ADR and docs.
5. Review, fixes, closing evidence, push and PR. Review done and fixed (below); the push and PR are left to the lead.

## Closing evidence

Evidence vocabulary: VERIFIED (run in this environment, output observed), UNVERIFIED (env), ASSUMED. Commands from `apps/web` unless stated. Figures below are from the final run after the review fixes (the pre-review run was 228 passed). Fixtures Playwright: `pnpm test:e2e` -> 230 passed, 76 skipped, 0 failed (38.2 s; +2 from `e2e/replay-fidelity.spec.ts`). A first post-fix run had 2 failures, both the axe spec's Escape step: a real defect in the P2-2 fix (React flushes the inspector's own Esc before the document listener sees the same key, so one press closed the inspector and left replay), fixed with a keydown snapshot and a unit test, then 230/230. No flake re-run was needed.

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | state equals an independent recomputation; last event equals the summary | VERIFIED `vitest run tests/replay-projection.test.ts` (step 1); on the page, `tests/replay-run.test.tsx` compares header figures at five playheads with figures reduced from the raw events here and with the live readouts at the end |
| 2 | pacing clamps, scales, labels; canonical order | VERIFIED `tests/replay-pacing.test.ts` (includes the skewed multi-agent run) |
| 3 | network isolation | VERIFIED `e2e/replay.spec.ts` "acceptance 3": `context.route` aborts any non-GET or cross-origin request (none), and the page made 0 requests after replay started through play-to-error at 5x and on to the end. Chrome re-fetches the tab icon `/icon.svg` (resource type "other") on every address-bar URL change, so replay's `?t=` writes show 1-2 of those; excluded from the count and shown to be the browser's own by a control test that changes the URL with replay never entered |
| 4 | viewer: metadata only, nothing extra fetched | VERIFIED `tests/replay-run.test.tsx` (viewer projection, no request while replaying, withheld wording), `e2e/replay.spec.ts` (fixtures viewer, coding run), and API-backed `e2e/replay-viewer-real.spec.ts` inside `AUTH_E2E_DB_NAME=abb_p12_auth` with that file appended to the script's Playwright command in a temporary copy (15 passed, "AUTH-E2E PASSED"). `scripts/auth-e2e.sh` lists the file in its Playwright command (commit `f0da604`; review P3-3 corrected this row) |
| 5 | transport by mouse and keyboard, no key clashes, slider, axe, 375 px, reduced motion | VERIFIED `tests/replay-transport.test.tsx`, `tests/replay-hook.test.tsx`, `tests/replay-run.test.tsx` (keyboard precedence), `e2e/replay.spec.ts` (keyboard, 375 px bar and 44 px targets, axe light and dark at start/first error/inspector/waterfall, reduced motion: paused, `document.getAnimations()` empty and `animation-name: none` at the fault) |
| 6 | playhead in the URL | VERIFIED `e2e/replay.spec.ts`: `?t=` through reload and with `?view=`, leave removes `t`, goBack/goForward across pages, unknown id notice, no hydration warning on a deep link. Limitation observed: Next ignores state-less `pushState` entries on back, so only navigations the app makes round trip; replaceState writes never create entries |
| 7 | success test at 1440 and 375 | VERIFIED `e2e/replay.spec.ts` "acceptance 7": 2 actions, stops at the first error unaided (about 6 s at 1x), headline, cost so far, retries, errors and fault line equal values computed from the fixture events |
| 8 | 10,000-event scrub and 5x playback | VERIFIED `e2e/replay-perf.spec.ts`: 12 scrub jumps median 16 ms, max 25 ms (bounds 150 median, 500 max); 5x playback for 3 s on the timeline and on the waterfall: max frame gap 17 ms, no long task |
| 9 | multi-agent | VERIFIED `e2e/replay-agents.spec.ts` (spans appear, an open span grows then closes, agent highlight, skew note present on run 7 and absent on run 2, fault line on the waterfall); the existing fixture run 7 is already clock-skewed, so no new fixture was needed |
| 10 | no new endpoint, no migration; one additive field pair (ADR-121); existing tests pass; modified tests listed | VERIFIED: the API change is `cost_usd`/`cost_unpriced` on `EventOut` only (`runs/schemas.py`, `runs/service.py`, `cost/repository.py`; no route, no migration); full API suite `uv run pytest` (with `tests/authz`) 1,860 passed in 947.7 s on `abb_p12_full`; `ruff check`, `ruff format --check` (286 files), `uv run mypy` (264 source files), `python -m abb_api.openapi --check`, `pnpm gen:api:check` clean; web vitest 1,144 passed in 69 files, `tsc`, `eslint`, `prettier --check`, `pnpm build` clean; fixtures Playwright above; API-backed scripts with `abb_p12_*` databases: `auth` 17 passed, `findings` 6, `multiagent` 6, `coding` 6, `stream` 5, `eval` 7, `analytics` 4, all "PASSED" (screenshot churn outside `docs/screenshots/phase-12/` reverted). `scripts/quality.sh full` and CI: UNVERIFIED (env), left to the PR. Adversarial review: all findings fixed, dispositions below |

Regression found and fixed in step 4: the Replay button wrapped to a second row at 375 px and pushed the fault line below the fold in `e2e/five-second.spec.ts` (outside replay). At 375 px the tabs, Context and Replay now share one row (`run-replay.css`: tab counts and the key hint hidden below 26 rem).

Decisions taken in step 3 (recorded here, none changes D1-D6): the inspector follows the playhead when it is open and playback is paused or stepping (not on each tick, each follow being a request); Space on the timeline or waterfall plays/pauses while replaying (Enter still opens a row); `[`/`]` keep panning on a focused waterfall; "Show it", `e` and Evidence jumps move the playhead; Evidence and the context column are whole-run views with a one-line note; focus stays on the Replay button on entry and returns to it only when the transport's own Leave button is used.

## Modified existing tests

None weakened, skipped or deleted. `git diff main...HEAD --name-status` over `apps/web/tests`, `apps/web/e2e`, `scripts`, `packages`, `apps/api` shows the files below as modified; everything else is added.

| File | Why it changed |
| --- | --- |
| `apps/web/tests/tokens-use.test.ts` | step 2: one allowlist line for `replay.css`'s first-error mark on the scrub bar (`--fail`, a failure marker) |
| `apps/web/tests/replay-projection.test.ts` | review P1-2: the independent recomputation rounds the cost to nine places like the API's summary (was six); the projection now rounds to nine so a real server-priced run's sum equals `estimated_cost_usd` |
| `apps/web/tests/spanEvents.ts` | review P1-2: the event factory adds `cost_unpriced: false`, which the generated `EventOut` type now requires (a helper, no assertion) |
| `apps/web/e2e/replay-viewer-real.spec.ts` | review P1-1/P1-2: two tests appended (owner and viewer end on the header's Cost and Files changed on a server-priced run), plus the `figure` import; the two existing tests are untouched |
| `apps/api/tests/authz/test_content_withheld.py` | review P1-2: one test appended (`cost_usd`/`cost_unpriced` are identical for every role); nothing else changed |
| `scripts/auth-e2e.sh` | step 4: the replay spec in the Playwright line (`f0da604`); review: ingests a third run "Auth: replay run" (two server-priced calls with no cost attribute, two edited files) and passes `E2E_RUN_REPLAY` |

Non-test source changed for tests: `apps/web/src/fixtures/builder.ts` emits `cost_usd`/`cost_unpriced` on model calls like the API now does, and its summary cost is rounded to nine places like the worker's (existing fixtures are otherwise untouched; no new fixture was needed because the existing `wrongFixCodingRun` already edits two files).

Added: `tests/replay-{projection,pacing,hook,transport,run,waterfall-bounds,fidelity,review}`, `e2e/replay.ts`, `e2e/replay{,-agents,-perf,-shots,-viewer-real,-fidelity}.spec.ts`, `apps/api/tests/test_event_costs.py`.

## Code review dispositions

Review: `docs/plans/completed/phase-12-code-review.md` (P0 0, P1 2, P2 2, P3 9). Each fix has a test that failed on the old code (run against the pre-fix sources with `git stash`) and passes now.

| # | Severity | Status | Fix and test |
| --- | --- | --- | --- |
| P1-1 | P1 | fixed | `project.ts` takes the withheld branch from `withheld_attributes.includes("file.path")` (`isWithheld`), counts each such event on its own and flags `filesApproximate`; the header reads "up to N" with a tooltip and screen-reader text (`lib/replay/figures.ts`, `Metric`); at the last event of a finished run the summary's `files_modified` replaces the per-event count (`ReplayCtx.filesModified`, passed only when the summary is current and the run is over). Tests: `replay-fidelity.test.ts` (three modified events on two paths: owner 2, viewer 3 flagged; exact at the end; baseline and wrong-fix runs), `replay-review.test.tsx` (owner vs viewer on the page), `e2e/replay-fidelity.spec.ts` (fixtures, owner and viewer, no path leaks), `e2e/replay-viewer-real.spec.ts` (API-backed). Before: viewer 1 for 2 files. |
| P1-2 | P1 | fixed, real priced cost per event (not the floor fallback) | Feasible and cheap: ADR-121 adds `cost_usd`/`cost_unpriced` to `EventOut`, from one batched, workspace-scoped primary-key lookup per page of the stored cost lines (`CostRepository.event_costs`). Replay adds `cost_usd` per completed call (provider > price book > client, as the engine settled it); calls the server has not priced yet fall back to `cost.provider_usd` then `cost.estimated_usd` and read "about"; unpriced calls make the figure "at least $X" with "N calls unpriced" (header and headline detail); `unpricedCalls` is rendered. Tests: `apps/api/tests/test_event_costs.py` (events add up to `estimated_cost_usd` = 6.25 on a plain SDK run with no cost attribute, one lookup per page, workspace scoping, index plan on 200,000 rows), `tests/authz/test_content_withheld.py` (every role), `replay-fidelity.test.ts` and `replay-review.test.tsx` (every fixture run equals its summary with the attribute stripped; floor and "about" wording on the page), `e2e/replay-viewer-real.spec.ts` (real API: replay ends on "$5.75" for both roles). Measured: walking a 10,000-event run (20 pages of 500) went from 780-830 ms to 910-940 ms median, about +5 ms a page. |
| P2-1 | P2 | fixed | The hook holds one `ReplayProjection` and calls `setEvents` on every change of the list (version-keyed memo), so a live append keeps the accumulators and checkpoints; ADR-120 now says what runs (the remaining per-arrival work is light O(n) passes, not a re-walk). Test: `replay-review.test.tsx` appends one event to a 10,000-event run and asserts `apply` is not called (before: ~10,000 calls), then exactly the new event on the next move; a late event mid-run still starts over correctly. |
| P2-2 | P2 | fixed (two defects found on the way) | `inspectorOpen` may be a function, read at key time; the run page answers "is the inspector on screen" (`inspected` or a pending notice), not "is an id set". The dead-zone path is Play from the end (the follow only runs while paused, so the inspector's event is in the future): one Esc now leaves replay. Also fixed: the "Loading the event" notice appeared for the hidden event (it tested `drawerEventId`, now `drawerId`), and the inspector's own Esc must not also leave replay once React has flushed it (snapshot at keydown capture; found by the axe spec in Chromium, unit test `asks whether the inspector was open when the key went down`). Tests: `replay-review.test.tsx`, `e2e/replay.spec.ts` axe. |
| P3-1 | P3 | fixed | `enter` returns on an empty run, so `r` agrees with the disabled button. Test: `replay-review.test.tsx`. |
| P3-2 | P3 | fixed (wording) | The run page never autoplayed, so replay always opens paused and reduced motion adds instant updates; criterion 5, D4's summary and ADR-120 say so, the hook's `autoplay` option stays (and stays reduced-motion aware) for other callers. The reduced-motion spec already asserts what is true (paused, no animation). |
| P3-3 | P3 | fixed | Plan row 4, `docs/TESTING.md` and the modified-tests note corrected (the script lists the spec; its edit is listed). |
| P3-4 | P3 | fixed | `policy.action.blocked` in a run that ends BLOCKED sets the status line to "Blocked"; the headline reads "Blocked at step N" when the reached event is not an error. Tests: `replay-fidelity.test.ts` (including a run that is not blocked in the end). |
| P3-5 | P3 | fixed | A collapsed group carries `aria-current`/`data-current` when the playhead is inside it; the progress line says when a filter hides the playhead's event. Tests: `replay-review.test.tsx`. |
| P3-6 | P3 | fixed | The wait already done in a gap is kept across a speed change or a live arrival (fraction of the gap), and pause then play starts afresh. Tests: three in `replay-review.test.tsx` (before: the gap restarted). |
| P3-7 | P3 | fixed in part | `durationMs` is computed once in `load` (test poisons the parsed times and the value is unchanged); the per-tick `slice` is left as the reviewer noted (fine at 10,000, covered by the perf spec). |
| P3-8 | P3 | declined | Not reproduced, and every guard that tells our own write from an outside one by timing (ignore while a write is pending, remember recent ids) can swallow a genuine back/forward or pasted link; the cost of the race is a paused playhead at the previous position, one click to recover. Left as is and recorded here. |
| P3-9 | P3 | accepted as is | The reviewer's own recommendation is to leave it unless reported; filed as KI-131 (S3, to file). |

New known issues: KI-130 (per-event cost outside replay still reads the attribute; stream frames carry no `cost_usd`) and KI-131 (slider chatter). Neither blocks the phase.
