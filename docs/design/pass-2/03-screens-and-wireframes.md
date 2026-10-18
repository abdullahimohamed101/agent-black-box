# Design pass 2, step 3: screens, wireframes and visual hierarchy

Status: written 2026-10-16 from Prompt 2 (ASCII wireframes), the visual-hierarchy template at the end of Prompt 5 and Prompt 1
section 3 (`docs/design/design-pass-2-prompts.md`), using the direction in `01-direction.md` (type scale, palette roles, the one
red line) and the IA in `02-ia-and-flows.md`. Every "today" claim cites the audit (`01-direction.md` Part A) or a screenshot.

## 0. Shared system (measurements used by every screen)

**Breakpoints** (min-width): `phone` < 600 · `narrow` 600-899 · `medium` 900-1279 · `wide` ≥ 1280. The three wireframe widths
are 1440 (wide), 768 (narrow) and 375 (phone). The run page alone has a fourth concern: viewport height under 576 px makes it a
scrolling document (as today's `max-height: 36rem` rule).

**Grid.** No column grid for chrome; a 4 px baseline and a 24 px gutter at ≥ 900, 16 px below. Content width is the viewport minus
gutters (tables and the timeline want width); prose (ledes, settings help, approval detail, empty states) is capped at 72 ch
(about 640 px at 14 px). Vertical rhythm: 8 px inside a block, 16 px between related blocks, 32 px between sections, 48 px above a
section that starts a new topic.

**Zones on every document page, top to bottom:**

```text
bar        44 px   brand · five items · scope · search · person            (canvas, hairline below)
title     ≥ 62 px  h1 24/30 at 24 px below the bar, right-aligned status or primary action; optional lede 14/20 under it
tabs       36 px   optional; one component; current item: ink 600 + 2 px accent underline; others ink-2 400
work               tables, lists, forms, the timeline
```

**Type steps (from step 1):** display 32/40 600 · h1 24/30 600 · h2 18/24 600 · h3 14/20 600 · body-lg 14/20 · body 13/20 ·
small 12/16 · mono 13/20 and 12/16. Two weights. No caps, no tracking.

**Colour use (from step 1):** canvas for everything; raised only for menus and the phone inspector; hairline rules; ink and
ink-2 for text; accent for links, focus, selection, the one filled button per page, the running dot; fail for the fault line and
the failed word; caution for waiting, retry, expired, near-limit and destructive text; mark for the brand cut and the recording
dot. Contrast targets: body on canvas ≥ 7:1, small text ≥ 4.5:1, ink-2 on canvas ≥ 4.5:1 (step 4 computes and asserts).

**Rows.** Table rows 32 px (13/20 text, 6 px padding), hairline between rows, hover = accent-wash at 50 % (instant, no transition),
selected = accent-wash with a 2 px accent inset on the left. Column heads 12/16 ink-2, sentence case, hairline-strong below.
Numbers right-aligned in mono, tabular.

**Controls.** Buttons 28 px tall at 13 px (32 px in forms at 14 px), 4 px radius, hairline-strong border, ink text; one filled
accent button per page at most; destructive buttons have caution text and no fill; fields 32 px, 4 px radius, hairline-strong,
canvas background, 2 px accent focus ring at 2 px offset.

**The one red line.** 2 px, full width of the list or timeline pane, drawn directly above the first-error row; its label
("first error · step 8", small, fail colour, 600) sits on the line at the right end, inside a 16 px canvas gap so the line looks
cut, like the brand mark. Draws once (280 ms `scaleX` from the left) when first on screen; static under reduced motion. The
miniature: a 16 x 2 px fail dash before a failure sentence in a list row. Nothing else is red except the status word.

**Status words.** `✓ Success` ink-2 · `✕ Failed` fail 600 · `✕ Timed out` fail 600 · `⊘ Blocked` fail 600 · `● Running` ink with an
accent dot · `‖ Waiting` caution · `– Cancelled` ink-2 · `◌ Queued` ink-2. Glyph `aria-hidden`; the word is the name.

**Focal-point rule.** Each screen names one focal point below. Nothing else on the screen may be larger, bolder or more
saturated than it. The audit's failure (A.1, A.3) was several candidates at once.

---

## 1. Runs (home) — replaces Overview + Runs list

**Purpose.** See what the agents did lately and go straight to anything that went wrong.
**Key elements.** Title line with a 7-day sentence; search field (combobox) with chips; facet column; the list; "Load more".
**Primary action.** Open a run (the row).
**Secondary.** Needs attention (facet preset), Compare (mode toggle), search, Load more, clear filters.
**Navigation.** Bar; row → Run; Compare → Compare; the summary sentence's "Analytics" link.

### 1440

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ⌁ Agent Black Box   Runs ·  Review 3   Analytics   Experiments   Settings            demo / All projects ▾  ⌕  ◦ you │  44
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                              │  24
│ Runs                                                                                     [Compare]           │  h1 24/30
│ Last 7 days: 12 runs, 3 failed, $22.15, 2 running now. Analytics →                                           │  lede 14/20 ink-2
│                                                                                                              │  16
│ [ ⌕  Search runs   status:failed tool:github "npm test"                                              ?   ]  │  field 32
│ ( status: failed ✕ ) ( tool: github ✕ )                                                 13 runs · Clear       │  chips 24
│                                                                                                              │  16
│ Status           │ Status      Run                                   Agent          Started   Duration    Cost     Findings │ heads 12
│  Needs attention │ ───────────────────────────────────────────────────────────────────────────────────────────────────────── │
│  Failed       3  │ ✓ Success   Ship feature: multi-agent              planner        5d ago      11 s   $0.0600     –       │ 32
│  Running      1  │ ● Running   Triage open issues                     triage-agent   5d ago       –     $0.0060     –       │
│  Waiting      1  │ ✕ Failed    Migrate billing schema                 coding-agent   5d ago      14 s   $0.0356     –       │ 32
│  Success      7  │             ▬ Failed at step 7 · 2 retries · $0.0167 spent after                                        │ +20 (small, ink-2; the dash is the only red)
│ Started          │ ‖ Waiting   Deploy to staging                      deploy-agent   5d ago       –     $0.00       –       │
│  Last hour       │ ✓ Success   Refactor payments module               refactor-agent 5d ago   5m 04s    $21.00      –       │
│  Last 24 hours   │ ✕ Failed    Fix flaky checkout test                coding-agent   5d ago     8.8 s   $0.0100   2 critical│
│  Last 7 days  ·  │             ▬ Failed at step 8 · 1 retry                                                                 │
│ Errors and retries│ ✓ Success   Sync customer records   2 retries     sync-agent     5d ago     8.6 s   $0.0070   1 warning │
│  Has errors      │ …                                                                                                        │
│  Retried         │ [Load more]                                                              13 of 13 runs shown            │
└──────────────────┴───────────────────────────────────────────────────────────────────────────────────────────────────────────┘
   facets 176 px      list: the rest (1216 px)
```

Annotations: the facets write chips into the field (one source of truth, the URL's `?q=`); "Needs attention" is the first facet,
and selects `status:failed,timed_out,blocked OR findings:critical`; the Compare toggle turns the status column into checkboxes
and the title's right side into "Pick two runs · [Compare these]". The `?` in the field opens the grammar help inside the listbox.

### 768

```text
┌──────────────────────────────────────────────────────────────┐
│ ⌁ ABB   Runs · Review 3  Analytics  Experiments  Settings  ⌕ ◦ │ 44 (brand shortens to the glyph + "ABB"; the scope control moves onto the title line)
├──────────────────────────────────────────────────────────────┤
│ Runs                                   demo / All projects ▾ │ h1; the scope control sits on the title line from 600 to 899
│ Last 7 days: 12 runs, 3 failed, $22.15, 2 running. Analytics →│
│ [ ⌕ Search runs                                          ? ] │
│ ( status: failed ✕ )                      13 runs · Clear    │
│ Narrow ▾  Needs attention · Failed 3 · Running 1 · Last 24 h │ facets as one wrapping chip row under a disclosure (open by default)
│ Status      Run                             Started  Cost    │ Agent and Duration columns dropped; they live in the row's second line
│ ✕ Failed    Migrate billing schema           5d ago  $0.0356 │
│             ▬ Failed at step 7 · coding-agent · 14 s         │
│ ✓ Success   Ship feature: multi-agent        5d ago  $0.0600 │
│             planner · 11 s                                   │
└──────────────────────────────────────────────────────────────┘
```

### 375

```text
┌───────────────────────────────────┐
│ ☰  ⌁ Agent Black Box        ⌕  ◦ │ 44; ☰ opens the five items + scope as a sheet
├───────────────────────────────────┤
│ Runs                              │ h1 24/30
│ demo / All projects ▾             │ scope on its own 32 px line
│ 12 runs this week, 3 failed.      │ lede shortened by the component at < 600 (count and failures only)
│ [ ⌕ Search runs               ? ] │
│ ( status: failed ✕ )     Clear    │
│ Narrow results ▾                  │ closed by default on a phone
│ ───────────────────────────────── │
│ ✕ Failed · 5d ago       $0.0356   │ each run is a 2-3 line block, 44 px min, the whole block is the link
│ Migrate billing schema            │ body 600
│ ▬ Failed at step 7 · 2 retries    │ small ink-2, the dash red
│ ───────────────────────────────── │
│ ✓ Success · 5d ago      $0.0600   │
│ Ship feature: multi-agent         │
│ planner · 11 s                    │
│ ───────────────────────────────── │
│ [Load more]                       │
└───────────────────────────────────┘
```

Reflow: ≥ 1280 facet column 176 px + list; 900-1279 facet column 160 px, Findings column stays, Duration drops; 600-899 facets become a
chip row, table keeps Status, Run, Started, Cost; < 600 rows become blocks, the facets a closed disclosure, Compare mode becomes a
checkbox at the block's right.

### Visual hierarchy

1. **Content priority.** Primary: the rows, and within a row the failure line (the only red marks on the page). Secondary: the
   search field and chips. Tertiary: the 7-day sentence, facets, Load more. CTA: none filled; Compare is an outline toggle.
2. **Visual weight.** h1 24/30 600; everything else 13 px except the lede at 14; the run name 600 only on failed rows (A.2 today
   bolds every name at 500); the failure sentence 12/16 ink-2 with the 16 x 2 px red dash; status words 13 px, Failed 600 in fail.
   White space: 24 px under the bar, 16 px between field and chips, 16 px between chips and the table head.
3. **Layout.** Two zones side by side (facets 176 px, list fluid) under a one-column head; alignment on a single left edge at the
   24 px gutter; the facet column's left edge is that edge; the table's first column starts at the same x as the facet heading
   text plus the column width. Proximity: a failed row's second line sits 2 px under its first, so the pair reads as one row.
4. **Type.** h1 only; column heads small ink-2; no h2 on this page (today's "Recent failures"/"Recent runs" are gone).
5. **Colour.** Accent only on the Analytics link, chips' borders, the focused field and the Running dot; fail on the Failed word and
   the dashes (two per failed row); nothing else. Today's five hues (A.2) become one hue plus one dot.
6. **Scanning path and focal point.** Focal point: the first red dash in the list (or, with none, the h1). Path: h1 → sentence →
   down the status column (words aligned at one x) → the red dashes → across the row to Cost and Findings. The facets are to the
   left so the eye does not cross them on that path.
7. **Responsive.** Described above; on a phone the status word moves to the block's first line so the scanning column survives.

**What this screen loses versus today.** The Overview page and its five stat blocks, "Recent failures", the second table, 18 column
heads (A.1); the status toggle row, agent field, started select, Search button, "Search help" disclosure, "Needs attention" button
(A.2: 17 controls → 1 field, 1 toggle, 1 help link); the filled red chips, the green words, the blue running word, the red
failure sentences; the rail and its two boxes.

---

## 2. Run detail — the headline, the fault line, the tabs

**Purpose.** Understand what the run did and where it first went wrong, then read the evidence.
**Key elements.** Title line (name, status, age); headline sentence; figures line + Details; five tabs; Steps column; the pane
(timeline / waterfall / evidence / findings / evaluations); inspector; replay transport when on.
**Primary action.** Go to first error (`e`).
**Secondary.** Tabs, Filter, Errors only, Replay (`r`), Details, Compare (from the list), Show evidence.
**Navigation.** Title line "Runs /" link; bar; inspector links to the waterfall and evidence; Findings → evidence rows.

### 1440 (failed run, inspector closed)

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ⌁ Agent Black Box   Runs ·  Review 3   Analytics   Experiments   Settings            demo / All projects ▾  ⌕  ◦ you │ 44
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Runs / Fix flaky checkout test                                                     ✕ Failed · 5d ago         │ 24 px top; h1 24/30; status 13 px 600 fail
│                                                                                                              │ 8
│ Failed at step 8 after 6.5 s: the test command exited 1.                        [Go to first error   e]      │ headline 24/30 600 ink (NOT red), max 72 ch
│ 1 retry · 8.8 s · $0.0100 · 1,700 in / 160 out · 3 errors                                         Details ▾  │ figures 13/20 mono, ink-2 labels; 8 px above
│                                                                                                              │ 16
│ Timeline 12   Waterfall   Evidence 4   Findings 2   Evaluations                          Filter ▾  ☐ Errors only │ tabs 36; hairline below spans the width
├──────────────┬───────────────────────────────────────────────────────────────────────────────────────────────┤
│ Steps        │ Offset    Event                     Detail                                  Duration           │ Steps 192 px; pane fluid
│ 1 Read       │ +0 ms     run.started               Fix flaky checkout test                   –                 │ rows 32; mono for offset, type, duration
│   cart.ts    │ +100 ms   agent.started                                                       –                 │
│ 2 Model      │ +500 ms   file.read                 src/checkout/cart.ts                      –                 │
│   model-x    │ +800 ms ▸ llm.request · model-x     (2 events)                              1.2 s               │ group row: ▸ disclosure, name 600
│ 3 Edit       │ +2.4 s    file.modified             src/checkout/cart.ts +4 −1                –                 │
│   cart.ts    │━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  first error · step 8 ━━━━│ THE LINE (2 px fail), label at right in the cut
│ 4 Command ✕  │ +6.5 s    shell.command.completed   pnpm test checkout · exit 1             3.8 s  ✕ error      │ "✕ error" ink 600, not red
│   pnpm test  │ +7.5 s    retry.attempted           Retry #1                                  –    ‖ retry      │ "retry" caution
│ 5 Retry #1   │ +7.8 s    shell.command.started     pnpm test checkout --retry                –                 │
│ 6 Command ✕  │ +8.7 s    shell.command.completed   pnpm test checkout --retry · exit 2     900 ms ✕ error      │
│   --retry    │ +8.8 s    run.failed                                                          –    ✕ error      │
│              │                                                                                                │
│              │ 12 of 12 events                                                                                │ foot 28 px, small ink-2; no key caps
└──────────────┴────────────────────────────────────────────────────────────────────────────────────────────────┘
Inspector (on row selection): a 416 px (26 rem) column at the right, canvas background, hairline on its left, slides 8 px in;
heading = event type (mono 18/24 600), "What went wrong" as the first section on an error event (h3 + sentence + exit code),
then payload sections as hairline bands; [Show evidence] and [Open in waterfall] as text buttons; Esc closes.
Details ▾ (under the figures): id · agent · model · ordered by sequence · started 2026-10-07 09:03:00Z · tool calls 0 · files changed 1 — one mono line that opens in place.
Running run: title line "● Running · started 2 s ago"; headline "Running · step 6 · last event 2 s ago"; no line, no red; new rows appear at the bottom (no rise); scrolled away → a "3 new events ↓" text button pinned at the pane's bottom edge.
```

### 768

```text
┌──────────────────────────────────────────────────────────────┐
│ ⌁ ABB   Runs · Review 3  Analytics  Experiments  Settings  ⌕ ◦ │ 44
├──────────────────────────────────────────────────────────────┤
│ Runs / Fix flaky checkout test             ✕ Failed · 5d ago │ h1 20/26 at < 900 (the one place h1 shrinks)
│ Failed at step 8 after 6.5 s: the test                       │ headline 20/26 600 ink
│ command exited 1.           [Go to first error]              │
│ 1 retry · 8.8 s · $0.0100 · 3 errors              Details ▾  │ figures drop tokens at < 900 (into Details)
│ Timeline 12  Waterfall  Evidence 4  Findings 2  Evaluations  │ tabs scroll sideways if needed, no fade
│ Steps ▸ 6 (2 failed)                     Filter ▾ ☐ Errors   │ Steps column folds to one 32 px strip above the pane (opens as an overlay list)
│ +0 ms    run.started     Fix flaky checkout test          –  │ Offset 72 px, Event+Detail fluid, Duration 56 px
│ +500 ms  file.read       src/checkout/cart.ts             –  │
│━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ first error · step 8 ━│
│ +6.5 s   shell.command.completed  pnpm test checkout · exit 1 ✕ │
│ …                                                            │
└──────────────────────────────────────────────────────────────┘
Inspector at 600-1279: an overlay from the right, 416 px, raised background, the pane behind stays; Esc or [Back].
```

### 375

```text
┌───────────────────────────────────┐
│ ☰  ⌁ Agent Black Box        ⌕  ◦ │ 44
├───────────────────────────────────┤
│ ‹ Runs                            │ 32; the back link is the title line's first level
│ Fix flaky checkout test           │ h1 20/26
│ ✕ Failed · 5d ago                 │ 13 px, Failed in fail 600
│ Failed at step 8 after 6.5 s: the │ headline 20/26 600 ink
│ test command exited 1.            │
│ [Go to first error]               │ 44 px tall, full width, outline
│ 1 retry · 8.8 s · $0.0100 ·       │ figures wrap; Details ▾ on the last line
│ 3 errors              Details ▾   │
│ Timeline 12 · Waterfall · Evid… › │ tabs scroll sideways; current underlined
│ Steps ▸ 6 · Filter ▾ · Errors ☐   │ 36 px toolbar row
│ ───────────────────────────────── │
│ +0 ms    run.started              │ two-line rows: offset + type on line 1, detail on line 2; 44 px min
│          Fix flaky checkout test  │
│ +500 ms  file.read                │
│          src/checkout/cart.ts     │
│━━━━━━━━━━━━━━━━ first error · 8 ━━│ the line is visible by y ≈ 330 of 812 on a failed run (acceptance: first viewport)
│ +6.5 s   shell.command.completed ✕│
│          pnpm test checkout · exit 1
│ …                                 │ the list has a fixed height (virtualised) and the page scrolls (today's stacked mode)
└───────────────────────────────────┘
Inspector at < 600: full-screen raised sheet under the bar with [‹ Back] and the event type as its heading; focus moves to it.
```

Reflow: ≥ 1280 three columns (Steps 192 / pane / inspector 416 when open; the Steps column folds to a strip at ≥ 1280 only
when the pane would drop below 560 px); 900-1279 Steps a strip, inspector an overlay; 600-899 as 768; < 600 as 375; height < 576
makes the page a document at any width.

### Visual hierarchy

1. **Priority.** Primary: the headline sentence (what happened) and the red line (where). Secondary: the rows around the line, the
   Go button. Tertiary: figures, Steps, tabs, foot. CTA: Go to first error, right-aligned on the headline line, outline accent.
2. **Weight.** Headline 24/30 600 ink is the largest text; the title line is also 24 but ink-2 for the "Runs /" part and 600 ink for
   the name, so the name and the headline are the two bold things, the headline longer. The line is 2 px of the only saturated
   colour and spans the full pane: at 1440 it is a 1,000 px red stroke and nothing else is red, which is the whole design. Status
   word 13 px. Figures 13 px mono ink-2. Rows 13 px ink. Space: 24 px above the title, 8 px to the headline, 8 px to the figures,
   16 px to the tabs; header total 164 px at 1440 (today 262 px, A.3).
3. **Layout.** Header is one column across the full width; the work area is Steps | pane | inspector. The pane's left edge is the
   same x as the headline's first letter when the Steps column is folded; with it open the Steps text starts at the gutter and the
   pane's offset column starts after the 192 px column + 1 px hairline + 16 px. Group rows indent their children by 16 px.
4. **Type.** h1 (name) 24; headline 24 (not a heading; a `p` with `data-testid=headline`); h3 14/20 only inside the inspector; rows
   13/20 with mono for offset, event type and duration; the fault label 12/16 600 fail.
5. **Colour.** Fail: the status word, the line, the label. Caution: "retry" words, "Waiting" states, "Reconnecting". Accent: the Go
   button, links, the selected row's inset, the running dot, the playhead in replay. Everything else ink / ink-2 on canvas. The
   waterfall's lanes keep their categorical palette, desaturated so no lane colour outranks the line.
6. **Scanning path and focal point.** Focal point: the red line. Path: headline (what) → the line (where) → the row under it → Go /
   inspector (why) → Findings tab (cause). On a healthy or running run the focal point is the headline sentence; nothing is red.
7. **Responsive.** Headline and line in the first viewport at every width; the Steps column is the first thing to fold; the
   inspector is a column only at ≥ 1280; the figures lose tokens first, then files; the foot's count stays.

**What this screen loses versus today.** Three bands of header (identity line of 7 items, 9 readouts, the upper-case tab row;
A.3), the serif and condensed faces, the red headline, 8 tinted rows and their inset bars, the first-error outline (the line
replaces it), 5 red "✕ error" tags (now ink), the Context column with Findings cards, Health, Agents and Evaluations (now a Steps
column and two tabs), the Context toggle and its vertical strip label, the event-class chips and Collapse groups (now in Filter),
7 drawn key caps, the search field in the bar (now a button), the rail.

---

## 3. Compare

**Purpose.** See where two runs diverged and what that cost.
**Key elements.** Title; divergence sentence; delta line; A/B header; steps table with the disclosure of same steps; Summary,
Evaluations, Cost by model, Files as sections.
**Primary action.** Jump to divergence. **Secondary.** Previous / Next difference, Show earlier steps, open either run.
**Navigation.** Title line "Runs /" link; A and B names link to the runs.

### 1440

```text
│ Runs / Compare                                                                                               │ h1
│ The runs diverged at step 22: A read app/session.py, B read app/oauth.py.                                     │ sentence 18/24 600 ink (h2 size; not red)
│ The first difference in outcome is at step 26.  Right minus left: duration −2.2 s · cost −$0.0309 · calls −3 · files +1 │ 13 mono ink-2
│                                                                                                              │
│ A  Fix OAuth session expiry  ✓ Success          │ B  Fix OAuth session expiry  ✕ Failed                     │ two cells, hairline between; names 600; ids small mono under (not beside)
│    run_…0011 · coding-agent · 5d ago            │    run_…0012 · coding-agent · 5d ago                     │
│ Steps 34, 13 differ      [Jump to divergence] [‹ Previous] [Next ›]                                          │ toolbar 36
│ Step   A, left run                                          B, right run                                     │ heads small
│        Show the 18 earlier steps (the same in both runs) ▸                                                   │ a text disclosure row, not a button block
│ 19     tool.call.completed  read_file  success · 300 ms     tool.call.completed  read_file  success · 300 ms │
│━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ divergence · 22 ━━│ a 2 px ink rule with a label (a divergence is not a failure, so it is not red)
│ 22  ≠  file.read  app/session.py                            file.read  app/oauth.py                          │ changed rows: "≠" glyph + 600 on the differing cell
│ 26  ≠  shell.command.completed … success                    shell.command.failed … exit 1  ✕ error            │ "first outcome difference" as a small label on the right of the row, ink 600
│ 30  A  tool.call.completed  git_commit                      No step on this side                              │ ink-2 for the empty side
│ Summary ▾   Evaluations   Cost by model   Files                                                               │ sections as h2 bands below the table
```

Note on colour: the divergence rule is ink, 2 px, with a label, because a divergence is not a failure; the red line appears on
this page only if one side failed, as the miniature red dash before "exit 1" on the B side's first-error row. Today's page draws
the divergence as an ink rule already (`compare-1440-light.png`); the red "First outcome difference" label becomes ink.

### 768 and 375

```text
768: A and B cells stack to one row each (A above B); the steps table keeps three columns (Step, A, B) with wrapping cells;
     the toolbar wraps to two rows; sections become disclosures closed by default except Summary.
375: the steps table becomes a list of step blocks: "22 ≠" heading line, then "A  file.read app/session.py" and
     "B  file.read app/oauth.py" on two lines; Jump / Previous / Next as a 44 px sticky row under the title; A/B header as two blocks.
```

### Visual hierarchy

1. Primary: the divergence sentence and the divergence rule. Secondary: the differing rows (≠, 600). Tertiary: deltas, A/B cells,
   the sections. CTA: Jump to divergence (outline).
2. Sentence 18/24 600 (smaller than a run headline on purpose: this is analysis, not an incident); rows 13; cells that differ 600;
   the rule 2 px ink with a 12/16 label. 16 px between sentence and deltas, 24 px to the A/B row, 16 px to the toolbar.
3. One column; the A/B cells and the table columns share the same two x positions so the eye moves straight down each side.
4. h1, one h2-sized sentence, h2 for sections, small heads.
5. Ink and ink-2 only, accent on links and the pressed toolbar button, fail only as the miniature dash on a failed side's first
   error. Today's amber/accent left bars on the A/B cells go.
6. Focal point: the divergence rule. Path: sentence → rule → first ≠ row → right side → Next.
7. As above; the toolbar is sticky at < 600.

**Loses versus today.** The long ids in the A/B header line (now small under the names), the two coloured left bars, the red
"First outcome difference" label (now ink), the key-hint sentence (now tooltips), the Steps h2 above the toolbar.

---

## 4. Review › Approvals

**Purpose.** Decide what an agent may do next. **Key elements.** Review tabs (Approvals · Security findings); state tabs as
chips (Pending · Decided · All); the table; the request panel.
**Primary action.** Approve (in the panel). **Secondary.** Deny, comment, open the run, state filter.
**Navigation.** Bar (count); row → panel (`?approval=`); panel → run.

### 1440

```text
│ Review                                                                                  3 waiting            │ h1; right: count sentence 13 ink-2
│ Approvals ·   Security findings 10                                                                           │ tabs 36 (one component)
│ Pending 3   Decided   All                                                                                    │ chip row 28 (same component, "chip" density)
│ Request                      Risk        State        Requested   Time left         │ github.push               [Close] │ list 800 px; panel 416 px (26 rem) from the right, hairline on its left
│ github.push  origin main     ◆ R3        ‖ Waiting    11 m ago    5 h 59 min        │ ‖ Waiting · 5 h 59 min left        │ selected row: accent-wash + 2 px inset
│ github.merge release/2026.10 ◆ R3        ‖ Waiting    20 m ago    1 h 59 min        │ Capability   github.push           │ panel: a dl, label 13 ink-2 / value 13 mono
│ secrets.read prod/db-password ◆ R4 (filled) ‖ Waiting 23 m ago    4 h 59 min        │ Resource     origin main            │
│                                                                                     │ Risk         ◆ R3                  │
│                                                                                     │ Policy       approve-push-main v3  │
│                                                                                     │ Approver     Admin or above        │
│                                                                                     │ Expires      in 5 h 59 min; then the action is denied │
│                                                                                     │ Requested by run …0020 · release-agent · Demo project │
│                                                                                     │ Arguments                          │ h3
│                                                                                     │ { "branch": "main", "force": false, "remote": "origin" } │ pre, mono 12, hairline box (the one box on the page)
│                                                                                     │ Comment (optional)   [__________ ] 0 / 1000 │
│                                                                                     │ [Approve]  [Deny]                  │ Approve = the page's one filled accent button; Deny outline, caution text
│ An approval authorises one action, once. Policies apply to agents that ask through the SDK guard.           │ foot small ink-2
```

### 768 / 375

```text
768: the panel is an overlay from the right (416 px, raised) over the list; Request column keeps capability + resource on two lines;
     Requested and Time left merge into one "11 m ago · 5 h 59 min left" cell.
375: tabs scroll; the list is blocks (capability 600 / resource small / "◆ R3 · ‖ Waiting · 5 h 59 min left"); a tap opens the panel
     as a full-screen sheet with [‹ Back]; Approve and Deny are 44 px full-width buttons pinned at the bottom of the sheet, the
     confirm step replaces them in place ("Approve github.push on origin main?  [Approve] [Cancel]").
```

### Visual hierarchy

1. Primary: the pending rows and, once open, the capability + resource in the panel. Secondary: Approve. Tertiary: the dl,
   arguments, the foot. CTA: Approve (filled), Deny (outline caution).
2. h1 24; panel heading 18/24 mono 600; rows 13; R4 is a filled shield glyph and 600 (ADR-141 UI), R3 outline caution; the Waiting
   word caution. 24 px gutters; the panel's inner padding 24 px.
3. List + panel, split at 800 / 416 at 1440; the panel's label column 112 px.
4. h1; tabs; h3 for Arguments and Comment; no h2 (the tabs are the section).
5. Caution for Waiting and R3; ink for R4 and Denied; accent for Approve and links; no red (a denial is not a failure, ADR-141).
6. Focal point: the open request's capability line (or the first pending row when none is open). Path: tabs → rows (capability
   column aligned) → panel heading → Approve.
7. As above.

**Loses versus today.** The bordered panel card (now a hairline column), the illustration on the empty state, the "3 requests
loaded (end of list)" lede (now a count on the right), the separate `.apr-tabs` style, the rail count badge box (now text in the
bar item).

---

## 5. Settings › Notify › Alerts

**Purpose.** Tell people when something needs them. **Key elements.** Settings sub-nav; tabs Rules · Destinations · History;
one table per tab; inline add form; foot sentence.
**Primary action.** Add a rule (or Add a destination when there is none). **Secondary.** On/Off, Edit, Delete, Send a test.
**Navigation.** Settings sub-nav; History rows link to the destination and the run.

### 1440 (on the settings pattern; see section 8 for the pattern itself)

```text
│ Settings                                                                                                     │ h1
│ People        │ Alerts                                                                        [Add a rule]  │ sub-nav 192 px; section h2 18/24; the one filled button
│  Members      │ Tell a person or a channel when a run fails, a finding appears, an approval waits or spend   │ lede 14/20 ink-2, 72 ch
│  Sessions     │ goes over a limit. Alerts are sent as soon as possible and may arrive more than once.        │
│ Access        │ Rules 2 ·   Destinations 2   History                                                         │ tabs
│  API keys     │ Name                  When                     Only if                     Sends to           On   Last fired          │ heads small
│  Policies     │ Failed runs           A run fails              Demo project · failed,      Ops webhook        ◉    2026-10-12 14:30 UTC  Edit · Delete │ row 32 + a second small line for conditions; Edit/Delete as text buttons in ink-2, visible on hover and focus, always at < 900
│  Capture and  │                                               timed out · 1 per 10 min   Team Slack (failing)│
│  detectors    │ High severity findings A security finding      any project · release-agent Ops webhook       ◉    2026-10-11 08:05 UTC  Edit · Delete │
│ Limits        │                                               · High or worse                                   │
│  Quotas       │                                                                                              │
│  Retention    │ Add a rule (inline, opens under the table): Name [____]  When [A run fails ▾]  Only if … Sends to ☐ Ops webhook ☐ Team Slack  Cooldown [10] min   [Save rule] [Cancel] │
│  Pricing      │                                                                                              │
│ Notify        │                                                                                              │
│  Alerts ·     │                                                                                              │
│ Record        │                                                                                              │
│  Audit log    │                                                                                              │
```

### 768 / 375

```text
768: sub-nav becomes a select-like disclosure on the title line ("Settings: Alerts ▾") listing the five groups; the table drops
     Last fired into the second line; Edit · Delete always visible.
375: rows become blocks (name 600 / when · only if small / sends to small / On switch at the right); the add form is full width,
     one field per line, Save 44 px full width.
```

Hierarchy: primary the rules table; CTA Add a rule (filled, top right of the section); the "failing" destination word in
caution is the only coloured word; focal point the first rule's name; path h2 → tabs → names → On column. Loses: the 7
upper-case heads, the amber-outlined Delete buttons (now text), the `.al-tabs` style, the rail, 12 regions → 4.

---

## 6. Review › Security findings

**Purpose.** See what agents touched that deserves a look. **Key elements.** Review tabs; facet column (Severity, Detector,
Project, Run started); the table; Load more; the clean state with the detector list.
**Primary action.** Open a finding's run (Show evidence → `?finding=`). **Secondary.** Narrow, Load more, Clear filters.

### 1440

```text
│ Review                                                                                    10 findings        │
│ Approvals 3   Security findings ·                                                                            │
│ Severity         │ Severity   Finding                                                      Run                     When   │
│  Critical     3  │ ◆ Critical  Probable: a credential appeared in captured text              Export build logs        6d   │ Critical: filled shield + 600
│  High         6  │            Rule: a redaction record names a credential pattern                                   │ rule line small ink-2
│  Medium       3  │ ◇ High      Probable: a privileged database statement was run            Nightly dependency …    6d   │ High: outline shield caution + 600
│ Detector         │ ◇ Medium    Probable: a host outside the allowlist was contacted          Export build logs        6d   │ Medium: outline ink-2, 400
│  Unexpected … 3  │ …                                                                                                      │
│  …               │ [Load more]                                                                                           │
│ Project          │                                                                                                        │
│ Run started      │                                                                                                        │
```

Project column dropped at every width (the scope control already says it; a project facet remains). 768: facets a chip row;
375: blocks (severity + when on line 1, finding 600 on line 2, rule small, run link on line 4). Hierarchy: primary the Critical
rows (weight, not colour); CTA none filled; focal point the first filled shield; path tabs → severity column → finding text → run.
Loses: Project column, the separate page title "Security" (it is Review), the rail.

---

## 7. Settings page pattern (one pattern for all nine tabs)

**Purpose.** Change one thing about the workspace and see that it was stored.
**Key elements.** h1 "Settings" once; a grouped left sub-nav (People: Members, Sessions · Access: API keys, Policies, Capture and
detectors · Limits: Quotas, Retention, Pricing · Notify: Alerts · Record: Audit log); the page: h2 (the tab's name, right-aligned
primary action), one-sentence lede, sections as hairline bands, each section a label column (192 px) and a control column,
actions at the end of the section, "Saved" as a status line, errors as one sentence under the field.
**Primary action.** One filled button per tab at most (Create invitation, Create a key, Publish, Save, Add a rule). **Secondary.**
Row actions as text (Remove, Revoke, Edit), destructive in caution text with a confirm-in-place second click.

### 1440 (Quotas, the tab the audit measured)

```text
│ Settings                                                                                                     │ h1
│ People        │ Quotas                                        Project [Demo project ▾]                        │ h2; the scope select on the h2 line
│  Members      │ What one project may store. At a limit new items are refused with a clear code; what it      │ lede 14/20 ink-2
│  Sessions     │ already has keeps working. Changes are written to the audit log. Daily figures count the UTC  │
│ Access        │ day and start again at 00:00 UTC.                                                            │
│  API keys     │ ──────────────────────────────────────────────────────────────────────────────────────────── │ hairline
│  Policies     │ Distinct agents      12 of 500 (2 %)            ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁  Within limit    Limit [Default 500      ] │ row 48: name 600 / usage mono / 4 px bar 160 px / state word / override field
│  Capture and  │ Runs per day         41,200 of 50,000 (82 %)    ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▁▁▁▁  ◐ Near limit   Limit [Default 50,000   ] │ Near limit: caution word, bar in caution
│  detectors    │ Events per day       5,000,000 of 5,000,000     ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇  ● Limit reached Limit [Default 5,000,000] │ reached: ink 600 word, bar ink; a one-sentence note under the row: "Events beyond the limit are refused until 00:00 UTC. Replays are still accepted."
│ Limits        │ Artifact storage     1.25 GiB of 5 GiB (25 %)   ▇▇▇▇▇▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁  Within limit    Limit [Default 5 GiB    ] MiB │
│  Quotas ·     │ ──────────────────────────────────────────────────────────────────────────────────────────── │
│  Retention    │ Leave a field empty to use the default. Lowering a limit below current use refuses new items  │ help 13 ink-2
│  Pricing      │ only. A change applies to the next request.                                   [Save limits]  │ the one filled button, right-aligned at the section end
│ Notify        │ Saved 14:02 UTC                                                                               │ role=status, small ink-2 (after a save)
│  Alerts       │                                                                                              │
│ Record        │                                                                                              │
│  Audit log    │                                                                                              │
```

The same pattern on the other tabs (key elements only):

| Tab | Sections (hairline bands) | Filled button | Row actions |
| --- | --- | --- | --- |
| Members | Members table (member, role select, joined); Invitations (email + role form; open invitations table) | Create invitation | Remove (caution text), Revoke |
| Sessions | Your sessions table (device, signed in, last seen, this session marked) | none | Sign out that session (caution) |
| API keys | Keys table (name, project, scopes, created, last used); Create a key form; the secret shown once in a mono hairline box with Copy | Create a key | Revoke |
| Policies | Scope select on the h2 line; Editor (textarea, gutter); Issues list; Dry run (results band); Publish (comment field); History (disclosure, diff) | Publish | Start from the example, Dry run (outline) |
| Capture and detectors | Capture mode (radios with sentences); Detectors (checkbox list with mutes); Destination allowlist (list + add field) | Save | Remove host (caution) |
| Retention (19b) | One row per data class: name, current days, default, override field, "what a purge removes" sentence once in the lede | Save retention | none |
| Pricing | Prices table (model, input, output, source); add override form | Save prices | Remove override |
| Alerts | see section 5 | Add a rule | Edit, Delete |
| Audit log | Filters as a chip row (actor, action, when); table (when, actor, action, target, request id) | none | Load more |

### 768 / 375

```text
768: the sub-nav collapses into "Settings: Quotas ▾" on the title line (a disclosure listing the groups); each quota row wraps to
     two lines (name + usage / bar + state + field).
375: each quota is a block: name 600 / "41,200 of 50,000 today (82 %)" / 4 px bar full width / "◐ Near limit" / Limit field full
     width; Save limits 44 px full width at the section end; the lede collapses to its first sentence with "More ▾".
```

### Visual hierarchy

1. Primary: the section rows (the data the person came to change). Secondary: the one filled button. Tertiary: lede and help.
   CTA at the end of the section it saves, right-aligned, so reading order is read → change → save.
2. h1 24 once; h2 18 per tab; row names 13 600; usage mono 13; help 13 ink-2; the sub-nav 13 with group names small ink-2.
   32 px between sections; 8 px inside a row.
3. Sub-nav 192 px + page fluid, capped at 960 px for forms (tables may be wider); label column 192 px inside a section; a single
   left edge for all control columns on a tab.
4. h1, h2, h3 only for a sub-section inside a band (e.g. "Arguments"); never a fourth level.
5. Accent: the filled button, links, focus; caution: Near limit, destructive text; ink 600 for "Limit reached"; no red (a quota is
   not a failure, ADR-154's wording), no cards, no fills behind rows.
6. Focal point: the first row of the first section (on Quotas, "Runs per day" if it is the one in caution: the pattern lets a
   caution word be the only coloured thing). Path: h2 → lede → rows → the button.
7. As above.

**Loses versus today (A.5).** Four cards with 6 px radii and surface fills, the 999 px progress bars (now 4 px rules), the
notice banner inside a card (now a sentence under the row), the form below the cards (now the field in the row), the 8/9-tab
horizontal sub-nav (now grouped and vertical), the "Dashboard"-style lede with its link, 18 regions → 3 bands.

---

## 8. Login and no-access

**Purpose.** Get in, or learn why not. **Key elements.** Brand; display headline; optional one-sentence notice; one button; one
aside. **Primary action.** Sign in. **Secondary.** none (no-workspaces: Sign out). **Navigation.** none.

### 1440 / 768 / 375 (the same layout; the column is 400 px wide, centred, 96 px from the top at ≥ 600, 48 px at < 600)

```text
┌──────────────────────────────────────────┐
│ ⌁ Agent Black Box                        │ brand 14 600; glyph with the mark-coloured cut
│                                          │ 32
│ Sign in to see what                      │ display 32/40 600 (28/36 at < 600)
│ your agents did.                         │
│                                          │ 16
│ ⓘ No access yet. Ask a workspace owner   │ notice: one sentence 14/20 ink with a leading glyph; no bar, no fill (only when ?error=)
│   to invite you.                         │
│                                          │ 16
│ [ Sign in ]                              │ the one filled button, 36 px, 120 px wide (full width at < 600)
│ You sign in with your organisation's     │ aside 13 ink-2
│ identity provider.                       │
└──────────────────────────────────────────┘
Variants on the same frame: "No workspaces yet" (display headline, sentence, [Sign out] outline); "Workspace unavailable" (sentence +
"Reload to try again"); not-found (headline "This page isn't here", sentence, [Go to your runs]; the tape illustration stays, 48 px
tall, ink-2); error boundary (headline, sentence with the digest in mono, [Try again] [Go to your runs]). Invite: headline "Join demo",
sentence "Signed in as you@example.com", [Accept invitation]; signed-out variant: [Sign in to accept].
```

Hierarchy: primary the headline; CTA the button; focal point the headline; path brand → headline → notice → button. Colour: accent
on the button only; the notice is ink (today's amber left bar and fill go); the brand's cut carries the mark. Loses versus today:
the bordered card (`main.auth` border; now a column on the canvas), the amber notice bar and fill, the hairline under the brand.

---

## 9. Acceptance against the pipeline

- One dominant focal point per screen: Runs = the first red dash (or the h1); Run = the red line; Compare = the divergence rule;
  Approvals = the open request's capability; Alerts = the first rule name; Security = the first filled shield; Settings = the first
  row of the first section; Login = the headline. Nothing on each screen is larger, bolder or more saturated than its focal point.
- The same settings pattern is reused for all nine tabs (section 7's table).
- Wireframes at 1440, 768 and 375 for every priority screen, with the breakpoint rules in section 0 and each screen's reflow line.
- Each screen states what it loses versus today, with the audit reference.
- Counts after, for the step 6 review to check against `01-direction.md` A.7: Runs page regions 4 (bar, field, facets, table) vs 13
  + 17 controls; Run page bands above the first row 4 (bar, title, headline, figures, tabs = 164 px) vs 6 (262 px); red elements on a
  failed run 3 (word, line, label) vs about 20; type sizes on the run page 5 vs 14; typefaces 2 vs 4; nav items 5 vs 8; tab styles
  1 vs 4; settings regions on Quotas 3 vs 18.
