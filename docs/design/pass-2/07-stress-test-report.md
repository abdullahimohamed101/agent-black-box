# Design pass 2, step 6: the length-expansion stress test

Status: run 2026-10-16 on `feature/design-pass-2` against the fixtures build. Method: prompt 3 of `design-pass-2-prompts.md`.

## 1. How it was tested

- **Mechanism (test-only, nothing ships):** `apps/web/e2e/pseudo-localise.ts` rewrites every text node after hydration and keeps
  rewriting what React renders later (a `MutationObserver`). Each run of letters grows by 30%, 50% or 70% with filler letters, so
  spaces stay where they were and lines wrap as they would in another language. The fourth pass is +70% plus a 60-character
  unbroken token (`customer_support_escalation_...`) appended to names: page titles, a table's first-column link, code. It covers
  the typed copy modules in `src/copy/`, fixture data (agent, model and run names, verdict sentences) and server strings alike.
- **Screens (27 URLs):** Runs (and filtered), a failed and a finished run, Compare, Approvals (and an open request), Security
  findings, all ten Settings tabs, Alerts History and Destinations, Analytics (three tabs), Experiments, an experiment with a
  verdict, an experiment with nothing to compare, sign-in. Each at 1440, 768 and 375, light and dark: **162 tests, 4 passes each.**
- **Checks per pass** (`layoutProblems`): no sideways page scroll (`html` and `body`); nothing outside the viewport unless a
  scroll region owns it; no clipped text (hidden overflow with text cut off) except a designed ellipsis that carries a `title`;
  no text over text and no control over control; the top bar keeps its controls inside the viewport, not over each other and under
  150 px tall. The +70% and names pass also runs axe (wcag2a, wcag2aa; serious and critical) and the console must stay clean.
- **Allowances of the checker** (`e2e/pseudo-localise.ts`, review finding P3-10): an ellipsis counts as designed when the
  element carries a `title` (the "clipped" check) and, for the "ellipsis without a full text" check, a `title` or an
  `aria-label`; elements inside `[hidden]` or `[aria-hidden='true']` are skipped (decorations and chart marks, whose text
  equivalent is a table that is checked), as are closed `<details>`, an off-screen skip link and visually hidden boxes. So a
  `title` on a parent does not count for a child, and a clipped mark inside an `aria-hidden` chart is not reported.
- **Baseline:** a +0% pass of the same checker was run by hand on every screen (not part of the spec) to separate what the
  expansion breaks from what was already wrong.

## 2. What broke, where, and what was done

| Screen / widths | Broke at | What | Fix |
| --- | --- | --- | --- |
| Top bar, 375 (every screen) | +50% | The scope button (`demo / All projects`) ignored its container and pushed under the search icon: `.scope-button { max-width: 20rem }` replaced the `max-width: 100%` of the shared rule | `max-width: min(20rem, 100%)`; the same for the person button; the scope name carries a `title` |
| Page titles, 768 and 375 (Runs, Review, Analytics, Experiments) | +70% with a long name | An unbroken token in an `h1` made the page 897 px wide and pushed the Compare button out | Global `overflow-wrap: anywhere` on `h1`, `h2`, `h3` (the title line already had it) |
| Compare, 1440 | +50% | The "First outcome differs" badge (nowrap) ran out of the 11 rem step column over the step text | The badge wraps inside the flags row; grid items of the aligned rows get `min-width: 0` |
| Analytics Cost, 768 | +30% | The chart figure column (`auto`) took the width of a long note and the note overlapped the bar label | Third bar column is `fit-content(14rem)` and the figure may wrap (the 14 rem cap was already the phone rule) |
| Analytics, 375 | +70% with names | A wide table's scroll region was not keyboard focusable (axe `scrollable-region-focusable`) | The four Analytics table wrappers are focusable labelled regions |
| Timeline, story, waterfall, evidence, scope and person (all widths) | already at +0% | Designed ellipsis with no way for a pointer to read the cut text | `title` on each truncated element (KI-299 for the keyboard case) |

Re-checked as the brief asked, all clean after the fixes at every expansion: the **long verdict sentence and the 14 rem
bar-value column** in Experiments (1440, 768, 375); the **stacked experiment tables** below 600 px (each cell keeps its
`data-label` line); the **global table padding** and the **h1 and h2 sizes** on the run page, Compare and every Settings table at
+70%.

## 3. Result

`e2e/stress-expansion.spec.ts`: 162 passed. Full fixtures Playwright: 820 passed, 111 skipped (the real-API specs). No test was
weakened; the checker's allowances are: scroll regions that are designed (`pre`, `textarea`, `.table-scroll`, `.table-wrap`,
`.tabs`, the Compare toolbar), the off-screen skip link, the content of a closed `<details>`, the visually hidden chart tables,
and a lone glyph (a disclosure triangle) whose box is a fallback font's line box.

## 4. What remains

| KI | Sev | What |
| --- | --- | --- |
| KI-297 | S3 | A proxy, not translations: no RTL, no tall-script line boxes, no real compounds; the product is English only |
| KI-298 | S4 | The tab strip (and Compare's toolbar) scrolls with the scrollbar hidden: later tabs have no visible hint on a narrow phone |
| KI-299 | S4 | Truncated rows give the full text in `title` only; a keyboard user gets it in the inspector |
| KI-300 | S4 | Other `.table-wrap` regions rely on their fixtures fitting; a long value could trip the focusable-region rule |
