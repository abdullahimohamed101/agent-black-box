# Agent Black Box: UI direction

Concept and plan for turning the current web UI from a centred page into an application.
Read the summary first; the three parts underneath are the working document for the engineer.
Concept renders: `app-run-waterfall-1600x1000.png`, `app-run-story-1600x1000.png`, `app-runs-list-1600x1000.png`, `app-light-1600x1000.png` (same folder).

## Summary (plain language)

1. The owner is right. The current UI reads as "generated" because it is a document, not an application: one centred 80rem column under a top bar, every screen a stack of heading, stat tiles, table.
2. Nothing in it is ugly. It is well-built, accessible and honest. It is just unshaped: default system font, default dark grey, eight identical tiles, a run page that scrolls for 2,000 px.
3. The fix is structural first, cosmetic second. A persistent left rail (workspace, project, primary nav), a slim top bar (breadcrumb, search/command slot, live status), a full-width content area, a sticky run header, tabs, and a right inspector that replaces the overlay drawer.
4. The direction is "flight-deck instrument panel": the product is a flight recorder, so the UI borrows the one thing cockpits do better than any dashboard: a strict colour grammar (red warning, amber caution, green normal, cyan advisory) and dense readouts in a monospace face.
5. The recorder orange from the earlier concept renders stays, but only as the brand mark and the REC indicator. It is not the interaction colour. That keeps us off the "near-black plus one acid accent" template.
6. Type: IBM Plex Sans Condensed for headings and labels, IBM Plex Sans for body, IBM Plex Mono for every number, id and event type. System-ui goes.
7. The stat tiles become one readout strip; the first-error banner becomes one sentence in the header with a "Show it" link; the story, agents and findings become a left rail inside the run page instead of sections stacked above the timeline.
8. Motion is almost nil: 120-180 ms ease-out on the inspector and selection, nothing on first paint, and every rule has a reduced-motion fallback.
9. Everything the tests rely on survives: roles, test ids, keyboard shortcuts, virtualization, SSE, viewer-withheld states. The plan is nine commits, landing after Phase 10, 16 and 11 merge.
10. One decision for the owner: ship the light theme as a user toggle or keep system preference only. Recommendation: system preference only in this phase, with the token set already supporting both (the light render proves it).

### Before / after

| Area | Today | Proposed |
| --- | --- | --- |
| Frame | Top bar only; `.shell main { max-width: 80rem; margin: 0 auto }` centres everything | Left rail (224 px) + 44 px top bar + full-width content; no max-width |
| Navigation | Three links in the top bar; project is a grey crumb | Rail: workspace switcher, project switcher, Overview / Runs / Analytics, then workspace settings; planned areas shown as "Planned" |
| Run header | h1 + badge + sentence + 8 stat tiles + meta line + red banner | One sticky header: title, status pill, tags, one-line id strip, headline with "Show it", one readout strip, tabs |
| Run page | Story, Agents, Timeline/Waterfall sections stacked; page scrolls | Tabs: Story, Timeline, Waterfall, Findings, Evidence, Compare (planned); the pane fills the viewport |
| Event detail | `.drawer` grid column that appears beside the timeline | Persistent right inspector (392 px) with Details / Attributes / Payload / Identifiers tabs; same roles and test ids |
| Runs list | Chips row + table in a column | Search/command slot with query chips (Phase 16, planned), facet column, dense table with Findings column |
| Status | Pill with glyph + word, five tones | Same semantics, cockpit grammar: red warning, amber caution, green normal, cyan advisory, grey neutral |
| Type | `ui-sans-serif, system-ui` for everything; `ui-monospace` for types | Plex Sans Condensed (display, labels), Plex Sans (body), Plex Mono (data) |
| Colour | `#0e1013` canvas, `#161a20` surface, `#5aa7ff` blue accent | `#14181d` graphite canvas, `#1b2027` surface, `#62c6e8` advisory cyan; orange only for the mark and REC |

---

## Part 1: Ruthless UI editor (unslop)

### A. Slop inventory

Evidence is quoted from `apps/web/src/app/globals.css`, `waterfall.css`, the components, and the screenshots in `scratchpad/demo/`.

| # | Finding | Evidence | Severity |
| --- | --- | --- | --- |
| 1 | System font as the brand voice | `body { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif }` (globals.css:22) | Critical |
| 2 | Centred document column instead of an application layout | `main { max-width: 56rem; margin: 0 auto }` then `.shell main { max-width: 80rem }` (globals.css:29, 109); every screenshot shows ~130 px of dead margin each side at 1440 | Critical |
| 3 | Default dark theme: near-black plus one blue | `--bg: #0e1013; --accent: #5aa7ff` (globals.css:11, 15). Not acid, but it is the GitHub-dark default and carries no product meaning | High |
| 4 | Eight identical stat tiles, wrapping to a second row with one orphan | `.stats { grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr)) }` and `.stat { border; border-radius: 6px; background: var(--surface) }`; `demo-run-page-1440x1500.png` shows "Files changed" alone on row two | High |
| 5 | The run page is a scroll of sections, not a workspace | `RunDetail.tsx` renders crumbs, h1, headline, live bar, notice, stats, meta, banner, CodingSummary, AgentTree, then the timeline section in sequence; `demo-run-page-1440x1500.png` is 2,000 px tall before the waterfall starts at y=1,340 | High |
| 6 | Top bar is the only chrome; no persistent context | `AppShell.tsx` renders brand, switcher, crumb, three links; nothing on the left; no search slot; no live status outside the run page | High |
| 7 | The event drawer is a card that appears inside the content grid | `.split[data-drawer="open"] { grid-template-columns: minmax(0,1fr) minmax(18rem,26rem) }`, `.drawer { border-radius: 6px; max-height: min(68vh, 44rem) }` (globals.css:388, 455). It has its own scroll, its own border, its own radius: a card inside a card | High |
| 8 | Banner as a red card with a button | `.banner-bad { background: var(--bad-bg); border: 1px solid var(--bad) }` plus "Show it" button (RunDetail.tsx:327) repeats the headline sentence right above it | Medium |
| 9 | Pill radii mixed: 999px chips, 6px cards, 4px tags | `.chip { border-radius: 999px }`, `.badge { border-radius: 999px }` and later `.badge { border-radius: 4px }` (globals.css:204, 537: the second rule silently overrides the first) | Medium |
| 10 | Underlined blue links as table row primary action | `a { color: var(--accent) }` with default underline; `demo-runs-list.png` shows every run name underlined | Medium |
| 11 | Fieldset legend floated as a label ("View", "Event classes", "Status") | `legend { float: left }` (globals.css:297); reads as a form, not a toolbar | Medium |
| 12 | Section headings in the run page say what the component is, not what the reader needs | "Waterfall" h2 above a "View: Timeline / Waterfall" radio (RunDetail.tsx:357) | Medium |
| 13 | Dashboard and Analytics repeat the tile grid | `Dashboard.tsx` `dl.stats` with hints; `Analytics.tsx` `Cost` tiles then bars; identical shape, three screens | Medium |
| 14 | Spinner is the only animation; no transitions at all, so state changes snap | `.spinner { animation: spin 0.8s linear infinite }` is the only `transition`/`animation` in both stylesheets | Low |
| 15 | Earlier concept renders (`scratchpad/graphics/*.png`): near-black `#0b0e13`, one "recorder orange" `#f5a623` accent, glow backgrounds, 24-60 px drop shadows, marketing headlines over a UI frame | `abb.css`: `--accent: #f5a623`, `--shadow: 0 24px 60px rgba(0,0,0,.55)`, `.glow` | High (as an app direction; fine as social graphics) |

The earlier renders are on the anti-slop list for a reason: the orange reads well in a poster but as the interaction colour of a tool it would colour every link, focus ring and selected row "warning amber" in the same grammar the product needs for retries and clock skew. It is kept as a mark, not as an accent (see C).

### B. What to keep (it already works)

1. Status is always glyph + word, never colour alone (`StatusBadge.tsx`, `LiveBar`). This is the accessibility backbone and the basis of the cockpit grammar below.
2. The one-sentence headline (`headline()` in `RunDetail.tsx`): "Failed after 2 retries in 14 s. First error at +6.2s: run_migration · ConnectionTimeout." This is the best thing on the page; the redesign builds the header around it.
3. The waterfall semantics: critical path as a thick bottom edge, hatched error bars, dashed unfinished edge, clock-skew tag, agent chips per row (`waterfall.css`). Keep every one; only the chrome around them changes.
4. The keyboard model: `j/k`, `Enter`, `e`, `Escape`, listbox with `aria-activedescendant`, virtualized rows (`Timeline.tsx`, `Waterfall.tsx`). Untouched.
5. Honest copy: "Content hidden by your role. You can see that this event has a payload, not what it says.", "Partial data: some recent events may be missing until the connection recovers." Keep the voice; it is the product's credibility.

### C. Unslop direction

**Aesthetic: flight-deck instrument panel.** The product records what an agent did and replays it after the fact, like a flight data recorder. Flight decks solved the problem this UI has: a lot of dense, live, numeric state that must be scannable in seconds, with a colour grammar nobody has to learn twice.

**One signature move:** the *readout strip* under the run title. Not tiles: one bordered row of monospace readouts with small-caps condensed labels (Duration 11.0 s · Cost $0.060 · Tokens 6.0k in 750 out · Model calls 5 · Tool calls 1 · Retries 0 · Errors 1 · Agents 5). Errors and retries light in their annunciator colour only when non-zero. Everything else on the page is quieter than this strip.

**Palette roles (dark default / light):**

| Role | Dark | Light | Use |
| --- | --- | --- | --- |
| bg | `#14181d` | `#eef1f4` | canvas behind panes |
| surface | `#1b2027` | `#ffffff` | rail, top bar, run header, inspector, lane headers |
| surface-2 | `#222932` | `#f5f7f9` | raised controls |
| border | `#2e3641` | `#d3d9e0` | hairlines between panes and rows (structural, not decorative) |
| ink | `#e6ebf0` | `#171b21` | body text (14.9:1 / 15.2:1 on bg) |
| ink-2 | `#c5cdd6` | `#2f3845` | secondary text (11.1:1 / 10.5:1) |
| muted | `#9aa6b4` | `#566272` | labels, ids, timestamps (7.2:1 / 5.5:1; 5.3:1 / 5.2:1 on the selection colour) |
| accent (advisory cyan) | `#62c6e8` | `#0b6f8f` | links, selected row, focus ring, active tab (9.1:1 / 5.0:1) |
| ok (normal green) | `#5fcf84` | `#1b7a3a` | success |
| warn (caution amber) | `#f2b33d` | `#8a5a00` | retries, waiting, clock skew, approvals pending |
| bad (warning red) | `#ff6f66` | `#b3261e` | errors, failed, blocked, timed out |
| mark (recorder orange) | `#ff7a1a` | `#d9650f` | brand glyph and REC dot only; never text (3.2:1 on light bg) |
| sel | `#1f3340` | `#dceef5` | selected row / finding / nav item |

Tinted backgrounds (`ok-bg`, `warn-bg`, `bad-bg`, `accent-bg`) are in `app.css`; every tone on its tint is at least 4.7:1 (measured, see the contrast table in the self-check).

**Type pairing:**
- Display and labels: IBM Plex Sans Condensed 500/600 (run title, page title, tab labels, small-caps section labels, status pills). Condensed is the instrument-panel voice: upright, narrow, no personality flourishes.
- Body: IBM Plex Sans 400/500/600.
- Data: IBM Plex Mono 400/500 for every number, id, event type, path and attribute value, with `font-variant-numeric: tabular-nums` globally.
- One superfamily, three widths: real contrast (condensed vs normal vs mono), shared x-height and metrics. Load from Google Fonts with `display=swap` or self-host the three files in `apps/web/public/fonts` [NEEDS INPUT: self-host vs Google Fonts; recommendation: self-host, no third-party request from an observability tool].
- Banned from now on: `system-ui`, `ui-sans-serif`, `-apple-system`, `Segoe UI`, Inter, Roboto, Arial as the UI face. They stay only at the end of the fallback stack.

**What NOT to do next pass:** no gradients, no glow, no drop shadows larger than 1 px, no illustration, no icon packs beyond the 10 line icons in the rail, no rounded 999px pills (4 px everywhere), no hover scale, no "AI" sparkle, no second accent, no empty-state illustrations, no new dependency for any of this.

### D. Rewrite plan

**Must-fix**
1. Replace the centred column with the rail + top bar + full-width content frame (AppShell, globals.css).
2. Load and apply the three Plex faces; set `tabular-nums` and the mono face on every numeric and id cell.
3. Collapse the eight tiles and the meta line into the readout strip; make the run header sticky.
4. Turn the Timeline/Waterfall radio and the Story/Agents sections into tabs; the pane fills the viewport height.
5. Make the drawer a persistent right inspector pane (same component, same roles, new placement and tabs).

**High-impact upgrades**
6. Cockpit colour grammar applied to StatusBadge, tags, timeline and waterfall rows, readouts; remove the second `.badge` rule.
7. Runs list: facet column, dense table, Findings column (Phase 10), search slot reserved for Phase 16's `SearchBox` and `QueryChips`.
8. Dashboard becomes "Overview": one readout strip (success rate, failures, cost, median duration, running) and the two tables; no tiles.
9. A real 150 ms motion layer for selection, inspector open/close, live rows (Part 3).

**Nice-to-have**
10. Collapsible rail (56 px icon mode) remembered in `localStorage`.
11. Resizable inspector (CSS `resize: horizontal` on the aside, min 20rem, max 44rem).
12. Light theme toggle in the rail footer (tokens already exist).

### E. Concrete edits

Highest-impact diffs against the real files. Class names are kept where tests or other phases reference them.

**1. `globals.css`: frame and tokens (replace lines 1-33 and 109-143)**

```css
:root {
  color-scheme: light dark;
  --bg: #eef1f4; --surface: #ffffff; --surface-2: #f5f7f9;
  --border: #d3d9e0; --border-strong: #b9c2cc;
  --fg: #171b21; --fg-2: #2f3845; --muted: #566272;
  --accent: #0b6f8f; --accent-bg: #dff1f7; --sel: #dceef5;
  --ok: #1b7a3a; --ok-bg: #e2f5e8; --warn: #8a5a00; --warn-bg: #fff1d1;
  --bad: #b3261e; --bad-bg: #fbe7e5; --info: var(--accent); --mark: #d9650f;
  --sans: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif;
  --cond: "IBM Plex Sans Condensed", var(--sans);
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  --rail-w: 14rem; --top-h: 2.75rem; --insp-w: 24.5rem;
  --ease-out: cubic-bezier(0.2, 0, 0, 1); --t-fast: 120ms; --t-base: 180ms;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14181d; --surface: #1b2027; --surface-2: #222932;
    --border: #2e3641; --border-strong: #3c4654;
    --fg: #e6ebf0; --fg-2: #c5cdd6; --muted: #9aa6b4;
    --accent: #62c6e8; --accent-bg: #17313d; --sel: #1f3340;
    --ok: #5fcf84; --ok-bg: #16301f; --warn: #f2b33d; --warn-bg: #3a2c0f;
    --bad: #ff6f66; --bad-bg: #3b1d1b; --mark: #ff7a1a;
  }
}
@media (prefers-reduced-motion: reduce) { :root { --t-fast: 0ms; --t-base: 0ms; } }

body { margin: 0; background: var(--bg); color: var(--fg); font: 13px/1.45 var(--sans); font-variant-numeric: tabular-nums; }
.num, .tl-off, .tl-dur, .tl-type, .wf-meta, .wf-tick, .stat dd, .fields dd, td.num, .meta, .id { font-family: var(--mono); }
h1, h2, h3, .tabs, .ro dt, .topbar nav, .subnav { font-family: var(--cond); }

/* Application frame: rail + top bar + content. No max-width anywhere. */
.shell { display: grid; grid-template-columns: var(--rail-w) minmax(0, 1fr); grid-template-rows: var(--top-h) minmax(0, 1fr); height: 100dvh; }
.rail { grid-row: 1 / 3; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; min-height: 0; }
.topbar { grid-column: 2; display: flex; align-items: center; gap: .9rem; padding: 0 1rem; border-bottom: 1px solid var(--border); background: var(--surface); }
.shell main { grid-column: 2; min-height: 0; overflow: auto; padding: 0; max-width: none; margin: 0; }
main.page { padding: 1rem 1.25rem; }                 /* settings, analytics, overview keep a padded scroll page */
main.app-page { display: flex; flex-direction: column; overflow: hidden; }   /* run page and runs list fill the viewport */
@media (max-width: 52rem) {
  .shell { grid-template-columns: minmax(0, 1fr); grid-template-rows: var(--top-h) auto minmax(0, 1fr); }
  .rail { grid-row: 2; grid-column: 1; flex-direction: row; overflow-x: auto; border-right: 0; border-bottom: 1px solid var(--border); }
  .topbar, .shell main { grid-column: 1; }
}
```

**2. `AppShell.tsx`: rail + top bar (replace the `<header className="topbar">` block)**

```tsx
return (
  <div className="shell">
    <a href="#main" className="skip">Skip to content</a>
    <aside className="rail" aria-label="Primary">
      <Link href="/" className="brand"><span className="mark" aria-hidden="true" />Agent Black Box</Link>
      <div className="scope"><WorkspaceSwitcher /><ProjectSwitcher base={base} /></div>
      <nav aria-label="Primary" className="nav">
        <Link href={dash} aria-current={cur(dash, true)}>Overview</Link>
        <Link href={runs} aria-current={cur(runs, false)}>Runs</Link>
        <Link href={analytics} aria-current={cur(analytics, false)}>Analytics</Link>
        {ws?.mode === "session" && <>
          <div className="nav-h">Workspace</div>
          <SettingsLinks base={base} />   {/* the same permission-filtered list SettingsNav renders */}
        </>}
      </nav>
      {ws?.mode === "session" && ws.user && <div className="rail-foot who">…email, SignOutButton…</div>}
    </aside>
    <header className="topbar">
      <Crumbs base={base} />                   {/* "Runs / Ship feature: multi-agent" from the pathname */}
      <div className="cmd" role="search" aria-label="Search runs">   {/* Phase 16 mounts SearchBox here */}
        <span className="muted">Search runs or jump to an event…</span><kbd>⌘K</kbd>
      </div>
      <LiveIndicator />                        {/* "REC · 2 running" from useRuns({statuses:["RUNNING"]}, 1); "Finished" on a closed run */}
    </header>
    <main id="main" tabIndex={-1}>{children}</main>
  </div>
);
```

The `nav[aria-label="Primary"]` stays (tests query `getByRole("navigation", { name: "Settings" })` for the settings sub-nav, which keeps rendering on the settings pages; the rail only adds links).

**3. `RunDetail.tsx`: header, readout strip, tabs (replace lines 284-336)**

```tsx
<div className="run-detail app-page">
  <header className="run-head">
    <div className="run-title">
      <h1>{r.name ?? r.id}</h1>
      <StatusBadge status={r.status} />
      {retries > 0 && <span className="tag tag-warn">{retries} retr{retries === 1 ? "y" : "ies"}</span>}
      <span className="id muted">{shortId(r.id)} · {r.agent_id ?? "—"} · {models} · started {formatTimestamp(r.started_at)}</span>
    </div>
    <p className="headline" data-testid="headline">
      {headline(r, errText, errOffset)}{" "}
      {err && <button type="button" className="linkish" onClick={jumpToError} data-testid="first-error">Show it <kbd>e</kbd></button>}
    </p>
    {stream.state !== "off" && stream.state !== "ended" && <LiveBar state={stream.state} status={nowDoing} />}
    {r.summary_state !== "current" && <p role="status" className="notice">…</p>}
    <dl className="readouts" aria-label="Run summary">
      <Readout label="Duration" value={formatDuration(…)} />
      <Readout label="Cost" value={formatCost(…)} />
      <Readout label="Tokens" value={`${formatInt(in)} in ${formatInt(out)} out`} />
      <Readout label="Model calls" value={…} />
      <Readout label="Tool calls" value={…} />
      <Readout label="Retries" value={…} tone={retries ? "warn" : undefined} />
      <Readout label="Errors" value={…} tone={errors ? "bad" : undefined} />
      {multiAgent && <Readout label="Agents" value={String(agents.length)} />}
      <Readout label="Files changed" value={…} />
    </dl>
    <RunTabs view={view} onChange={changeView} counts={{ events: events.length, spans: spans.length, findings }} />
  </header>
  <div className="work" data-inspector={drawerEvent ? "open" : "closed"}>
    <section className="pane" aria-labelledby="tl-h">…existing timeline / waterfall / story / findings by tab…</section>
    {drawerEvent && <EventDrawer runId={runId} event={drawerEvent} onClose={closeDrawer} />}
  </div>
</div>
```

`data-testid="first-error"` moves from the banner `div` to the "Show it" control; the three specs that use `getByTestId("first-error")` and `getByRole("button", { name: "Show it" })` keep passing because the accessible name is still "Show it" (the `kbd` is `aria-hidden`).

**4. `globals.css`: readouts, tabs, work area, inspector (replace `.stats`, `.stat`, `.split`, `.drawer` blocks)**

```css
.run-head { position: sticky; top: 0; z-index: 2; padding: .75rem 1.25rem 0; background: var(--surface); border-bottom: 1px solid var(--border); }
.run-title { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; }
.run-title h1 { margin: 0; font: 600 1.25rem/1.2 var(--cond); }
.readouts { display: flex; flex-wrap: wrap; margin: .6rem 0 0; border-top: 1px solid var(--border); }
.ro { padding: .5rem 1.1rem .6rem 0; margin-right: 1.1rem; border-right: 1px solid var(--border); min-width: 6rem; }
.ro:last-child { border-right: 0; }
.ro dt { font: 600 .66rem var(--cond); letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
.ro dd { margin: .05rem 0 0; font: 500 1.05rem/1.2 var(--mono); }
.ro[data-tone="bad"] dd { color: var(--bad); }  .ro[data-tone="warn"] dd { color: var(--warn); }
.tabs { display: flex; gap: .125rem; margin-top: .125rem; }
.tabs [role="tab"] { padding: .5rem .75rem .55rem; color: var(--muted); font: 500 .8125rem var(--cond); border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; background: none; }
.tabs [role="tab"][aria-selected="true"] { color: var(--fg); border-bottom-color: var(--accent); }

.work { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr); }
.work[data-inspector="open"] { grid-template-columns: minmax(0, 1fr) var(--insp-w); }
.pane { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.timeline, .wf-scroll { flex: 1; height: auto; min-height: 0; border: 0; border-radius: 0; }   /* was height: min(68vh, 44rem) */
.drawer { border: 0; border-left: 1px solid var(--border); border-radius: 0; padding: 0; background: var(--surface); max-height: none; min-height: 0; display: flex; flex-direction: column; overflow: auto;
  transition: transform var(--t-base) var(--ease-out), opacity var(--t-fast) linear; }
@starting-style { .drawer { transform: translateX(.75rem); opacity: 0; } }
@media (max-width: 52rem) { .work[data-inspector="open"] { grid-template-columns: minmax(0, 1fr); } .drawer { position: fixed; inset: var(--top-h) 0 0 0; z-index: 3; } }
```

**5. `StatusBadge.tsx` / `.badge`: one rule, cockpit tones (delete the second `.badge` block at globals.css:534-555)**

```css
.badge { display: inline-flex; align-items: center; gap: .35rem; font: 600 .6875rem var(--cond); letter-spacing: .08em; text-transform: uppercase; padding: .1rem .5rem .1rem .45rem; border-radius: 4px; border: 1px solid currentColor; white-space: nowrap; }
.badge[data-tone="ok"]   { color: var(--ok);   background: var(--ok-bg); }
.badge[data-tone="bad"]  { color: var(--bad);  background: var(--bad-bg); }
.badge[data-tone="warn"] { color: var(--warn); background: var(--warn-bg); }
.badge[data-tone="info"] { color: var(--accent); background: var(--accent-bg); }
.badge[data-tone="neutral"] { color: var(--muted); }
```

`badge-ok/bad/warn` (used by CodingSections) become `data-tone` on the same element; one find-and-replace.

**6. `RunsTable.tsx` / runs list layout**

```css
.list-grid { flex: 1; min-height: 0; display: grid; grid-template-columns: 13.75rem minmax(0, 1fr); }
.facets { border-right: 1px solid var(--border); background: var(--surface); overflow: auto; }
table th { font: 600 .66rem var(--cond); letter-spacing: .1em; text-transform: uppercase; color: var(--muted); position: sticky; top: 0; background: var(--surface); }
td.name a { color: var(--fg); font-weight: 500; text-decoration: none; }   /* underline only on hover/focus */
td.name a:hover, td.name a:focus-visible { text-decoration: underline; }
```

The `form[role="search"][aria-label="Filter runs"]` with its chips moves into `.facets` as vertical checkboxes (same inputs, same labels; `getByRole("button", { name: "Clear filters" })` stays). The search slot above the table is empty until Phase 16's `SearchBox` mounts in it.

---

## Part 2: Polish frontend UI (design engineer)

### Design thinking

**Purpose and user.** A developer opens a run because something is wrong, expensive or slow. In the first five seconds they need: what happened (one sentence), how bad (status, errors, cost), where (the first error), and a way to drill from "where" to the exact event with its payload. Team leads open the runs list to see what is failing and what is costing money this week. Both are reading instruments, not browsing content.

**The one tone extreme: instrument panel, committed.** Not "dashboard-ish", not "developer-tool-ish". The product is literally named after the flight data recorder, and a flight deck is the best-studied dense, live, safety-critical UI there is. Three things transfer directly:
- A fixed colour grammar (red = warning, amber = caution, green = normal, cyan = advisory). The product already has `ok/warn/bad/info` tones; this just makes the rule explicit and keeps every other element achromatic so the grammar is readable.
- Readouts, not cards: a value, a small-caps label, a unit, in a mono face, in a strip. No tile borders competing with the data.
- Structural hairlines: every line on the screen separates two things; none are decoration.

What we do *not* borrow: skeuomorphic bezels, LED fonts, green-on-black. The surface is matte graphite, the type is a modern Plex superfamily, and the whole thing is calm.

**Differentiation (the thing people will remember).** The readout strip plus the annunciator grammar: "Errors 1" is the only red thing in the header, "Retries 2" the only amber, and when you select the hatched red bar in the waterfall the same red appears once in the inspector callout. Someone who has used it will say "it tells you what's wrong in the colours an aircraft would use."

**Constraints.** Plain CSS, no Tailwind, no new runtime dependency; axe must stay clean; Playwright specs select by role and test id; two virtualized lists with fixed row heights; SSE live updates; mobile must not scroll horizontally; viewer-withheld states must stay visible and honest.

### A. Critique

**Hierarchy and scan path**
1. The run page has no fixed point. Title, badge, sentence, live bar, tiles, meta, banner, story, agents, then the timeline: the eye walks 1,300 px before the data. The first error is said three times (sentence, banner, story item 4) before you can click it in context. *AI-default tell: everything gets its own section with an h2.*
2. Stat tiles are equal weight for unequal information. "Files changed: 0" is the same size as "Errors: 1". The eye cannot find the anomaly.
3. Agents (5) is a list of sentences with indentation as a tree; the same information is in the waterfall's lanes one scroll down.

**Typography**
4. System-ui at 16 px body makes a dense tool feel like a settings page; numbers are proportional (`11 s`, `$0.0600`, `6,000 in / 750 out` do not align in a column). `tabular-nums` is applied only to `.tl-off/.tl-dur/.wf-meta`.
5. Headings are the same face, bolded. Nothing distinguishes a label from a value from a heading except size.

**Colour and contrast**
6. `--muted: #9aa3b0` on `#0e1013` is 7:1: fine. But the chosen blue `#5aa7ff` is both the link colour, the selection colour, the chart colour (`.bar-fill`) and the "Running" tone, so "info" and "interactive" are indistinguishable. *AI-default tell: one accent for everything.*
7. The tile backgrounds `#161a20` against the canvas `#0e1013` are 1.2:1: the borders do all the work, so every tile needs a border, so the page is a grid of boxes.

**Layout and spacing**
8. `max-width: 80rem; margin: 0 auto` plus `padding: 1rem` wastes 15% of a 1440 px screen and 35% of a 1920 px screen; the waterfall, the one element that wants width, gets the least.
9. The timeline and drawer are capped at `min(68vh, 44rem)` inside a scrolling page: two nested scroll regions, and the drawer scrolls independently of the row it describes.

**Surfaces, cards and chrome**
10. Card-in-card: `.drawer` (6 px radius, border, surface) inside `.split` inside `.timeline-section` on a page with tiles that use the same radius, border and surface. The chrome does not say which thing is a container and which is content.

**Motion and interaction**
11. No transitions at all, so opening the drawer reflows the grid from one column to two in one frame and the timeline jumps 26rem narrower. Selection changes have no settling.

**Copy in the UI**
12. Good voice, but labels describe the widget: "View", "Event classes", "Waterfall" as an h2 above a "Waterfall" radio. "7 runs loaded (end of list)" is fine; "What the agent did" is excellent and should become the Story tab's name.

**Accessibility and responsive risk**
13. The `legend { float: left }` trick places form legends as inline labels; it works for screen readers but visually it is a form. The chip checkboxes are `999px` pills with a native checkbox inside: three visual languages (pill, checkbox, button) for one action. Mobile already collapses columns (`@media (max-width: 52rem)`), which must survive the rail.

### B. Direction

**Aesthetic:** flight-deck instrument panel (Part 1C has the palette and type; not repeated here).

**Why it fits:** see Design thinking above. It is grounded in the product's name and job; it gives the product a rule (the colour grammar) rather than a style; and it demands density and calm, which is what the owner asked for.

**Signature element:** the readout strip under the run title (Part 1C).

**Layout concept in one sentence:** a persistent rail and slim top bar frame a full-width content area in which list-like data gets a facet column, run data gets a sticky instrument header with tabs, and the selected event gets a right inspector pane that never covers the data it describes.

```
┌──────────┬──────────────────────────────────────────────────────────────────────────┐
│ ■ Agent  │ Runs / Ship feature: multi-agent     [ Search runs or jump to event ⌘K ] ● REC│
│ Black Box├──────────────────────────────────────────────────────────────────────────┤
│          │ Ship feature: multi-agent  ✓SUCCESS  1 error, recovered  run_…7 · planner │
│ Workspace│ Succeeded in 11 s. It recovered… First error at +4.6s: search_docs  Show it│
│ Project  │ DURATION  COST   TOKENS        MODEL  TOOL  RETRIES  ERRORS  AGENTS  FILES │
│          │ 11.0 s    $0.060 6.0k in 750   5      1     0        1       5       0     │
│ Overview │ Story  Timeline 30  [Waterfall 12]  Findings 1  Evidence  Compare·planned  │
│▸Runs   7 ├─────────────────────────────────────────────────────┬────────────────────┤
│ Analytics│ Tree|Lanes  − + Fit 1.0×  ▬ critical  ▨ error       │ tool.call.failed    │
│ Experim. │ Span            0s    2.2s   4.4s   6.6s   8.8s 11s │ +4.6s · #14 · rsrch │
│ Policies │ ▾ Run           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │ Details Attrs Payld │
│ Alerts   │   planner plan   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │ ✕ HttpError         │
│          │   researcher      ━━━━━━━━━━━━━━━━━━━━━             │ Tool   search_docs  │
│ WORKSPACE│  ▸ search_docs           ▨▨          600 ms ✕       │ Status failed       │
│ Members  │   coder            ━━━━━━━━━━━━━━━━━━━━━━━          │ http.url [withheld] │
│ API keys │   tester         ┄━━━━━━━┄ clock skew               │ { "query": … }      │
│ Pricing  │   reviewer                              ━━━━━━      │                     │
│ Audit    ├─────────────────────────────────────────────────────┤ Open in Timeline → │
│ ◯ owner  │ 30 events · 12 spans            j k move ↵ open e  │                     │
└──────────┴─────────────────────────────────────────────────────┴────────────────────┘
```

**Motion intent:** still by default; motion only confirms selection, preserves the inspector's spatial origin (it slides 12 px from the right edge), and keeps new live rows from startling (a 400 ms background tint fade). Part 3 has the specs.

### C. Polish plan (ordered)

| # | What | Why | Acceptance criteria |
| --- | --- | --- | --- |
| 1 | Tokens and fonts: new `:root` palette (both schemes), Plex faces, `tabular-nums` on body | Everything else inherits from this; one commit changes the whole feel without touching markup | Fonts load with `font-display: swap`; no FOUT taller than 100 ms on the fixtures build; contrast script (`contrast.mjs` in this folder) reports every text role >= 4.5:1 |
| 2 | Application frame: rail, top bar, full-width main; mobile stacks the rail horizontally | The owner's complaint in one commit | At 1440 and 1920 the main content starts at 224 px and ends at the viewport edge; at 375 px no horizontal scroll (`document.documentElement.scrollWidth <= innerWidth` in a Playwright check); axe clean on every existing spec |
| 3 | Status grammar: one `.badge` rule, tones for tags, timeline and waterfall row markers; links lose default underline inside tables | Removes the double `.badge` definition and gives every state one colour | Visual check of `demo-failure-retry` scene: exactly one red element in the header (Errors readout), amber on Retries and the two retry rows |
| 4 | Run header: readout strip, id line, headline with inline "Show it", sticky | Replaces 8 tiles + meta + banner with one block; the first error is stated once | `getByTestId("headline")`, `getByTestId("first-error")`, `getByRole("button", { name: "Show it" })` pass; header height <= 190 px at 1440 |
| 5 | Tabs: Story, Timeline, Waterfall, Findings, Evidence, Compare (planned, disabled) | Replaces the radio and the stacked sections; keeps `?view=` in the URL | `getByRole("radio", { name: "Waterfall" })` is replaced in specs by `getByRole("tab", { name: /Waterfall/ })` in the same commit (6 call sites + 3); Findings tab mounts Phase 10's `Findings`, Story mounts `CodingSummary` + `AgentTree`; `data-testid="story"` and `"agent-tree"` unchanged |
| 6 | Work area: pane fills the viewport; timeline and waterfall lose the `min(68vh, 44rem)` cap | One scroll region per pane; virtualizer's scroll element is the pane | `useVirtualizer` still gets `scrollRef` (the `.timeline` / `.wf-scroll` element); the 10,000-event fixture renders < 300 DOM rows (existing perf test) |
| 7 | Inspector: `EventDrawer` as the right pane with Details / Attributes / Payload / Identifiers tabs; mobile: fixed overlay below the top bar | Ends the card-in-card and the independent-scroll problem; keeps `role="dialog" aria-modal="false"`, "Close details", focus return | `getByRole("button", { name: "Close details" })`, `getByTestId("payload-withheld")` pass; Escape closes and returns focus to the listbox (existing behaviour) |
| 8 | Runs list: search slot (empty until Phase 16), facet column, dense table with sticky header and Findings column | List/detail shape; room for Phase 16 chips and Phase 11's compare checkbox as first cell | `form[role="search"][aria-label="Filter runs"]` stays; "Load more", "Clear filters" buttons keep their names; table has `caption` |
| 9 | Overview (dashboard) and Analytics: readout strips instead of tiles; charts untouched | Same grammar on every page | `getByRole("heading", { name: "Dashboard" })` keeps its text (the rail label says "Overview", the h1 stays "Dashboard" until a product decision [NEEDS INPUT]) |
| 10 | Motion layer (Part 3) | Last, because it depends on the final DOM | `prefers-reduced-motion: reduce` sets every duration to 0 ms; no animation on first paint |

### D. Implementation notes

- **Tokens** live at the top of `globals.css` as in Part 1E. Keep the names `--bg --fg --muted --border --accent --surface --sel --ok --bad --warn --info --bad-bg --warn-bg` because `waterfall.css`, `compare.css` (Phase 11) and Phase 10's additions reference them; add `--fg-2 --surface-2 --border-strong --accent-bg --ok-bg --mark --sans --cond --mono --rail-w --top-h --insp-w --t-fast --t-base --ease-out`.
- **Specificity**: stay at one class or one attribute selector; the only compound selectors are state ones (`.work[data-inspector="open"]`, `.badge[data-tone="bad"]`). Do not add ids or `!important`. Delete the first `main { max-width: 56rem }` rule and the second `.badge` rule rather than overriding them.
- **Components**: `AppShell.tsx` gains `ProjectSwitcher`, `Crumbs`, `LiveIndicator` (small, in `components/shell/`); `RunDetail.tsx` gains `Readout`, `RunTabs` (in `components/run/`); `EventDrawer.tsx` keeps its name and props, gains an internal tab state; `RunsList.tsx` renders the facets column and leaves a `<div className="search-slot" />` for Phase 16. `ViewSwitch.tsx` is replaced by `RunTabs` (delete after the specs are updated).
- **What NOT to touch**: `Timeline.tsx` and `Waterfall.tsx` internals (row heights, roles, virtualizer, keyboard handling), `lib/*`, `fixtures/*`, `server/*`, `waterfall.css` colour tokens for bars (only the container rules change), `CodingSections.tsx`, `ArtifactText.tsx`, the withheld text constants, the sign-in page beyond fonts and tokens.
- **Fonts**: three woff2 files in `apps/web/public/fonts`, `@font-face` in `globals.css`, `<link rel="preload">` in `layout.tsx`. Plex is OFL-licensed. [NEEDS INPUT: confirm self-hosting is acceptable for the private-hosting guide.]

### E. Self-check

- One justified aesthetic risk: the condensed display face and the annunciator colour grammar. It could read as "cold"; the mitigation is the warm, plain-language copy the product already has.
- Removed decorative accessories: the eight stat tiles (replaced by a strip), the red banner, the 999px pills, the drawer card radius and border, the glow and large shadows of the earlier concept renders, the "Activity" sparkline I tried in the runs table and deleted when it overflowed.
- Body text contrast: measured with the script below. Dark: ink 14.9, ink-2 11.1, muted 7.2, accent 9.1, ok 9.1, warn 9.6, bad 6.6 (on bg); on the selection colour muted is 5.3 and bad 4.8. Light: ink 15.2, muted 5.5, accent 5.0, ok 4.8, warn 5.2, bad 5.8; on tinted backgrounds the lowest is ok-on-ok-bg 4.7. The orange mark is 3.2 on the light canvas and is therefore never used for text.
- Complexity matches the vision: no new dependency, no JS for layout, two new small components, one CSS file edited plus two left alone.
- Brand facts invented: none. The mark (orange rectangle with two bars) is a placeholder glyph [NEEDS INPUT: a designed wordmark/mark]. Wordmark text stays "Agent Black Box".

Contrast check used (run with `node contrast.mjs` in this folder; the script is at the bottom of this document as a code block for reference):

```js
const L=h=>{const [r,g,b]=[1,3,5].map(i=>parseInt(h.slice(i,i+2),16)/255).map(c=>c<=.03928?c/12.92:((c+.055)/1.055)**2.4);return .2126*r+.7152*g+.0722*b};
const C=(a,b)=>{const x=L(a),y=L(b);return ((Math.max(x,y)+.05)/(Math.min(x,y)+.05)).toFixed(2)};
```

---

## Part 3: Motion and interaction craft

### A. Motion audit

| Interaction | Purpose | Frequency | Verdict |
| --- | --- | --- | --- |
| Live event rows arriving over SSE | Tell the reader something new appeared without yanking their eye | High (several per second on an active run) | Tone down: no slide-in; a 400 ms background tint that fades, only on rows inside the viewport; nothing when the list auto-scrolls |
| View switch Timeline <-> Waterfall (now tabs) | Preserve "same run, different projection" | Low | Keep, minimal: 120 ms opacity cross-fade of the pane; the tab underline moves instantly (no sliding indicator) |
| Inspector open / close | Preserve spatial origin (it comes from the right edge, next to the row that opened it) | Medium | Keep: 180 ms translateX(12px)->0 + opacity on open; 120 ms opacity on close; the grid column change itself is not animated |
| Selection moves (j/k, click) | Confirm the row | Very high | Keep as instant background change; a 120 ms `background-color` transition only on pointer selection, 0 ms on keyboard (keyboard users move fast and expect snap) |
| Waterfall zoom / pan | Keep orientation | Medium | Keep bars instant (they are positioned by the virtualizer on every render); animate nothing. The axis ticks fade 120 ms when they are replaced |
| Finding -> evidence jump | Teach where you landed | Low | Keep: 600 ms highlight pulse on the target row (box-shadow inset ring fades), single run, interruptible |
| Filter chip changes | Confirm the change | Medium | Kill animation; the count line (`data-testid="progress"`) updates text, which is confirmation enough |
| Runs list live refresh | Show new runs without moving the selected row | Medium | Tone down: new rows get the same 400 ms tint; no reordering animation; selection anchored by key |
| Sign-in / workspace switch | Context change | Rare | Keep: 180 ms opacity fade of `main` on route change; nothing else |
| Spinner (`.spinner`) | Loading | Medium | Keep; already has reduced-motion handling |
| Hover on rows, buttons | Affordance | Very high | Keep as instant colour; no transform, no scale |
| First paint | None | Every load | Kill: no entrance animation on any element at load |

### B. Motion language

**Personality in one sentence:** instruments do not animate; they update, and the only motion is the one that tells you where a panel came from.

- Duration range: 120 ms (state), 180 ms (panel enter), 400-600 ms (attention fades). Nothing over 600 ms.
- Easing defaults: `--ease-out: cubic-bezier(0.2, 0, 0, 1)` for enters and position; `linear` for opacity fades under 150 ms; exits use `ease-in`-free simple opacity (softer than enters by being shorter and transform-free).
- Springs: not used. There is no drag-release or physics-driven gesture in this product; CSS transitions are interruptible and sufficient. If the waterfall ever gets pointer-drag panning, a spring is still not needed (pan follows the pointer 1:1, no release animation).
- Staggers: none. Live rows arrive continuously; staggering them would create a visible queue.
- What stays still on purpose: bars in the waterfall, numbers in readouts (they change value, never count up), tab underline, rail, top bar, table rows on sort/filter, the focus ring.

### C. Specs per interaction

1. **Inspector open**
   - Trigger: Enter/click on a row, "Show it", evidence link. States: closed -> open.
   - Properties: `.drawer { transform: translateX(0); opacity: 1 }` from `@starting-style { transform: translateX(.75rem); opacity: 0 }`; `transition: transform var(--t-base) var(--ease-out), opacity var(--t-fast) linear`.
   - Duration: 180 ms. Interruptible: yes (CSS transition; a second selection replaces content without re-running the enter: the element persists, only its children change).
   - Reduced motion: `--t-base: 0ms; --t-fast: 0ms` (set globally under `prefers-reduced-motion: reduce`), so it appears in place.
2. **Inspector close**
   - Trigger: Close button, Escape. Properties: opacity 1 -> 0 over 120 ms, then unmount (`transitionend` or a 120 ms timeout guarded by the reduced-motion query); the grid column collapses after unmount. Focus returns to the listbox before the fade starts (existing `requestAnimationFrame(focusTimeline)`).
   - Reduced motion: unmount immediately.
3. **Row selection**
   - Trigger: click or j/k. Properties: `background-color`, `box-shadow`. Pointer: `transition: background-color var(--t-fast) linear`. Keyboard: the listbox sets `data-nav="keyboard"` on keydown and the transition is 0 ms under `[data-nav="keyboard"] .tl-row`.
   - Interruptible: yes. Reduced motion: 0 ms.
4. **Live event row arrival**
   - Trigger: a row whose `event_id` was not in the previous render and is inside the viewport (the virtualizer tells us). Properties: one-shot `animation: arrive 400ms linear` on `background-color` from `var(--accent-bg)` to transparent.
   - Rule: never animate rows revealed by scrolling; mark newly arrived ids in a `Set` with a 500 ms expiry so re-renders do not replay it.
   - Reduced motion: no animation; the row simply appears (the live bar already announces activity with `aria-live="polite"`).
5. **Tab switch (Timeline <-> Waterfall <-> Story <-> Findings)**
   - Trigger: tab click, `?view=` change. Properties: `.pane > * { animation: fade-in var(--t-fast) linear }` on mount. The underline moves instantly.
   - Interruptible: a second switch within 120 ms just mounts the new pane. Reduced motion: 0 ms.
6. **Finding -> evidence jump**
   - Trigger: `jumpTo(eventId)`. States: the target row gets `data-landed` for 600 ms. Properties: `box-shadow: inset 0 0 0 2px var(--accent)` fading to the normal selected state via `animation: landed 600ms var(--ease-out) 1`.
   - Interruptible: a new selection removes `data-landed`. Reduced motion: the ring is shown solid for 600 ms then removed (no fade), which still tells you where you landed.
7. **Waterfall zoom / pan**
   - No animation on bars. Axis tick labels: `opacity` 0 -> 1 over 120 ms when the tick set changes (keyed by value). Reduced motion: 0 ms.
8. **Filter changes**
   - No animation. The `progress` status text updates; `aria-live="polite"` already announces it.
9. **Runs list live refresh**
   - Same as 4 for new rows; existing rows keep DOM identity (React keys by run id) so nothing moves except the inserted row at the top. The selected row stays selected by id, not index.
10. **Sign-in and workspace switch**
    - `main` gets `animation: fade-in var(--t-base) linear` on route change (Next.js `template.tsx` in the `w/[workspace]` segment). Reduced motion: none.

### D. Implementation checklist

- [ ] Add `--t-fast`, `--t-base`, `--ease-out` tokens; zero them under `prefers-reduced-motion: reduce` (one rule covers every transition).
- [ ] Define `@keyframes fade-in`, `arrive`, `landed` once in `globals.css`; nothing else uses `@keyframes` except the existing `spin`.
- [ ] Only `transform`, `opacity`, `background-color`, `box-shadow` are transitioned; never `width`, `height`, `grid-template-columns` (the inspector column change is a reflow, not an animation).
- [ ] `focus-visible`: keep the global `outline: 2px solid var(--accent); outline-offset: 2px`; inside dense rows use `outline-offset: -2px` so the ring is not clipped by `overflow: hidden` (existing pattern in `.timeline:focus-visible .tl-row[data-selected]`).
- [ ] Hit areas: rows are 30-36 px tall (fine); tab targets >= 32 px tall with `padding: .5rem .75rem`; rail links 32 px; the inspector tabs 30 px; the "Show it" inline control gets `padding: .1rem .35rem; margin: -.1rem -.35rem` so its hit area is larger than its text; the `kbd` hints are `aria-hidden`.
- [ ] Keyboard-vs-pointer selection: set `data-nav` on the listbox (`onKeyDown` -> `"keyboard"`, `onPointerDown` -> `"pointer"`).
- [ ] Live arrival marking happens in `useLiveEvents` consumers, not in the virtualizer; a `Set<string>` with timestamps, pruned on each render, bounded to the last 200 ids (bounded buffers, INV-4 spirit).
- [ ] Playwright: one spec asserts that with `reducedMotion: "reduce"` the computed `transition-duration` of `.drawer` is `0s`, and that no element animates at load (`document.getAnimations().length === 0` after `load`).
- [ ] axe: motion adds no new roles; keep `aria-live` regions unchanged.

### E. Do's and don'ts

Do
1. Let numbers change in place; the readout is the animation.
2. Slide the inspector 12 px from where it belongs, once, and never bounce.
3. Tint a new live row for 400 ms, only if it is on screen.
4. Zero every duration under reduced motion with one token rule.
5. Return focus before you fade anything out.

Don't
1. Don't animate the first paint, the rail, the tabs' underline or the bars.
2. Don't scale, bounce, or move anything on hover.
3. Don't animate `grid-template-columns` when the inspector opens; let it reflow.
4. Don't stagger live rows; they are a stream, not a reveal.
5. Don't run a keyframe that cannot be interrupted by the next keypress.
