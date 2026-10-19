# Agent BlackBox: demo video brief (v2 direction for `scripts/record-demo.mjs`)

Status: written 2026-10-18 for Phase 20 G1. Directs the second version of the recorder and storyboard that the
`phase-20-video` worktree started (`docs/design/video-storyboard.md`, `scripts/record-demo.mjs`, `scripts/encode-demo.sh`).
What we say comes from `docs/design/launch/positioning-brief.md` (caption bank, section 7); every caption below cites its bank
number. What we may claim comes from the claims table in `docs/plans/active/phase-20-release-polish.md`. The product look is
`docs/design/pass-2/01-direction.md` ("One Red Line"): red only for the fault line and the failure words, in the video as in the app.

Constraints restated: name "Agent BlackBox"; silent, captions only, no music, no voice-over (D17); 1920x1080 main cut 60-90 s;
a 15-20 s teaser loop; recorded from the `make demo` stack (scripted agents, no API key) with Playwright and ffmpeg 9; nothing
shown that is not built; "Open source. Self-hosted." only after G2; `--live` never shown or implied.

Evidence vocabulary as in AGENTS.md: VERIFIED (run here), UNVERIFIED (env), ASSUMED. Section 3.5 has the proof-of-concept numbers.

---

## 0. What the v1 storyboard gets right and what v2 changes

Keep from v1: the scene table as the single source of truth, names read from one `DATA` table and clicked by visible name
(never an id), the bounded `optional` steps, the caption div on `<html>` so React never sees it, the no-upload rule, the
`[CONFIRM AFTER STEP 10]` discipline, the claims gate on scene 10.

Change in v2 (each has a reason in the sections below):

| v1 | v2 | Why |
| --- | --- | --- |
| Playwright `recordVideo` (VP8, fixed 1 Mbit/s, 1x pixels) | CDP `Page.startScreencast` at `deviceScaleFactor: 2`, JPEG q92 frames with timestamps, ffmpeg lanczos downscale to 1080p | `recordVideo` has no quality control and the Chromium recorder's bitrate is hard-coded at about 1 Mbit/s ([playwright#31424](https://github.com/microsoft/playwright/issues/31424)); it also records at 1x density, which looks soft on any modern display ([alexwlchan](https://alexwlchan.net/2026/record-browser-video/)). Section 3.5 shows the difference in frames. |
| 25 fps | 30 fps constant (frames carry real durations; the encoder resamples) | Cursor travel and the 280 ms fault-line draw read as motion, not steps. |
| A round dot cursor, instant moves (`steps: 30`) | An arrow cursor with eased travel, a hover dwell, a press state and a click ripple, driven from Node at 60 Hz | Viewers follow the cursor; a dot that teleports is the one thing that makes a recording look fake. |
| No zoom, no spotlight | Two camera moves (1.6x on the sentence, 1.4x on the inspector) and a dim-spotlight for rows | At 1080p the 24 px headline is 2.2 % of the frame height; the one thing the video exists to show must fill the frame once. |
| Captions 28 px regular, 1500 px box | Captions 34/44 px semibold, one line, max 9 words, timed by length (section 4) | Reading-speed rules (Netflix 20 cps ceiling, BBC 160-180 wpm) and the social-feed habit of reading captions at a glance. |
| 10 scenes, 78 s, mostly holds | 14 beats, 74 s, every beat is "one idea, one motion, one caption" | Attention research on silent autoplay: decide in 3 s, one idea per scene, show the failure before the fix. |
| MP4 only | MP4 H.264 + WebM VP9 + AV1 MP4 (hero), poster, `.vtt`/`.srt`, transcript, teaser loop, GIF for the README | The landing page is static HTML with `<video>`; the README cannot autoplay a video. |
| Captions set per scene, same text for the whole scene | Captions have their own timeline inside a beat | A 10 s scene with one caption wastes 7 s of reading time. |

---

## 1. Thesis and arc

**Thesis (one sentence):** Your agent ran and something broke; Agent BlackBox shows you the one line where, and everything
it recorded around it.

**Narrative shape:** failure, then the fix path, then the width. The first 20 s are one failed run and the one red line. The
next 20 s are what the record contains (the event, the cost, the retry, the finding, its evidence). The last 30 s widen to many
agents, a person in the loop, the week's cost, and close on the name. This is the "show the failure, then the fix" shape the
best silent developer-tool clips use; the Raycast 2025 teaser (39 s of cropped real UI, no walkthrough) is the reference for
"real interface, lit for one detail at a time" ([moonb breakdown](https://www.moonb.io/blog/product-launch-video)); Warp's
explainer is the reference for "every keystroke shows a real workflow win" ([eChai](https://echai.ventures/videos/warp-intro-to-warp-terminal-agents-and-cloud)).
We do not copy either: no presenter, no drama lighting, no timestamps chapter list (the clip is under 90 s).

**Emotional arc, second by second (what the viewer should feel):**

| t | Feeling | What causes it |
| --- | --- | --- |
| 0-3 s | Recognition: "that is my runs list" | A calm monochrome table; the only colour is four red "Failed" words. The brand caption names the product once. |
| 3-7 s | Curiosity: "what broke?" | The cursor drifts to a Failed row whose second line already says "Failed at step 7 · 2 retries · $0.0167 spent after". Click. |
| 7-13 s | Relief: "oh, it tells me" | The run page: one sentence, zoomed to fill the frame. "Failed at step 7 after 6.2 s: run_migration, ConnectionTimeout." |
| 13-16 s | A small sting: "and it cost me after it broke" | Spotlight on "2 retries · 8.2 s and $0.0167 spent after". |
| 16-21 s | Orientation: "and here is where" | Zoom out; the red line across the timeline with "First error, step 7"; the only red on the screen. |
| 21-27 s | Trust: "this is the actual event, not a summary" | Press e; the inspector slides in; "What went wrong: run_migration failed: ConnectionTimeout", type, duration, ids, payload. |
| 27-32 s | "It understands what an agent is" | Rows: `llm.request.completed model-x · 2,300 in / 340 out · $0.0189`; `retry.attempted Retry #1`. |
| 32-39 s | "And it checked for me" | Findings tab: Critical, Warning; Show evidence jumps to the exact rows. |
| 39-46 s | Scale without noise | Five lanes in the waterfall, the critical path edge, "No end recorded". |
| 46-52 s | Control | Review: a Waiting request, risk class, the arguments, Approve/Deny (never clicked). The foot sentence about the SDK guard is in frame. |
| 52-58 s | Accountability | Analytics: "$22.16 spent in the last 7 days, $1.23 per run"; "Where the money was wasted". |
| 58-63 s | Safety | Security findings: destructive command, secret file read, risky git; a "Rule:" line. |
| 63-68 s | Comfort | The run page in the light scheme. |
| 68-74 s | Resolve: "I could run this" | The canvas, the wordmark, the closing caption (G2-gated variant later). |

Three rules that follow from the arc: no scene without a visible change (a hold is a motion that has stopped, not a still);
the fault line is on screen three times (list dash, timeline, waterfall) and nothing else red is ever in frame; the cursor never
covers text that a caption is asking the viewer to read.

---

## 2. Shot list

Conventions: `t` is seconds from the first frame of the cut; "route" is the app path under `/w/{workspace}/projects/all`
unless workspace-level; selectors are Playwright locators the v1 table already uses or the test ids listed in
`apps/web/src` (`week-sentence`, `waterfall`, `report-headline`, `approval-row`, `headline`); cursor positions are viewport
CSS px at 1920x1080; Z = zoom (section 3.1), S = spotlight (3.2), C = caption (section 4, bank number from the positioning
brief in brackets); transitions are "cut" (a route change, no effect) unless stated. Demo-seeded names are marked
[CONFIRM AFTER STEP 10] exactly as v1 does; the fixtures names are what the proof of concept used.

### 2.1 Main cut (74 s)

| # | t | Route / screen | UI action (selector) | Cursor path and dwell | Camera | Caption (bank) | Out |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.0-3.0 | `/runs`, dark. Wait `getByRole("heading",{name:"Runs"})` and `document.fonts.ready`. | none | Parked at (1500, 300), still. | Z1.0 | 0.3-3.0 "Agent BlackBox records what your agents did." [1] | hold |
| 2 | 3.0-7.0 | `/runs` | `scrollIntoViewIfNeeded` on the failed run link; hover 400 ms; click at 5.6 s. | Travel to the centre of `getByRole("link",{name:$failedRun})` (about 900 px, 900 ms); dwell 400 ms; press 90 ms. | Z1.0 | 3.2-5.6 "A failed run. Open it." [3] | cut on navigation |
| 3 | 7.0-13.0 | `/runs/{id}` Timeline. Wait `getByRole("tab",{name:"Timeline",exact:true})`, then 400 ms so the fault line's 280 ms draw is in the recording. | none | Park at (1500, 300) during the load (travel 500 ms). | 7.6 s: Z1.6 on the `h1` headline: origin = formula in 3.1 with the headline's left-centre as focus; hold 4.0 s. | 8.0-12.6 "One sentence says where it broke." [4] | zoom out at 12.6 (600 ms) |
| 4 | 13.0-16.5 | same | none | still | S on the detail line `.run-head` second line ("2 retries · 8.2 s and $0.0167 spent after") [selector: the line under the h1, `data-testid="headline-detail"`], dim 55 %, 200 ms in. | 13.2-16.3 "Spend after the first error, counted." [6] | spotlight out 200 ms |
| 5 | 16.5-21.5 | same | none | Travel to a point 60 px left of `.tl-fault-label`, 24 px below the rule (the cursor points at the line without covering the label); dwell. | S on the fault-line row + label (the `[data-first-error]` row's box grown 8 px). | 16.8-21.2 "The red line is the first error." [7] | spotlight out |
| 6 | 21.5-27.5 | same, inspector | `page.keyboard.press("e")` at 21.8; wait `getByRole("dialog")`; the inspector slides 8 px in (160 ms). | Still; the key chip "e" shows 600 ms (3.4). | 22.6 s: Z1.4 with focus on the inspector's "What went wrong" block (`getByRole("dialog").getByText("What went wrong")`); hold 3.6 s. | 21.8-24.4 "Press e. The failed tool call opens." [8]; 24.7-27.3 "What went wrong, from the event itself." [9] | zoom out at 27.0 |
| 7 | 27.5-32.5 | same | `Escape` closes the inspector. | Travel to the `llm.request.completed` row (first match), dwell 1.2 s; travel to the first `retry.attempted` row, dwell. | S follows the hovered row (two spotlights, 2.2 s each). | 27.8-30.0 "Every model call carries tokens and cost." [10]; 30.3-32.4 "Retries are retries, not log lines." [11] | spotlight out |
| 8 | 32.5-39.5 | Findings tab | Click `getByRole("tab",{name:"Findings",exact:true})` at 33.0; at 35.8 click `getByRole("button",{name:"Show evidence"}).first()`; wait for the selected row outline (`[role="row"][aria-selected="true"]`). | Travel to the tab (dwell 300 ms), click; travel to Show evidence, dwell 400 ms, click; after the jump park at (1500, 300). | Z1.0 | 33.2-35.6 "Findings, from deterministic detectors." [12]; 36.4-39.3 "Each finding links to its evidence." [13] | cut |
| 9 | 39.5-46.5 | `/runs` then the multi-agent run, Waterfall tab | goto `/runs`; click `getByRole("link",{name:$multiRun})`; click `getByRole("tab",{name:"Waterfall",exact:true})`; click `getByRole("button",{name:"Lanes"})` if present (`optional`); wait `getByTestId("waterfall")`. | Travel to the run link, click (no dwell: this is a transit); travel to Waterfall, click; travel along the thick critical-path bar (`[role="row"][data-critical]` first bar, 600 px right, 1.5 s). | Z1.0; at 44.0 S on the row with the thick edge, 2.2 s. | 41.0-43.6 "Five agents, one run, one picture." [16]; 44.0-46.3 "The thick edge is the critical path." [17] | cut |
| 10 | 46.5-52.5 | `/w/{workspace}/approvals`, Pending | wait `getByRole("heading",{name:"Review"})`; click `getByTestId("approval-row").first()` at 48.0; the panel opens. Never click Approve or Deny. The foot sentence "Policies apply to agents that ask through the SDK guard." must be in frame [CONFIRM AFTER STEP 10: where the foot renders at 1080 high; if it is below the fold the beat scrolls it into view before the caption]. | Travel to the row, dwell 300 ms, click; park at (700, 950) (over no text). | Z1.0; at 49.5 S on the panel's capability/resource/risk block, 2.5 s. | 47.0-49.4 "The agent asked. A person decides." [22]; 49.8-52.2 "One approval, one action, once." [23] | cut |
| 11 | 52.5-58.5 | `/analytics`, Cost | wait `getByTestId("report-headline")`. | Park at (1500, 300). | Z1.0; at 55.3 S on "Where the money was wasted" (`getByRole("heading",{name:"Where the money was wasted"})` grown to its figures row), 2.8 s. | 53.0-55.2 "What the week cost, per run." [20]; 55.5-58.3 "Failed runs, retries, spend after errors." [21] | cut |
| 12 | 58.5-63.5 | `/w/{workspace}/security` (Review > Security findings) | wait the list; hover the first finding's row; click it if that opens a "Rule:" line (`optional`). | Travel to the first `getByTestId("security-row")`, dwell 1.5 s. | Z1.0; S on the hovered row 2.2 s. | 59.0-61.6 "Destructive commands, secret reads, risky git." [14]; 61.8-63.4 "Every finding states its rule." [15] | cut |
| 13 | 63.5-68.5 | the failed run again, Timeline, light scheme | goto the failed run; `page.emulateMedia({colorScheme:"light"})` at 64.0 (the app follows the system; there is no toggle). | Park at (1500, 300). | Z1.0 | 64.4-67.6 "Light and dark follow your system." [27] | fade 300 ms to canvas |
| 14 | 68.5-74.0 | Closing card: the canvas colour with the wordmark SVG (`brand/pulse-mark.svg` + the name) centred, drawn by the overlay, not a page. | none | hidden | Z1.0 | 69.0-73.6 "Agent BlackBox: a flight recorder for AI agents." (positioning brief headline option 3, allowed as the one-line description). **After G2 and AC1 only:** "Open source. Self-hosted. Apache-2.0." [26] then the repository URL [28]. | hold, end |

Timing budget: 74.0 s, inside 60-90. If `make demo` has no Security findings, cut beat 12 (69 s). If the approvals foot cannot
be in frame, cut beat 10's second caption and keep the first (the caption is only what the page says).

### 2.2 Teaser loop (18 s, for the landing hero and the README)

The teaser is beats 1-6 compressed, with no brand caption (the page around it carries the name) and a dip-to-canvas seam.

| # | t | Screen | Action | Camera | Caption |
| --- | --- | --- | --- | --- | --- |
| T1 | 0.0-2.5 | `/runs` | fade in from canvas 250 ms; cursor travels to the failed row (starts at 0.6) and clicks at 2.2 | Z1.0 | 0.4-2.2 "A failed run. Open it." [3] |
| T2 | 2.5-8.0 | run page | fault line draws on arrival; zoom in at 3.2 | Z1.6 on the headline, hold 4.0 s | 3.4-7.6 "One sentence says where it broke." [4] |
| T3 | 8.0-12.0 | run page | zoom out 600 ms; cursor to the fault line | S on the fault row | 8.6-11.8 "The red line is the first error." [7] |
| T4 | 12.0-17.5 | inspector | press e at 12.2; Z1.4 at 12.8 | hold 3.6 s | 12.4-14.6 "Press e. The failed tool call opens." [8]; 14.9-17.2 "What went wrong, from the event itself." [9] |
| T5 | 17.5-18.0 | | fade to canvas 250 ms, then loops to T1's fade-in | | |

Seam: both ends are the flat canvas colour (`#101419`), so the loop is a 500 ms dip that reads as a blink, not a jump cut.
A crossfade seam (xfade of the tail onto the head) is the alternative and is not used: the two frames differ in layout, and
the blur of a crossfade on text reads as a defect. The dark dip is invisible on the landing page because the hero's background is
the same token.

### 2.3 Social cut (1080x1350, optional, after the main cut is approved)

Worth doing only if the launch posts go to X or LinkedIn with the video attached; otherwise the 16:9 teaser with the poster is
enough. If done: it is **a separate take, not a crop**. Record with viewport 1080x1350 CSS px at `deviceScaleFactor: 2`
(the app's 768-1080 layout is the one in `docs/screenshots/pass-2/*-768-*`), caption 40/50 px, zoom max 1.4, the same scene
table with beats 1-8 only (the waterfall and analytics do not fit a narrow frame well). Budget 35 s, under 8 MB H.264.

---

## 3. Camera and cursor specification (implementable numbers)

All overlays live on `<html>` outside `<body>`; the zoom is a CSS transform on `<body>`; so the cursor, spotlight and caption
never scale and `getBoundingClientRect` keeps returning viewport coordinates that already include the transform (Playwright's
`boundingBox()` is therefore still correct for clicks while zoomed).

### 3.1 Zoom (camera)

- Levels: 1.0 (default), 1.4 (a panel or a row block), 1.6 (one sentence). Never above 1.6: recording is at 2x, so 1.6 leaves
  every glyph rasterised above its 1080p output size; above 2.0 text would soften.
- Timing: in 700 ms, out 600 ms, `cubic-bezier(0.65, 0, 0.35, 1)` (symmetric ease-in-out, the viewer's eye arrives with the
  frame). Minimum hold at a level: 2.5 s. Never start a zoom during a navigation, a tab switch or the inspector's slide (wait
  for the DOM you are zooming on to be visible first).
- Implementation: `body { transform-origin: Ox Oy; transform: scale(s); transition: transform 700ms cubic-bezier(.65,0,.35,1) }`
  and `html { overflow: hidden }` for the whole recording. The zoom-out sets `transition-duration: 600ms` first.
- Origin rule (no canvas edge ever shows): with origin `(Ox, Oy)` and scale `s`, the visible window is
  `x in [Ox - Ox/s, Ox + (W - Ox)/s]`, which lies inside `[0, W]` whenever `0 <= Ox <= W` (same for y). So: compute the
  focus point `c` (the centre of the element you want framed) and set `Ox = (cx - W/(2s)) / (1 - 1/s)`, then clamp `Ox` to
  `[0, W]` (same for `Oy` with `H`). Clamping means a focus near an edge simply pins the window to that edge.
- Fixed-position chrome inside `<body>` (the top bar) scales with the page. That is intended: the camera moves, the page
  does not change.
- Under `prefers-reduced-motion` the app disables its own motion; the recorder sets `reducedMotion: "no-preference"` so the
  fault line draws and the inspector slides (both are part of the story).

### 3.2 Spotlight (dim everything but one rect)

- One `div` on `<html>`: `position: fixed; border-radius: 4px; pointer-events: none; box-shadow: 0 0 0 9999px rgba(16,20,25,.55)`
  on dark, `rgba(29,34,41,.45)` on light; `transition: opacity 200ms linear, left/top/width/height 260ms cubic-bezier(.65,0,.35,1)`.
- Rect: the target's `getBoundingClientRect()` grown by 8 px on every side; moving the spotlight to another target animates
  the rect (one element, the box slides), which is how beat 7 walks from the model-call row to the retry row.
- Hold 2.0-2.8 s per rect; out 200 ms. Never combine with a zoom above 1.0 (one emphasis device at a time).

### 3.3 Cursor

- Shape: a 24 px SVG arrow (`M5 3l14 9-6.2 1.2L16.5 20l-2.6 1.2-3.7-6.6L5 19z`), white fill, `#111` 1.4 px stroke,
  `drop-shadow(0 1px 1.5px rgba(0,0,0,.6))`; hotspot at the SVG origin. The same arrow on light pages (the stroke carries it).
- Travel: duration `clamp(350 ms, 0.9 ms per px of distance, 1100 ms)` with `cubic-bezier(0.22, 1, 0.36, 1)` (fast start,
  long settle: the eye catches the departure and the arrival is soft). Path bows 4 % of the distance (max 40 px) perpendicular
  to the straight line (`sin(pi * p)`), so no travel is a ruler line. Driven from Node at 60 Hz with `page.mouse.move`;
  the overlay follows the real `pointermove` events, so the hover state of the app and the drawn cursor cannot drift apart.
- Arrival settle 120 ms, then hover dwell 400 ms before any click (the app's hover state is visible for that long).
- Click: `mouse.down`, 90 ms, `mouse.up`; during the press the arrow scales to 0.88; a 44 px ring at the click point animates
  `scale .3 -> 1.15, opacity .9 -> 0` over 380 ms `cubic-bezier(.2,.7,.3,1)`. After a click that navigates, the cursor stays
  put until the new page is ready, then travels to its parking spot in 500 ms.
- Parking spots (never over text being read): (1500, 300) on run pages (the right third of the header, below the Go button);
  (700, 950) on Review; the cursor is hidden (`opacity: 0`, 200 ms) during the closing card and during zooms above 1.0 unless
  the beat says the cursor points at something.
- Position persists across navigations through `sessionStorage` (v1's trick, kept) so a new document mounts the arrow where
  the old one left it.

### 3.4 Key chip (the only key cap in the video)

The app draws no key caps (design decision). The video shows a keypress with its own chip: a 44x44 px rounded 4 px box,
`rgba(16,20,25,.92)` with a 1 px `rgba(255,255,255,.14)` border, Plex Mono 22 px, bottom-right at (1860, 996) inside the safe
area, visible 600 ms, in/out 120 ms. Used for `e` (beat 6) and `Escape` is not shown (the caption carries it).

### 3.5 Proof of concept (what was run here)

Script: `docs/design/launch/video-concepts/poc-record.mjs` + `poc-encode.sh` (not for commit; the Sonnet builder folds the
mechanics into `scripts/record-demo.mjs`). Run against the fixtures build of `apps/web` on port 3101 (this worktree's own
build; the shared port 3100 belongs to the `phase-20-video` builder) under the heavy mutex.

[POC RESULTS: filled in section 3.6 below after the takes]

---

## 4. Caption typography and timing

- Face: IBM Plex Sans 600 (the app's own face, self-hosted; the overlay reads `--font-plex-sans` from `<body>` and sets it on
  `<html>` so the caption uses the same loaded font). No second face, no italics; the only colour inside a caption is
  `#ff8a80` on the words "failed" / "first error" when the caption is about the fault (One Red Line applies to captions too).
- Size: 34 px / 44 px line height at 1080p (3.1 % of frame height; a 9-word caption is about 45 characters = 760 px wide,
  well inside the 90 % title-safe width). Netflix's guide works at 42 characters per line and two lines maximum
  ([Netflix English TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-Timed-Text-Style-Guide));
  we use one line and 9 words, so no caption needs a break. The social cut uses 40/50 px.
- Box: `rgba(16,20,25,.92)` fill, 1 px `rgba(255,255,255,.14)` border, radius 4 px, padding 14 px 26 px, centred, box bottom at
  84 px from the frame bottom (7.8 %: inside the 5 % action-safe band and above any player control bar that overlays the bottom
  5-6 %). On the light scene the box stays dark (constant legibility, one caption style).
- Motion: in 160 ms opacity, out 120 ms; a caption never slides. Gap between consecutive captions >= 250 ms (the eye registers
  a change). A caption is never on screen across a cut; it goes out before the route changes and the next comes in once the
  new page is ready.
- Duration rule: `max(2.0 s, characters / 15 + 0.6 s)`, maximum 6 s. Reasoning: 15-17 characters per second is a comfortable
  adult reading speed for one-line captions; Netflix's ceiling is 20 cps and BBC's 160-180 words per minute is about 13-15 cps;
  the +0.6 s is the saccade to the lower third and back. Worked examples: "A failed run. Open it." (22 chars) = 2.1 s;
  "One sentence says where it broke." (33) = 2.8 s; "Destructive commands, secret reads, risky git." (46) = 3.7 s.
- Words: max 9 per caption (bank rule), sentence case, full stops, no exclamation marks, no key caps drawn in the text (the
  word "e" in "Press e" is enough with the chip on screen).
- Accessibility: captions are burned in (the social and README copies have no track support) **and** delivered as a sidecar
  `.vtt` and `.srt` for the landing page's `<video><track kind="captions">`. A silent video is "video-only, prerecorded"
  under WCAG 1.2.1, which asks for a text alternative with correctly sequenced descriptions of the visual information, not
  just the captions ([W3C Understanding SC 1.2.1](https://www.w3.org/WAI/WCAG22/Understanding/audio-only-and-video-only-prerecorded.html));
  so a `transcript.md` is generated from the scene table (section 5.6) and linked under the video.

---

## 5. Encode pipeline (exact commands)

Inputs: `frames/f00000.jpg ...` (3840x2160, JPEG q92) and `frames.ffconcat` with per-frame durations, written by the recorder.
ffmpeg 9.0.2 here has `libx264`, `libvpx-vp9`, `libsvtav1` (no `libaom`), VERIFIED by `ffmpeg -encoders`.

### 5.1 Master (one lossless-enough intermediate, then every deliverable comes from it)

```sh
VF="scale=1920:1080:flags=lanczos,fps=30,format=yuv420p"
ffmpeg -y -f concat -safe 0 -i frames.ffconcat -vf "$VF" \
  -c:v libx264 -preset slow -crf 14 -g 60 -keyint_min 30 -an master-1080p30.mp4
```

Lanczos for the 2x downscale (sharper text edges than bicubic); `fps=30` resamples the variable-duration frames to a
constant rate; `yuv420p` because 4:4:4 does not play in browsers. The master is never published.

### 5.2 Main cut, H.264 MP4 (universal fallback)

```sh
ffmpeg -y -i master-1080p30.mp4 -c:v libx264 -preset slow -crf 20 -profile:v high -level 4.1 \
  -g 60 -keyint_min 30 -pix_fmt yuv420p -movflags +faststart -an demo-1080p.mp4
```

Budget: <= 12 MB for 74 s (about 1.3 Mbit/s; UI footage is mostly static so CRF 20 lands well under). `+faststart` so the
browser starts before the file finishes downloading. No `-tune`: `stillimage` softens the cursor motion, `animation` adds
reference frames for no gain on flat UI (checked on the PoC, section 3.6).

### 5.3 Main cut, WebM VP9 (first `<source>` after AV1)

Two-pass constrained quality, per Google's VP9 VOD guide (1080p 30 fps: target 1800k, crf 31, tile-columns 2, g 240;
[developers.google.com/media/vp9/settings/vod](https://developers.google.com/media/vp9/settings/vod)), with `-row-mt 1`:

```sh
ffmpeg -y -i master-1080p30.mp4 -c:v libvpx-vp9 -b:v 1200k -minrate 600k -maxrate 1740k -crf 31 \
  -tile-columns 2 -g 240 -row-mt 1 -quality good -speed 4 -pass 1 -an -f null /dev/null && \
ffmpeg -y -i master-1080p30.mp4 -c:v libvpx-vp9 -b:v 1200k -minrate 600k -maxrate 1740k -crf 31 \
  -tile-columns 2 -g 240 -row-mt 1 -quality good -speed 1 -pass 2 -an demo-1080p.webm
```

Budget <= 8 MB. (The target bitrate is below Google's 1800k because the content is flat UI; raise to 1800k if text edges
ring in the check frames.)

### 5.4 Hero AV1 (optional, smallest; Safari 17+ on Apple silicon, Chrome, Firefox)

```sh
ffmpeg -y -i master-1080p30.mp4 -c:v libsvtav1 -preset 4 -crf 32 -g 240 -pix_fmt yuv420p \
  -svtav1-params tune=0:fast-decode=1 -movflags +faststart -an demo-1080p-av1.mp4
```

SVT-AV1 docs: presets 4-6 are the quality/speed balance, crf 30 is a 1080p starting point, `tune=0` favours visual sharpness
([SVT-AV1 Ffmpeg.md](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Ffmpeg.md)). Ship as `video/mp4; codecs=av01.0.08M.08`.
Skip film-grain (no grain in UI footage). Budget <= 5 MB.

### 5.5 Poster, teaser, GIF

```sh
# Poster: the fault-line beat (t = 17.5 s), PNG for the README, WebP for the site
ffmpeg -y -ss 17.5 -i master-1080p30.mp4 -frames:v 1 poster.png
ffmpeg -y -ss 17.5 -i master-1080p30.mp4 -frames:v 1 -c:v libwebp -quality 82 poster.webp

# Teaser (a separate 18 s take, same pipeline to its own master), with the dip-to-canvas seam baked in:
ffmpeg -y -i teaser-master.mp4 -vf "fade=t=in:st=0:d=0.25:color=0x101419,fade=t=out:st=17.75:d=0.25:color=0x101419" \
  -c:v libx264 -preset slow -crf 21 -g 60 -pix_fmt yuv420p -movflags +faststart -an teaser-1080p.mp4
ffmpeg -y -i teaser-1080p.mp4 -c:v libvpx-vp9 -b:v 900k -crf 33 -tile-columns 2 -g 180 -row-mt 1 -quality good -speed 1 -an teaser-1080p.webm
ffmpeg -y -i teaser-1080p.mp4 -c:v libsvtav1 -preset 4 -crf 34 -g 180 -pix_fmt yuv420p -svtav1-params tune=0:fast-decode=1 -movflags +faststart -an teaser-1080p-av1.mp4

# README GIF (GitHub autoplays GIFs and nothing else; keep it small): 960x540, 12 fps, palette per clip
ffmpeg -y -i teaser-1080p.mp4 -vf "fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" teaser-960.gif
```

Teaser budgets: H.264 <= 2.5 MB, VP9 <= 1.5 MB, AV1 <= 1 MB, GIF <= 4 MB (GitHub renders README images from the repo; a
4 MB GIF is the upper end of acceptable; if it lands above, drop to 10 fps or 800 px). Landing-page markup:

```html
<video autoplay muted loop playsinline preload="metadata" poster="assets/poster.webp" width="1920" height="1080">
  <source src="assets/teaser-1080p-av1.mp4" type='video/mp4; codecs="av01.0.08M.08"'>
  <source src="assets/teaser-1080p.webm" type="video/webm">
  <source src="assets/teaser-1080p.mp4" type="video/mp4">
</video>
```

Source order most-efficient first; `muted` is what lets it autoplay; `playsinline` keeps iOS inline; `poster` is the LCP
candidate ([web.dev video performance](https://web.dev/learn/performance/video-performance)). The full cut gets `controls`
and `<track kind="captions" src="assets/demo.vtt" srclang="en" label="English" default>`. Under
`@media (prefers-reduced-motion: reduce)` the site shows the poster and a play button instead of autoplay (one CSS rule plus
`autoplay` removed by... the site has no JavaScript (D2, D12), so ship two `<video>` elements and show one per media query).

### 5.6 Sidecars (generated by the recorder from the real caption timestamps, never typed by hand)

- `demo.vtt` / `demo.srt`: one cue per caption with the start/end the recorder logged (`marks` in `meta.json`), text exactly
  the burned-in caption without the colour markup.
- `transcript.md`: the beat table's "screen + action" column rendered as sequenced sentences ("The Runs list. The cursor
  opens the failed run 'Migrate billing schema'. The run page: Failed at step 7 after 6.2 s: run_migration,
  ConnectionTimeout. ...") plus the captions, for the WCAG 1.2.1 text alternative.
- `meta.json`: scene ids, beat start/end, caption cues, frame count, viewport, the app commit, the demo seed hash.

### 5.7 Playwright capture (why not `recordVideo`)

`recordVideo` offers only `dir` and `size` ([playwright.dev/docs/videos](https://playwright.dev/docs/videos)); Chromium's recorder is VP8 at a hard-coded ~1 Mbit/s, one thread
([playwright#31424](https://github.com/microsoft/playwright/issues/31424), closed as a duplicate of #17217 in May 2026). The v2 recorder opens a CDP session
(`context.newCDPSession(page)`) and calls `Page.startScreencast({format:"jpeg", quality: 92, maxWidth: 3840, maxHeight: 2160, everyNthFrame: 1})`,
acks every `Page.screencastFrame`, writes each frame with its arrival time, and emits `frames.ffconcat` with real durations.
Frames arrive only when the compositor produces one (a still page sends nothing), which is exactly right for a concat list:
a 3 s hold is one frame with `duration 3.0`. Headless Chrome delivers screencast frames at the device scale, so the frames
are 3840x2160 (VERIFIED in 3.6). Timing fidelity: durations come from `performance.now()` at arrival, not from the
screencast's own timestamp, so playback time equals wall time (v1's "lead-in to trim" workaround goes away: the recorder
starts the screencast when beat 1 starts).

---

## 6. Data and seed requirements for `make demo` (asks to the demo-data builder)

The video reads every name from `DATA`; these are the properties the seeded data must have for the footage to tell the
story. Each is a concrete ask.

1. **One failed run with a legible cause, early.** A run that ends `run.failed` where the first error is a tool call with a
   one-word error type (`ConnectionTimeout`, `PermissionDenied`), at step <= 9 so the fault line sits in the first screen of the
   timeline without scrolling at 1080 high (15-18 events total). Headline <= 70 characters so it fits at 1.6x
   ("Failed at step 7 after 6.2 s: run_migration, ConnectionTimeout." is 64). At least one retry after the error so "$X spent
   after" is non-zero and "2 retries" appears in the detail line. Candidate: the simple custom agent (`examples/python/scripted_agent.py`,
   one deliberate failure and a retry, D9); the coding example's `--variant wrong-fix` fails at step 24 and needs a scroll,
   so it is the fallback, not the first choice. [CONFIRM AFTER STEP 10]
2. **That run has findings with evidence.** >= 1 Critical and >= 1 Warning finding on the failed run, each with "Show
   evidence" resolving to events in the first screen (the jump outline must be visible without scrolling).
3. **A retry loop finding.** One run (can be the same) where the retry detector fires ("Probable: ... retried N times" or the
   fixed wording the detector uses), so beat 7's `retry.attempted` rows are not the only retry story. Not captioned beyond
   bank 11 unless step 10 confirms the wording.
4. **Multi-agent waterfall with five lanes.** `examples/multi-agent` with `--skew-ms` and `--fail-tool` so the Lanes view shows
   five agents, one error bar, the thick critical path and the skew outline. Lane names <= 12 characters (`planner`,
   `researcher`, `coder`, `tester`, `reviewer`) so the chips do not truncate at 1.0x.
5. **One pending approval.** A Waiting request with a capability like `github.push`, a resource, risk R3, arguments of 3 lines
   or fewer, expiry hours away (not minutes: "1 h 29 min left" drifts between takes and the time-left column is in frame).
   The page foot "Policies apply to agents that ask through the SDK guard." must render on the Approvals tab at 1920x1080
   without scrolling, or the builder tells me where it is. [CONFIRM AFTER STEP 10]
6. **Security findings, three rules.** >= 3 security findings across the demo runs with different rules (destructive command,
   secret file read, risky git) so beat 12's caption is literally true on screen.
7. **The 7-day sentence and Analytics are non-trivial.** >= 12 runs, >= 3 failed, a non-zero "Where the money was wasted"
   with failed runs, retries and after-the-first-error all > $0; at least two agents and two models in the bars.
8. **Names.** Run names are plain-English tasks (<= 40 characters, no ticket ids longer than 6 characters, no emails, no real
   company or person names, no "test"/"foo"); agent names are role words (`coding-agent`, `support-agent`); the model name is
   the scripted one (`scripted-small` / `model-x`), never a real provider model name (that would imply `--live`).
9. **Order in the list.** The failed run must be within the first 8 rows of Runs (started-desc), so beat 2's travel is short
   and the row is above the fold: run it last in `scripts/demo.sh`, or second to last before the multi-agent run.
10. **Time labels.** Relative ages ("12 m ago", "5 h 59 min left") drift between takes; nothing in a caption may quote them,
    and the QA frame check (section 7) looks for any absolute timestamp with today's date in frame (there are some in the
    inspector's Identifiers block; acceptable, they are not secrets).
11. **No real identity.** The demo stack signs in as `owner@local.test`; the person button shows it in the top bar. That
    string is allowed (positioning brief rule) but the person menu is never opened. The workspace slug reads `local` (demo)
    or `demo` (fixtures) in the scope control: both acceptable.
12. **Determinism.** Two consecutive `make demo` runs produce the same names, step numbers, error types, costs and finding
    counts (AC8). The recorder's `DATA` table and the captions quote only those.

---

## 7. QA checklist and risks

### 7.1 Before publishing (run on every take)

- [ ] `ffprobe` on each deliverable: 1920x1080, 30/1, yuv420p, no audio stream, `faststart` (moov before mdat: `ffprobe -v trace 2>&1 | grep -m2 "type:'m"` shows `moov` first), sizes within budget.
- [ ] One frame per caption extracted (`-ss` at each cue's midpoint) and looked at as an image: text complete, box inside safe area, no overlap with app text, no caption across a cut.
- [ ] One frame per zoom at full level: text edges crisp (compare a 400x200 crop with the 1.0x frame of the same text), no canvas edge, no clipped top bar.
- [ ] The only red in any frame is the fault line, the list dash, the failure words and the caption's fault words. Grep the frames: a script samples every 10th frame and counts pixels within 12 units of `#ff8a80` outside the known rects; more than 0.3 % of a frame fails the take.
- [ ] No secret, key, token, `.env` content, real email (other than `owner@local.test`), real hostname or real model name in any frame (`strings` on nothing: this is a visual check; also grep the page text the recorder logs per beat for `sk-`, `AKIA`, `@gmail`, `anthropic`, `openai`, `claude`, `gpt`).
- [ ] The cursor never covers a caption's subject; the cursor is parked during zooms.
- [ ] Caption cues in `demo.vtt` match the burned-in text one for one; the transcript lists every beat.
- [ ] Two takes in a row diff-equal in `meta.json` except for frame counts within 5 % and timestamps (determinism across takes).
- [ ] Beat durations within +-150 ms of the table (`meta.json` marks).
- [ ] The light-scheme beat shows the same run, and the caption box is still the dark box.
- [ ] The closing caption is the pre-G2 variant unless the G2 row in the claims table is VERIFIED.
- [ ] Nothing on screen says "live", "real model", a provider name, "production", "SaaS", "enterprise".
- [ ] Teaser loops without a visible jump: play it three times through in a browser (`<video loop>`), watch the seam.
- [ ] GIF under 4 MB; poster under 400 KB (D11's screenshot budget applies to the README image).

### 7.2 Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Screencast frames stall in headless Chrome (reported on playwright#31424 for `recordVideo`) | low-medium | The recorder asserts >= 1 frame per 2 s during motion beats and aborts the take with a reason; `headless: false` under `xvfb`/a real display is the fallback and the script accepts `--headed`. |
| Font not ready at the first frame (Plex loads via `next/font`, self-hosted) | medium | Wait `document.fonts.ready` and 300 ms before the screencast starts; the caption reads the body's font variable. |
| Relative times drift ("9 d ago", "5 h 59 min left") so takes differ | certain | Never caption them; QA compares `meta.json`, not pixels; the demo is re-seeded right before the take. |
| The 280 ms fault-line draw is missed (page ready before the screencast catches the frame) | medium | 400 ms wait after the Timeline tab is visible is already in beat 3; if the draw is still missed, the recorder reloads the run page once the screencast is running (the draw happens on first mount). |
| Zoom on `<body>` breaks a sticky/fixed element's position | low (the top bar is fixed and scales as designed) | PoC checked the run page and the Runs list; QA frame check for the Review and Analytics pages before the take. |
| Approvals foot not in frame at 1080 high | medium | Beat 10 scrolls it into view or the second caption is cut (section 2.1 note). |
| `make demo` names differ from `DATA` | certain until step 10 | One-place edit; the recorder fails fast with the missing name in the message. |
| CPU contention on the shared 2-CPU machine makes travel timing uneven | medium | Motion is time-based (eased on `performance.now()`), not step-based, so a slow tick drops frames rather than slowing the motion; record under the heavy mutex with nothing else running. |
| GitHub README cannot autoplay video | certain | GIF for the README, MP4 attached to the release for the full cut; the site hosts the `<video>`. |
| G2 (repo public) slips | medium | The closing beat has two caption variants; the pre-G2 one is the default in the table. |
| VP9 two-pass at `-speed 1` takes long on 2 CPUs (about 8-12 min for 74 s) | certain | Run under the mutex; `-speed 2` is acceptable if time matters (small size cost). |

---

## 8. Handoff for the Sonnet builder (ordered, small, testable)

Each step ends with a command and the thing to look at. Work in the `phase-20-video` worktree on `scripts/record-demo.mjs`;
keep v1's scene-table shape; nothing below needs a new dependency (CDP comes with Playwright; ffmpeg is installed).

1. **Capture path.** Replace `recordVideo` with the CDP screencast (section 5.7): JPEG q92, `maxWidth 3840 x 2160`, ack each
   frame, write `frames/fNNNNN.jpg` + `frames.ffconcat` with durations from `performance.now()`. Test: `--dry-run` against the
   fixtures build on 3100 (`ABB_WEB_DATA_SOURCE=fixtures ABB_WEB_ALLOW_FIXTURES=1`), `node -e` prints the first frame's size as
   3840x2160 from `meta.json`; `ffprobe frames/f00000.jpg`.
2. **Overlay v2.** Replace the dot with the arrow cursor, add the ripple, the spotlight div, the key chip and the body-zoom
   function (section 3; the PoC file `docs/design/launch/video-concepts/poc-record.mjs` has a working `overlay()` to lift).
   Test: a 5 s clip that clicks one link and zooms once; extract 3 frames; look at them.
3. **Motion driver.** Add `travel(to)` (eased, bowed, 60 Hz), `dwell`, `click`, `zoomTo(locator, level)`, `spotlight(locator)`,
   `keyChip(key)` as step kinds in the scene table (`move` becomes `travel`, keep `click/press/goto/wait/scheme`). Test: unit-test
   the bezier solver (`f(0)=0, f(1)=1, monotonic`) in `apps/web/tests` or a `node --test` file beside the script.
4. **Captions with their own cues.** A beat's `captions: [{at, text, bank}]` list; the driver schedules them on the beat's
   clock; duration rule from section 4 as the default `until`; the driver logs each cue's real start/end into `meta.json`.
   Test: `--dry-run` prints the cues; `demo.vtt` is written and validates (`node -e` parse: WEBVTT header, `-->` lines).
5. **Scene table v2.** Encode section 2.1 (14 beats) and 2.2 (teaser, `--teaser`) with fixtures names; keep the
   `[CONFIRM AFTER STEP 10]` marks. Test: full dry run against fixtures, total length 74 +- 1 s in `meta.json`.
6. **Encode script v2.** `scripts/encode-demo.sh <take-dir>` runs section 5.1-5.6 (master, mp4, webm, av1 if `libsvtav1`,
   poster png+webp, vtt/srt/transcript copy, and with `--teaser` the fade seam and GIF). Prints an `ffprobe` line per output
   and fails on any budget overrun. Test: run on the dry-run take; open the mp4 in a browser.
7. **Frame QA script.** `scripts/video-qa.mjs <take-dir>`: extracts a frame per caption midpoint and per zoom, runs the red-pixel
   count and the text grep (section 7.1), writes `qa/` with the frames and a `qa.json`; non-zero exit on a failure. Test: plant a
   red box in one frame with ffmpeg `drawbox` and see it fail.
8. **Real take.** After step 10 (`make demo`) is merged: fill `DATA.demo`, replace every `[CONFIRM AFTER STEP 10]`, record
   twice, run the QA script on both, compare `meta.json`. Look at every QA frame as an image. Attach the frames and the
   `ffprobe` lines to the plan's evidence.
9. **Site wiring (with the landing-site builder).** Teaser `<video>` block from section 5.5 with the reduced-motion variant,
   the full cut with `controls` and the `<track>`, the transcript under it; sizes checked by `scripts/check-site.sh`
   (budget excludes the video files; they live under `site/assets/` and are listed in the release checklist with sizes).
10. **README.** The GIF with the poster as `alt`, linking to the full cut (release attachment after G2).

Not in scope for the builder: music, voice-over, any `--live` footage, a social cut before the main cut is approved.

---

## Sources

- Playwright `recordVideo` limits and the fixed bitrate: https://github.com/microsoft/playwright/issues/31424 ; https://playwright.dev/docs/videos
- 1x density of Playwright recordings, Screen Capture API alternative: https://alexwlchan.net/2026/record-browser-video/
- Launch-video formats (Linear Initiatives 4:15 walkthrough; Warp 2.0 2:27; Raycast 2025 0:39 cropped UI): https://www.moonb.io/blog/product-launch-video
- Developer-tool demo video corpus (33 of 49 under 1:30; 42 of 49 with voice-over, which we deliberately do without): https://advids.co/blog/developer-tool-demo-videos-code-on-screen
- Warp explainer pattern: https://echai.ventures/videos/warp-intro-to-warp-terminal-agents-and-cloud
- Silent viewing and the 3-second decision: https://www.bytecap.io/blog/science-of-silent-viewing-short-form-video-retention ; https://openclip.app/guides/hook-optimization-guide ; https://bluetext.com/blog/closed-captions-silent-video/
- Caption numbers: Netflix English Timed Text Style Guide (20 cps adult, 42 cpl, 2 lines): https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-Timed-Text-Style-Guide ; BBC/industry reading-speed summaries: https://support.limecraft.com/en/support/solutions/articles/48001249679 ; https://www.bookwriter.vip/subtitle-reading-speed-checker
- Screen Studio cursor/zoom conventions (spring motion, auto-zoom on clicks, motion blur): https://screen.studio/guide/animations ; https://screen.studio/guide/auto-zoom ; https://daveswift.com/screen-studio/
- VP9 VOD settings (1080p30: 1800k, crf 31, tile-columns 2, g 240, two-pass): https://developers.google.com/media/vp9/settings/vod
- SVT-AV1 ffmpeg guidance (presets 4-6, crf ~30 at 1080p, tune=0): https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Ffmpeg.md
- Hero video markup and source order: https://web.dev/learn/performance/video-performance
- Loop seams (xfade rules, cut-and-swap, why crossfades blur text): https://trac.ffmpeg.org/wiki/Xfade ; https://discourse.vidvox.net/t/crossfade-a-looped-clip-with-itself/549
- Accessibility: WCAG 1.2.1 video-only text alternative: https://www.w3.org/WAI/WCAG22/Understanding/audio-only-and-video-only-prerecorded.html ; captions open vs closed: https://kb.daisy.org/publishing/docs/wcag/captions-prerecorded.html
- Product: `docs/design/pass-2/01-direction.md`, `04-colour-type-tokens.md`, `05-microcopy-and-states.md`; `docs/screenshots/pass-2/`; `docs/design/launch/positioning-brief.md`; `docs/design/video-storyboard.md`; `scripts/record-demo.mjs` (phase-20-video worktree); `docs/plans/active/phase-20-release-polish.md` (claims table, D4, D9-D11, D15, D17).
