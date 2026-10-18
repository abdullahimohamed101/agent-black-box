# Design pass 2, step 5d: the consistency report

Status: written 2026-10-16 at the end of step 5d. Step 6 (stress test, review, PR) extends it. "Before" is the audit of
`01-direction.md` Part A where that audit counted the screen, and the code read for Analytics and Experiments, which it did not
count. "After" is `docs/design/pass-2/scripts/count-step5d.mjs` run against the fixtures build at 1440 x 900 light (workspace `demo`);
the screens are the list in `docs/design/pass-2/scripts/screens5d.mjs`. Regions are landmarks, tables, forms, fieldsets, tab lists and any
element with its own border box or fill (table cells and list items aside), so a hairline-separated block counts as one.

## 1. Every screen, after

| Screen | h1 at (x, y), size / weight | Table head | Regions | Font sizes | Weights | Tab lists | Interactive targets | Fault-coloured elements |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Runs | 24, 68 24 px / 600 | 12 px ink-2, 12 px inset | 6 | 4 (12 13 14 24) | 400, 600 | 0 | 62 | 12 (the failed words and dashes of 18 rows) |
| A failed run | 104, 68 (after "Runs /") 24 / 600 | no table | 26 | 5 (12 13 14 18 24) | 400, 600 | 1 | 20 | 3 (the fault line, its label, the status word) |
| Compare | 104, 68 24 / 600 | 12 px ink-2, flush | 10 | 5 | 400, 600 | 0 | 17 | 4 (two status words and their glyphs) |
| Review, Approvals | 24, 68 24 / 600 | 12 px ink-2, flush | 6 | 4 | 400, 600 | 2 | 20 | 0 |
| Review, Security findings | 24, 68 24 / 600 | 12 px ink-2, flush | 6 | 4 | 400, 600 | 1 | 36 | 0 |
| Settings, Members | 137, 68 (after "Settings /") 24 / 600 | flush | 8 | 5 | 400, 600 | 0 | 27 | 0 |
| Settings, Sessions / API keys / Capture and detectors / Quotas / Retention / Pricing / Audit log | 137, 68 24 / 600 | flush where there is a table | 5 / 8 / 14 / 10 / 7 / 8 / 5 | 5 each | 400, 600 | 0 | 20 to 35 | 0 |
| Settings, Policies | 137, 68 24 / 600 | flush | 11 | 5 | 400, 600 | 0 | 29 | 0 |
| Settings, Alerts | 137, 68 24 / 600 | flush | 6 | 4 | 400, 600 | 1 | 30 | 0 |
| **Analytics** | 24, 68 24 / 600 | 12 px ink-2, flush | 14 | 5 (12 13 14 18 24) | 400, 600 | 1 | 25 | 8 (failed and blocked words in the run tables only) |
| **Experiments** | 24, 68 24 / 600 | 12 px ink-2, flush | 6 | 5 | 400, 600 | 0 | 21 | 0 |
| **An experiment** | 185, 68 (after "Experiments /") 24 / 600 | 12 px ink-2, flush | 12 | 5 | 400, 600 | 0 | 23 | 2 (the Failed word of an attached run) |
| Sign-in | 536, 152 32 / 600 (display) | no table | 1 | 2 (14 32) | 400, 600 | 0 | 2 | 0 |

What the table shows: one h1 size (24 / 600) and one h1 baseline (y = 68) on every page inside the shell; the sign-in page alone
uses the display step. Every page uses the same four to five type sizes out of the seven steps; weights are 400 and 600 on every
screen. A fault-coloured element exists only where a run failed (a status word, the fault line); Review, Settings, Analytics
charts and Experiments' own elements have none.

## 2. Before and after, by screen

| Screen | Before (01 Part A) | After |
| --- | --- | --- |
| Overview (now the top of Runs) | 13 regions, 5 stat blocks, 2 tables with 18 upper-case heads, 7 hues, 12 type sizes, no focal point | merged into Runs: one sentence above the list; 6 regions, 4 sizes |
| Runs list | 3 filtering systems, 17 controls before the first row, 5 status hues, 11 red elements | 1 search field and 1 facet column; 6 regions; the status words are the only fault colour |
| Run detail (failed) | 6 bands above the first event (262 px), 9 readouts, 7 identity items, about 20 red elements, 14 type sizes, 4 typefaces | 26 regions (the timeline rows count), 5 sizes, 2 families, 3 fault-coloured elements, the first row is one headline and one tab list below the title line |
| Compare | not counted in 01 | 10 regions, 5 sizes, the divergence sentence as its headline; the 16.2 px divergence name (an `em` size) was found by this sweep and moved to the 14 px step |
| Approvals | rail 5 + chrome, upper-case heads | 6 regions, 4 sizes, 2 tab lists (the page's tabs and the state chips), 0 red |
| Security findings | filled severity chips | 6 regions, 4 sizes, 0 red |
| Alerts | 12 regions for 2 rows, 7 upper-case heads, a third tab style, 6 buttons for 2 rules | 6 regions, 4 sizes, the shared Tabs |
| Settings (one tab) | 9 tabs, 3 visual languages on one tab, 18 regions on Quotas | 10 tabs in 5 named groups, one pattern; Quotas 10 regions |
| **Analytics** | three stacked sections (Cost, Reliability, Performance) under one lede, 11 readout tiles in three bordered strips, about 10 h3 headings, a red column bar chart, no headline, no tab list, the exclusion note a muted paragraph, 4 type sizes of body text | one title row with one control row, **1 tab list, 1 sentence per report**, hairline blocks, thin ink and neutral marks, **no red** (8 fault-coloured elements are the failed words of run rows), the exclusion note is the shared notice |
| **Experiments** | an `h1` with a paragraph, a table with muted descriptions, the create form open under the table, `compare-table` padding unlike every other table | title, one sentence, hairline table, the create form as a hairline section; a table row like every settings row |
| **An experiment** | a back paragraph, an h1, three banners, four sections of tables with a 700-weight row head on every metric, no statement of which variant did better | the title line, **one sentence that names the winner in words**, one notice, a small chart (ink for the lead), hairline blocks, stacked tables below 600 px |
| Stylesheets | 33 font sizes, 4 families, 4 weights, 12 radii, 9 tracking values, 14 upper-case rules, 5,671 lines in 22 files | font sizes: the 7 tokens plus the 13 px body base, the 20 px phone headings and the 28 px phone sign-in headline; 2 families; weights 400 and 600; radii 0, 4 px and circles; tracking 0; no upper case (all asserted by `tests/type-scale.test.ts`); 6,275 lines in 32 files (more lines, because the system is now many small files, not layers: nothing wins by specificity over an older phase) |

## 3. Inconsistencies found by the sweep, and fixed

1. **Compare's tables** inset their first column 12 px while every other table is flush: `.compare-table` padding now matches the
   global rule, with the last column flush right.
2. **Compare's divergence name** was 16.2 px (`0.9em` of the 18 px sentence), the only off-scale size left in the app: now the 14 px
   mono step. The type-scale test's shorthand rule was tightened (it accepted any `var(--type-...-weight)` token, so the literal
   size passed) and now requires a size token.
3. **Table padding** was `0.25rem 0.6rem` globally and `0.5rem 0.75rem 0.5rem 0` in Settings: one rule now (space-2 vertical,
   space-3 on the right, flush left and flush on the last column), so Review, Analytics, Experiments and Compare align with their
   titles and hairlines.
4. **The global h1 and h2** were `1.4rem` and `1.1rem` behind a legacy `.page:not(...)` override: the globals are now the h1 and
   h2 steps and the override is deleted.
5. **Radii of 6, 3 and 2 px** (`.panel`, a security skeleton, a waterfall bar) moved to 4 px or 0.
6. **Analytics' stat tiles, bordered strips and red column bars** were the last place the Phase 21 readout strip, a second chart
   language and the red error column survived: all deleted (`.stat*`, `.stats`, `.page-head`).
7. **A phone**: the bar rows of a chart overflowed 25 px at 375 px (the figure column with its note is nowrap): the track now runs
   under the label and figure and the figure may wrap. Experiment tables stack into blocks below 600 px (a 5-column table was
   clipped inside its scroll region).
8. **Empty states** on Analytics (cost, wasted spend) and Experiments drew the trace motif: `Empty` has no motif now.

## 4. Differences that remain on purpose

- **The Runs table is denser than the others** (32 px rows, 12 px cell inset) because it is a scanning list with a facet column
  beside it and a selection column; every other table has 40 px rows and is flush with the title.
- **Section headings**: Settings sections are h2 at 18/24 (they are the content); Analytics and an experiment open with an 18/24
  sentence and their blocks are h2 at the 14/20 step (KI-294).
- **Title lines**: pages under Runs, Settings and Experiments show "Parent / name" and so the h1 starts further right; the h1 is
  24 / 600 at y = 68 on all of them.
- **Sign-in** is the display step (32 / 600), the only page without the shell.
- **Fault colour** is on the failed word of a run row wherever a run row is listed (Runs, Compare's side cells, Analytics' run
  tables, an experiment's attached runs): that is the rule, not an inconsistency.

## 5. The final state of the old system

- `tokens.aliases.css` is deleted (with its generator, its import and its test); no custom property outside the token file is an
  alias. The kept custom properties are defined next to their readers.
- The red allow-list in `tests/tokens-use.test.ts` is five entries: the badge status word of a failed, timed-out or blocked run,
  `.tl-fault` (line and label), `.wf-fault-*`, `.run-fail-dash`, and the `:root` token definitions. `tests/tokens-colour-rules.test.ts`
  pins the length at exactly 5, the red literals at zero and the red tokens at the primitives and `color.status.fault`.
- `Empty` has no motif. Drawings remain on the first-run guide, the not-found page and a not-found or expired-trace error state.
- `scripts/dead-css.mjs` lists class names with no literal reader in `src/**/*.ts(x)` (informational); the 16 it still lists are
  built from a prefix (`diff-*`, `tok-*`, `wf-kind-*`) or are read through a computed class name.
