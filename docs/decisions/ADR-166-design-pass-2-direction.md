# ADR-166: Design pass 2 direction ("One Red Line")

Status: Accepted (design pass 2 step 4 landed the tokens, 2026-10-16): accepted by the assistant under the user's standing rule for the overnight run (user asleep); reversible, see alternatives. Written in step 1 as Proposed. Amends ADR-110
(fonts), ADR-111 (visual direction) and the Phase 21 decisions in `docs/plans/completed/phase-21-ui-shell-and-design-pass.md`
(D1 frame, D3 rail). Changes no data contract and no API. The number 166 is the one the pipeline reserved
(`docs/design/design-pass-2-pipeline.md`); ADR-159 to ADR-165 are left free for Phase 19b and 19c.
Date: 2026-10-16
Phase: design pass 2

## Context

After running the Phase 21/22 UI the user said it "looks very convoluted". The audit in `docs/design/pass-2/01-direction.md`
Part A counted why: a 224 px rail with two bordered switcher boxes and eight links plus a breadcrumb that repeats the rail; an
Overview with five stat blocks and two nine-column tables and no focal point; a runs list with three filtering systems and five
status hues; a failed run page with six bands of header before the first event, nine readouts, four typefaces, fourteen type
sizes and about twenty red elements; nine settings tabs with no shared layout; and stylesheets carrying 33 font sizes, 12 radii,
9 tracking values and six phase layers that win over each other by specificity.

ADR-111's rule "red means failure and nothing else" is obeyed by the code and still fails the reader, because it bounds the hue
and not the count. The "flight-deck instrument panel" metaphor (Phase 21) is the source of the condensed upper-case labels, the
readout strips and the key caps; ADR-111's serif failure sentence added a fourth voice.

## Decision

1. **Direction: "One Red Line".** A calm, editorial-technical surface (Linear / Stripe-docs restraint) whose single signature is
   the fault line. Red appears exactly twice in the product: the 2 px fault line (with its label, and a 16 px miniature dash before
   a failure sentence in lists) and the status word Failed / Timed out / Blocked. No red fills, tints, headlines, counts or bars.
   Dials: variance 2, motion 2, density 5 (data dense, chrome airy).
2. **Navigation: one top bar, five items.** The left rail, the switcher boxes and the rail foot are removed. The top bar holds the
   brand, Runs, Review, Analytics, Experiments, Settings, a scope control ("workspace / project"), a search button (`/`) and a
   person menu (email, sign out). Overview is merged into Runs; Approvals and Security are two tabs of one "Review" page (routes
   unchanged); Alerts moves under Settings. `nav[aria-label="Primary"]` keeps its name.
3. **Typefaces: Plex Sans and Plex Mono only.** Plex Sans Condensed and Plex Serif are removed from the app (amends ADR-110 and
   ADR-111 "Plex Serif"). Seven type steps (32/24/18/14/14/13/12 with fixed px line heights), weights 400 and 600, no
   letter-spacing, no upper-case labels, sentence-case column heads.
4. **Palette: eleven roles.** canvas, raised, hairline, hairline-strong, ink, ink-2, accent, accent-wash, fail, caution, mark
   (values in `01-direction.md` Part B.4, OKLCH with hex). Removed: `--surface-2`, `--ok`, `--ok-bg`, `--info`, `--fail-bg`,
   `--wait-bg`, the accent fill, and the ADR-111 aliases once nothing reads them. Success is a check glyph in ink-2; running is an
   accent dot; waiting, retry, expired and destructive are caution text.
5. **Status grammar kept, fills dropped.** Glyph plus word (ADR-111), severity by weight (ADR-131), policy outcomes by glyph and
   weight (ADR-141 UI) all stay. The one filled chip for a failed run (ADR-111) is replaced by the red word.
6. **Run page.** Three header lines (title line with status and age; the headline sentence in ink at 24 px; five figures with the
   rest under Details), five tabs (Timeline, Waterfall, Evidence, Findings, Evaluations), a Steps column, the inspector. The
   context column's Findings, Health, Agents and Evaluations sections are removed from the column; health dimensions are not shown
   on the run page at all (the findings say what matters; the numbers stay in the API).
7. **Motion.** Keep the fault-line draw, the inspector slide and the jump outline; remove the headline fade and the live-row rise.
   The reduced-motion contract and `tests/motion-contract.test.ts` are unchanged.
8. **One settings pattern** for all nine tabs (grouped left sub-nav; title, one-sentence lede, hairline sections with a label column
   and a control column, actions at the end of a section; no cards). Quotas' cards and progress bars become one row per quota.
9. **Shapes.** Radii 4 px on controls and 0 elsewhere; one hairline; no shadows; no tone bars; one notice style; one tab component;
   one table style. Illustrations only on the first-run guide and not-found.

## Alternatives considered

- **Keep the instrument-panel metaphor and tidy it** (fewer readouts, fewer colours, same faces). Rejected: the audit traces the
  extra typefaces, caps, strips and key caps to the metaphor itself; tidying leaves the cause and the next phase re-adds a strip.
- **A lighter rail (11-12 rem, nav only)** instead of a top bar. Rejected for the run page: the rail costs width the timeline needs
  whenever the inspector is open (today the pane drops under 480 px at 1408 px, `docs/screenshots/phase-21/README.md`), and with
  five items the bar costs no height the header stack does not already pay. Recorded because a rail is the easier revert: the
  `AppShell` keeps one nav component either way.
- **Keep Overview as a thin "today" page.** Rejected: its figures are one sentence; its two tables are the runs list.
- **Fold Approvals, Security and Alerts into one "Attention" area** (the pipeline's question). Partly adopted: Approvals and
  Security are queues a person must act on and share a list shape; Alerts is configuration with a log and belongs with API keys and
  quotas. Keeping Security separate from Approvals was the other option; rejected because both are "findings about agents a
  reviewer reads", both are workspace-scoped and both have a three-state tab bar today.
- **A new type pairing** (Geist Sans + Geist Mono, or Source Sans 3 + Source Code Pro, both OFL). Rejected: the complaint is
  quantity, not the face; a third family swap in two phases is its own noise. Plex stays, reduced to two families.
- **Keep the serif for the one failure sentence** (ADR-111's argument: "reserved for the one sentence that matters"). Rejected:
  with the fault line as the only red mark, the sentence no longer needs a second voice to be found; the serif was the
  fourth typeface on screen.
- **Keep green for success.** Rejected: on a list where most rows succeed, green is the most frequent colour and so the least
  informative; the check glyph in ink-2 carries it.
- **Keep the filled red chip for failed runs** (scans well in a list). Rejected: with no other colour in the list, a red word scans
  as well; the chip is a fill, and fills are what the eye counts.

## Which ADR-111 rules are kept or amended

| ADR-111 rule | Status |
| --- | --- |
| Fault line marks the first error; rows above are quiet | Kept; "rows below carry a faint tint" amended to no tint |
| Palette hex values | Kept for accent, fail, caution, mark; canvas and ink re-specified in OKLCH (`01-direction.md` B.4); surface collapses into canvas |
| Old names stay as aliases | Amended: aliases are deleted once `grep` finds no reader (step 4 lists every reader count; step 5 deletes) |
| Red is for failure only; `tokens-use.test.ts` guards it | Kept and tightened: red only for the fault line and the failed word; the allow-list shrinks |
| Destructive buttons and removed diff lines in amber | Kept |
| Green stays but is quiet | Amended: green removed |
| Status is glyph plus word, no outline, no upper case | Kept |
| Only a failure-tone status is a filled chip | Amended: no filled chip |
| Type scale: Sans 13 px, Condensed headings, Mono numbers, Serif headline | Amended: Sans and Mono only; seven steps |
| Plex Serif vendored and loaded | Amended: removed from the app (files deleted; OFL note stays for Sans and Mono) |
| Brand mark option A | Kept |
| Motion language 120-280 ms, ease-out, transform and opacity, zero under reduce | Kept; two animations removed (headline fade, live-row rise) |
| Phase 13 severity by shield glyph and weight, never red | Kept |
| Phase 14 outcomes by glyph and weight; denial is ink; waiting amber | Kept |

## Token decisions (step 4, 2026-10-16)

Decided by the assistant under the same standing rule; each is reversible and the alternative is named.

1. **Source and generator.** `apps/web/design-tokens/tokens.json` (Style Dictionary naming, `value` or `light`/`dark` per leaf,
   `{path}` references, primitives and semantic layers) is the only source of colour, type, space, radius and motion values.
   `apps/web/scripts/tokens-build.mjs` (stdlib node, no dependency) generates `src/app/tokens.generated.css` and
   `src/app/tokens.aliases.css`; `tokens:build`, `tokens:check` (also in `scripts/quality.sh`) and `tokens:report` are pnpm scripts.
   Alternative rejected: the real Style Dictionary package (a dependency and a config for one output format).
2. **Theme mechanism unchanged.** The app has no theme switch: light is the default and dark follows `prefers-color-scheme`. The
   generated file mirrors that exactly (no `[data-theme]` selector was added; the direction bans a theme toggle).
3. **Hex is rendered, OKLCH is authored.** The direction's OKLCH roles are converted to sRGB hex by `tokens-lib.mjs` and only the
   hex is emitted, so the contrast numbers are computed on exactly what the browser paints. The direction's "approximately" hexes
   were rough: converting its own OKLCH gives dark canvas `#101419` (the direction said about `#15191e`, which is L 21 %, not 19 %),
   light ink `#1d2229` (not `#22272e`), light ink-2 `#5c646f` (not `#626c78`), light canvas `#f6f8fa` (not `#f7f8fa`). OKLCH is the
   spec, so the converted hex wins; no hue was changed. Kept hex roles (accent, accent-wash, fail, caution, mark, both themes)
   are unchanged ADR-111 values. Alternative rejected: also emit `oklch()` inside `@supports`, which paints a value the table
   did not measure.
4. **One role added: `color.border.control`.** The direction's hairline-strong (L 82 %) is 1.6:1 on the canvas; WCAG 1.4.11 asks
   3:1 for the boundary of a field. A decorative table rule and a field boundary are different jobs, so `border.strong` stays the
   direction's value (decorative floor 1.3:1 in the test, never the only cue) and `border.control` (3.4:1 light, 4.2:1 dark) is the
   field and button boundary. Step 5 moves those readers to it. Alternative rejected: darken hairline-strong to 3:1 everywhere
   (heavy rules on every table, against "one hairline weight").
5. **Aliases, not renames.** Every pre-pass-2 custom property keeps its name and resolves to a new token when the mapping is
   faithful (`--bg`, `--ink`, `--muted`, `--accent`, `--fail`, `--wait`, `--mark`, `--sel`, `--border*`, `--fs-*`, `--sans`,
   `--mono`, `--ease-out`). `--surface` maps to `color.bg.raised` for now (screens still draw cards); step 5 collapses it into
   canvas. `--fg-2` maps to ink (no role between ink and ink-2). Removed roles keep a value under `color.legacy.*` (`surface-2`,
   `ok`, `ok-bg`, `wait-bg`, `fault-bg`) so no screen breaks; they are listed as "deleted in step 5" with their reader counts in
   `docs/design/pass-2/04-colour-type-tokens.md`. Nothing was deleted in step 4.
6. **Fonts.** Plex Sans and Plex Mono are the only families in the token set. Condensed and Serif stay declared in `layout.tsx`
   (screens still render in them until step 5) but are `preload: false`, so a page fetches them only if it uses them. `next/font`
   preloads per family, not per file, so Sans Medium (500) remains in the preload set until step 5 removes the 500 weight; this
   amends ADR-110's "all seven are preloaded". Step 5 deletes the two faces, their files and `--cond`/`--serif`.
7. **The red rule is a test.** `tests/tokens-colour-rules.test.ts` fails if any colour token other than the fault tokens (and the
   legacy red tint until step 5) resolves to a red-ish hue (OKLCH hue 340 to 40 with chroma), and counts the red-ish literals left
   in stylesheets. `tests/tokens-use.test.ts` keeps its selector allow-list (ADR-111: "red means failure and nothing else"), now
   police both the old and the new token names, and the list's length is a ratchet. The direction deliberately tightens ADR-111
   from "failure" to "the fault line and the failed word"; today's 32 allowed failure selectors (tinted rows, red headline, chip,
   counts) violate the tightened rule and stay on the list as recorded debt: step 5 removes them and the list shrinks to the fault
   line and the failed word. No test was deleted for this.
8. **Raw colour ratchet.** Literal colours outside the token files are counted per stylesheet and may only shrink: 12 in
   `globals.css` (syntax highlighting, var fallbacks, a mask stop) and 35 in `waterfall.css` (the ADR-071 data palette). The
   waterfall palette keeps its values in step 4 (moving and desaturating it belongs with the waterfall rebuild in step 5).
9. **Motion tokens added, `--t-*` kept.** `motion.duration.*` (including the 280 ms fault draw and 160 ms inspector slide) and the
   easing are generated and zero under reduced motion; the legacy `--t-*` durations stay in `globals.css` because
   `tests/motion-contract.test.ts` reads them there.
10. **Tests that changed value, not rule.** `tokens.test.ts`, `tokens-contrast.test.ts`, `policy-css.test.ts` and
    `security-severity.test.tsx` parsed `globals.css`'s `:root`; the values moved, so they now read the same properties through
    `tests/tokenCss.ts` (generated tokens, aliases, then globals, as the browser loads them). Every assertion and threshold is
    unchanged except `tokens-contrast.test.ts` "matches the plan's base colours", which pinned the old canvas and surface
    (`#eef1f4/#ffffff`, `#12161b/#1a2027`) and now pins the new ones (`#f6f8fa/#ffffff`, `#101419/#1b2025`).

## Step 5a decisions (shell, sign-in pages, Runs home; 2026-10-16)

Decided by the assistant under the same standing rule where `02-ia-and-flows.md` and `03-screens-and-wireframes.md` left a choice or
disagreed with the build brief; each is reversible and the alternative is named.

1. **Below 900 px: the Menu button holds the five links; the scope control stays in the bar.** `03` draws the five links in the bar at
   768 and puts the scope on the title line at 375; `02` and the brief say "below 900 the links collapse into a Menu button" and "the
   mobile top region must not stack bands". Followed `02` and the brief: at 375 the bar is Menu, the brand mark, the scope (truncating),
   search and the person icon, one 44 px band; the brand name returns at 600 px. Alternative: scope on its own line (a second band).
2. **Search is one icon button at every width.** The field takes the bar's place while open (the `/` key and the button), so the bar
   stays calm at 1024 where five links, the scope and a 22 rem field do not fit. The drawn `/` key cap is gone (tooltip and
   `aria-keyshortcuts` name it). Alternative: an always-open field narrowed to 12 rem.
3. **Review leads to Approvals when the person may read it, else Security findings; both routes are unchanged.** The two queues become
   tabs in 5c. Until then Security findings (and Alerts) are TEMPORARY entries in the person menu so nothing is orphaned (KI-279).
4. **The person menu exists in fixture mode as "Account"** (the temporary links only, no email, no sign-out), and Settings stays
   session-only as before. Alternative: hide the menu in fixture mode (Alerts and Security findings would be unreachable there).
5. **Needs attention is the first facet, not a page button.** It is the legacy multi-status parameter (`?status=FAILED,TIMED_OUT,BLOCKED`),
   unchanged, and shows as one chip (`status: failed, timed out, blocked`). The facets are a disclosure that is closed below 900 px
   (`03` says open at 600 to 899: a wall of chips above the list at 768 is the convolution the pass removes), so on a phone and a tablet
   the preset folds away with them.
6. **One filter field, no Search button.** Enter commits (the phone keyboard says "Search"); a `?` button opens the grammar and the
   fields the API allows. URL parameters are unchanged (`q`, `status`, `agent`, `range`); an agent text that is not a valid slug is a
   chip that says so. "Clear filters" stays as the one reset (the zero-result state has no second one).
7. **The 7-day sentence** reads `Last 7 days: 12 runs, 73% succeeded, 4 failed, $22.16 spent, 3 in progress.` from the same aggregate
   the Overview used (`/v1/analytics/summary`); zero is said in words ("none failed"), and below 600 px only the runs and failures
   clauses remain. "In progress" (queued, running or waiting) replaces `03`'s "running now" because that is what the aggregate counts.
   The sentence is silent while loading, on error and with no runs (the list speaks).
8. **Row anatomy:** status glyph and word (check, cross, circle-slash, dot, double bar, dash, dotted circle), the run name 600 only on
   failed rows, the failure line with the 16 x 2 px dash, findings by weight (critical bold, the rest regular). Duration drops below
   1280 px, Agent and Duration move under the name below 900 px, rows are blocks below 600 px (CSS only, same markup).
9. **One notice style everywhere it is shared:** the tone-bar and fill rule for `.state`, `.notice`, `.withheld`, `.search-problem`,
   `.search-notice` and `.evaluation[data-state="failed"]` is now "no bar, no fill", with a small "i" mark on notices. This reaches
   the screens of later batches early on purpose (one rule, not five). Routine empty and error states drop their drawing
   (`Empty motif="none"`, `ErrorState` keeps the tape only for not-found); the first-run guide and not-found keep theirs.
10. **Shared primitives moved to tokens now:** every `button` boundary reads `--color-border-control` (KI-274, partly), the filled
    button and `.badge` use the new grammar, document-page `h1` is Plex Sans 24/30 (the Condensed h1 stays on the run page until 5b),
    `select` is `max-width: 100%`. Tokens added: `layout.topbar`, `layout.control`, `layout.touch`; `--top-h` is now an alias of
    `layout.topbar`; `--rail-w` is deleted.
11. **Copy lives in typed modules** (`apps/web/src/copy/shell.ts`, `auth.ts`, `runs.ts`, `states.ts`) beside the spec
    `docs/design/pass-2/05-microcopy-and-states.md`; later batches add their own module and section. The app had no strings
    pattern before (copy was inline), so this is new.
12. **Red allow-list: 32 to 26 selectors** (`tests/tokens-use.test.ts`, ratchet in `tests/tokens-colour-rules.test.ts`): removed the
    filled status chip, the red error state, the red search problem and caret, the failed evaluation panel, the red error count and
    the red failure sentence; added the miniature dash. Raw colour literals are unchanged (12 in `globals.css`, 35 in
    `waterfall.css`: no rebuilt screen read them).

## Step 5b decisions (run page and Compare; 2026-10-16)

Decided by the assistant under the same standing rule where `03-screens-and-wireframes.md` left a choice or disagreed with the
build brief; each is reversible and the alternative is named.

1. **Five readouts: Duration, Cost, Tokens, Retries, Errors.** From the audit's nine (duration, cost, tokens, model calls, tool
   calls, retries, errors, files changed, agents): the two things a failed run is judged by (retries, errors) and the three a
   finished run is judged by (time, money, volume). Model calls, tool calls, files changed and agents move into Details with the
   seven identity items (id, agent, model, ordering, started). No readout is coloured (the red count and the amber retries are
   gone). Below 600 px tokens and then retries leave the line and join Details, as `03` says ("the figures lose tokens first").
   Alternative: keep model and tool calls on the line (`01` says five, `03`'s wireframe shows five).
2. **Details is a button with `aria-expanded` and a region, not a `<details>`.** The toggle sits at the right end of the figures
   line and the content opens under it; a native disclosure would have moved the toggle when it opened. The Steps strip below
   1100 px is a native `<details>` (its summary is the whole row).
3. **The headline is a sentence with a full stop**, composed in `runHeadline.ts` from the same facts as before (Phase 22,
   SUMMARY_VERSION 6): `Failed at step 8 after 6.5 s: shell command completed, error.` replaces `Failed at step 8 (+6.5 s): ...`
   (the parenthesis was a log string, not a sentence); running, waiting, success, recovered and cancelled follow the table in
   `05-microcopy-and-states.md` section 5.2. The second line (retries, time and cost after the first error, total) stays, in
   small ink-2 type, because the cost of the failure is the Phase 22 acceptance; below 600 px it moves into Details so the line
   stays in the first screen. Alternative: drop the second line (rejected: it is the "what did the failure cost" answer).
4. **Go to first error is the one outline button of the header** (accent border, no fill, no drawn key cap; `e` stays, named in
   the tooltip and `aria-keyshortcuts`). Its accessible name changed from "Show it": e2e and unit specs follow.
5. **One tab component, `Tabs`**: `role="tablist"` of buttons with roving tabindex, automatic activation on arrow keys (Left,
   Right, Home, End), `aria-selected`, `aria-controls` the panel; counts are decoration (`aria-hidden`). The run page's panel is
   the pane (`role="tabpanel"`). 5c and 5d use the same component for Review, Alerts and Settings. `?view=` keeps working and
   gains `findings` and `evaluations`; `?finding=` opens the Findings tab. The old radio tabs (`.seg-item`) and `ViewSwitch` are
   deleted.
6. **Findings and Evaluations are tabs; the Context column and its toggle are gone.** The left column is Steps (the coding story)
   and, for a run with more than one agent, Agents (the filter): absent for a run with neither. Health dimensions are not shown
   anywhere (ADR decision 6, API untouched). The Findings tab holds the Security group above the reliability findings, as one
   hairline list each. "Show evidence" from Findings or Evaluations switches to the timeline so the row is selected and marked;
   the deep link `?finding=` stays on Findings and opens the inspector there. Alternative: stay on the tab (rejected: the landing
   outline and the selection live on the timeline).
7. **Layout breakpoints.** Steps is a column from 1100 px (and 576 px of height) and a one-row strip above the pane below that;
   the inspector is a column from 1280 px and an overlay from the right below it (a full-screen sheet below 600 px); the page is a
   scrolling document below 832 px or 576 px of height, as before (the fixed-height `.run-detail` and the page padding 0 are kept
   and tested). Timeline rows are 32 px (36 before) and 48 px on a phone (two lines: offset and type, then the detail).
8. **The fault line**: 2 px, the label ("First error, step 8") in fault-coloured text cut into the line by the canvas colour, over the
   empty end of the detail column (left of duration and status) so it does not collide with the row's own marker. On a failed run
   page the red elements are exactly two: the line with its label and the status word.
   The jump outline is accent for the first error too (it was red), the replay scrub bar's first-error tick is ink, the waterfall's
   error bars are ink hatching and timeouts caution hatching, severity "Critical" and failed tests are ink 600 (`.badge[data-tone=bad]`
   is no longer red; only a run's status badge is, by `data-status`).
9. **Rows**: no tinted rows, no class column, no left bars. An error is the word "✕ error" in ink 600 in the status column, a retry
   a caution "↻ retry" token, the selected row accent wash with a 2 px accent edge. Collapse groups moved into the Filter popover
   (`01` Part C 18). Keyboard shortcuts are unchanged; the key hints are one plain sentence in the foot from 900 px.
10. **Waterfall colours** are `color.data.*` tokens (six lane hues and eight agent hues, OKLCH, chroma 0.02 to 0.09, none
    red-ish: the magenta agent colour became an olive; bar fills are at least 3:1 and chip text at least 4.5:1 in both themes,
    computed in `tokens.json`'s `$contrast`). Syntax colours are `color.syntax.*`. No stylesheet carries a colour literal any more.
11. **Fonts**: Condensed, Serif and Sans Medium files, `--cond`, `--serif` and every 500 weight are deleted (11 remaining readers in
    `alerts.css`, `policy.css`, `security.css`, `settings.css` were migrated to Sans/600; those screens keep their layouts until 5c
    and 5d). Plex Mono has no SemiBold file here: its Medium file is registered as weight 600 so a mono word at 600 is real glyphs,
    not a synthetic bold. Four files are preloaded (about 100 KB).
12. **Aliases deleted** (zero readers after migration): `--surface-2`, `--fg`, `--fg-2`, `--accent-bg`, `--info`, `--bad`,
    `--fail-bg`, `--bad-bg`, `--warn`, `--wait-bg`, `--warn-bg`, `--ok`, `--ok-bg`, `--cond`, `--serif`, `--mark` (its readers use `--color-brand-mark`), and `color.legacy.*` with them.
    Kept (still read by 5c and 5d screens): `--bg`, `--surface`, `--border`, `--border-strong`, `--ink`, `--muted`, `--accent`,
    `--sel`, `--fail`, `--wait`, `--fs-*`, `--ease-out`, `--top-h` (reader counts after 5b: --muted 33, --accent 24, --surface 19, --ink 19, --wait 17, --border 37, --border-strong 7, --fail 8, --bg 4, --sel 3, --fs-title 7, --fs-body 7, --ease-out 5, --top-h 4, --fs-headline 1, --fs-small 1).
13. **Ratchets**: red allow-list 26 to 7 selectors (the status word, the fault line and label, the waterfall line and flag, the
    miniature dash, `:root`, and two TODOs: the Analytics error column for 5c, `.field-error` for 5d); raw colour literals 47 to 0.
14. **Compare**: the title line "Runs / Compare" (h1 `Compare`), the divergence sentence at the h2 step in ink (600), one mono
    delta line, two cells (A and B: the run name as the link, status, then id, agent and age small, a hairline between), the
    aligned steps with a 2 px ink rule at the divergence, changed rows 600 on both sides, no tint, no coloured bars; the
    "First outcome difference" flag is ink 600. Below 832 px the cells stack; below 640 px a step is a block with `A` and `B`
    prefixes and the toolbar is a sticky scrolling row of 44 px buttons. The Steps h2 is visually hidden. The run names come from
    `useRun` for each side (two small extra reads; the comparison API has no names).

## Step 5c decisions (Review, Alerts under Settings, the one Settings pattern; 2026-10-16)

Decided by the assistant under the same standing rule where `03-screens-and-wireframes.md` left a choice or disagreed with the
build brief; each is reversible and the alternative is named.

1. **Review is a route group, not a new route.** `app/w/[workspace]/(review)/layout.tsx` holds the shell and `ReviewFrame` (the h1
   "Review", the waiting count, the shared `Tabs`); `approvals/` and `security/` move under the group and keep their addresses
   (`/w/{ws}/approvals`, `?state=`, `?approval=`, `/w/{ws}/security`, `?severity=` and the rest). Choosing a tab is a `router.push` to
   the other route; the tab list lives in the shared layout so it keeps focus while the page below changes. A tab the role may not
   read is absent; a role that reads neither gets no tab list and the page's own "Not available for your role". The top-bar link
   still goes to Approvals when readable, else Security findings. Alternative: one client page switching a `?tab=` parameter
   (rejected: it changes the two addresses people already have saved and the Approvals deep links).
2. **Counts.** The only count in the title line is "N waiting" (pending approvals; the same request the bar polls). The Approvals tab
   carries no count (the number would appear three times: bar, title, tab) and the Pending chip carries it, as `03` draws; the
   chip reads the cache and never makes a request of its own (`useCachedPendingCount`), so the Decided tab still does not poll. The
   Security findings tab has no count: a number needs a request of its own and the list says how many are loaded.
3. **Approvals list is five columns** (Request, Risk, State, Requested, Time left). Run link, project and policy are in the request
   panel (they were in both). The panel is a column from 1024 px with a hairline on its left (no card) and replaces the list below
   that width (an overlay would leave the covered list in the tab order); its Close button is the "Back". Approve is the page's one
   filled button, Deny is outline with caution text (a denial is not a failure, ADR-141), and the two-step confirm sentences and
   buttons ("Confirm approve") are kept as they were: the wording is tied to the API's semantics and to e2e specs. On a phone each
   request is a wrapping block (capability, resource, then risk, state, age and time left on one line) and the decide buttons are
   44 px and full width.
4. **Security findings**: the facets and list are the Runs list's classes (not forked); the Project column is dropped (the scope
   control and the Project facet say it). Severity stays glyph and word (shield filled and 600 for Critical, outline caution for
   High, ink-2 below). Order: the API's runs-newest-first order is kept, and within one run the most severe finding comes first
   (`mostSevereFirst`), so the order reads without colour; it never moves a finding across runs, so a "Load more" page boundary
   cannot reorder what is on screen. Alternative: sort the whole list by severity (rejected: pagination would lie).
5. **One Settings page.** `SettingsFrame` (in `settings/layout.tsx`) renders the shared `TitleLine` ("Settings /" link, then the
   tab's name as the one h1), the grouped section list and one content column (60 rem; prose 72 ch). Alternative: h1 "Settings"
   with the tab as an h2 (`03` section 7): rejected because the title line is already the pattern of the run page and Compare and
   gives each tab a real document title. Sections are h2 (18/24), rows h3; a tab never has a fourth level.
6. **Sections are stacked, not label-and-control columns.** `03` describes a 192 px label column beside a control column; the tabs
   hold tables (members, keys, audit, prices, rules) that need the full 960 px, so each section is a hairline band with its heading,
   one sentence, the work and (last) the one filled button. The label-over-field rule applies to every form. Alternative: the
   two-column form for short forms only (rejected: two layouts again).
7. **Grouped list and its fold.** People: Members, Sessions; Access: API keys, Policies, Capture and detectors; Limits: Quotas,
   Retention, Pricing; Notify: Alerts; Record: Audit log. The Security tab is renamed "Capture and detectors" (route
   `/settings/security` unchanged) so it is never confused with Review's Security findings. Below 900 px one button, "Section:
   Quotas", opens the same list (one DOM list, CSS decides); `03` put the disclosure on the title line, but the title line already
   names the tab, so the button is the first row under it. The top bar's Settings link lands on the first tab the role may read
   (`firstSettingsHref`), not always Members.
8. **Tables on a phone become blocks** with the column's name before each value. The names are copied from the table's own
   `thead` to `data-label` by a `MutationObserver` in the frame (attributes only), so no table repeats its heads by hand and a new
   column is labelled for free; below 600 px `.table-wrap` tables are `display: block`. Quotas, Approvals, Security findings and the
   policy tables have their own block layouts.
9. **Destructive actions are two steps in place** (`TwoStep`): Remove a member, Revoke an invitation and Revoke an API key (new:
   one press before), Sign out everywhere else, Remove an allowed host (one step: it only edits the draft until Save). Alert rule
   and destination Delete ("Delete NAME" then "Keep"), destination rotate and a retention shortening ("Save and shorten") already
   had a second step and keep it. Ending one session is one step (it signs one device out and the list says so). Destructive
   buttons are caution text with no fill; Escape or Cancel goes back and returns focus.
10. **Errors are ink.** A field error is one sentence under the field with the word "Error:" before it (a 600 weight, never red);
    a failed write is the shared notice line with the API's sentence and request id. The red allow-list is 7 to 6 selectors
    (`.field-error` is gone; the Analytics error column is the last non-fault red, 5d).
11. **Quotas** are hairline rows (name and default, usage in mono, a 4 px bar, glyph and word, the override field in the row). Near
    limit (from 80 %) is caution text and a caution bar; at the limit is ink 600 and an ink bar (it was caution: `03` says a limit
    reached is not a fault and not a warning, it is a state). The input keeps its accessible name (the quota's name) so the row's
    visible label is its label.
12. **Read-only roles get one sentence**, not a disabled form: "Your role can read this but not change it. Owners and admins can."
    (Members, API keys, Pricing; Quotas, Retention and Policies keep their own tested sentences).
13. **Alerts**: `/w/{ws}/settings/alerts?tab=rules|destinations|history`; `/w/{ws}/alerts` is a redirect page that keeps every query
    parameter. The sub-tabs are the shared `Tabs` (`tabs-ruled`) with the URL's `?tab=` as before; the signing-secret panel, its
    leave guard, send-test and the previews are unchanged. The person menu loses the temporary Security findings and Alerts entries
    (KI-279 closed).
14. **Empty states** on every 5c screen are one headline and one sentence with no drawing (`Empty motif="none"`).
15. **Aliases deleted**: `--bg`, `--surface` (it collapses into canvas), `--border-strong`, `--ink`, `--muted`, `--accent`, `--sel`,
    `--fail`, `--wait`, `--fs-title`, `--fs-body`, `--ease-out`, `--top-h`, `--sans`. Kept for 5d (read only by `analytics.css`):
    `--border` (1), `--mono` (2), `--fs-headline` (1), `--fs-small` (1). The contrast tests are written in colour roles
    (`roles()` in `tests/tokenCss.ts`) that read the real token names; the pairs and thresholds are unchanged.

## Consequences

- Steps 2 and 3 use this ADR and `01-direction.md` as the rubric; step 4 implements the tokens and type; step 5 the screens.
- Tests that assert copy or structure change in known places and are listed with reasons in step 5's plan: the "Overview" and
  rail links in `e2e/*` and `tests/*` (4 files name the Primary nav, 3 click Overview or Experiments by name), the settings tab
  labels (7 files), `tokens.test.ts` and `tokens-contrast.test.ts` (the token set), `tokens-use.test.ts` (the red allow-list),
  `security-severity.test.tsx` (pairs), the `.seg-item` radio tabs in the run-page specs.
- The ids, model and ordering of a run move from the header into Details; nothing is lost, one click further.
- Health dimensions leave the run page UI. The API, the findings and `rebuild-findings` are untouched (INV-2); a later phase may
  surface them on Analytics.
- The `docs/design/ui-direction.md` "Frame" and "Run page" sections are rewritten when step 5 lands; until then that file
  describes the shipped UI and this ADR the target.
- Font payload drops from 191.5 KB to about 100 KB (five files).
