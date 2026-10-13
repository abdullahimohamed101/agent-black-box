# Phase 12: Visual replay (level 1)

Status: Implemented; pending review (2026-10-12; steps 1-4 committed, step 5 is review, fixes, push and PR). Decisions D1-D6 were settled with the user one question at a time (grill, 2026-10-12).
Owner: web (no API change planned). Branch: `feature/phase-12-visual-replay` (from `main` `4941661`).
Depends on: Phase 22 (merged: run header, fault line, motion layer, context column), Phase 9 (waterfall, agent tree, skew detection), Phase 15 (roles, withholding).
Spec refs: §29, §87 level 1, §62 invariants (INV-5 withheld content), `docs/design/ui-direction.md`, ADR-111.

## Outcome

An engineer can replay a recorded run: a playhead moves along the existing run page and every panel shows the state as of that moment (events so far, cost so far, retries, errors, files changed, the diff and shell output that had happened, the fault line once reached). Nothing is executed and nothing reaches the original systems.

## Non-goals

- No execution of any kind: level 2 (mocked replay) and level 3 (live branch replay) stay deferred. Replay never calls a model, a tool or an external system.
- No new server state (saved replays, annotations, bookmarks) and no new API endpoints. The playhead is the URL.
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

1. The state at any playhead position equals an independent from-scratch computation over events up to that position (property test over the fixture runs: header figures, visible events, error count, retries, cost so far, files changed); at the last event it equals the run summary.
2. Pacing: gaps clamp to 80 ms-1.5 s at 1x and scale at 2x/5x; order is canonical; clamped gaps are labelled (unit tests, including a skewed multi-agent run).
3. Network isolation (Playwright): during replay of the failed fixture every request is same-origin GET; any other request fails the test; playing from loaded data makes zero requests.
4. Viewer replay shows only metadata and "Content withheld" panels; nothing a viewer could not already see is fetched (component test and API-backed spec).
5. Transport: all controls work by mouse and keyboard; Space/Left/Right/`[`/`]`/`r`/Esc do not clash with existing shortcuts; the scrub bar is an accessible slider; axe clean; 375 px layout works; reduced motion opens paused with instant updates.
6. The playhead round-trips through the URL (`?t=`) including reload and back/forward; an unknown or withheld id degrades to the start with a notice.
7. The success test of D5 passes at 1440 and 375 px.
8. Performance: the 10,000-event fixture scrubs start-to-end in under 100 ms per jump (bounded in a test) and plays at 5x without the page freezing.
9. Multi-agent: waterfall spans appear and grow with the playhead, the agent tree highlights the current agent, the skew note shows for a flagged run.
10. No new API endpoint, no schema change; existing tests pass; every modified existing test is listed with its reason in this plan; `scripts/quality.sh full` green in CI; adversarial review has no open P0/P1.

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
5. Review, fixes, closing evidence, push and PR.

## Closing evidence

Evidence vocabulary: VERIFIED (run in this environment, output observed), UNVERIFIED (env), ASSUMED. Commands from `apps/web` unless stated. Fixtures Playwright: `pnpm test:e2e` -> 228 passed, 74 skipped, 0 failed (59.8 s; an earlier full run during a machine stall timed out on 21 tests and was discarded, the replay specs alone passed 31/31 both times).

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | state equals an independent recomputation; last event equals the summary | VERIFIED `vitest run tests/replay-projection.test.ts` (step 1); on the page, `tests/replay-run.test.tsx` compares header figures at five playheads with figures reduced from the raw events here and with the live readouts at the end |
| 2 | pacing clamps, scales, labels; canonical order | VERIFIED `tests/replay-pacing.test.ts` (includes the skewed multi-agent run) |
| 3 | network isolation | VERIFIED `e2e/replay.spec.ts` "acceptance 3": `context.route` aborts any non-GET or cross-origin request (none), and the page made 0 requests after replay started through play-to-error at 5x and on to the end. Chrome re-fetches the tab icon `/icon.svg` (resource type "other") on every address-bar URL change, so replay's `?t=` writes show 1-2 of those; excluded from the count and shown to be the browser's own by a control test that changes the URL with replay never entered |
| 4 | viewer: metadata only, nothing extra fetched | VERIFIED `tests/replay-run.test.tsx` (viewer projection, no request while replaying, withheld wording), `e2e/replay.spec.ts` (fixtures viewer, coding run), and API-backed `e2e/replay-viewer-real.spec.ts` inside `AUTH_E2E_DB_NAME=abb_p12_auth` with that file appended to the script's Playwright command in a temporary copy (15 passed, "AUTH-E2E PASSED"). The committed `scripts/auth-e2e.sh` is outside this phase's paths and does not yet list the new file: add `e2e/replay-viewer-real.spec.ts` to its Playwright line |
| 5 | transport by mouse and keyboard, no key clashes, slider, axe, 375 px, reduced motion | VERIFIED `tests/replay-transport.test.tsx`, `tests/replay-hook.test.tsx`, `tests/replay-run.test.tsx` (keyboard precedence), `e2e/replay.spec.ts` (keyboard, 375 px bar and 44 px targets, axe light and dark at start/first error/inspector/waterfall, reduced motion: paused, `document.getAnimations()` empty and `animation-name: none` at the fault) |
| 6 | playhead in the URL | VERIFIED `e2e/replay.spec.ts`: `?t=` through reload and with `?view=`, leave removes `t`, goBack/goForward across pages, unknown id notice, no hydration warning on a deep link. Limitation observed: Next ignores state-less `pushState` entries on back, so only navigations the app makes round trip; replaceState writes never create entries |
| 7 | success test at 1440 and 375 | VERIFIED `e2e/replay.spec.ts` "acceptance 7": 2 actions, stops at the first error unaided (about 6 s at 1x), headline, cost so far, retries, errors and fault line equal values computed from the fixture events |
| 8 | 10,000-event scrub and 5x playback | VERIFIED `e2e/replay-perf.spec.ts`: 12 scrub jumps median 16 ms, max 25 ms (bounds 150 median, 500 max); 5x playback for 3 s on the timeline and on the waterfall: max frame gap 17 ms, no long task |
| 9 | multi-agent | VERIFIED `e2e/replay-agents.spec.ts` (spans appear, an open span grows then closes, agent highlight, skew note present on run 7 and absent on run 2, fault line on the waterfall); the existing fixture run 7 is already clock-skewed, so no new fixture was needed |
| 10 | no endpoint or schema change; existing tests pass; modified tests listed | VERIFIED: no file under `apps/api`; web vitest 1,112 passed in 67 files, `tsc`, `eslint`, `prettier --check`, `pnpm build` clean; fixtures Playwright above; table below. API-backed scripts with `abb_p12_*` databases: `auth` 15 passed, `multiagent` 6, `coding` 6, `stream` 5, `findings` 6, all "PASSED". `scripts/quality.sh full` and CI: UNVERIFIED (env), left to the PR. Adversarial review: step 5 |

Regression found and fixed in step 4: the Replay button wrapped to a second row at 375 px and pushed the fault line below the fold in `e2e/five-second.spec.ts` (outside replay). At 375 px the tabs, Context and Replay now share one row (`run-replay.css`: tab counts and the key hint hidden below 26 rem).

Decisions taken in step 3 (recorded here, none changes D1-D6): the inspector follows the playhead when it is open and playback is paused or stepping (not on each tick, each follow being a request); Space on the timeline or waterfall plays/pauses while replaying (Enter still opens a row); `[`/`]` keep panning on a focused waterfall; "Show it", `e` and Evidence jumps move the playhead; Evidence and the context column are whole-run views with a one-line note; focus stays on the Replay button on entry and returns to it only when the transport's own Leave button is used.

## Modified existing tests

None weakened, skipped or deleted. `git diff main...HEAD --name-status` over `apps/web/tests`, `apps/web/e2e`, `scripts`, `packages`, `apps/api` shows one modified file; everything else is added.

| File | Why it changed |
| --- | --- |
| `apps/web/tests/tokens-use.test.ts` | step 2: one allowlist line for `replay.css`'s first-error mark on the scrub bar (`--fail`, a failure marker) |

Added: `tests/replay-{projection,pacing,hook,transport,run,waterfall-bounds}`, `e2e/replay.ts`, `e2e/replay{,-agents,-perf,-shots,-viewer-real}.spec.ts`.
