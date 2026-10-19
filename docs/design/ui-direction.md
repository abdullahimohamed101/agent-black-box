# Agent BlackBox: UI direction (as landed in Phase 21)

The web UI is an application, not a centred page: a left rail, a slim top bar, full-width content, and a run page whose header is an
instrument panel over a work area that fills the viewport. This file records what shipped and the rules the stylesheets follow. The
original three-part working document (slop inventory, polish plan, motion audit) was cut down to this once the phase landed; the
decisions it produced are D1-D16 in `docs/plans/completed/phase-21-ui-shell-and-design-pass.md` and the screenshots are in
`docs/screenshots/phase-21/{before,after}/`.

Concept renders (the target look; the shipped UI follows them except where "Left out" says so): `docs/design/concepts/app-run-story-1600x1000.png`,
`app-run-waterfall-1600x1000.png`, `app-runs-list-1600x1000.png`, `app-light-1600x1000.png`.

## Direction

"Flight-deck instrument panel": the product is a flight recorder, so the UI borrows the one thing cockpits do better than dashboards, a strict
colour grammar and dense readouts in a monospace face. Orange (`--mark`) is the brand mark and the recording dot only; it is never an
interaction colour. System light and dark (no toggle). IBM Plex, self-hosted (ADR-110).

## Frame

```text
.shell   grid: 14rem rail | 1fr    rows: 2.75rem top bar | 1fr
  aside.rail   brand, workspace and project switchers, nav[aria-label=Primary] (Overview, Runs, Security, Analytics, Experiments, Settings in session mode), sign-out
  header.topbar   breadcrumb (nav[aria-label=Breadcrumb]), live indicator ("Recording" while a run is RUNNING, else "Idle")
  main#main.page      document pages: scrolls the document
  main#main.app-page  run page only: fixed height, header on top, work area scrolls inside
```

Pages outside the shell (`/`, `/login`, `/invite`, workspace unavailable) are a centred `main.auth` card with the brand mark. Below `52rem`
(or below `36rem` of height) the rail becomes a strip under the top bar and the run page is a scrolling document again with the inspector as
an overlay.

## Run page

```text
header.run-head   h1, status badge, retries tag, id line (mono) / headline sentence with the inline "Show it" control / readout strip
div.pane-head     view tabs (native radios drawn as tabs: Timeline n | Waterfall | Evidence n)  ·  Context toggle
div.work          context column (Story, Agents, Findings, Evaluations; 18rem; CLOSED by default, a 2.25rem strip; the Context button reads "Context · 2 findings"; once opened it stays open beside the inspector, which narrows to 20rem under 88rem, and folds only below 1280 px) | pane (timeline, waterfall or evidence) | inspector (26rem, draggable)
```

- The class-chip toolbar is one row; the chip group scrolls sideways (a fade marks the edge) while "Errors only" and "Collapse groups" stay pinned. Below `40rem` of pane width the "Event classes" legend is visually hidden but keeps naming the group.
- The folded context strip is a button with an icon, the vertical label and count badges (findings in amber, agents). Its accessible name is fixed ("Show run context"); the title spells the counts.
- Story steps carry one marker, the list number; the tone shows in the label's colour and words ("fail", "Retry #1"). Agents are clamped to two lines with the full text in the DOM and the title.
- Rows adapt to the pane's own width (container queries), not the viewport's, because the pane is narrow whenever the inspector is open.
- Virtualised lists (`.timeline`, `.wf-scroll`) always have a definite height; 10,000 events render under 80 rows.

## Tokens and type

Colour tokens live in the first two blocks of `apps/web/src/app/globals.css`; `apps/web/tests/tokens.test.ts` parses them and asserts every text role on
every background in both schemes is at least 4.5:1. Status grammar: red warning (`--bad`), amber caution (`--warn`), green normal (`--ok`),
cyan advisory (`--accent`), grey neutral; always a glyph and a word as well as colour. 4 px radii everywhere (6 px maximum), structural hairlines instead of cards, no shadows except the waterfall tooltip.

Type: Plex Sans Condensed for headings, column heads (small caps, 0.66rem, letter-spaced) and tabs; Plex Sans for body at 13 px; Plex Mono, tabular, for every
number, id, offset and event type. Every table in the app shares one dense style (small-caps column heads, hover row); pages share one head (`.page-head`: h1 plus a one-line lede, or h1 plus its controls).

## Motion

Phase 22 step 6 replaced the Phase 21 layer. Every animation lives in `apps/web/src/app/run-motion.css`, inside `@media (prefers-reduced-motion: no-preference)`,
so a person who asks for reduced motion gets the final states and nothing else. Language: 100-280 ms, ease-out `cubic-bezier(0.2, 0, 0, 1)`, `transform` and
`opacity` only, no springs, no bounce, no loops. `tests/motion-contract.test.ts` fails when any keyframe, animation, transition or `scroll-behavior` in `src/app` is
neither gated, nor switched off under `reduce`, nor timed only by the `--t-*` tokens (zero under `reduce`).

- Fault line: draws itself once (280 ms, `scaleX` from the left on the timeline, `scaleY` from the top on the waterfall) when the first error is first on screen;
  `RunDetail` marks `data-fault-intro` for 340 ms, once per page, so a filter or view change never replays it.
- Headline: a change of state (running to failed) fades the new one in over 120 ms; the first headline on screen, and skeleton to ready, do not.
- Jump (`Go`, `e`, "Show evidence"): scrolls, then a 600 ms outline fades once (red for the first error, the accent for other evidence). Reduced motion draws the same outline statically for the 600 ms.
- Inspector: opens with a 160 ms slide of 8 px and fade; closes in 100 ms over 4 px (the last event stays mounted, `inert`, until then).
- Live rows: the cells of a row that arrives on the stream fade in over 180 ms with a 4 px rise, all at once. The timeline follows the live edge only while the reader is on it; scrolled away, it does not move and a "N new events" control appears.
- Row hover is an instant background change; no row has a transition. The live-connection glyph is a steady dot.
- The loading spinner (`States.tsx`) is the one remaining loop; it has a reduced-motion override.
- Nothing animates on first paint apart from a failed run's fault line: `e2e/design.spec.ts` asserts no animation is left running and none loops, on every route.

## Replay

Phase 12 (ADR-120) adds a replay mode to the run page. The header gets a `Replay` toggle beside `Context` (`aria-pressed`, key hint `r`); when on, a transport
bar is docked under the header (in the fixed-height frame it is a flex item that never scrolls away; in the stacked phone layout it sticks under the top bar).
Everything below reads one playhead (`replay.state`), so the page is the same page, "as of this moment":

- Header: the figures are as of the playhead (`Elapsed`, cost so far, tokens, calls, retries, errors, files changed) and a figure the replay cannot state exactly says so in words: Cost
  reads "at least $X" with "N calls unpriced" under it, or "about $X" while the server has not priced a call yet; a viewer's Files changed reads "up to N" (paths are withheld) until the
  last event. The status badge becomes a plain mono line
  ("Running, 1 error so far, as of this moment"), never the final status. The headline reads "Running, step N, replay" until the first error is reached and then the
  first-error line in the existing failure style; it never states the outcome. Leaving replay restores the live header.
- Timeline: rows after the playhead are not in the list, so it grows as the run plays and follows the live edge only while the reader has not scrolled away (the
  existing "N new events" control). The row at the playhead carries `aria-current="step"` and a leading edge mark in the accent colour; the fault line appears
  when it is reached.
- Waterfall: spans appear when the playhead reaches their start and open spans grow to it along the run's full axis ("in progress"); the agent tree marks the current
  agent with the same accent edge. Evidence and the context column's findings and agent totals are whole-run views and say so in one line.
- Red stays for failure only: the transport uses the accent for the playhead and play, and the one failure mark is the first-error tick on the scrub bar.
- No animation is added. Replay updates are instant; the fault line's Phase 22 draw-in plays at the moment it is reached, only under `no-preference`.
- At 375 px the transport keeps play, step, time and the scrub bar and moves speed, the two toggles and "Leave replay" into an Options menu; targets are 44 px.
- Keys: Space play/pause, Left/Right step, `[` `]` speed, `r` enter or leave, Esc closes the inspector and then leaves; a focused control, a text field or a widget with its own
  arrow keys (the scrub bar, the view tabs) keeps its keys. Focus stays on the Replay button on entry (the transport is the next tab stop) and returns to it when the
  transport's own Leave button is used.

## Security (Phase 13)

Security findings (ADR-131) follow the same grammar as every other status, with no new colour token. Severity is a shield glyph plus a word, carried by
weight: Critical is a filled shield in ink with a bold word, High an outlined shield in amber (`--wait`) with a semi-bold word, Medium and below an outlined
shield in muted ink. Never red: red stays for a failed run (`tests/tokens-use.test.ts` covers `security.css`; `tests/security-severity.test.tsx` asserts the
contrast of every pair in both themes). The glyph is `aria-hidden`; the word is the name.

- Run page: a quiet chip beside the status ("N security findings", only when there are some) opens the Context column on a separate Security group above
  the reliability findings: one line per finding (severity, the API's fixed title, a one-line rule in words, "Show evidence", or "No event to show" for an
  artifact-only finding). The Context button reads "Context · 2 security, 1 finding". Replay keeps the group live (whole run) under the existing one-line note.
  `?finding=<id>` (the link from the Security page) opens the column, marks that finding and opens its evidence.
- Only the API's fixed strings and counts are rendered (`securityLine` reads known fields and ignores the rest); the evidence link is where role rules apply.
- Workspace page `/w/{workspace}/security` (rail item after Runs): dense table (severity, title and rule, run as a link, project, when), facet column
  (severity, detector, project, run-started presets) whose clicks write the URL (`pushState`), cursor paging, skeleton, error with retry, "Nothing suspicious found"
  with the detectors that are on, "No findings match" with Clear filters. In a project the rail link is the same page pre-filtered with `?project=`. At 375 px the
  facets fold into "Narrow results" and rows become cards. BILLING (no `run.read`) sees "Not available for your role".
- Settings `/w/{workspace}/settings/security` (tab shown with `project.write`): capture mode radios with plain explanations, detector mutes, destination allowlist
  with client-side validation that mirrors the API; a save shows what the API stored (no optimistic UI); changes are audit-logged.
- No animation was added.

## Approvals and Policies (Phase 14)

Same grammar as Security (ADR-111): an outcome is a glyph and a word, carried by weight, never a new hue and never red. Waiting and expired are amber
(`--wait`), Approved, Allowed and Used are a check in ink, Denied is an outlined slashed circle in ink with a bold word (a denial is a policy outcome, not a
failed run), Cancelled is muted. Risk class is the Security shield (filled for R4, amber outline for R3, muted below). `policy.css` is covered by the red-only,
motion and contrast tests (`tests/policy-css.test.ts`); no animation was added.

- Words that state the guarantee (D1): "Policies are enforced for agents that ask Agent BlackBox through the SDK guard. They do not stop code that bypasses it."
  sits at the top of the Policies page in a bordered note; no "protected" badge anywhere.
- Approvals `/w/{workspace}/approvals` (rail item with a live pending count, polled every ~30 s on a visible tab; shown with `approval.read`): tabs Pending, Decided, All as
  links writing `?state=`; the open request is `?approval=<id>`. Table (request with scrubbed resource, risk, state, run, project, requested, time left, policy and
  version), cursor paging, pending list re-read every ~10 s on a visible tab. The detail is a sticky side panel (full screen at 375 px, where the list steps aside):
  the intent as the API returns it, arguments as formatted JSON text, Approve and Deny with an optional comment (1,000 characters) and a confirm step; the API's fixed
  errors are shown as fixed sentences and the answer takes the focus. Time left is the server's `seconds_left` aged by the time since it arrived, never negative, "Expired" at zero.
- Policies `/w/{workspace}/settings/policies` (tab with `policy.read`; editing needs `policy.write`): scope picker (`?project=`), plain monospace editor with a line-number gutter and
  no editor library, debounced validation by the API with `line:column` issues that move the caret, "Start from the example", Dry run with a result panel (counts, rules, up to 25 samples
  linking to the run and `?event=`), Publish with an optional comment and `base_version`, a conflict notice with a reload action, version history with read-only versions and a unified
  diff (added lines bold ink, removed lines amber), an unsaved-changes guard, and a pointer to the audit log.
- Run page: `policy.decision`, `policy.action.blocked` and `approval.*` rows read "Allowed by rule vN" / "Allowed (default)", "Denied by rule vN" plus the policy's own reason, "Approval requested",
  "Approved", "Approval expired", "Approval cancelled", "Approval used", in the existing "Approvals & policy" class, with a small "policy service" marker (full sentence for assistive tech and
  the inspector) on events whose `sdk.name` is `abb-policy-service`. A WAITING_FOR_APPROVAL run's headline reads "Waiting for approval: <capability>" with the real time left (roles that
  may read approvals) and a banner with "Open Approvals". Nothing about the fault line changed: a denial is not an error event.

## Left out of this phase (deliberately)

- Top-bar search slot and command palette (D6); the runs-list search box stays on its page.
- Inspector tabs (D8, would be `role=tab` without panels), a collapsible icon rail, a light/dark toggle, a designed brand mark (the glyph is a placeholder).
- "Planned" rail items (Policies, Alerts) and the Compare tab: they would be dead links.
- A fade between views (the inspector fade came back in Phase 22; specs that run axe right after a click wait for animations to settle, `e2e/motion.ts`).
- Sticky table headers (cannot work inside the horizontally scrolling `.table-wrap`), a wide inspector mode.
- Settings pages are restyled by the same rules but are session-only; their screenshots come from `auth-e2e` into `docs/screenshots/phase-15/`.

## Rules for the next change

1. New colours are tokens with a contrast row in `tokens.test.ts`; no raw hex in components.
2. A new status is a `badge` with `data-tone`, a glyph and a word.
3. A new table uses the global table rules; a new page starts with `.page-head`.
4. A new animation names its duration token and has a `prefers-reduced-motion` rule; it never animates opacity of text that a spec measures right after an action.
5. Untrusted text (run names, agent ids, paths, commands, evaluation text) is rendered as a text node, never HTML.
