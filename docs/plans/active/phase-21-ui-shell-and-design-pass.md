# Phase 21: UI application shell and design pass

Status: Planned 2026-10-11, not started. Decisions confirmed by the user 2026-10-11: after Phases 10, 16 and 11 merge; self-hosted IBM Plex; system light/dark only; page title
"Dashboard" with rail label "Overview"; Evidence as its own run tab; WebM video (MP4 needs an approved ffmpeg install). See `docs/IMPLEMENTATION_PLAN.md`, "Planned additions".


How to build the direction in `ui-direction.md` as a real phase in this repository. Written against `main` at `b368182` and the three worktrees (`abb-worktrees/phase-10`, `phase-16`, `phase-11`) as read on 2026-10-11. Nothing here has been run; every acceptance criterion is UNVERIFIED (env) until the phase executes it.

## Outcome

The web UI becomes an application: a persistent rail, a slim top bar with a search/command slot and live status, full-width content, a sticky instrument-style run header with tabs, and a right inspector that replaces the drawer card. Every existing role, test id, keyboard shortcut, virtualized list, SSE path and viewer-withheld state keeps working, and `scripts/quality.sh full` plus the Playwright suites stay green after every commit.

## When it lands

**After Phase 10, 16 and 11 merge, in that order (their plans already fix 9 -> 10 -> 16 -> 11).** Reasons, from reading their diffs and plans:

- Phase 10 (`abb-worktrees/phase-10`, 21 web files): +51 lines in `RunDetail.tsx` (Findings panel, `jumpTo`, remote drawer event), +115 lines in `globals.css`, +19 in `RunsTable.tsx` (findings column), `queries.ts`, fixtures.
- Phase 16 (plan `phase-16-search-investigation.md`, "Web" section): new `SearchBox`, `QueryChips`, `Facets`, `UnindexedNotice`; edits `RunsListPage.tsx`, `RunsList.tsx`, `RunsTable.tsx` ("Matched" column), `queries.ts::useRuns`, `fixtures/runs.ts`; appends `.facets`/`.unindexed` rules to `globals.css`.
- Phase 11 (plan `phase-11-evaluations.md`, "Web"): `RunDetail.tsx` (Evaluations block after Findings), `RunsTable.tsx` (Compare checkbox first cell), `AppShell.tsx` (Experiments link), new `app/compare.css` imported from `layout.tsx`, `queries.ts`, `fixtures/api.ts`.

This phase rewrites `AppShell.tsx`, the top of `RunDetail.tsx`, the layout half of `globals.css`, `RunsList.tsx` and `RunsTable.tsx`. Rebasing three feature branches over that would cost more than waiting. So: branch `feature/phase-21-ui-shell` from `main` after Phase 11 merges; until then this folder is the plan.

If the owner wants the shell earlier, the only safe subset is step 1 (tokens and fonts) because it touches only the first 80 lines of `globals.css` and `layout.tsx`, which none of the three phases edit. [NEEDS INPUT: wait (recommended) or ship step 1 early.]

## Ordered steps (one commit each; `scripts/quality.sh full` green after each)

Effort is in steps and sub-items, not days. Each step is one focused sitting for one engineer or agent.

### 1. `feat(web): design tokens, Plex fonts, tabular numerals` (effort: small)
- Files: `apps/web/src/app/globals.css` (lines 1-79: `:root` palettes, `body`), `apps/web/src/app/layout.tsx` (font preload links), `apps/web/public/fonts/*.woff2` (3 files: Plex Sans 400/500/600 as one variable file if available, Plex Sans Condensed 500/600, Plex Mono 400/500), `apps/web/src/app/fonts.css` (new, `@font-face`, imported from `layout.tsx` like `waterfall.css`).
- Token names in `ui-direction.md` Part 1E §1. Keep every existing token name; add the new ones. Remove the `main { max-width: 56rem }` rule here (it is overridden by `.shell main` already, so nothing changes visually yet).
- Must not break: nothing visual beyond fonts and colours; axe (colour contrast rule) on every spec.
- Verify: `pnpm --filter @abb/web test`, `pnpm --filter @abb/web test:e2e`, `node scratchpad/design/contrast.mjs` (copy into `apps/web/scripts/` if the team wants it in-repo), screenshot `fixtures.spec.ts` pages before/after.
- Decision recorded in an ADR? Yes, a short one: "ADR-110: self-hosted IBM Plex superfamily; no runtime font requests" (skill `write-adr`). [NEEDS INPUT: Google Fonts instead; the direction recommends self-hosting.]

### 2. `feat(web): application frame: rail, top bar, full-width main` (effort: medium)
- Files: `components/AppShell.tsx` (rewrite per Part 1E §2), new `components/shell/{ProjectSwitcher,Crumbs,LiveIndicator}.tsx`, `components/SettingsNav.tsx` (export the permission-filtered tab list so the rail reuses it), `globals.css` (`.shell`, `.rail`, `.topbar`, `.nav`, `.scope`, `.cmd`, mobile rule), `app/w/[workspace]/settings/page.tsx` etc. unchanged (they render inside `main`).
- Pages that should fill the viewport (`runs`, `runs/[runId]`) get `className="app-page"` on their root; others get `page`. Both classes live in `globals.css` (Part 1E §1).
- `ProjectSwitcher`: a `<details>` of links like `WorkspaceSwitcher` (same reason: no listbox roles on a page that has one). Options come from `useWorkspaceOptional().projects`, plus "All projects". In fixture mode without a workspace provider it renders the crumb text only.
- `LiveIndicator`: reads `useRuns({ project, statuses: ["RUNNING"] }, 1)` (already polled) and renders "REC · n running" with the orange dot, or nothing on pages where the provider is absent. `role="status"`, `aria-live="off"` (it changes often; the run page's `live-status` region already announces).
- Must not break: `getByRole("navigation", { name: "Settings" })` (the sub-nav still renders on settings pages), `getByRole("link", { name: "API keys" })`, `getByRole("button", { name: "Sign out" })`, skip link, `main#main[tabindex=-1]`, mobile: no horizontal scroll at 375 px (rail becomes a horizontal strip under the top bar).
- Verify: axe on `fixtures.spec.ts`, `auth.spec.ts`; new Playwright check: at `{ width: 375, height: 812 }` `document.documentElement.scrollWidth <= window.innerWidth` on `/w/local/projects/all/runs` and one run page; screenshots at 1440 and 375.

### 3. `refactor(web): one status grammar: badge tones, tags, row markers, link styling` (effort: small)
- Files: `globals.css` (delete the second `.badge` block at the old 534-555, replace the first; `.tag`, `.chip` radius 4 px; `td a` underline on hover/focus only), `components/CodingSections.tsx` (`badge-ok|bad|warn` -> `data-tone`), `waterfall.css` (`.tag-crit` colour, `.wf-chip` radius; bars untouched).
- Must not break: `StatusBadge` text (`✓ Success`, `✕ Failed`, `❙❙ Awaiting approval`) is asserted by text in specs; keep glyph + word.
- Verify: vitest `run-detail.test.tsx`, `coding.test.tsx`; screenshot the failed run: exactly one red element in the header.

### 4. `feat(web): run header: readout strip, id line, inline first-error control, sticky` (effort: medium)
- Files: `components/RunDetail.tsx` (lines 283-336 of today's file, plus Phase 10's Findings call and Phase 11's Evaluations block stay where they are for now), new `components/run/Readout.tsx`, `globals.css` (`.run-head`, `.run-title`, `.readouts`, `.ro`, `.headline`; delete `.stats`, `.stat`, `.banner*` after Dashboard/Analytics move in step 9: until then keep them).
- `data-testid="first-error"` moves to the inline "Show it" control; `getByRole("button", { name: "Show it" })` still resolves (the `kbd` hint is `aria-hidden`). `getByTestId("headline")` unchanged. `aria-label="Run summary"` moves from `dl.stats` to `dl.readouts`.
- Must not break: `live-status`, `partial-data`, `progress` test ids; the `notice` for summary state; `LiveBar` placement (inside the header, under the headline).
- Verify: `run-detail.test.tsx`, `live-run.test.tsx`, `stream.spec.ts`, `coding.spec.ts`; header height at 1440 <= 190 px (Playwright `boundingBox`).

### 5. `feat(web): run tabs replace the view switch; story, findings and evidence become tabs` (effort: medium)
- Files: new `components/run/RunTabs.tsx` (`role="tablist"`, `role="tab"`, `aria-selected`, arrow-key roving focus; `?view=` stays the source of truth and gains `story|findings|evidence`), `RunDetail.tsx` (render by tab: Story = `CodingSummary` + `AgentTree`; Timeline; Waterfall; Findings = Phase 10's `Findings`; Evidence = the artifact list from `CodingSections` data [NEEDS INPUT: whether Evidence is its own tab or stays inside the inspector; the mockup shows a tab]; Compare = disabled tab with "Planned" until Phase 11's compare page exists, then a link to it), delete `components/ViewSwitch.tsx`, update `tests/run-view.test.tsx` and the 9 spec call sites (`getByRole("radio", { name: "Waterfall" })` -> `getByRole("tab", { name: /^Waterfall/ })`, same for Timeline) in the same commit.
- Default tab: `story` when `isCodingRun(events)` or findings exist, else `timeline`; `?view=waterfall` links keep working.
- Must not break: `data-testid="story"`, `"agent-tree"`, `"waterfall"`, `"waterfall-empty"`, `"wf-zoom"`; `getByRole("button", { name: "Lanes" })`; `jumpToError` must switch to a tab that can show the event (timeline) when the current tab cannot.
- Verify: `run-view.test.tsx`, `multiagent.spec.ts`, `fixtures.spec.ts` (waterfall section), axe.

### 6. `feat(web): work area fills the viewport; timeline and waterfall lose the height cap` (effort: small)
- Files: `globals.css` (`.work`, `.pane`, `.timeline { flex: 1; height: auto }`), `waterfall.css` (`.wf-scroll` same), `RunDetail.tsx` (wrap in `.work`, set `data-inspector`).
- Must not break: the virtualizers' scroll element is still `.timeline` / `.wf-scroll` (they get the height from flex instead of `min(68vh, 44rem)`); `waterfall-perf.test.ts` row-count assertion; `scrollToIndex` on selection.
- Verify: `waterfall-perf.test.ts`, `timeline.test.ts`, the 10,000-event fixture page: DOM row count < 300, scroll to the end works, `j/k/PageDown` unchanged.

### 7. `feat(web): event inspector replaces the drawer card` (effort: medium)
- Files: `components/EventDrawer.tsx` (keep the name and props; add `insp-tabs` with Details / Attributes / Payload / Identifiers as `role="tablist"`, default Details; the sections stay in the DOM order they have now so `getByTestId("payload-withheld")`, `"diff"`, `"shell-panel"`, `"test-result"`, `"file-section"`, `"exit-code"`, `"risk-class"`, `"sensitive-path"` keep resolving: tabs scroll to the section rather than unmounting it [simplest; a later pass can switch to real panels]), `globals.css` (`.drawer` as a pane: no radius, no outer border, `border-left`, full height; `@starting-style` enter; mobile fixed overlay), `RunDetail.tsx` (remove `.split`, `data-wide`; the inspector width is `--insp-w`, with `data-wide` becoming `--insp-w: 44rem` for coding events).
- Must not break: `role="dialog" aria-modal="false" aria-labelledby="drawer-title"`, `getByRole("button", { name: "Close details" })`, Escape handling and focus return, viewer-withheld text, the 20,000-char payload clip.
- Verify: `coding.spec.ts` (viewer withheld, diff, shell), `auth.spec.ts` (`viewer-payload-withheld` screenshot), `stream.spec.ts`; reduced-motion check (`transition-duration` is `0s`).

### 8. `feat(web): runs list: search slot, facet column, dense table` (effort: medium)
- Files: `components/RunsList.tsx` (the `form[role="search"]` moves into `.facets` as vertical checkbox groups; the status chips become `.facet label`; the agent text input and range select stay in the facet column; `<div className="search-slot" />` above the table for Phase 16's `SearchBox` + `QueryChips`), `components/RunsTable.tsx` (sticky `th`, `td.name`, mono numeric cells; Phase 10's findings column and Phase 11's compare cell are preserved), `components/RunsListPage.tsx` (`app-page` root), `globals.css` (`.list-head`, `.list-grid`, `.facets`, `.facet`, table rules).
- Phase 16 note: if Phase 16 has already landed `Facets.tsx`, reuse it for the facet column instead of the chip conversion and delete the duplicate; the search slot then holds `SearchBox`.
- Must not break: `caption`, `getByRole("button", { name: "Load more" })`, `"Clear filters"`, `getByRole("link", { name: "Migrate billing schema" })`, `runs-list.test.tsx`, `getByRole("cell", …)` queries in `auth.spec.ts`.
- Verify: `runs-list.test.tsx`, `fixtures.spec.ts`, axe; table fits at 1440 without horizontal scroll inside `.table-wrap` (the wrap stays for narrow viewports).

### 9. `refactor(web): overview and analytics use readout strips; motion layer` (effort: small)
- Files: `components/Dashboard.tsx` (`Stat` -> `Readout`; h1 text stays "Dashboard" unless the owner renames it [NEEDS INPUT]), `components/Analytics.tsx` (Cost tiles -> readouts; bars and columns untouched), `globals.css` (delete `.stats`/`.stat`; add `--t-*` tokens, `@keyframes fade-in|arrive|landed`, the `arrive` rule for new rows, `data-nav` rule), `components/Timeline.tsx` and `Waterfall.tsx` (only: set `data-nav` on keydown/pointerdown and pass a `newIds` set for the arrive tint; row markup otherwise unchanged), `lib/live.ts` consumer in `RunDetail.tsx` (compute `newIds`, bounded to 200, pruned after 500 ms).
- Must not break: `dashboard.test.ts`, `analytics.test.tsx`, `analytics.spec.ts` (`getByRole("region", { name: "Cost" })` etc.), `live-hook.test.tsx`.
- Verify: Playwright with `reducedMotion: "reduce"`: `document.getAnimations().length === 0` after load on the run page; with motion on: the inspector's computed transition is `180ms`.

### 10. `docs(phase-21): screenshots, design doc, state` (effort: small)
- Files: `docs/screenshots/phase-21/` (before/after set below), `docs/design/ui-direction.md` (this folder's document, trimmed to what landed), `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md` (Phase 21 line), `docs/plans/completed/phase-21-ui-shell.md`, `docs/KNOWN_ISSUES.md` for anything UNVERIFIED.

Total: 10 steps, roughly 3 small, 5 medium, 2 small docs/tokens; the critical path is 2 -> 4 -> 5 -> 6 -> 7.

## What must not break (checklist for every step)

- axe (`wcag2a`, `wcag2aa`) in `fixtures.spec.ts`, `coding.spec.ts`, `auth.spec.ts`: no new violations; the contrast rule is the one most likely to fire, so run `contrast.mjs` against any new colour before using it.
- Selectors used by specs and tests (counted from `apps/web/e2e` and `apps/web/src`): test ids `live-status, headline, progress, first-error, payload-withheld, agent-tree, story, key-token, test-result, shell-panel, risk-class, partial-data, invite-link, diff, waterfall, waterfall-empty, wf-zoom, sensitive-path, file-section, exit-code, artifact-progress`; roles and names `button "Show it" / "Lanes" / "Tree" / "Load more" / "Close details" / "Clear filters" / "Collapse groups" / "Expand groups" / "Sign out" / "Today"`, `listbox /Run timeline/`, `option /llm\.request\.completed/`, `navigation "Settings"`, `heading "Dashboard" / "Analytics" / "Recent failures" / "Members"`, `region "Cost" / "Reliability" / "Performance"`, `list "Cost by model" / "Cost by agent" / "Cost by source"`, the analytics tables by caption. The only intentional change is `radio "Timeline|Waterfall"` -> `tab`, done in step 5 with its call sites.
- Keyboard: `j k ArrowUp ArrowDown PageUp PageDown Home End Enter Space Escape e` in both lists; Escape in the inspector; focus return on close; skip link.
- Virtualization: `useVirtualizer` scroll elements and `ROW_HEIGHT` (36 / 32) unchanged; the inspector must not resize rows.
- SSE: `useLiveEvents`, `LiveBar` states (`live, connecting, reconnecting, unauthorized, unavailable, ended, off`), `partial-data` text, polling fallback in fixture mode.
- Viewer-withheld states: `WITHHELD_TEXT`, "Content hidden by your role…", `[withheld]` attributes, `stream-unauthorized` message; the inspector's Attributes tab must show the withheld marker, not hide the row.
- Mobile: no horizontal scroll at 375 px on any page; the inspector becomes a fixed overlay under the top bar; the rail becomes a horizontal strip; `.tl-row` and waterfall column rules at `max-width: 52rem` keep working.
- Fixture mode: `ABB_WEB_DATA_SOURCE=fixtures` renders every page without a workspace provider (`useWorkspaceOptional()` may be null): the rail shows the brand and primary links only.

## Avoiding conflicts with Phase 10, 16 and 11

- Order: land after all three (see "When it lands"). The phase branch starts from the merged `main`.
- `globals.css`: Phases 10 and 16 append rules at the end; Phase 11 uses its own `compare.css`. This phase edits the top (tokens) and the middle (layout) and leaves appended blocks in place, then restyles Phase 10's findings rules and Phase 16's facets in step 8/9 by editing those blocks, not by overriding them.
- `RunDetail.tsx`: Phase 10's `Findings` call and Phase 11's Evaluations block are moved into tabs in step 5 as whole blocks; their props and the `jumpTo` helper are reused unchanged.
- `RunsTable.tsx`: Phase 10's findings column, Phase 16's "Matched" column and Phase 11's compare cell are kept in their positions; this phase changes only classes and header styling.
- `AppShell.tsx`: Phase 11 adds one `Experiments` link; this phase rewrites the file, so the link is carried into the rail (it is already drawn there in the mockups, tagged Planned until the page exists).
- `queries.ts`, `fixtures/*`, `upstream.ts`: not touched by this phase except one `useRuns` call for the live indicator (no new endpoint).

## Verification plan

- Per step: `pnpm --filter @abb/web test`, `pnpm --filter @abb/web test:e2e` (fixtures build on port 3100 as configured), `scripts/quality.sh full`.
- Before/after screenshot set (Playwright against the fixtures build, 2x, saved to `docs/screenshots/phase-21/`): `runs-list-1440`, `run-multiagent-waterfall-1440`, `run-failed-story-1440`, `run-coding-inspector-diff-1440`, `dashboard-1440`, `analytics-1440`, `settings-members-1440`, `login-1440`, `runs-list-375`, `run-375-inspector-open`, `viewer-payload-withheld-1440`, each taken from the current `main` first (the "before" set) with the same script. A one-page `docs/screenshots/phase-21/README.md` pairs them.
- Manual browser pass (required by AGENTS.md for UI changes): keyboard-only walk of the run page (tab to the listbox, `j` ×5, `e`, `Enter`, Escape, focus back on the listbox), `prefers-reduced-motion` on, 200% browser zoom at 1440 (no clipped readouts; the strip wraps), light scheme.
- Performance: the existing `waterfall-perf.test.ts` plus a Playwright timing of first paint on the 10,000-event run before/after (fonts must not add more than ~100 ms with preload).

## Risks

1. Spec churn around the view switch (9 call sites). Mitigation: step 5 updates them in the same commit; `ViewSwitch` is deleted so nothing can keep using it.
2. Inspector tabs changing DOM presence could break coding specs. Mitigation: tabs scroll to sections in this phase (all sections stay mounted); real panels are a follow-up.
3. Fonts: self-hosted files add ~90 KB; a missing preload causes a visible swap. Mitigation: preload the three files, `font-display: swap`, measure.
4. Mobile layout with a rail. Mitigation: the rail becomes a horizontal strip at `max-width: 52rem`; the Playwright scroll-width check runs on every page.
5. The sticky header eats vertical space on short laptops (768 px). Mitigation: header <= 190 px; readout labels and the id line hide below 52rem; if still tight, the tabs row can scroll horizontally.
6. Rebase cost if Phases 10/16/11 slip. Mitigation: wait; or ship step 1 only.
7. The `LiveIndicator` adds one more poll per page. Mitigation: it reuses the existing `useRuns` cache key for RUNNING so the runs page pays nothing extra; other pages poll at the existing `POLL_MS * 5`.

## Effort estimate

Ten steps: three are a single sitting each (1, 3, 6), five are a full sitting with tests (2, 4, 5, 7, 8), two are short (9, 10). The whole phase is one branch, ten commits, with the screenshots as the gate. Nothing requires Docker or Postgres; the fixtures build covers every screen, so every criterion is verifiable on this machine.
