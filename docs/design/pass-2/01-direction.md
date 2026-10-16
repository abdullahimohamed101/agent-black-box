# Design pass 2, step 1: direction (taste before pixels)

Status: written 2026-10-16 from the pipeline in `docs/design/design-pass-2-pipeline.md` (step 1) and Prompt 4 in
`docs/design/design-pass-2-prompts.md`. Decisions are recorded in `docs/decisions/ADR-166-design-pass-2-direction.md`.
The user's problem statement: after running the Phase 21/22 UI, it "looks very convoluted" aesthetically.

Evidence vocabulary: every number in Part A was counted on a file or screenshot named beside it. Where a count came from the
running app (`http://localhost:3000`, workspace `local`, 1440 x 900, dark scheme) it says so. Nothing here was measured by running
tests or builds.

---

## Part A: the audit (why it feels convoluted)

### A.0 The shell, which every screen pays for

Counted on `apps/web/src/components/AppShell.tsx`, `apps/web/src/app/globals.css` lines 180-478, the running app at 1440 (Overview,
Runs, Approvals, Security, Settings/Members) and `docs/screenshots/phase-22/step-7/runs-list-1440-light.png`.

| What | Count | Where |
| --- | --- | --- |
| Chrome before any content, desktop | rail 224 px wide (14 rem of 1440 = 15.6 %) plus a 44 px top bar | `--rail-w: 14rem`, `--top-h: 2.75rem` |
| Bordered regions in the rail | 5: brand cell, workspace box, project box, nav list, rail foot | `.brand`, `.switcher`/`.switcher-static` (bordered, `--surface-2` fill), `.nav`, `.rail-foot` |
| Upper-case tracked micro-labels in the shell | 3 on every page: WORKSPACE, PROJECT, RECORDING / IDLE | `.switcher::before` (0.66 rem, 0.1 em tracking), `.live-indicator` (0.6875 rem, 0.1 em) |
| Primary nav items (owner, session mode) | 8: Overview, Runs, Security, Approvals, Alerts, Analytics, Experiments, Settings | `AppShell.tsx` lines 56-91; seen in the running app |
| Second-level tab bars, each with its own CSS | 4 styles: `.subnav` (Settings, 8 tabs today, 9 with Retention on the 19b branch), `.al-tabs` (Alerts, 3), `.apr-tabs` (Approvals, 3), `.seg-item` (run views, 3, upper-case tracked) | `globals.css` 1575-1597 and 862-900, `alerts.css` 5-30, `policy.css` 40-65, `settings.css` |
| "Where am I" shown twice | the rail's current item (`aria-current`, accent left bar, `--sel` fill) and the breadcrumb in the top bar | `.nav a[aria-current]`, `Crumbs.tsx` |
| Top-bar elements | breadcrumb, a 22 rem mono search field with a `/` key cap, the recording indicator (dot + word) | `.topbar`, `.topbar-search` (`flex: 0 1 22rem`), `.live-indicator` |
| Chrome stacked above content on a phone (375) | 4 bands: top bar, brand + two switcher boxes, nav row, then the page; a failed run's headline starts about 230 px down an 812 px screen | `docs/screenshots/phase-22/step-5/context-open-375-light.png`; `globals.css` 411-478 |

### A.1 Overview (`/w/{ws}/projects/all`, `Dashboard.tsx`)

Counted on `docs/screenshots/phase-21/after/dashboard-1440-light.png` (12 runs) and the running app (`local`, 50 runs, dark).
The Phase 21 screenshot predates ADR-111 (its badges are outlined pills); the running app shows the glyph-and-word badges. Counts
below are from the running app where they differ.

| Measure | Count | Evidence |
| --- | --- | --- |
| Distinct bordered or filled regions on screen | 13: 5 rail regions, top bar, search field, the stats strip (one bordered band cut into 5 cells by hairlines), 2 tables with bordered rows, the brand cell, plus the page itself | `.stats` (border top and bottom), `.stat` (border-right) |
| Simultaneous stat blocks | 5 (Success rate, Failures, Cost, Median duration, Running agents), each a label, a 1.05 rem mono value and a hint line = 15 text items in one band | `Dashboard.tsx` lines 72-104 |
| Tables | 2 ("Recent failures" and "Recent runs"), 9 columns each (Status, Run, Agent, Started, Duration, Cost, Events, Errors, Findings), 18 upper-case column heads in total; the failed runs appear in both tables | `RunsTable.tsx` via `Dashboard.tsx` 106-118 |
| Distinct colours in use (hues, not tokens) | 7: ink, muted, accent blue (every run name, two links, current nav), failure red (status words, error counts, findings), green (success words), amber (awaiting approval), the orange recording dot; plus 3 greys of surface, surface-2 and bg | `globals.css` tokens 4-47; `.runs td.num strong { color: var(--bad) }` |
| Distinct type sizes on screen | 12: 0.66, 0.6875, 0.75, 0.8, 0.85, 0.9375, 0.95, 1.05, 1.375 rem plus 13 px body plus the mono 0.8 rem cells and the 0.75 rem badge; 3 families (Sans, Condensed, Mono); weights 400, 500, 600 | `globals.css` 94-120, 210-217, 272-293, 480-528, 612-700, 1932-1960 |
| Buttons and links above the fold | 2 text links in the lede and foot, 8 run-name links, 8 nav links, 2 switchers, sign out, the search field: 22 interactive targets | screenshot |
| Competing focal points | the h1 "Dashboard" (1.375 rem), five large mono numbers, five red "Failed" words (the same three runs twice), every run name in accent blue, the orange RECORDING dot, the blue "Cost and reliability analytics →" link | screenshot |

Reading: the page has no first thing to look at. The eye lands on the five numbers because they are the largest type, but the
numbers answer a question nobody arrived with ("median duration?"); the failures that would answer "is anything wrong" are
below, in the same red as the counts beside them, and then again in the second table.

### A.2 Runs list (`/w/{ws}/projects/all/runs`, `RunsList.tsx`)

Counted on `docs/screenshots/phase-22/step-7/runs-list-1440-light.png` (13 runs) and the running app (50 runs).

| Measure | Count | Evidence |
| --- | --- | --- |
| Ways to filter the same list, offered at once | 3: the query grammar field (`status:failed tool:github ...`), a 9-segment status toggle row with an agent field and a started select, and a left facet column (Status, Started, Errors and retries); status is offered in all three | screenshot; `runs-list.css` 19-97; `globals.css` 1967-2043 |
| Interactive controls before the first row | 17: 2 page actions (Needs attention, Compare), the search field, Search button, Search help, 9 status segments, agent field, started select; plus 12 facet buttons beside the table | screenshot |
| Rows of control chrome above the data | 3 (page head with actions, search row, filter row) at 1440; the first run row starts 190 px below the top bar | screenshot |
| Status hues in one list | 5: green Success (7 rows), red Failed and Blocked (4 rows, filled chips), amber Awaiting approval, accent-blue Running, plus red, amber and blue findings counts ("2 · critical", "1 · warning", "1 · info") | screenshot; `StatusBadge`, `.badge[data-status=FAILED]` filled |
| Red elements | 11: 4 filled chips, 4 red failure sentences ("Failed at step 7"), 1 red findings count, 2 red "Blocked" | `runs-list.css` 125-133 |
| Type sizes | 11 (page h1 1.375, lede 0.8, toggle 0.75, legend 0.66 upper, facet h3 0.75, facet 0.8, th 0.66 upper, td 0.8, run name 0.8, fail line 0.78, detail 0.74 mono) | `globals.css`, `runs-list.css` |
| Borders | 6 systems: the search field, the segmented toggle (joined borders, `margin-left: -1px`), the agent field, the select, the table hairlines, the facet active fill | `globals.css` 1990-2021 |

Reading: a search box with its own grammar, a row of toggles and a facet column are three products' worth of filtering on one
screen. The failed runs are visible, but so is everything else: with five status hues and three filled red chips, a Failed row
is one coloured thing among coloured things.

### A.3 Run detail, failed run (`/runs/{id}`, `RunDetail.tsx`, context column open)

Counted on `docs/screenshots/phase-22/step-5/context-open-1440-light.png` and the 375 version beside it.

| Measure | Count | Evidence |
| --- | --- | --- |
| Horizontal bands above the first event row | 6: top bar, identity line, headline, detail line, figures line, view tabs, then the pane toolbar = the first row is 262 px down at 1440 | screenshot; `run-header.css`, `.pane-head`, `.pane .toolbar` |
| Items on the identity line | 7 in mono: name, status chip, `run_…000010`, `coding-agent`, `model-x`, `ordered by sequence`, `started 2026-10-07 09:03:00Z` | `RunDetail.tsx` 689-725 |
| Readouts on the figures line | 9: Duration, Cost, Tokens in/out, Model calls, Tool calls, Retries, Errors, Files changed (and the tokens pair) | `RunDetail.tsx` 732; `globals.css` 546-556 ("Nine readouts fit one row from 1280") |
| Sections in the context column (failed run) | 5: Findings (with "Analysis is current."), two finding cards, Health, "What the agent did" (6 steps), Evaluations (illustration, headline, lede, evaluator radio) | `run-context.css`, `Findings.tsx`, `Evaluations.tsx` |
| Sections in the context column, running run with nothing wrong | 7: Findings note, Overall, five health dimensions each with a rule sentence and a raw-input line (`status: RUNNING · error count: 0 · retry count: 0`), "No findings yet", Evaluations | `docs/screenshots/phase-22/step-3/running-1440-light.png`; `globals.css` 1812-1851 |
| Red elements on one failed-run screen | about 20: the filled Failed chip, the 1.5 rem serif headline, "Errors 3", the critical finding's bar and word, two "Command failed" story steps, "Health: ✕ Bad", 8 rows tinted `--bad-bg` with a red inset bar, the first-error outline and its red label tab, five "✕ error" tags | `.tl-row[data-error]`, `.tl-row[data-first-error]`, `.hl[data-kind=failed] .hl-lead`, `.finding[data-severity=critical]`, `.story li[data-tone=bad]` |
| Bordered or filled regions | 16: rail (5), top bar, search, run head, pane head, context column, 2 finding cards, toolbar, 3 toolbar buttons, Go button, Context button, pane foot, inspector when open | screenshot |
| Type on one screen | 14 sizes and all 4 families: Condensed (h1 0.9375, tabs 0.8 upper, context toggle 0.72 upper, legend 0.66 upper, context h2 0.75), Serif (headline 1.5), Mono (id 0.75, detail 0.8125, figures 0.75, event types 0.8, kbd 0.7), Sans (rows 0.84, story 0.88, chips 0.72, foot 0.75) | `run-header.css`, `globals.css` 862-906, 1057-1116, 1795-1808 |
| Keyboard hints drawn as key caps on the page | 7 at once: `/` in the search field, `e` in the Go button, `j` `k` `↵` `e` `esc` in the pane foot | screenshot |
| Controls in the pane toolbar | 3 (Filter ▾, Errors only, Collapse groups); the earlier chip toolbar in step 3 had 8 (6 class chips + 2) | step-5 vs step-3 screenshots |

Reading: ADR-111 said "colour only ever means a problem" and the stylesheets obey it (`tokens-use.test.ts` greps for red). But the rule
limits the hue, not the quantity. A failed run paints its one problem twenty times, so the fault line, which was meant to be the
signature, is one red thing among twenty. The rows above the line are "quiet" only relative to the rows below.

### A.4 Alerts (`/w/{ws}/alerts`, Rules tab)

Counted on `docs/screenshots/phase-17/alerts-rules-1440-light.png` (2 rules).

| Measure | Count | Evidence |
| --- | --- | --- |
| Regions | rail (5), top bar, search, page head, tab bar, primary button, a 7-column table, a foot note = 12 for two rows of data | `alerts.css` |
| Controls per row | 3 (On toggle, Edit, Delete in an amber outline) = 6 buttons for 2 rules, plus Add a rule | screenshot |
| Column heads | 7 upper-case (Name, When, Only if, Sends to, On, Last fired, Actions) | screenshot |
| Colours | ink, muted, accent (Add a rule fill, tabs), amber (Delete outline, "failing"), orange dot | screenshot |
| The page's own tab style | `.al-tabs`: sentence case, a third tab treatment next to `.subnav` and `.seg-item` | `alerts.css` 5-30 |

Reading: the content is calm; the shell and the three competing tab styles are not. Two rules should not need twelve boxes.

### A.5 Settings, one tab (`/w/{ws}/settings/quotas`)

Counted on `docs/screenshots/phase-19a/quotas-1440-light.png`.

| Measure | Count | Evidence |
| --- | --- | --- |
| Tabs in the settings sub-nav | 8 today (Members, API keys, Pricing, Security, Quotas, Policies, Audit log, Sessions); 9 with Retention (`feature/phase-19b-data-lifecycle`) | `SettingsNav.tsx`; `git ls-tree feature/phase-19b-data-lifecycle` |
| Visual languages on one tab | 3: four bordered cards (6 px radius, `--surface` fill) each with a title, a status glyph and word, a bold figure, a progress bar and "Default limit."; one amber notice banner inside a card; a four-field form with a filled Save button | `quotas.css` 13-77 |
| Regions | rail (5), top bar, search, sub-nav, 4 cards, 1 notice, 4 fields, 1 button = 18 | screenshot |
| Progress-bar colours | 3 (muted grey, amber at 82 %, dark amber at 100 %) and a 999 px radius unlike every other radius in the app | `quotas.css` 53-64 |

Reading: Settings is nine pages that each invented a layout. Quotas uses cards; Members uses a table and an inline form (running
app); Sessions and Policies use yet other shapes. There is no settings pattern, only settings pages.

### A.6 The stylesheets (where the complexity lives)

Counted with `grep` over `apps/web/src/app/*.css` (22 files, 5,671 lines; `globals.css` alone 2,141).

| Measure | Count |
| --- | --- |
| Distinct font sizes declared | 33 (0.625 rem to 1.4 rem, plus 12, 13, 18, 20, 22, 30 px); the three most used are 0.75, 0.8 and 0.72 rem, so "small" exists in three sizes a pixel apart |
| Font families loaded | 4 (Plex Sans 3 weights, Condensed 2, Mono 2, Serif 2): 9 woff2 files, 191.5 KB, all preloaded by `next/font` |
| Font weights used | 4 (400 x20, 500 x42, 600 x64, 700 x7) |
| Letter-spacing values | 9 distinct (0 to 0.12 em) in 31 rules; 14 `text-transform: uppercase` rules |
| Border-radius values | 12 distinct (0, 2, 3, 4, 6, 999 px, 50 % and five asymmetric pairs); `ui-direction.md` says "4 px everywhere (6 px maximum)" |
| Colour tokens | 16 semantic in `:root` plus 6 aliases (`--fg`, `--bad`, `--warn`, `--info`, `--bad-bg`, `--warn-bg`) kept "so other stylesheets and tests keep working" (ADR-111); 64 raw hex values outside the token blocks (syntax-highlight colours, the waterfall palette, illustrations) |
| Layers stacked in `globals.css` | section comments name Phase 4, 6, 10, 15, 21 and 22; `run-header.css` opens with "Selectors carry `.run-head` so they win over the Phase 21 header rules in globals.css"; `settings.css` exists only to beat `.subnav`'s upper-case rule with higher specificity |

Reading: the product has been designed six times, each phase adding a layer on the one before and winning by specificity. The user's
word "convoluted" is literally right: the CSS is convolved, and the screen shows every layer at once (a condensed upper-case tab bar
from Phase 21 next to a serif headline from Phase 22 above a Phase 10 finding card inside a Phase 22 context column).

### A.7 Headline numbers

- 8 primary nav items + 4 second-level tab styles + 2 switcher boxes; "where am I" shown twice.
- Overview: 13 regions, 5 stat blocks, 2 tables with 18 column heads, 7 hues, 12 type sizes, no single focal point.
- Runs list: 3 filtering systems, 17 controls before the first row, 5 status hues, 11 red elements.
- Run detail (failed): 6 bands above the first event (262 px), 9 readouts, 7 identity items, about 20 red elements, 14 type sizes, 4 typefaces, 7 drawn key caps.
- Running run: 7 context sections for a run with nothing wrong.
- Settings: 9 tabs, 3 visual languages on one tab, no shared pattern.
- CSS: 33 font sizes, 4 families, 12 radii, 9 tracking values, 6 phase layers, 5,671 lines.

---

## Part B: Prompt 4

### 1. Design read (one line)

Reading this as: a diagnostic product surface for engineers who open it because something went wrong, with a calm, precise,
instrument-like language, leaning toward editorial-technical restraint (the Linear and Stripe-docs family), not the cockpit.

The prompt allows one clarifying question when two real directions remain. There were two: (a) keep the "flight-deck instrument
panel" (ADR-111, dense readouts, condensed upper-case labels, a serif failure sentence) and only tidy it, or (b) replace the
instrument metaphor with a quieter editorial one and keep only the fault line. Decided for (b) without asking, because the audit
shows the instrument metaphor is where the extra typefaces, upper-case tracking, readout strips and tinted rows come from; tidying
would leave the cause. The alternative is recorded in ADR-166.

### 2. The three dials

| Dial | Value | Pipeline's starting point | Why |
| --- | --- | --- | --- |
| DESIGN_VARIANCE | 2 | 3-4 | The data supplies all the variety this product needs (names, paths, commands, numbers). Every layer of designed variance on top (four typefaces, a serif sentence, condensed caps, cards inside columns) is what reads as convoluted. 2 means: one grid, one alignment, symmetry broken only by content. |
| MOTION_INTENSITY | 2 | 3-4 | People arrive stressed. Keep exactly what carries meaning: the fault line drawing once, the inspector sliding in, a jump outline. Drop the headline fade and the live-row rise (ADR-111 step 6); rows simply appear. Zero under `prefers-reduced-motion` stays. |
| VISUAL_DENSITY | 5 | 5-6 | Rows stay dense (13 px, 32 px rows, tabular mono numbers): that is the work. Chrome goes airy: one band of navigation, 24 px gutters, 32 px between sections. Density of data, not of furniture. |

### 3. Anti-default lock

Banned, generic AI defaults: purple or blue-to-violet gradients; Inter + slate; three equal feature cards; glass and blur; cream
"premium" parchment; drop shadows as hierarchy; icon-only navigation with tooltips; a theme toggle; sparklines in stat tiles;
a dashboard of KPI tiles.

Banned, this product's own accumulated defaults (the audit's findings): upper-case tracked micro-labels; the condensed face; the
serif; more than two typefaces on a screen; the filled status chip; tinted rows; left tone bars on panels; bordered switcher
boxes; cards inside columns; three tab styles; drawn key caps as decoration; nine readouts in a row; a "health" block that says
OK five times; illustrations on routine empty states; radii other than 0 and 4 px.

### 4. Taste system

**Aesthetic name: "One Red Line".** It fits because the product's whole promise is "we show you where it broke": one line on a
quiet page is that promise drawn, and everything the audit counted as noise is what competes with that line.

**Signature element (the one move):** the fault line is the only red mark on any screen. A 2 px red rule across the timeline (and
down the waterfall) at the first error, with a small label on the rule, and in miniature a 16 px red dash before the failure
sentence in the runs list. The status word "Failed" is red; nothing else is. No red fills, no tinted rows, no red headline, no red
counts, no red finding bars. On a healthy run there is no red anywhere, which is the point: red becomes information again.

**Palette roles** (OKLCH with hex fallbacks; step 4 computes the contrast table by script and may nudge lightness, never hue):

| Role | Light | Dark | Use |
| --- | --- | --- | --- |
| canvas | `oklch(97.8% 0.003 250)` ≈ `#f7f8fa` | `oklch(19% 0.012 255)` ≈ `#15191e` | the page; tables and panels sit directly on it (no second surface) |
| raised | `oklch(100% 0 0)` `#ffffff` | `oklch(24% 0.012 255)` ≈ `#1f242b` | menus, the phone inspector overlay, the search listbox only |
| hairline | `oklch(90% 0.006 250)` ≈ `#dde1e6` | `oklch(32% 0.012 255)` ≈ `#333a43` | every divider and row rule |
| hairline-strong | `oklch(82% 0.010 250)` ≈ `#c4cad2` | `oklch(42% 0.012 255)` ≈ `#4a525c` | field borders, table head rule |
| ink | `oklch(25% 0.015 255)` ≈ `#22272e` | `oklch(92% 0.006 250)` ≈ `#e4e8ec` | text, status glyphs, severity (by weight) |
| ink-2 | `oklch(50% 0.020 255)` ≈ `#626c78` | `oklch(72% 0.012 250)` ≈ `#a0a8b2` | secondary text, column heads, success and cancelled words |
| accent | `oklch(47% 0.10 250)` ≈ `#2a5f8f` (kept) | `#7db3e6` (kept) | links, focus ring, selection edge, the one filled button, the running dot |
| accent-wash | `oklch(94% 0.02 250)` ≈ `#e6eef6` (kept) | `#1f3040` (kept) | selected row, pressed toggle |
| fail | `oklch(50% 0.17 28)` ≈ `#b3261e` (kept) | `#ff8a80` (kept) | the fault line, the miniature dash, the word Failed / Timed out / Blocked. Nothing else. |
| caution | `oklch(48% 0.11 75)` ≈ `#8a5a00` (kept) | `#ffcb6b` (kept) | the words Waiting, Retry, Expired, Near limit; a destructive button's text. Text only, no fill. |
| mark | `#d9650f` (kept) | `#ff7a1a` (kept) | the brand glyph's cut and the recording dot. Never an interaction colour. |

Removed roles: `--surface-2`, `--ok`/`--ok-bg` (success is ink-2 with a check glyph; a green word in every row is decoration),
`--info`, `--fail-bg`, `--wait-bg`, `--accent-bg` as a fill (kept only as the selection wash), the six aliases once no stylesheet reads
them. The waterfall's categorical lane palette (ADR-071) stays as data colour, desaturated to sit under the fault line (step 4).

**Type pairing:** IBM Plex Sans (text, headings, navigation) and IBM Plex Mono (every id, number, path, command, event type).
Two families from one superfamily, already self-hosted under SIL OFL 1.1 (`apps/web/src/fonts/OFL.txt`), no third-party request.
Plex Sans Condensed and Plex Serif are dropped from the app (4 files, 91 KB of the 191.5 KB preloaded; ADR-110 and ADR-111
amended). Justification for keeping Plex rather than a new pairing: the complaint is about quantity, not the face; Plex Sans at
13 px with tabular Mono is already the clearest thing on the screen, and a new face would be a third design in two phases.
Alternative considered and rejected: Geist Sans + Geist Mono (also OFL, also free) would read as "Vercel"; Source Sans 3 + Source
Code Pro (OFL) is as good but gains nothing over Plex and costs a swap.

**Type scale** (7 sizes, 2 weights, 2 families; line heights fixed in px so bands do not jitter):

| Step | Size / line | Weight | Use |
| --- | --- | --- | --- |
| display | 32 / 40 | 600 | sign-in, no-access, first-run headline only |
| h1 | 24 / 30 | 600 | page title; the run headline sentence (ink, never red) |
| h2 | 18 / 24 | 600 | section heading |
| h3 | 14 / 20 | 600 | sub-section, settings field group, inspector section |
| body-lg | 14 / 20 | 400 | prose: settings help text, approval detail, setup guide, empty states |
| body | 13 / 20 | 400 | default; table rows; navigation |
| small | 12 / 16 | 400 | column heads (sentence case, ink-2), captions, ids, hints |
| mono | 13 / 20 and 12 / 16 | 400 | numbers, ids, paths, event types, commands; `font-variant-numeric: tabular-nums` |

Hierarchy rules: weight carries emphasis (600), never colour and never caps; no letter-spacing anywhere; no 500 weight; no italic
except code comments; upper case only for acronyms; a label is sentence case; the only thing larger than h1 is display, and it
never appears inside the shell. Radii: 4 px on controls, 0 elsewhere. No shadows. One hairline weight.

**Primary-viewport layout idea (the run page at 1440):**

```text
┌ top bar 44 ────────────────────────────────────────────────────────────────────────────────────┐
│ ⌁ Agent Black Box   Runs  Review  Analytics  Experiments  Settings      demo / All projects ▾  ⌕  ◦ you │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Runs / Fix flaky checkout test                                                      Failed · 5d ago   │
│                                                                                                   │
│ Failed at step 8 after 6.5 s: the test command exited 1.                      [Go to first error  e]  │
│ 1 retry · 8.8 s · $0.0100 · 1,700 in / 160 out · 3 errors                                 Details ▾   │
│                                                                                                   │
│ Timeline 12   Waterfall   Evidence   Findings 2   Evaluations                     Filter ▾  Errors only│
├──────────────┬──────────────────────────────────────────────────────────────────┬─────────────────┤
│ Steps        │ +0ms     run.started        Fix flaky checkout test              │ (inspector,     │
│ 1 Read       │ +100ms   agent.started                                           │  opens on       │
│ 2 Model      │ +500ms   file.read          src/checkout/cart.ts                 │  selection,     │
│ 3 Edit       │ +800ms ▸ llm.request        model-x (2 events)           1.2 s  │  26 rem)        │
│ 4 Command ✕  │ +2.4s    file.modified      src/checkout/cart.ts +4 -1          │                 │
│ 5 Retry #1   │━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ first error · step 8 ━━━│                 │
│ 6 Command ✕  │ +6.5s    shell.command      pnpm test checkout · exit 1  3.8 s ✕ │                 │
│              │ +7.5s    retry.attempted    Retry #1                             │                 │
│              │ +8.8s    run.failed                                           ✕  │                 │
└──────────────┴──────────────────────────────────────────────────────────────────┴─────────────────┘
```

One band of navigation, three lines of header (title line, headline, figures), one tab row, and the red line. The left
column is the agent's steps and nothing else; findings and evaluations are tabs, not a column.

**Motion intent (dial 2):** moves: the fault line draws once (280 ms) when first on screen; the inspector slides 8 px in (160 ms);
a jump lands with a 600 ms outline that fades. Stays still: headlines, status changes, arriving live rows, hover, tabs, menus,
toggles. Under `prefers-reduced-motion` everything is static and complete (existing contract, `tests/motion-contract.test.ts`).

**Voice cues:** sentences, not labels ("Failed at step 8 after 6.5 s: the test command exited 1", not "FAILED · step 8 · exit 1").
One verb per button ("Approve", "Deny", "Go to first error"). Numbers carry units and thousands separators. No upper-case shouting,
no key caps as decoration (shortcuts live in the tooltip and one "Keyboard" line in Settings or a `?` sheet). The product never says
"Dashboard"; the home is "Runs". Empty states are one sentence and one action, no illustration except the first-run guide and the
not-found page.

**Non-goals (what this design refuses):** a theme toggle; an icon rail; KPI tiles and sparklines on an overview; colour-coded
event classes in the timeline; a second accent; cards as a layout device; red for anything but the fault and the failed word;
green anywhere; decorative illustrations; more than one settings layout; animations that explain nothing.

### 5. First-build checklist (for steps 4 and 5, in order)

1. Tokens (step 4): replace the `:root` blocks with the eleven roles above under the existing names where a name survives
   (`--bg` → canvas, `--surface` → canvas too, `--border`, `--ink`, `--muted` → ink-2, `--accent`, `--sel`, `--fail`, `--wait`, `--mark`);
   delete `--surface-2`, `--ok*`, `--info`, `*-bg` fills and the aliases once `grep` shows no reader; add the 7-step type tokens;
   computed contrast table and a failing test for any pair under target.
2. Fonts: remove the Condensed and Serif `localFont` declarations and their files; `--cond` and `--serif` resolve to `--sans`
   until every reader is deleted.
3. Shell (5a): top bar with brand, five links, scope control, search button, person menu; delete the rail, the switcher boxes,
   the upper-case live indicator (a dot with an accessible name stays), `Crumbs` becomes the page title line.
4. One tab component and one table style; delete `.seg-item`, `.al-tabs`, `.apr-tabs`, `.subnav` in favour of it.
5. Runs (5b): merge the Overview figures into one sentence above the list; delete `Dashboard.tsx`'s stats strip and second
   table; one search field plus facets; delete the segmented status row, agent field and started select.
6. Run page (5b): three-line header, five tabs, Steps column, the fault line as the only red; delete tinted rows, finding cards,
   health dimensions, the figures beyond five (into Details), the drawn key caps.
7. Review (5c): Approvals and Security under one page title with two tabs and one list style; Alerts under Settings.
8. Settings (5d): one page pattern (title, one-sentence lede, hairline sections with a label column and a control column,
   actions at the end of a section) applied to all nine tabs in a grouped left sub-nav.
9. Step 6: expansion stress test, then the review against this document: one focal point per screen, box and colour counts
   down against Part A.

---

## Part C: removed or demoted from today's UI (explicit list)

| # | Today | Decision | Why (audit ref) |
| --- | --- | --- | --- |
| 1 | Left rail (224 px) with brand, two bordered switcher boxes, 8 links, email and Sign out | Removed; replaced by one 44 px top bar with 5 links, a scope control, search and a person menu | A.0: 15.6 % of the width and 5 regions on every screen; "where am I" shown twice |
| 2 | Overview page (5 stat blocks, two tables) | Merged into Runs as one sentence of 7-day figures above the list | A.1: no focal point; failures listed twice |
| 3 | Security, Approvals, Alerts as three top-level items with three tab styles | Approvals + Security merged into "Review" (two tabs, one list style); Alerts moved under Settings | A.0, A.4: three workspace-level queues with three tab components; alerts are configuration |
| 4 | Three filtering systems on the runs list | One: the search field with chips, plus facets that write chips; the segmented status row, agent field and started select removed | A.2: 17 controls before the first row |
| 5 | About 20 red elements on a failed run | One red line, one red word; tinted rows, red headline, red counts, red finding bars, "✕ error" in red removed | A.3 |
| 6 | Nine readouts and seven identity items in the run header | Five figures on one line (duration, cost, tokens, errors, retries); id, model, ordering and the rest under "Details" | A.3: 262 px of header |
| 7 | Context column with Findings, Health, Story, Agents, Evaluations | A "Steps" column only; Findings and Evaluations become tabs; Health dimensions removed; Agents live in the waterfall | A.3: 7 sections on a healthy run |
| 8 | Plex Sans Condensed and Plex Serif | Removed from the app | A.6: 4 families, 91 KB, two extra voices |
| 9 | Upper-case tracked labels (switchers, RECORDING, column heads, tabs, legends) | Removed; sentence case, ink-2, 12 px | A.0, A.6: 14 rules, 9 tracking values |
| 10 | 33 font sizes, 4 weights, 12 radii | 7 sizes, 2 weights, 2 radii | A.6 |
| 11 | Filled status chip for failed runs; green success words; blue "Running" word; "info" findings in blue | Failed is a red word; success a check in ink-2; running an accent dot with the word in ink; findings by weight (ADR-131 kept) | A.2: five hues in one list |
| 12 | Cards (quotas, findings, health dimensions, finding list, `.panel`) | Removed; hairline sections | A.5 |
| 13 | Left tone bars on notices, states, withheld, search problems | One notice style: a sentence with a leading glyph, no bar, no fill | A.3, A.5 |
| 14 | Drawn key caps (7 on the run page) | Removed from the page; shortcuts in tooltips and a `?` sheet | A.3 |
| 15 | Illustrations on routine empty states (Approvals, Security, Members) | Kept only on first-run and not-found | A.4 running app: a motif above "Nothing is waiting for you" |
| 16 | The Projects list page `/w/{ws}` | Removed (redirect to Runs); the scope control lists projects | duplicates the switcher |
| 17 | "Needs attention" and "Compare" as page-head buttons | "Needs attention" becomes a facet preset; Compare stays as a mode toggle in the list head | A.2 |
| 18 | Event-class chip toolbar (6 chips) and "Collapse groups" | Filter menu only; "Collapse groups" inside it | A.3 step 3 vs step 5 |

What is kept from ADR-111 on purpose: the fault line and its one-time draw; the glyph-and-word status grammar; severity by weight;
"red only for failure" (now also "red only once"); amber for waiting, retry and destructive; the steel-blue accent as the only
interaction colour; the orange mark; system light and dark; the motion contract and its tests; the brand mark option A.
