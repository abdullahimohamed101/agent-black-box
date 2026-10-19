# Landing site brief: Agent BlackBox v2 ("One Red Line", on the web)

Status: design brief for the v2 redesign of `site/`, written 2026-10-18 for the Phase 20 builder (step 13 of
`docs/plans/active/phase-20-release-polish.md`). Supersedes the layout of the baseline `site/` being built in the
`phase-20-site` worktree; keeps its sources of truth (tokens, fonts, claims table, `scripts/check-site.sh`).

Inputs read: the plan's decisions D2, D5, D11-D13, D15, D16 and its "Claims to verify" table; ADR-166 and
`docs/design/pass-2/01-direction.md` (the "One Red Line" direction); `apps/web/design-tokens/tokens.json` and the CSS the
app's generator emits; 14 product screenshots in `docs/screenshots/pass-2/`; the baseline `site/` (`index.html`,
`build.mjs`, `screenshots.json`); the sibling briefs `docs/design/launch/positioning-brief.md` (WHAT we say: headline,
proof points P1-P6, glossary, FAQ, hold-until-G2 table; it landed after the concepts were built, and section 3 below
now follows it) and `docs/design/launch/video-brief.md` (the 18 s teaser loop the site hosts, section 3 item 5 below).
Where the positioning brief says the site is "no script" (its section 7) it predates the owner's constraint change;
its copy stands, its medium note does not. Only claims in the plan's table are used; `--live` is never mentioned on
the page (the footer status line admits it exists and is untested, verbatim from the positioning brief).

Constraint revision received mid-task (from the owner, through the coordinator): the no-JavaScript rule is lifted.
The constraints that remain hard are listed in section 2 and the whole brief is written against them.

Evidence vocabulary: VERIFIED = run here and observed; ASSUMED = reasoned.

---

## 1. Design principles, and what "top-notch" means here

A developer who lands on this page is deciding, in under ten seconds, whether this is a real tool or a slide deck.
"Top-notch" for this product is not spectacle; it is the feeling that **the page was made by the people who made the
product, with the same hand, and that nothing on it is pretending**. Concretely:

1. **The fault line is the idea.** One red line on the page, drawn once, where the agent broke. The hero *is* a run
   (device-less, real data shape, real typography), not a picture of one. Red appears on that line, its label, and the
   words `Failed` / `error` / `run.failed`. Nowhere else: not in buttons, headings, callouts, icons, the OG card's
   background, or hover states.
2. **The page is the product, more composed.** Same tokens, same two typefaces, same hairlines, same status grammar
   (glyph + word), same 4 px control radius and 0 elsewhere, no shadows, no cards as a layout device. The only things the
   product does not have are four larger display steps and the section rhythm.
3. **Proof by the real thing.** Every image is a screenshot the release script regenerates (`D11`); each is cropped to one
   insight and annotated with numbered hairline callouts positioned in *source pixels* (so they stay exact at every width).
   No device frames, no browser chrome, no mock data in images.
4. **Honest to the claim.** Every sentence maps to a row in the claims table. No counts, no "production ready", no
   "trusted by", no stars widget (a GitHub fetch would be a third-party request anyway). The trust signals are the
   license, the self-host commands, "no API key", "no third-party requests", and the fact that the page proves the last one.
5. **Commands over promises.** The primary call to action is "run it on your machine": the quickstart's five commands you
   can copy, visible in the first scroll. Reading the source is the secondary action. There is no sign-up, there is nothing to sign up to.
6. **Fast by construction, not by optimisation.** Static export, three font files, no external request, DOM hero
   (nothing to download for the LCP element), lazy screenshots in sized boxes, one small JS bundle for three islands.
   Budgets in section 9 are enforced by a script, not by intent.
7. **Motion carries meaning or does not happen.** The fault line draws once (280 ms, the product's own duration); the
   inspector swaps content; sections settle into place on scroll. Everything is complete and legible under
   `prefers-reduced-motion`, with JavaScript disabled, and in a browser without scroll-driven animations.

Non-goals (refused on purpose): a theme toggle (ADR-166 non-goal; the page follows the system), hero video, logo walls,
testimonials, gradients, glass, glow, a second accent, animated backgrounds, "AI" sparkle iconography, carousels,
cookie banner (there is nothing to consent to), newsletter box, chat widget.

---

## 2. Constraints and the stack decision

### Hard constraints (owner, 2026-10-18)

| # | Constraint | How the brief meets it | Check |
| --- | --- | --- | --- |
| C1 | Zero third-party requests at runtime | fonts, images, scripts and styles are all relative URLs in the export; no `<link rel=preconnect>`, no remote `src` | `check-site.sh network` (Playwright request log, every origin must equal the page's) |
| C2 | Static export to GitHub Pages under `/agent-black-box/` | Next `output: "export"`, `basePath`/`assetPrefix` from `ABB_SITE_BASE_PATH` (default `/agent-black-box`), `trailingSlash: true`, `.nojekyll`; the export is also served at `/` in the check so both work | `check-site.sh links` serves the export under the sub-path and crawls it |
| C3 | Real HTML in the static output (readable without JS, indexable) | every section is a Server Component; the three islands render their full initial state on the server (rows, selected row, inspector text, first gallery panel, `<pre>` blocks) | `check-site.sh nojs`: Playwright with `javaScriptEnabled: false` asserts the hero rows, quickstart commands and all gallery images exist in the DOM |
| C4 | WCAG AA, reduced motion for all motion, light + dark | tokens' contrast table; axe on both schemes; every animation inside `prefers-reduced-motion: no-preference`; islands check `matchMedia` before animating | `check-site.sh a11y` (axe, both schemes, 375 and 1440) |
| C5 | Performance budget, enforced | section 9 | `check-site.sh perf` |
| C6 | Dependencies pinned and justified; prefer few | section 2.3; `scripts/audit-lockfiles.sh`, `scripts/check-pins.sh` unchanged | existing scripts |

### 2.1 Decision: Next.js static export, as a pnpm workspace package `site/`

Evaluated: (a) **Next 16 static export**, (b) **Astro**, (c) **Vite + React**.

- **(a) Next static export - chosen.** Zero new framework dependencies: `next@16.4.x`, `react@19.3.x`,
  `react-dom` are already pinned by `apps/web`, so the lockfile gains nothing a reviewer has not already audited.
  The design-tokens pipeline (`apps/web/scripts/tokens-lib.mjs`), `next/font/local` with the same `--font-plex-sans`
  / `--font-plex-mono` variable names, the Playwright + axe setup, and the team's component habits all carry over.
  Cost: the React runtime (~95 KB gzipped for an App Router page, ASSUMED from Next 15/16 baselines; measured in step
  H4) is paid for three small islands. That is inside the owner's 150 KB budget and is the price of "same hand as the
  product". Static export supports everything the page needs; it does not support `next/image` optimisation (we
  pre-generate WebP ourselves, section 8) or middleware (not needed).
- **(b) Astro - rejected.** Best raw numbers (0 KB JS for the static parts, islands only hydrate what they need) and a
  very good fit for a page like this. Rejected because it is a second framework in the repo: a new build tool, a new
  component syntax, its own Vite and ~250 transitive packages to audit under `audit-lockfiles.sh`, and nobody on the
  project maintains it. If the budget in section 9 is ever missed because of the React runtime, Astro is the recorded
  fallback (it can host the same React islands).
- **(c) Vite + React SPA with pre-render - rejected.** Needs a pre-render plugin or a Playwright snapshot step to satisfy
  C3, plus routing, head management and font loading that Next already provides.

Next-specific settings (step H1): `output: "export"`, `basePath`, `assetPrefix`, `trailingSlash: true`,
`images: { unoptimized: true }`, `reactStrictMode: true`, `experimental.optimizePackageImports` not needed. The `<head>`
uses the Metadata API with `metadataBase` unset (relative) and explicit `openGraph.images` pointing at the exported PNG.

### 2.2 Motion and interaction libraries: none

CSS does the fault-line draw, the scroll-settle and transitions; React state does the hero selection, the copy buttons and
the gallery tabs. A motion library (Framer Motion ~32 KB gz, GSAP ~25 KB) buys nothing the spec needs. Scroll-driven
animations use `animation-timeline: view()` inside `@supports` with the static end state as the default.

### 2.3 Dependencies (the five questions, `docs/BUILD_PROMPT.md`)

| Package | Scope | Platform provides? | Maintained | Surface | Local alternative | Lock-in | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `next`, `react`, `react-dom` | runtime+build | no | yes, already pinned in `apps/web` | already audited | n/a | same as the app | reuse the exact pins |
| `@playwright/test`, `@axe-core/playwright` | dev (checks) | no | already pinned | already audited | n/a | none | reuse |
| `typescript`, `eslint` config | dev | already in root | yes | already audited | n/a | none | reuse |
| image encoder (`sharp`) | build | **Chromium can encode WebP from a canvas** (Playwright, already present) | - | large native package | yes: `scripts/site-images.mjs` draws each PNG to a canvas in headless Chromium and exports WebP at q=0.82 | - | **not added**; AVIF is dropped (Chromium cannot encode it; WebP is 4-6x smaller than PNG for UI screenshots, enough for the budget) |
| Lighthouse | dev | no | yes | large, slow on a 2-CPU box | yes: measure LCP/CLS with `PerformanceObserver` inside Playwright under CDP CPU/network throttling | - | **not added** |

Net new packages in the lockfile: **zero**. `site/package.json` lists the same versions as `apps/web` so pnpm dedupes.

---

## 3. Page architecture

One page, eleven sections. Order follows the pattern the Evil Martians study found across 100 dev-tool pages
(hero -> trust -> problem-led features -> proof -> supporting -> final CTA; source list in section 13) adapted to a
product with no customers to quote: the trust block is the self-host commands, and the proof is the product itself.
Copy comes from the positioning brief (its sections 1, 3, 7); the wording in the concept mockups predates it and is
superseded where it differs (the mockups' headline `See exactly where your agent broke.` is the brief's option 2
in spirit; the page uses option 1).

| # | Section (`id`) | Purpose | Copy (positioning brief §7 unless marked PROPOSED) | The one thing the reader must feel or believe |
| --- | --- | --- | --- | --- |
| 0 | Top bar | orientation; one outlined control | brand wordmark; How it works · Screens · Run the demo · Docs · `Source on GitHub` (outlined; G2-gated: absent until the repository is public) | "this is a real project with a repository" |
| 1 | Hero (`#top`) | the promise and the proof in one screen | H1 `See what your agent actually did.`; sub `A self-hosted flight recorder for AI agents. Every model call, tool call, command and retry, as typed events, and the step where it broke.`; primary `Run the demo` (-> `#quickstart`); secondary `Source on GitHub` (G2-gated; one CTA until then); fold line `Apache-2.0 · Python 3.12 · Docker · PostgreSQL only` ("Apache-2.0" only after AC1 and G2); **the trace** (island, P1: the failed run with the fault line and the inspector open) with caption | "that is what my failed run would look like, and I can see why it failed from here" |
| 2 | Before and after the line (`#why`) | P1+P2+P4 in the product's own terms, anchored to the trace | left: `Before the line: what the agent did` + 3 mono rows; right: `After the line: what it cost` + 3 mono rows (`2 retries · $0.0167 spent after` as the figure) (PROPOSED headings; rows are product strings) | "it records what happened *and* what the failure cost" |
| 3 | Quickstart (`#quickstart`) | the primary action; trust by commands | `Run it in about two minutes`; the five commands from `docs/quickstart.md` verbatim (AC12; until that doc exists, the README's block); the demo sentence (`make demo` records three scripted agents … No API key, no network. The same runs, findings and costs every time.) gated on AC7/AC8; the four-line SDK snippet from the README; prerequisites `Python 3.12 · Node 22 · Docker` | "I could have this running in a few minutes without asking anyone for a key" |
| 4 | How it works (`#how`) | P2, the mechanism in three steps (scroll narrative) | sticky: `Typed events, then one run.` (PROPOSED) + the lede `Accepted events are immutable; summaries, costs and findings are rebuilt from them.`; steps `01 Wrap the agent` (SDK snippet), `02 Events arrive typed` (event JSON), `03 Read the run from the top` (the headline sentence) | "it is typed events, not log lines; that is why the UI can be this specific" |
| 5 | Watch (`#watch`) | the 18 s silent teaser loop from the video brief (G1-gated; the section does not render until the files exist) | `Ninety seconds, no sound.` (PROPOSED) link to the full cut; `<video autoplay muted loop playsinline preload="metadata" poster>` with `<track kind="captions">`, per video-brief §5.5; reduced motion: poster + play button | "I can see it move without installing it" |
| 6 | Screens (`#screens`) | proof gallery: P1, P3, P4, P5, P6 in that order, cropped and annotated | `The product, not a mockup of it.` (PROPOSED); lede `Every image on this page is a screenshot of the dashboard on the demo data, regenerated by script.`; per panel: the proof point's sentence as the note, the brief's qualifier in the caption (P6: "Policies apply to agents that ask through the SDK guard.") | "the rest of the product is as considered as the hero" |
| 7 | What it records (`#records`) | scope and integrations with the qualifier | the event families with one example each; `Captured telemetry is treated as hostile input`; `Adapters for LangGraph, OpenAI, Anthropic and MCP, tested offline against the real libraries` | "it fits the stack I have" |
| 8 | What stays on your machine (`#privacy`) | objections answered as facts, limit included | the three sentences from positioning §7 (redaction before storage and metadata-only by default; no third-party request from the product or this page; PostgreSQL and nothing else) plus the limit sentence (`Stored events are never scrubbed retroactively; unknown-shaped secrets can pass in full capture mode`) | "this will not leak my prompts or my keys, and they told me where it could" |
| 9 | Architecture in one picture (`#architecture`) + FAQ (`#faq`) | the README's six-line diagram as inline SVG with `Simple on purpose: no Kafka, no ClickHouse, no Redis until a measured trigger says otherwise.`; then the four FAQ items from positioning §7 (logs, LangSmith/Langfuse, OpenTelemetry, "does it stop a bad action") in `<details>` | "they have thought about the awkward questions" |
| 10 | Final CTA + footer | safety net, provenance, honest status | the five commands as one line + `Read the quickstart`; footer columns Product / Project / Community; the status line verbatim: `Early release. Single-node Docker Compose. No hosting guide yet (TLS, domain, real OIDC are documented as not covered). Real-model mode exists in two examples and has not been run by the project.`; `Apache-2.0 · fonts IBM Plex under SIL OFL 1.1 · this page makes no third-party request` | "early, honest, open" |

CTA wording everywhere is the positioning brief's: `Run the demo`, `Read the quickstart`, `Source on GitHub`,
`Read the security notes`; never "Get started", "Try it free". Navigation anchors: `#how`, `#screens`, `#quickstart`;
"Docs" links `docs/README.md` in the repository. All external links carry `rel="noopener"`; none is a dead link (the
repository link exists only after G2 and is checked by `release-checklist.sh links`). The teaser video files are
excluded from the page-weight budget (video brief) but the Watch section must not change LCP or CLS: `preload="metadata"`,
sized box, poster as a lazy image.

---

## 4. Wireframes

Legend: `═` section rule (1 px hairline), `━` the fault line (2 px red), `[ ]` control, `{ }` island.

### 4.1 Hero, 1440 (content width 1152, gutters 24)

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ ∿ Agent BlackBox                              How it works  Screens  Self-host  Docs [GitHub] │ 56 px, hairline below
├──────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                          │ 56
│  Open source · Apache-2.0 · self-hosted                     (mono 12, ink-2)             │
│  See exactly where                                          (56/60, 600, max 20ch)      │
│  your agent broke.                                                                       │
│  A flight recorder for AI agents. Every model call, tool call, shell command, retry and  │ (20/28, ink-2, 56ch)
│  failure is recorded as a typed event and shown as a run you read from the top, with    │
│  one red line at the first error.                                                        │
│  [ Run the demo on your machine ]  [ Read the source ]                                   │ 44 px buttons
│  No API key to try it · No third-party requests · Python SDK   (mono 12, ink-2)          │
│                                                                                          │ 40
│ ┌────────────────────────────────────────────────────────┬────────────────────────────┐ │ hairline-strong top/bottom
│ │ Runs / Migrate billing schema   ✕ Failed · 9d ago       │ tool.call.failed           │ │
│ │ Failed at step 7 after 6.2 s: run_migration, Connecti… │ 2026-10-07 … · #7 · coding │ │ 20/28 600
│ │ Duration 14 s  Cost $0.0356  Tokens 4,900 in / 460 out  │ What went wrong            │ │
│ │ +0ms    run.started  Migrate billing schema         —   │ run_migration failed: …    │ │ rows 36 px, mono 13
│ │ +100ms  agent.started                               —   │ Tool call                  │ │
│ │ +500ms  file.read  db/migrations/0042_billing.sql   —   │  Tool      run_migration   │ │
│ │ +800ms  llm.request.completed  model-x · 2,300 in…  2.1s│  Duration  3.1 s           │ │
│ │ +3.1s   tool.call.started  run_migration      First error, step 7 ─┐                │ │ label on the line
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┥ Payload       │ │ the fault line
│ │▌+6.2s   tool.call.failed  run_migration · Connecti… 3.1s  ✕ error │ { "error": {…} }  │ │ selected: wash + accent edge
│ │ +7.2s   retry.attempted  Retry #1 after Connection…  —   ↻ retry  │                   │ │ caution word
│ │ +10.3s  tool.call.failed  run_migration · Connecti… 2.9s  ✕ error │                   │ │
│ │ +14.4s  run.failed                                   —   ✕ error │                   │ │
│ │ 9 of 15 events shown        j and k move, Enter opens, e jumps to the first error    │ │ 12 px foot
│ └────────────────────────────────────────────────────────┴────────────────────────────┘ │ inspector 352 px
│  Demo data from make demo, rendered by the page from fixtures. The dashboard draws it the same way. (12, ink-2)
└──────────────────────────────────────────────────────────────────────────────────────────┘
```
Notes: the wireframe shows the concept's copy; the built page uses the positioning brief's H1 and sub (section 3), which
are shorter, so the trace rises by one line. The H1 and the trace header are the only two places with text at 20 px or
more above the fold. The fault line must be inside the first 900 px at 1440 (VERIFIED in the concept by measurement:
the first-error row's top is at y=799 at 1440x900; at 375x812 it is at y=911, the second screen, which is acceptable
because the headline, sub and both CTAs fit the first). The trace has no frame: a hairline-strong
rule above and below, raised background; the inspector is a hairline column, not a panel. No red besides the line, its
label, `✕ Failed` and the three `✕ error` words.

### 4.2 Hero, 375 (gutter 16)

```
┌───────────────────────────────┐
│ ∿ Agent BlackBox     [GitHub] │ 56
├───────────────────────────────┤
│ Open source · Apache-2.0 · …  │
│ See exactly where             │ 36/40
│ your agent broke.             │
│ A flight recorder for AI      │ 17/24
│ agents. Every model call, …   │
│ [ Run the demo on your mach… ]│ full-width buttons, stacked
│ [ Read the source ]           │
│ No API key · No third-party … │ wraps to 2 lines
├───────────────────────────────┤ trace is full-bleed (negative gutters)
│ Runs / Migrate billing schema │
│ ✕ Failed · 9d ago             │
│ Failed at step 7 after 6.2 s: │ 18/24
│ run_migration, ConnectionTim… │
│ Duration 14 s  Cost $0.0356 … │
│ +0ms   run.started  Migrat…   │ 3 columns: time 56 px, what 1fr, status 64 px (duration hidden)
│ +100ms agent.started          │
│ +3.1s  tool.call.sta… First error, step 7
│━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━│
│▌+6.2s  tool.call.fai… ✕ error │
│ +7.2s  retry.attempt… ↻ retry │
│ 9 of 15 events … (foot)       │
├───────────────────────────────┤ inspector moves below, hairline above
│ tool.call.failed              │
│ What went wrong …  Payload …  │
└───────────────────────────────┘
```
Notes: keyboard hints in the foot stay (they are true on a phone with a keyboard); rows remain 36 px tall for touch.
No horizontal scroll (VERIFIED in concept A after iteration 2: document width 375).

### 4.3 Before / after the line (section 2), 1440 and 375

```
1440: ┌──────────── 1fr ────────────┐   ┌──────────── 1fr ────────────┐
      │ Before the line: what the   │   │ After the line: what it     │  20/28 600
      │ agent did                   │   │ cost you                    │
      │ 3 lines ink-2 (40ch)        │   │ 3 lines ink-2               │
      │ llm.request.completed  2,300 in · $0.0189 │ tool.call.failed(red)  ConnectionTimeout │ mono rows, hairline
      │ file.read              0042_billing.sql   │ retry.attempted(caution)  #1, #2        │
      │ tool.call.started      run_migration      │ run.failed(red)   +14.4 s · $0.0356 total│
375:  one column, "Before" then "After", same rows.
```
No red rule in this section (the page has exactly one fault line, in the hero). The halves are tied to the trace by
sharing its mono row style.

### 4.4 Quickstart (section 3; the concept shows four lines, the page shows the five from `docs/quickstart.md`)

```
1440: ┌─── 4fr ───┐ ┌──────────────────────── 8fr ────────────────────────┐
      │ Four commands, your machine       │ shell                              [Copy] │ code head 12 mono
      │ The demo runs a scripted agent    │ $ git clone https://github.com/<owner>/agent-black-box.git │ 13/21 mono
      │ with a deliberate failure and a   │ $ cd agent-black-box                        │
      │ retry. Same trace every time; no  │ $ make setup   # Python env, JS deps, Postgres in Docker, migrations │
      │ key, no network.                  │ $ make demo    # starts the stack and runs three scripted agents    │
      │ Python 3.12 · Node 22 · Docker    └─────────────────────────────────────────────┘
      │ (mono 12)                           Nothing leaves your network: the demo makes no outbound request. (12 ink-2)
375:  stacked; the <pre> scrolls horizontally inside its box (the page does not).
```
`make demo` is plan step 10; if the builder reaches the site before step 10 lands, the demo line and the demo sentence
are omitted (claims table: "No API key needed to try it" and "Deterministic demo" are UNVERIFIED until step 10). The
`pip install` line is never shown before PyPI publication (positioning §8, Q4). Below the block: the README's four-line
SDK snippet in a second `CommandBlock` labelled `agent.py`.

### 4.5 How it works (section 4): sticky left, three steps right

```
1440: ┌── 5fr (sticky, top 64) ──┐  ┌──────────── 7fr ────────────┐
      │ How it works (mono 12)   │  │ 01  Wrap the agent           │ 20/28
      │ Three lines in,          │  │     2 lines ink-2            │
      │ one run out.   (32/40)   │  │     [agent.py        Copy]   │ code block, 6 lines
      │ 2-sentence lede (16/24)  │  │                              │ 64 px gap
      │                          │  │ 02  Events arrive typed      │
      │                          │  │     [event           Copy]   │ 3-line JSON
      │                          │  │ 03  Read the run from the top│
      │                          │  │     the headline sentence in mono ink-2
375:  heading block, then steps in one column; nothing sticky.
```
Scroll narrative: each step settles (opacity .25->1, 12 px rise) as it enters the viewport; CSS only (section 7).

### 4.6 Screens gallery (section 5)

```
1440: ┌── 13rem tabs ──┐  ┌───────────────────── 1fr (≈880 px) ─────────────────────┐
      │▌A failed run   │  │ ┌ shot box 16:10, hairline-strong, radius 4 ───────────┐ │
      │  Headline, …   │  │ │ screenshot at 125 % width, offset 60 source px down  │ │
      │  Runs          │  │ │  [1] hairline accent box on the headline sentence    │ │
      │  Status, cost …│  │ │  [2] box on the fault row                            │ │
      │  Waterfall     │  │ │  [3] box on the inspector                            │ │
      │  …             │  │ └──────────────────────────────────────────────────────┘ │
      │  (7 tabs)      │  │ 1 note   2 note   3 note    (3 columns, 14/20 ink-2)      │
      │                │  │ caption (12 ink-2)                                       │
375:  tabs become a horizontal scrolling row (bold labels, 2 px accent underline);
      shot box 4:3 at 160 % zoom; notes in one column.
```
Callouts: `--x/--y/--w/--h` in source pixels; the box is a size container; one source pixel is
`calc(var(--zoom) * 1cqw / 1440)`. VERIFIED in concept B: the same four numbers land on the headline, the fault row
and the inspector at 1440 and at 375.

### 4.7 Records / works with (section 6) and privacy (section 7)

```
1440: two columns of a <dl>: "Records" (7 terms, each one sentence) | "Works with" (SDK + 4 adapters, qualifier)
      then a 3-column row of privacy statements, each: 14/20 600 title + 2-line body. No icons.
375:  single column.
```

### 4.7b Watch (section 5), G1-gated

```
1440: eyebrow "Watch" · h2 "Ninety seconds, no sound." · link "Full cut (1:30)" right-aligned
      ┌──────────────── 16:9 box, hairline-strong, radius 4, max 1152 px ────────────────┐
      │ <video autoplay muted loop playsinline preload="metadata" poster=…>              │ the 18 s teaser (video brief §2.2)
      │   <source av1> <source webm> <source mp4> <track kind="captions" src=…vtt>       │
      └─────────────────────────────────────────────────────────────────────────────────┘
      caption 12 ink-2: "Captions only. The demo data, recorded by script."
375:  same, full-bleed; reduced motion and Save-Data: the poster (WebP) with a 44 px outlined play control that starts
      the loop on tap (one CSS rule plus a 10-line island; no autoplay).
```
Rendered only when `site/public/assets/video/teaser.*` exist at build time (a build-time `existsSync` in the server
component), so the page ships without it until G1.

### 4.8 Architecture + FAQ (section 9): the README diagram as inline SVG (hairlines, mono labels, no colour), then four
`<details>` items, hairline between items, 16/24 question, 16/24 ink-2 answer

The four questions and their answers are the positioning brief's §7 "Objections, answered plainly", verbatim: *Why not
just logs?*, *Why not LangSmith or Langfuse?* (naming them is the brief's recommendation, owner question 2 open), *Why not
OpenTelemetry?*, *Does it stop a bad action?*.

### 4.9 Final CTA and footer

```
┌─────────────────────────────────────────────────────────────────────────────┐ hairline above, canvas (no dark band)
│ Run it on a failed run of your own.                        (32/40, PROPOSED)│
│ [ git clone … && cd agent-black-box && make setup && make demo ]  [Copy]    │ one-line pre, 13 mono (the five commands)
│ [ Read the quickstart ]  [ Source on GitHub ] (G2)                           │
├═════════════════════════════════════════════════════════════════════════════┤
│ ∿ Agent BlackBox            Product          Project          Community     │ 4 columns (2fr 1fr 1fr 1fr)
│ Early release. Single-node  How it works     Source (G2)      Contributing  │ the status line, verbatim (positioning §7)
│ Docker Compose. No hosting  Screens          Docs             Security notes │
│ guide yet (…). Real-model   Run the demo     License          Code of conduct│
│ mode exists in two examples and has not been run by the project.            │
│ Apache-2.0 · fonts IBM Plex, SIL OFL 1.1 · this page makes no third-party request. (12 ink-2) │
└─────────────────────────────────────────────────────────────────────────────┘
375: brand block and status line full width, then 2x2 link columns.
```
No dark "closing band" (the Evil Martians pattern): on this page a tonal band would read as a second surface, which the
direction forbids; the rhythm change is the larger heading and the single-line command.

---

## 5. Typography, spacing, colour, dark mode

### 5.1 Type scale (extends the product's seven steps; nothing below 12 px)

| Step | Size/line (≥1024) | 600-1023 | ≤599 | Weight | Family | Use |
| --- | --- | --- | --- | --- | --- | --- |
| site-hero | 56/60 | 44/48 | 36/40 | 600 | Sans | H1 only |
| site-h2 (= product display) | 32/40 | 32/40 | 24/30 | 600 | Sans | section titles |
| site-h3 | 20/28 | 20/28 | 18/24 | 600 | Sans | sub-titles, step titles, trace headline |
| site-lede | 20/28 | 20/28 | 17/24 | 400 | Sans, ink-2 | hero lede |
| site-body | 16/24 | 16/24 | 16/24 | 400 | Sans | prose, FAQ |
| product body-lg | 14/20 | | | 400 | Sans | notes, nav, footer, buttons (600) |
| product small | 12/16 | | | 400 | Sans or Mono | eyebrow, captions, code heads, facts line |
| product mono | 13/20 | | | 400 | Mono | trace rows, code, commands; `font-variant-numeric: tabular-nums` |

Rules: letter-spacing 0 everywhere; no upper-case labels; `text-wrap: balance` on H1/H2, `pretty` on ledes; measure 56ch
for ledes, 60ch for prose, 20ch for the H1; headings use `max-width` in `ch` rather than manual breaks.
Fonts: `next/font/local` with the three files the baseline already copies (Sans 400, Sans 600, Mono 400; Mono Medium is
not used), `display: swap`, `preload: true`, `adjustFontFallback` on, exposing `--font-plex-sans` / `--font-plex-mono`
so the token-generated `--font-sans` / `--font-mono` resolve exactly as in the app (the concept had to define these
by hand; VERIFIED that without them the token `font` shorthand falls back to serif).

### 5.2 Spacing and rhythm

Base 4 px (the token `space` scale). Section padding `--section`: 112 px (≥1024), 80 (600-1023), 64 (≤599); sections
separated by one hairline (`border-subtle`). Inside a section: eyebrow -> title 16 px, title -> lede 20, lede -> content
48. Content width 1152 px (`72rem`) with 24 px gutters; 16 px at ≤599. Grid gaps 48 px between columns at 1440, 32 at
tablet. The trace, the gallery box and code blocks are the only elements with a visible boundary; they get
`border-strong` and radius 4.

### 5.3 Colour

Tokens only (the generated `tokens.css` is the first stylesheet; the site adds no colour literal). Roles used:
`bg-canvas` (page), `bg-raised` (trace, code blocks, gallery box), `bg-wash` (selected row), `text-primary`,
`text-secondary`, `text-link`, `border-subtle/strong/control`, `interactive-default/hover/active` (one filled button,
focus rings, selection edge, callout boxes and numbers), `status-fault` (**only**: fault line + label, `✕ Failed`, the
`✕ error` words, `tool.call.failed`/`run.failed` in section 2), `status-caution` (`↻ retry`, `retry.attempted`),
`brand-mark` (the cut in the pulse glyph), `syntax-*` (code). Callouts are accent, never red, so the eye still counts one
red thing per screen. `color-data-*` is not used (no waterfall drawn in DOM).

### 5.4 Dark mode

`prefers-color-scheme` only, through the generated tokens (`:root` light, `@media (prefers-color-scheme: dark)`
overrides), `<meta name="color-scheme" content="light dark">`, two `theme-color` metas. Screenshots are served per scheme
through `<picture><source media="(prefers-color-scheme: dark)">`: the release set must contain both variants of every
featured view (D11 says "where the README uses both"; the site uses both for all seven). The OG card is light only.
VERIFIED in both concepts: dark renders with the same hierarchy; the red-300 fault line reads at 2 px on neutral-850.

---

## 6. Components (React, `site/src/components/`)

| Component | Kind | Props / data | Server-rendered state | Client behaviour |
| --- | --- | --- | --- | --- |
| `TopBar` | server | nav items | full | none |
| `Hero` | server | copy | full | none |
| `FaultTrace` | **island** | `run: TraceFixture` (from `site/fixtures/migrate-billing-schema.json`, validated against `packages/event-schema` in a test) | all rows, the first-error row `aria-selected`, inspector for that row, foot text | roving `tabindex`, `j`/`k`/arrow move, `Enter`/`Space` select, `e` jumps to first error, hover selects (mouse only), inspector swaps with `aria-live="polite"`; fault-line draw is CSS and runs regardless of JS |
| `BeforeAfter` | server | rows | full | none |
| `CommandBlock` | **island** (small) | `lines`, `label`, `comment` spans | `<pre>` with `user-select: all`, `$` prompts `user-select: none`, a visually complete `Copy` button that is disabled until hydration | `navigator.clipboard.writeText` of the lines without prompts/comments; "Copied" for 1.5 s; `aria-live` status; falls back to selecting the block if the API is absent |
| `HowItWorks` | server | three steps | full | scroll-settle is CSS |
| `ScreenGallery` | **island** | `panels[]` (image pair, callouts in source px, notes, caption) | all seven panels in the DOM; panel 1 visible; others `hidden` **only after hydration** (no-JS users see all panels stacked with the tab list rendered as in-page links) | real `role="tablist"`/`tab`/`tabpanel`, arrow keys, `aria-selected`; swapping a panel does not animate |
| `Callout` | server | `--x/--y/--w/--h` | positioned via container units | none |
| `RecordsAndWorksWith`, `Privacy`, `Faq` (`<details>`), `FinalCta`, `Footer` | server | copy | full | none |

Island JS budget (section 9): the three islands together ≤ 25 KB gzipped on top of the framework.

---

## 7. Motion and interaction spec

| Moment | What moves | Duration / easing | Trigger | Reduced motion | No JS | No `view()` support |
| --- | --- | --- | --- | --- | --- | --- |
| Fault line draw | the 2 px rule scales from 0 to 1 (`transform-origin: left`), then its label fades in | 280 ms `cubic-bezier(.2,0,0,1)` (`--motion-duration-fault-draw`, `--motion-easing-out`), 600 ms after load; label 200 ms | page load (CSS `animation`) | static, complete | runs (CSS) | runs |
| Row selection | background to `bg-wash`, 2 px accent inset edge | 120 ms (`--motion-duration-fast`) | hover / focus / keys | instant | hover only | n/a |
| Inspector swap | content replaces; 8 px rise of the new content | 160 ms (`--motion-duration-inspector`) | selection | content replaces, no rise | static inspector | n/a |
| Scroll settle (steps in "How it works", gallery block) | opacity .25->1, 12 px rise | scroll-linked, `animation-range: entry 0% entry 40%` | `animation-timeline: view()` | not applied | runs (CSS) | not applied (`@supports`) |
| Copy | button label to "Copied" | 0 | click | n/a | disabled button | n/a |
| Gallery tab change | none | 0 | click / keys | n/a | all panels visible | n/a |
| Focus | 2 px accent outline, 2 px offset | 0 | keyboard | n/a | yes | n/a |
| Anchor navigation | `scroll-behavior: smooth` | browser | nav links | `auto` | yes | n/a |

Rule: every `@keyframes` and `transition` lives inside `@media (prefers-reduced-motion: no-preference)`; islands read
`matchMedia("(prefers-reduced-motion: reduce)")` before any JS-driven class that would animate (today none). The
fault-line draw is CSS only so it cannot depend on hydration timing. No parallax, no scroll-jacking, no autoplay.

---

## 8. Image spec and the featured screenshots

Formats: `<picture>` with `source type="image/webp"` (per scheme) and `<img>` PNG fallback, `width`/`height` attributes
from the PNG header, `loading="lazy"`, `decoding="async"`, inside a box with `aspect-ratio` so there is no layout shift.
The hero loads no image (DOM trace). WebP is encoded by `scripts/site-images.mjs` in headless Chromium (canvas
`toBlob("image/webp", 0.82)`), so no encoder dependency is added; the check fails if a WebP is larger than 120 KB or a
PNG larger than 400 KB (D11's budget). Sizes: 1440 px wide source only (the box shows it at 125 % / 160 % zoom, which is a
crop, not an upscale beyond 1:1 at any viewport ≥ 1152 px; at 375 the 160 % zoom shows a 900 px-wide crop at 375 px, a
downscale). No `srcset` needed. Alt text rule: one sentence, says what the view shows and where the red is, never
"screenshot of"; captions carry the context (view, tab, data set).

Featured views (from `docs/screenshots/pass-2/`, to be regenerated as the release set by step 12 with the same views):

Order follows the positioning brief's proof points (P1, P3, P4, P5, P6); each panel's notes use that proof point's
sentence and its caption carries the required qualifier.

| # | Tab label | Source today (release name) | Crop (zoom %, offset px) | Callouts (source px) | Why this one |
| --- | --- | --- | --- | --- | --- |
| 1 | Where it broke (P1) | `step5b-failed-inspector-1440-{light,dark}` (`failed-run-story`) | 125, 60 | headline (18,104,720,40); fault row (0,494,1024,36); inspector (1030,252,400,230) | the whole promise in one view |
| 2 | With evidence (P3) | `step5b-failed-findings-1440-*` (`findings`) | 140, 180 | the Critical finding row (216,420,900,32); "Show evidence" (1004,423,108,26) | findings point at events |
| 3 | What it cost (P4) | `step5d-analytics-cost-1440-*` (`cost-analytics`) | 140, 140 | "$22.16 spent…" sentence (24,168,640,28); "Where the money was wasted" block (24,232,640,110) | money in plain words |
| 4 | Many agents, one picture (P5) | `step5b-failed-waterfall-1440-*` (`waterfall-multi-agent` once the demo's multi-agent run exists) | 125, 60 | vertical fault line + label (728,336,130,220); critical path (452,362,668,24) | the same red line in a second shape |
| 5 | Two runs, step by step (P5) | `step5b-compare-1440-*` | 125, 40 | the divergence sentence (24,112,640,30); the step-22 "Divergence" row (24,510,1390,24) | unique capability; a sentence, not a diff viewer |
| 6 | A person in the loop (P6) | `step5c-review-approvals-detail-1440-*` (`policy-approval`) | 125, 60 | the pending `github.push` row (24,236,950,40); the detail header "Waiting · 5 h 59 min left" (1024,224,390,60) | a person in the loop, recorded |
| 7 | Runs | `step5d-sweep-runs-1440-*` (`runs-list`) | 125, 60 | failed row with "Failed at step 7 · 2 retries · $0.0167 spent after" (226,422,600,48); attention filters (20,228,140,150) | the list says where and what it cost |
| OG | - | hero trace drawn in HTML (not a screenshot) | - | - | section 10 |

Not featured and why: settings, members, API keys (configuration, not the story); analytics empty/error states; login
and not-found; security findings (would be an 8th panel; the brief caps at 7 so the tab row fits 1440 without
wrapping - if the positioning brief wants the security story, swap it for Compare). The 375 runs screenshot is used
only in the README, not on the site (the site itself is the mobile proof).

---

## 9. Performance and accessibility budgets, and the checks that enforce them

Budgets (transfer sizes, gzip/brotli as GitHub Pages serves them):

| Item | Budget | Measured in |
| --- | --- | --- |
| JavaScript, total for the page | ≤ 150 KB (framework ≤ 110, islands ≤ 25, Next runtime chunks ≤ 15) | `check-site.sh size` sums `_next/static/**/*.js` referenced by `index.html` |
| CSS | ≤ 25 KB | same |
| Fonts | 3 files, ≤ 70 KB total | same |
| Initial view (HTML + CSS + JS + fonts + favicon) | ≤ 400 KB | Playwright request log on first paint |
| Each screenshot | WebP ≤ 120 KB, PNG ≤ 400 KB | `site-images.mjs` |
| Whole page, everything loaded, light scheme | ≤ 1.5 MB | request log after scrolling to the end |
| LCP (the H1) | ≤ 2.0 s | Playwright + CDP: `Emulation.setCPUThrottlingRate 4`, `Network.emulateNetworkConditions` 1.6 Mbps down / 750 kbps up / 150 ms RTT, 375x812; `PerformanceObserver` `largest-contentful-paint` |
| CLS | ≤ 0.02 | `PerformanceObserver` `layout-shift` while scrolling the page |
| TBT proxy | long tasks after load ≤ 200 ms total | `PerformanceObserver` `longtask` |
| Hydration | islands interactive ≤ 1.5 s after LCP on the same profile | `check-site.sh perf` clicks a gallery tab and waits for the panel |

Accessibility: WCAG AA via the tokens' contrast table (generated), axe with no violations at 375 and 1440 in light and
dark; keyboard path through nav, trace rows, copy buttons, tabs and `<details>`; visible focus; `aria-live` on the
inspector and the copy status; `<main>`, one `<h1>`, sections labelled by their headings; skip link; the trace's rows are
`role="row"`-free list items with `aria-selected` on the selected item (a listbox pattern: `role="listbox"` on the list,
`role="option"` on rows); images have alt text; motion reduced as in section 7; no text below 12 px; touch targets ≥ 44 px.

`scripts/check-site.sh` (extend the baseline's; one subcommand per line, all run by default, `--only <name>` for one):

| Subcommand | Asserts |
| --- | --- |
| `build` | `pnpm --filter @abb/site build` exits 0 and `site/out/index.html` exists; no `<script src="http`/`//`, no `@import`, no `url(http`, no `<iframe>`, no inline event handlers in the export (the baseline's grep, kept) |
| `nojs` | with JS disabled: H1, trace rows (≥ 9), the 4 command lines, 7 gallery `<img>`, FAQ questions present in the DOM |
| `network` | every request during load + full scroll has the page's origin; count of external requests must be 0 (both when served at `/agent-black-box/` and at `/`) |
| `links` | every `href`/`src` in the export resolves (local) or is the repository URL (the one allowed external target); no `#` dead anchors |
| `size` | the budgets above from the export's file sizes (gzip computed locally) |
| `perf` | LCP/CLS/longtask under the throttled profile, three runs, median |
| `a11y` | axe, 2 widths x 2 schemes, 0 violations; keyboard walk (Tab reaches every control in order, Escape not required) |
| `motion` | with `reducedMotion: "reduce"`: computed `animation-name` is `none` on `.fault-line`, `.step`, `.gallery`; with no-preference the fault line's `transform` changes between 0 ms and 1 s |
| `shots` | screenshots of the export at 375 and 1440, light and dark, to `docs/screenshots/release/site-*.png` (AC11), and asserts no horizontal overflow (document width == viewport) |
| `claims` | greps the export for the positioning brief's banned words (§4: seamless, powerful, effortless, intuitive, enterprise-grade, production-ready, battle-tested, blazing, AI-powered, guardrails, real-time, …) and for `SaaS`, `SOC`, `stars`, `pip install`, digits followed by `tests`; requires the qualifier "tested offline" next to "LangGraph" and "ask through the SDK guard" next to "Policies"; the `--live` word may appear only inside the footer status line |

CI: `site-check` job runs all of it on the PR; `shots` writes nothing in CI (gated on `SHOTS_DIR`, like D11).

---

## 10. OG / social card and favicon

**Social card** `site/public/assets/social-card.png`, 1200x630, light scheme only, generated by `scripts/brand.sh` from
`brand/social-card.html` (Playwright, deviceScaleFactor 1, PNG ≤ 200 KB):

```
┌────────────────────────────────────────────────────────────┐ canvas #f6f8fa, 64 px padding
│ ∿ Agent BlackBox                       Open source · Apache-2.0 · self-hosted (mono 20, ink-2) │
│                                                            │
│ See exactly where                                          │ Plex Sans 600, 72/80, ink
│ your agent broke.                                          │
│                                                            │
│ ┌──────────────────────────────────────────────────────┐   │ miniature trace, 5 rows, mono 22, raised bg, hairlines
│ │ +0.8s   llm.request.completed   model-x        2.1 s │   │
│ │ +3.1s   tool.call.started       run_migration    —   │   │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━│ fault line 4 px (scaled), label "First error, step 7"
│ │ +6.2s   tool.call.failed        ConnectionTimeout  ✕ error │ red word
│ │ +7.2s   retry.attempted         Retry #1         ↻ retry │
│ └──────────────────────────────────────────────────────┘   │
│ A flight recorder for AI agents.          (Sans 28, ink-2) │
└────────────────────────────────────────────────────────────┘
```
Meta: `og:title` "Agent BlackBox", `og:description` = the hero lede, `og:image` relative to the export (absolute URL is
only known after G2; the Metadata API emits it from `metadataBase` when the owner sets `ABB_SITE_URL`), `twitter:card`
`summary_large_image`. Alt for the card: "Agent BlackBox: a run timeline with a red line at the first error."

**Favicon set** (D5, from `brand/pulse-mark.svg`): `favicon.svg` with an embedded `<style>@media (prefers-color-scheme:
dark)` that flips the stroke to neutral-100 (the orange cut stays orange-400 in dark); `favicon-32.png`,
`apple-touch-icon.png` 180 (mark on canvas with 20 px padding, no transparency), `icon-512.png` (for the README and a
`site.webmanifest` with `display: browser`). The wordmark in the top bar is the mark at 24 px + "Agent BlackBox" at
14/20 600, exactly as in the app.

---

## 11. Concept mockups: what was built, what was seen, what to take

Location: `docs/design/launch/landing-concepts/` (`base.css`, `tokens.css` generated from the app's tokens, the three
fonts, `concept-a.html`, `concept-b.html`, `shoot.mjs`, `shots/`). Static HTML + CSS with a 20-line inline script in A
standing in for the hero island. Screenshots at 1440 and 375, light and dark (full page, reduced motion so the final
state is visible) plus a 1440 hero shot with motion on, after the fault line has drawn. Three rounds: VERIFIED by viewing
the PNGs after each.

- **Round 1 finding:** both pages rendered in a serif. Cause: `tokens.css` defines `--font-sans` as
  `var(--font-plex-sans), "IBM Plex Sans", …` and the app sets `--font-plex-sans` through next/font; undefined, the whole
  `font` shorthand became invalid. Fix (and a rule for the builder, section 5.1): define the Plex variables. Also found:
  B's tabs were unstyled (missing wrapper), A's hero had a side aside that added noise, A's "how it works" overflowed at
  375 (`1fr` grid tracks), and full-page screenshots hid scroll-driven steps at 25 % opacity (now shot with reduced motion
  and a separate motion shot).
- **Round 2 finding:** A's fault line was below the 900 px fold at 1440 (trace started at y≈595); tightened the hero to
  3.5 rem top, 2.5 rem before the trace and a 2-line lede: the fault row is now at y≈805 with motion on. B's gallery crop
  was unreadable (whole 1440 screenshot in a 610 px box); replaced with the 125 % container-unit crop and source-pixel
  callouts, which land exactly at both widths. A's 375 width went from 488 to 375.
- **Round 3:** no overflow (only clipped spans inside scrolling `<pre>` boxes are reported, which is correct), dark mode
  holds in both, red appears only on the line, its label and the failure words.

**Verdict and the composite the builder should build:**
- **Hero: Concept A.** The trace with the inspector is the strongest single idea on either page: it is the product, and
  the fault line is at the first screen. B's page-wide rule is handsome but it is a *second* red line once the trace
  exists, and the direction allows one.
- **Section 2 from B** ("Before the line / After the line") directly under the hero, without B's rule.
- **Quickstart block from B** (install-first, like a runtime's page) as section 3, before "How it works", with the five
  commands instead of the concept's four.
- **"How it works" from A** (sticky left, three steps, code blocks with copy heads, scroll settle).
- **Gallery from B** with the `cqw` callouts; 7 panels per section 8.
- Headline: the positioning brief's `See what your agent actually did.` with its sub. The concepts' headlines (A: `See
  exactly where your agent broke.`, B: `Your agent failed at step 7. Here is why.`) were written before that brief
  existed and are superseded; they are its option 2 and are not used. The hero composition does not change: the
  trace under the headline is the proof the positioning brief asks for (P1, "the one screen that proves the headline").
- Watch section (video brief) sits between "How it works" and the gallery; the teaser is not the hero (the DOM trace is
  the LCP element and must stay text), which keeps the budget and the no-JS reading intact.

---

## 12. Handoff checklist for the Sonnet builder (ordered, each step small and testable)

Branch `feature/phase-20-release-polish`, plan step 13. Do not touch `apps/web` except to read tokens and fonts.

- **H1 Workspace package.** Add `site/` to `pnpm-workspace.yaml`; `site/package.json` (`@abb/site`, private, scripts
  `dev`, `build`, `check`), `next.config.ts` (section 2.1 settings, `basePath` from `ABB_SITE_BASE_PATH`),
  `tsconfig.json` extending the root. Same `next`/`react`/`react-dom` versions as `apps/web`. Test: `pnpm install
  --frozen-lockfile` changes nothing in the lockfile except the new importer; `scripts/audit-lockfiles.sh` passes.
- **H2 Tokens and fonts.** `site/scripts/tokens.mjs` writes `site/src/styles/tokens.css` from `tokens-lib.mjs` (same
  file the concept used); `site/src/styles/site.css` from `landing-concepts/base.css` (type scale, rhythm, buttons, code
  blocks, footer); `next/font/local` in `app/layout.tsx` exposing `--font-plex-sans`/`--font-plex-mono`; copy
  `OFL.txt` into `public/fonts`. Test: `pnpm --filter @abb/site build` and the export's CSS contains no colour literal
  outside `tokens.css` (`rg "#[0-9a-f]{6}" site/src/styles/site.css` empty).
- **H3 Static shell.** `app/page.tsx` with `TopBar`, `Hero` (copy from section 3; PROPOSED until the positioning brief
  lands), `Footer`, metadata (section 10, favicon links). Test: `check-site.sh build nojs` (temporarily with the hero
  rows missing allowed).
- **H4 FaultTrace island.** Fixture `site/fixtures/migrate-billing-schema.json` (the 15 events of the demo's failing run,
  9 shown); `FaultTrace.tsx` per section 6; the fault line as a `::before` on the first-error row with the CSS draw;
  inspector content per row type. Vitest: renders server-side with the first-error row selected; `j`/`k`/`e`/`Enter`
  behave; `aria-live` updates. Measure the island's gzipped size (budget 12 KB) and the total JS (print it in the check).
- **H5 BeforeAfter, Quickstart, CommandBlock.** `CommandBlock` with the copy behaviour and the no-JS state; the five
  commands are read from `docs/quickstart.md` at build time (one source, AC12) and shared with the final CTA; `make demo` line conditional on step 10 being merged
  (section 4.4). Test: Playwright clicks Copy and reads the clipboard (`context.grantPermissions(["clipboard-read"])`).
- **H6 HowItWorks.** Three steps, two `CommandBlock`s, sticky column, scroll settle inside `@supports` +
  `no-preference`. Test: `check-site.sh motion`.
- **H7 Images.** `scripts/site-images.mjs` (Playwright canvas -> WebP) over `docs/screenshots/release/*.png` into
  `site/public/assets/shots/`; fails on budget. `site/screenshots.json` gains `zoom`, `offsetY`, `callouts[]`, `notes[]`
  per section 8. Test: sizes printed and under budget; PNG header width/height written into the `<img>`.
- **H8 ScreenGallery island.** Tabs (real tablist), panels, `Callout` with container units, `<picture>` per scheme. Test:
  axe clean; with JS off all seven panels are in the DOM; with JS on only one is visible; arrow keys move tabs.
- **H9 Records, What stays on your machine, Architecture SVG, FAQ, FinalCta, footer status line.** Copy verbatim
  from `positioning-brief.md` §7; the limit sentence and the status line are not optional. Test: `check-site.sh claims`
  (also greps for the positioning brief's banned words list, §4, and for the status line's presence).
- **H9b Watch section (G1-gated).** Server component that renders only when the teaser files exist; markup from
  `video-brief.md` §5.5 with the reduced-motion/Save-Data poster variant; captions track. Test: with the files absent the
  section is not in the export; with placeholder files present, `check-site.sh perf` shows unchanged LCP/CLS and
  `motion` shows no autoplay under reduced motion.
- **H10 Brand assets.** `brand/social-card.html` + `scripts/brand.sh` output into `site/public/assets/`; favicon set;
  manifest. Test: AC9 (`file` reports 32/180/512 and 1200x630).
- **H11 check-site.sh.** Add `nojs`, `network` (both base paths), `links`, `size`, `perf`, `a11y`, `motion`, `shots`,
  `claims` as in section 9; keep the baseline's static greps. Test: each subcommand fails on a planted violation
  (an external `<img src>`, a 300 KB WebP, `animation` outside the media query, the word "SaaS").
- **H12 CI and make.** `site-check` job (Node 22, pnpm, Playwright Chromium cached, runs `check-site.sh` without
  `shots`); `make site` = build + check; `pages.yml` deploys `site/out` (D12, inert until Pages is enabled). Test: job
  green on the PR; `scripts/check-pins.sh` clean.
- **H13 Evidence.** Screenshots at 375/1440 light/dark into `docs/screenshots/release/site-*.png`; the measured numbers
  (JS KB, LCP, CLS) pasted into the plan's progress section with VERIFIED; anything not run marked UNVERIFIED (env).
- **H14 Baseline retirement.** Remove the baseline `site/build.mjs`, `site/src/site.css`, `site/index.html` once H1-H13
  pass (keep `repo-url.mjs` as the single source of the repository URL, imported by `next.config.ts`).

Stop and ask only for: the repository URL if it changes (plan `[NEEDS INPUT]` 2), enabling Pages (G2), and any new
dependency the five questions cannot justify.

---

## 13. Research notes and sources (patterns extracted, nothing copied)

- Evil Martians, "We studied 100 dev tool landing pages" (2025): centered hero with the visual directly under the
  headline is the dominant pattern; two CTAs with a specific primary verb; trust block right after the hero; problem-led
  feature blocks; FAQ near the end; a final CTA as the safety net. Adopted: order, two CTAs, FAQ, final CTA; replaced
  the logo/stat trust block with the self-host commands. https://evilmartians.com/chronicles/we-studied-100-devtool-landing-pages-here-is-what-actually-works-in-2025
- Linear (https://linear.app): device-less, full-width product images beside short two-line headings; a plain stat line;
  link columns in the footer. Adopted: device-less screenshots, short section headings, footer shape.
- Plausible (https://plausible.io): one static dashboard image under the hero; "open source, public and auditable" as a
  feature card; privacy stated as facts, repeatedly. Adopted: privacy as plain statements, license in the footer.
- Bun (https://bun.sh): the install command sits in the hero in a card with a prompt; code blocks have a filename head;
  benchmarks link to reproducible sources. Adopted: install-first block, filename/`shell` head on code blocks, "everything
  is regenerated by script" as the gallery lede.
- Zed (https://zed.dev): "Clone source" as a first-class CTA beside download; terminal-styled proof instead of a hero
  screenshot. Adopted: "Read the source" secondary CTA; DOM-drawn hero.
- Langfuse (https://langfuse.com): "open platform, open source" section listing license and self-host options; a
  "works with any stack" list. Adopted: the records/works-with definition list with the offline qualifier.
- Sentry (https://sentry.io/welcome/): quick-start code per platform at the end of the page. Adopted: the one-line command
  in the final CTA.
- Tailscale (https://tailscale.com): use-case cards and persona tabs - rejected here (cards as layout; personas we do not
  have).
- Refactoring UI summaries (secondary sources): emphasise with weight and colour before size; a 4 px base scale;
  start in grayscale, add colour last. Adopted throughout (two weights, ink/ink-2, red last and once).
  https://openskillindex.com/skills/bilalmirza2001-skills-refactoring-ui
- CSS scroll-driven animations: `animation-timeline: view()` with `animation-range`, final state as default inside
  `@supports`, nested in `prefers-reduced-motion: no-preference`; put `animation-timeline` after the `animation`
  shorthand. https://tympanus.net/codrops/?p=75216 , https://flaviocopes.com/css-scroll-driven-animations/
- Copy without JS: CSS cannot write the clipboard; `user-select: all` plus a real button is the progressive path;
  excluded prompts via `user-select: none` are handled inconsistently by browsers, so the JS copy strips them explicitly.
  https://bugs.webkit.org/show_bug.cgi?id=80159

Pattern not researched (no reliable source found): Ghostty's page fetch returned no content; its static-only policy is
reported second-hand only and is not relied on.
