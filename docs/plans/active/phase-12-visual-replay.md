# Phase 12: Visual replay (level 1)

Status: Planned (2026-10-12). Decisions D1-D6 were settled with the user one question at a time (grill, 2026-10-12).
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
