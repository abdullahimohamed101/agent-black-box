# Phase 21 plan review (adversarial, before implementation)

Reviewed: `docs/plans/active/phase-21-ui-shell-and-design-pass.md` as refreshed on 2026-10-12 against `main` `2c07adc`. Method: read the plan, `docs/design/ui-direction.md`, every file the plan names, every Playwright spec (`apps/web/e2e/*.spec.ts`) and vitest file (`apps/web/tests/*`), and grepped the selectors they use. Nothing was run; every "would fail" below is reasoned from the spec text, not observed.

Severity: P0 = the step cannot land as written (a locked suite fails or a rule is broken); P1 = ships a defect or a security/accessibility regression; P2 = quality or maintainability; P3 = nit. **Applied** means the plan was edited to resolve it; **Open** means a decision is recorded as `[DECIDED: …]` and the user can overrule it.

## Summary

The 2026-10-11 plan was written for a tree three phases older. Its central move (tabs replacing the stacked sections and the radio view switch) is incompatible with the five locked E2E suites, which read Story, Agents, Findings, Evaluations and the timeline in the same test and call `getByRole("radio", { name: "Waterfall" }).check()`. The refreshed plan keeps the application-frame outcome (rail, top bar, full width, instrument header, viewport-filling work area, right inspector) and moves the four sections into a left context column instead of tabs, keeps the radios as a tab-styled segmented control, and adds Evidence as a third view. Five P0s and six P1s were found and applied; eight decisions remain open with a default.

## P0 (applied)

| # | Finding | Evidence | Edit applied |
| --- | --- | --- | --- |
| P0-1 | Tabs that unmount Story / Findings / Evaluations break the locked suites. | `findings.spec.ts` 28-45: reads `findings` panel, clicks "Show evidence", then expects `option[selected]` visible, no tab click. `eval.spec.ts` 34-61 and 160-178: `getByTestId("evaluations")` and `form "Record a verdict"` visible right after `goto`. `coding.spec.ts` 32-53: `story` list items right after `goto`. `multiagent.spec.ts` 69-87: `agent-tree` buttons, then `.tl-agent` in the timeline, then `radio "Waterfall"`. `fixtures.spec.ts` 148-170, 252-326 likewise. | D2: context column, all four sections mounted and visible on load, DOM order unchanged. Old step 5 rewritten. |
| P0-2 | Replacing the radio view switch with `role="tab"` requires editing locked specs. | `multiagent.spec.ts` 82, 118, 123, 140; `fixtures.spec.ts` 257, 299, 306, 324, 333 (`toBeChecked`); `run-view.test.tsx` 28, 30, 36, 47, 76 (also `heading "Timeline" | "Waterfall"`). | D1: radios kept, styled as tabs; Evidence as the third radio; `#tl-h` keeps the view name. |
| P0-3 | Moving `data-testid="first-error"` onto the "Show it" button loses the error text the specs read through it. | `fixtures.spec.ts` 97 `toContainText("run_migration")`; `coding.spec.ts` 52 `toContainText("exit 1")`; `run-detail.test.tsx` 49-50 `toHaveTextContent("run_migration")`, 64; `run-view.test.tsx` 88. | D4: the test id wraps the first-error sentence plus the control inside the headline; `headline()` unchanged (4 direct assertions at `run-detail.test.tsx` 178-183). |
| P0-4 | Renaming `.stat` to `.ro` breaks the locked analytics suite. | `analytics.spec.ts` 31-32 `scope.locator(".stat").filter({ hasText })`, used at 44-45 for "Success rate" and "Cost". | D3: `.stats` / `.stat` kept as the readout strip's class names; the rules are rewritten in place. |
| P0-5 | The plan's effort/step list and file/line references were stale (written at `b368182`). | `RunDetail.tsx` is 487 lines with `Findings` at 351, `Evaluations` at 358, the banner at 339-349, the split at 449-482; `globals.css` is 1,012 lines (`.badge` duplicates at 201 and 534); `search.css` and `compare.css` exist; `AppShell.tsx` has the Experiments link; `RunsList.tsx` has `SearchBox`, `QueryChips`, `Facets`, `UnindexedNotice`, the compare bar. | "Current architecture" and "Affected files" rewritten with the real lines; the "When it lands" and "Avoiding conflicts" sections removed; the step count is 11 with per-step effort. |

## P1 (applied)

| # | Finding | Evidence | Edit applied |
| --- | --- | --- | --- |
| P1-1 | White-on-accent pressed buttons fail contrast in dark mode with the cyan accent (1.8:1), so axe fails the analytics window toolbar and the waterfall toolbar. | `globals.css` 706-710 `.toolbar button[aria-pressed="true"] { background: var(--accent); color: #fff }`; `analytics.spec.ts` runs axe after pressing "Today". Dark accent `#62c6e8` vs `#fff`. | D12: pressed = `var(--sel)` background, `var(--accent)` border, `var(--fg)` text (what `.wf-toolbar` already does). |
| P1-2 | The direction's `.cmd` class for the top-bar search slot collides with `pre.cmd` (shell command panel). | `globals.css` 602-613 `.term, .cmd`; `CodingSections.tsx` 184 `className="cmd"`; `coding.test.tsx` `querySelector("pre.cmd")`. | D11: `.topbar-crumbs`; and D6 removes the fake search slot altogether. |
| P1-3 | "Inspector tabs that scroll to sections" is `role="tab"` without `role="tabpanel"`: invalid ARIA that axe (`aria-required-children`) and screen readers would flag; it also changes `getByRole("tab")` queries later. | Old step 7. | D8: no inspector tabs this phase; condensed section labels; real panels as a follow-up. |
| P1-4 | A wide inspector (`data-wide` 44rem) plus a context column leaves the pane under 200 px at 1280 (Playwright's default), so the waterfall would overflow its own scroller and the keyboard specs would run against a sliver. | `globals.css` 531-533 `minmax(24rem, 44rem)`; width arithmetic: 1280 − 224 − 288 − 704 < 100. | D8 drops `data-wide` (26rem + CSS resize); D2's auto-collapse to a strip below 100rem gives the pane >= 764 px at 1440 and >= 604 px at 1280 with the inspector open; D9 container queries make rows adapt to the pane. |
| P1-5 | The row compaction rules are viewport-based, so a narrow pane beside the inspector would keep the wide row layout and overflow. | `globals.css` 514-524 and `waterfall.css` 374-383 use `@media (max-width: 52rem)`. | D9: `.pane { container-type: inline-size }` and `@container (max-width: 52rem)` for both rules. |
| P1-6 | Downloading font files is an action that needs the user's approval under the session's rules, and the plan said nothing; `public/` does not exist and the Dockerfile only copies `.next/standalone` and `.next/static`. | `apps/web/Dockerfile` 18-19; no `apps/web/public`. | D5: `next/font/local` (files bundled into `.next/static/media`, so the Dockerfile needs no change); the plan tells the implementer to ask before downloading; OFL.txt vendored. |

## P2 (applied unless marked open)

| # | Finding | Evidence | Edit |
| --- | --- | --- | --- |
| P2-1 | Sticky `th` cannot work inside `.table-wrap { overflow-x: auto }` (the nearest scroll container) and removing the wrapper breaks the 375 px rule. | `globals.css` 179-181, 1009-1012; `fixtures.spec.ts` 213-230. | Applied: D10 drops sticky headers. |
| P2-2 | A sticky run header plus the stacked mobile layout eats a short screen; the direction's 190 px cap is viewport-independent. | `fixtures.spec.ts` 213 uses 375×812; laptops at 768 px tall. | Applied: D10: no `position: sticky`; the app-page column keeps the header in place; stacked mode also at `max-height: 36rem`; AC8 caps the header at 190 px at 1440×900. |
| P2-3 | `aside.rail[aria-label="Primary"]` and `nav[aria-label="Primary"]` give two landmarks the same name. | Direction Part 1E §2. | Applied: the rail has no label; only the `nav` is "Primary" (`fixtures.spec.ts` 503 reads it). |
| P2-4 | The `WorkspaceSwitcher` dropdown is `position: absolute`; inside a rail that scrolls it would be clipped. | `globals.css` 820-836. | Applied: step 2 makes the list in-flow; `.rail` keeps `overflow: visible` with the nav scrolling. |
| P2-5 | The `LiveIndicator` "REC · n running" count is not obtainable from `useRuns` (cursor pagination, no total); the summary endpoint has it but polls every 60 s. | `queries.ts` 36-60, 221. | Applied: D13 shows "Recording" / "Idle" only, from the existing 15 s poll with `limit 1`. Open: drop it entirely. `[DECIDED: keep; remove if a reviewer of the diff objects]` |
| P2-6 | The scratch `contrast.mjs` is not a verification artefact; "run it against any new colour" is not command-checkable. | Old step 1. | Applied: D16 `tests/tokens.test.ts` parses `globals.css` and asserts the pairs. |
| P2-7 | `fullPage` screenshots on a page whose document does not scroll capture only the viewport; the locked suites write such screenshots into `docs/screenshots/phase-6|9|10|11`. | `coding.spec.ts` 53, 69, 94; `findings.spec.ts` 44, 56, 70; `multiagent.spec.ts` 66, 126. | Applied: D15 accepts this and the README says so. Open alternative: keep the run page a scrolling document and only cap the lists. `[DECIDED: app-page (viewport-filling) for the run page; it is the point of the phase]` |
| P2-8 | "Before" screenshots cannot be taken after the first style commit; the old plan had no mechanism. | Old "Verification plan". | Applied: `e2e/shots.spec.ts` lands in step 1 before any style change and the `before/` set is committed in that step. |
| P2-9 | The old step 8 moved the status chips into a facet column and reserved a "search slot" for Phase 16; Phase 16 is merged and renders `Facets` only for searches that returned facets, so a column would usually be empty. | `RunsList.tsx` 172, `Facets.tsx` 20-30. | Applied: runs list = toolbar rows + full-width table; facets a strip. |
| P2-10 | KI-108 and KI-109 name Phase 21 as their target; the old plan did not consider them. | `KNOWN_ISSUES.md` 100-101. | Applied: "Known issues considered" defers both with reasons. |
| P2-11 | The context column collapsing when an event opens (below 1600 px) is a behaviour change a user may find surprising. | D2. | Open. `[DECIDED: auto-collapse to a strip with a one-click restore and a header toggle that pins it open; revisit with the user after the screenshots]` Alternative: never auto-collapse and accept a 320 px pane at 1280. |
| P2-12 | The rail's "Workspace" link group (mockup) duplicates `SettingsNav` and would add a second landmark with the same links. | `SettingsNav.tsx`; `auth.spec.ts` 238. | Open. `[DECIDED: one "Settings" rail link; the sub-nav stays as the settings tab row]` |
| P2-13 | Top-bar search (⌘K) with no behaviour is a fake affordance; a real one would move `SearchBox`'s state out of the runs page the specs drive. | `search.spec.ts` whole file; `RunsListPage.tsx` URL state. | Open. `[DECIDED: no top-bar search this phase; `SearchBox` stays on the runs page as a full-width command-style input]` |
| P2-14 | `.timeline` / `.wf-scroll` with `flex: 1` need a definite height chain; in stacked mode there is none. | `Timeline.tsx` 45-48 virtualizer scroll element. | Applied: stacked mode restores `height: min(68vh, 44rem)`; AC8 checks `clientHeight > 300` in app mode. |

## P3 (applied)

- The radio inputs of the segmented control must stay "visible" to Playwright: `opacity: 0` over the full label, never `display: none` / `visibility: hidden` / 1×1 clipping (`.check()` needs an actionable element). Written into step 5's "must not break".
- `focusTimeline()` finds no listbox in the Evidence view; step 7 adds the region fallback so Escape still returns focus.
- `--info` stays defined (used by `compare.css` 170 and `globals.css` 963-965).
- The mobile rail strip must keep the switcher and sign-out reachable (scrollable strip), since `auth.spec.ts` signs out from a desktop viewport only but a phone user needs it too.
- The old plan's "9 call sites" for the radio were 12 (6 + 3 in e2e, 3 in vitest plus `toBeChecked`); moot after D1.

## Review by concern

### Sequencing
Critical path 1 → 2 → 4 → 5 → 6 → 7 holds; 3, 8, 9, 10 are independent of each other after 2. Step 5 is the only L; it deletes `.split*` and moves four components into a new wrapper, so it must land with `fixtures.spec.ts` entirely green before 6 restyles the inspector (6 depends on `.work`'s third column existing). Step 10 is last because `data-arrived` depends on the final row markup. The before-screenshot set is captured in step 1, which is why `shots.spec.ts` precedes any CSS change.

### Behaviour and security surface
- No endpoint, proxy allowlist, permission or redaction change. The Evidence view is a client-side projection of already-loaded, already-withheld events; it uses `contentText` so a viewer sees the withheld wording. AC11 checks the viewer-only fixture workspace in all three views and the inspector.
- Fonts: vendored, same-origin, served by Next from `/_next/static/media`; CSP unchanged (`default-src 'self'` covers `font-src`); AC10 asserts no request leaves `localhost:3100`. License: OFL 1.1, file committed.
- `LiveIndicator`: one extra query per page with the existing cache key; bounded interval; no new data exposure (the runs list already shows RUNNING runs to anyone who can list runs).
- No new dependency; `next/font/local` is part of `next`. The five dependency questions: platform provides it (yes), maintained (part of Next), surface area (one import), local alternative (hand-written `@font-face` + preload links: more code, no fallback metrics), lock-in (none beyond Next, already chosen).
- Untrusted text stays text: the Crumbs component renders the run name from the query cache as a text node; `h1 b` checks remain.

### Accessibility
- Focus order: skip link → rail (switcher, links, sign out) → top bar (crumbs are text; indicator is `role=status`) → main. The rail being first in DOM order is correct for a sidebar; the skip link still jumps to `main`.
- Keyboard model unchanged (lists own their keys). The segmented control is a native radio group (arrow keys move, Space selects). The context toggle is a button with `aria-pressed` and `aria-controls`. The inspector's `resize` is pointer-only, which is acceptable because the default width is usable.
- Reduced motion: one token rule zeroes everything; AC13.
- Contrast in both schemes: `tokens.test.ts` (every text role on bg, surface, sel, and each tone on its tint) plus axe in both schemes on 11 routes (AC5). The agent colour chips and waterfall bars keep their existing palette (unchanged risk).
- Landmarks: one `navigation "Primary"`, one `"Settings"` sub-nav on settings pages, `aside.context[aria-label="Run context"]`, the `dialog` inspector, `region` for the evidence table.

### Performance
- Virtualization untouched; the scroll elements keep definite heights in both modes (P2-14). AC8/AC12 keep the 10,000-event checks. The arrive tint is `background-color` only, bounded to 200 ids, and only rows the virtualizer renders get the attribute. Container queries cost nothing measurable at this DOM size. Fonts: three preloads, `swap`; the navigation timing is recorded on RUN 6 before and after.

### Sticky header on short screens
Resolved by D10: no sticky; the header is static in a fixed-height column; `max-height: 36rem` switches to stacked (document) mode. Open check for the manual pass: 1024×640 still uses the app layout (640 px > 576 px); the plan's manual pass includes that window.

### Tokens vs `compare.css` / `waterfall.css` / `search.css`
All three only read the kept token names (`--bg --fg --muted --border --accent --surface --sel --ok --bad --warn --info --bad-bg --warn-bg`) plus `waterfall.css`'s own `--wf-*`. Hard-coded radii (`999px` in `search.css` 67, `6px` in several) are changed in step 3 in their own files, not overridden. The `--wf-*` categorical palette is kept on purpose (data colours are not UI accent colours); it was not re-measured against the new backgrounds, so the axe runs on RUN 7 in both schemes are the check (chips are text; bars are not).

### "Looks like an application": objective checks
The plan's nine-point checklist runs in `design.spec.ts` (geometry, landmarks, no document scroll on the run page, one red/one amber in the header, radius and shadow scans, font faces, density numbers, no first-paint animation, inspector flush right). The subjective half is the paired screenshot set the user reviews.

### What is still missing
- A decision on the brand mark (placeholder glyph) — unchanged `[NEEDS INPUT]` from the direction.
- The demo video is out of scope (storyboard exists; WebM decided).
- The locked suites will be run only after steps 5, 6, 8 and 11; if the environment cannot free the ports, those criteria are `UNVERIFIED (env)` in the completion report, never PASS.

## Open decisions (defaults recorded in the plan)

1. Context column auto-collapse below 1600 px when the inspector opens — `[DECIDED: yes, with a strip button and a pin toggle]` (P2-11).
2. Rail "Workspace" link group vs one Settings link — `[DECIDED: one Settings link; sub-nav unchanged]` (P2-12).
3. Top-bar search — `[DECIDED: none this phase]` (P2-13).
4. `LiveIndicator` — `[DECIDED: keep, text-only Recording/Idle]` (P2-5).
5. Run page as viewport-filling `app-page` (viewport-sized `fullPage` screenshots) — `[DECIDED: yes]` (P2-7).
6. Runs list as a document page (no inner table scroll, no sticky `th`) — `[DECIDED: yes]` (P2-1, P2-9).
7. Inspector: fixed 26rem + CSS `resize`, no `data-wide` — `[DECIDED: yes]` (P1-4).
8. If the user is willing to let the five locked specs change, the radio switch and the context column can become real tabs in a follow-up phase — `[DECIDED: not in this phase; the specs stay untouched]`.
