# ADR-120: Visual Replay (Level 1) Is a Client-Side Projection over Loaded Events

Status: Accepted (Phase 12, step 4).
Date: 2026-10-12
Phase: 12

## Context

Spec §87 level 1 asks for a replay of a recorded run: play, pause, step, 1x/2x/5x over the recorded events, aware of reduced motion, with no
contact with the systems the run touched. Phase 12's plan settled six decisions with the user (D1-D6). This ADR records them and what
follows from them, so the rules that tests now enforce are written down where the next change will look.

## Decision

- **A reconstruction of the run page, not a video (D1).** Replay is a mode of the run page. One playhead drives the header figures, the
  timeline, the waterfall, the agent highlight, the story and the inspector. The first-error fault line appears when the playhead reaches
  it, and playback can pause there (on by default).
- **Client-side projection (D3).** `ReplayProjection` (`apps/web/src/lib/replay/project.ts`) computes the state as of an event index from
  the events the page already loaded through the existing read endpoints: counts, cost so far, tokens, files changed, elapsed time,
  status text, open and finished spans. It is incremental, with a checkpoint every 256 events, so a jump re-applies at most one block.
  Every panel reads `replay.state`; no component keeps a second copy of "the state so far". At the last event the state equals the run
  summary (a property test over the fixture runs).
- **Canonical order, never the clock (D2, D6).** The playhead is an index into the page's canonical order (`sortEvents`). A multi-agent
  run's timestamps can run backwards (clock skew), so wall-clock order is unsafe. Elapsed time never decreases, and when skew is
  flagged (`replayClockSkewed`: Phase 9's skewed-span flag, plus canonical order running backwards across agents) the transport says
  "Timing across agents is approximate".
- **Clamped pacing (D2).** Each gap plays at its recorded length clamped to 80 ms-1.5 s at 1x; 2x and 5x divide the delay. A clamped gap is
  labelled in words ("3.1 s tool call, skipped to 1.5 s") from event kinds only, never attributes. Stepping jumps by group (a request's
  started and completed events), with a "Group steps" toggle for single events.
- **The same-origin GET rule (D3).** While replay runs, every request must go to the app's own origin and be a GET, and playing from loaded
  data makes none. `e2e/replay.spec.ts` refuses every other origin and every non-GET (and fails on any attempt), and asserts that no
  request is issued by the page after replay starts. The one request seen is Chrome's own refresh of the tab icon (`/icon.svg`) whenever
  the address bar's URL changes; the spec shows the same fetch after a plain URL change with replay never entered, and counts it
  separately. Artifact content (a long shell log) still loads on demand through the existing artifact path when its step is open.
- **Roles are unchanged (INV-5).** A viewer's replay shows the metadata-only events of their run page; "Content withheld" panels stay;
  replaying issues no extra request. Nothing a viewer could not already see is fetched.
- **The playhead is the URL (D3).** `?t=<event id>` is written with `history.replaceState` (trailing-debounced) when playback pauses and
  when the person seeks, steps, enters or leaves, never on a tick. It is not `router.replace`, which would refetch the server component
  and break the "no request" rule. It coexists with `?view=`, survives a reload, is adopted when the URL changes from outside, and an
  unknown id degrades to the start with a notice. Leaving replay removes `t`.
- **No new endpoint, no schema change, no new state (D3).** Nothing is saved on the server; a live run replays only the events received so
  far (the playhead index stays put while events append).
- **Interaction (D4).** A Replay button in the run header (`aria-pressed`, shortcut `r`) docks a transport bar under it. Keys: Space
  play/pause, Left/Right step, `[` `]` speed, `r` enter or leave, Esc closes the inspector and then leaves replay; they yield to a
  focused control, a text field or a widget with its own arrow keys. On the waterfall `[` and `]` keep their existing meaning (pan) while
  it has focus. Reduced motion opens paused and every update is instant; the fault line's Phase 22 draw-in plays at the moment it is
  reached only when motion is allowed. At 375 px the bar keeps play, step and the scrub bar and moves speed, the toggles and "Leave replay"
  into an options menu. The scrub bar is a native range input with a text value.
- **What is not replayed.** Evidence stays a whole-run view (with a note), and the context column's findings and agent totals reflect the
  whole run (with a note); both are derived server-side from the finished run.

## Alternatives considered

- A server-side replay endpoint that returns state at an event: a new endpoint, a new request per scrub, and a second implementation of the
  figures that would drift from the page's. Rejected by D3.
- Pacing by the recorded clock: wrong for skewed multi-agent runs, and a 40 s model call would be 40 s of nothing. Rejected by D2.
- A second derived copy of "state so far" per panel (header from one function, waterfall from another): drifts. Rejected; one projection,
  one parity test.
- Level 2 or 3 replay (mocked or branching execution): out of scope; replay never runs anything.

## Consequences

- Replay cost is client-side and bounded: a scrub on the 10,000-event fixture settles in tens of milliseconds and 5x playback keeps
  frames at display rate (measured in `e2e/replay-perf.spec.ts`).
- A new panel on the run page must read `replay.state` (through `RunDetail`) or say that it is a whole-run view; the headline must not
  state an outcome the playhead has not reached (`replayHeadline`).
- The zero-request rule is a test, so a future change that adds a fetch to replay fails it.
- Browser behaviour (the tab-icon refresh) is documented rather than hidden; if a browser starts making other requests on URL change, the
  isolation test names them.
