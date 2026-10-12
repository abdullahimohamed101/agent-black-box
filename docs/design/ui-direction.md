# Agent Black Box: UI direction (as landed in Phase 21)

The web UI is an application, not a centred page: a left rail, a slim top bar, full-width content, and a run page whose header is an
instrument panel over a work area that fills the viewport. This file records what shipped and the rules the stylesheets follow. The
original three-part working document (slop inventory, polish plan, motion audit) was cut down to this once the phase landed; the
decisions it produced are D1-D16 in `docs/plans/active/phase-21-ui-shell-and-design-pass.md` and the screenshots are in
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
  aside.rail   brand, workspace and project switchers, nav[aria-label=Primary] (Overview, Runs, Analytics, Experiments, Settings in session mode), sign-out
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
div.work          context column (Story, Agents, Findings, Evaluations; 18rem; folds to a 2.25rem strip when the inspector opens and the pane would drop under 30rem, i.e. below 88rem) | pane (timeline, waterfall or evidence) | inspector (26rem, draggable)
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

Only `transform`, `opacity`, `background-color` and `box-shadow`; durations are tokens (`--t-fast` 120 ms, `--t-base` 180 ms, `--t-slow` 400 ms, `--t-land` 600 ms)
that are `0ms` under `prefers-reduced-motion: reduce`, and the keyframe rules are switched off there too.

- Inspector: slides in 12 px from the right over 180 ms (`@starting-style`, transform only).
- Rows: hover fades the background in 120 ms (the transition belongs to the hover state, so leaving and selecting are instant). While the keyboard drives the list (`data-nav="keyboard"`) there is no row transition at all.
- Live events: a row that arrives on the stream after the run loaded gets a 400 ms tint (`data-arrived`, at most 200 ids, pruned after 500 ms); the target of "Show it" / "Show evidence" gets a 600 ms inset ring (`data-landed`).
- The "Live" dot in the run header's live bar pulses (2 s) while, and only while, the event stream is connected.
- Nothing animates on first paint: `document.getAnimations().length === 0` after load on every route (`e2e/design.spec.ts`).

## Left out of this phase (deliberately)

- Top-bar search slot and command palette (D6); the runs-list search box stays on its page.
- Inspector tabs (D8, would be `role=tab` without panels), a collapsible icon rail, a light/dark toggle, a designed brand mark (the glyph is a placeholder).
- "Planned" rail items (Policies, Alerts) and the Compare tab: they would be dead links.
- A fade between views and an opacity fade on the inspector: axe (run right after the click in several locked specs) measures half-transparent text and fails contrast, so both were dropped for transform-only motion.
- Sticky table headers (cannot work inside the horizontally scrolling `.table-wrap`), a wide inspector mode.
- Settings pages are restyled by the same rules but are session-only; their screenshots come from `auth-e2e` into `docs/screenshots/phase-15/`.

## Rules for the next change

1. New colours are tokens with a contrast row in `tokens.test.ts`; no raw hex in components.
2. A new status is a `badge` with `data-tone`, a glyph and a word.
3. A new table uses the global table rules; a new page starts with `.page-head`.
4. A new animation names its duration token and has a `prefers-reduced-motion` rule; it never animates opacity of text that a spec measures right after an action.
5. Untrusted text (run names, agent ids, paths, commands, evaluation text) is rendered as a text node, never HTML.
