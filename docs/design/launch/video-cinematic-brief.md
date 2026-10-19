# Agent BlackBox launch film: cinematic brief (v3 direction for `scripts/record-demo.mjs` + a compositor)

Status: written 2026-10-19 after the owner watched the polished 70 s cut (`video-out-final/demo-1080p.mp4`: real app,
simulated cursor, in-page zoom, burned-in captions, silent) and called it honest and readable but not cinematic. This brief
keeps everything that made it honest (`docs/design/launch/video-brief.md` v2: real product, names clicked by visible name,
captions from the bank, the claims gate, the QA script) and adds the craft around the product. It amends v2 where the two
disagree; v2's sections 3.3 (cursor), 3.5 (CSP, hydration, 2x capture), 5 (encodes) and 7.1 (QA) stay in force.

Inputs read: v2 brief; `video-qa-report.md` section 6 (the criticisms); `positioning-brief.md` (caption bank, section 7, and the
claims table in `docs/plans/active/phase-20-release-polish.md`); `landing-site-brief.md` sections 1, 5, 10 (brand, type,
tokens, non-goals); ADR-166 and `docs/design/pass-2/01-direction.md` ("One Red Line"); `scripts/record-demo.mjs`,
`record-demo-lib.mjs`; `brand/pulse-mark.svg`, `site/public/assets/`; `apps/web/design-tokens/tokens.json`. Frames of the final
cut were extracted with ffmpeg (every 5 s, plus full frames at 1.5, 9.5, 18.5 and 66 s) and looked at.

Evidence words as in AGENTS.md: VERIFIED (run here, output seen), UNVERIFIED (env), ASSUMED. Section 7 is the proof of concept.

---

## 1. Concept and principles

**Concept, one paragraph.** The film is a quiet screening of the real product. A dark canvas, one framed window, one band of
text under it. The window is the app recorded at 2x, never a mock, never retouched; the canvas, the band, the two cards and
the camera are the film's own craft. The first 1.3 s is the mark and the wordmark on the canvas, which dips to the Runs list;
from then on the window never leaves the frame, the camera moves four times (three zooms, one walk of the spotlight), the
canvas darkens a step whenever the camera is close, and every cut lands on a caption change. Red is still only the fault line
and the failure words, now on a canvas so dark that the one red line is the brightest colour in the film. It ends on the mark
and the name, the way it began.

**What "cinematic but honest" means here (the principles the builder tests against):**

1. **The product is the footage.** Everything inside the window is the app as recorded, at 2x, with the app's own motion
   (fault-line draw, inspector slide). No frame is edited inside the window; no UI is drawn by the film. The only things the
   film draws are outside the window (canvas, band, cards) or are what a screen recorder would add (cursor, ripple, camera,
   spotlight).
2. **One stage, one light.** One window position for the whole cut (no window moves, no multi-window montage); the canvas is
   flat (gradients, glass and glow are non-goals in `landing-site-brief.md` section 1 and ADR-166); depth is one shadow under
   the window and a 1 px hairline. The canvas dims when the camera is close. That is the entire lighting vocabulary.
3. **Captions never touch the product.** The band is a fixed region under the window. A caption is text on the canvas, no box,
   no border. The QA criticism "captions sit on live content" is removed by geometry, not by placement per beat.
4. **Motion means something.** Four camera moves, each on the thing the caption names. The cursor travels only to click or to
   point; it is hidden during zooms and during page changes. Between beats the film cuts (a route change under a caption gap),
   it does not pan.
5. **Rhythm comes from the captions.** Silence is a constraint, so the cut's pulse is the caption cadence: 2.1-3.7 s per
   caption by the reading rule, 0.25-0.35 s gaps, cuts on gaps, a 1.2 s hold before the first caption of each page so the eye
   lands before the text does. Long hold = emphasis; the longest holds are the headline zoom and the fault line.
6. **Brand first and last, nowhere in between.** The mark and wordmark open and close the film. Inside, the app's own top bar
   carries the name. No watermark, no corner bug.
7. **Say only what is on screen.** Captions from the bank, max 9 words, the reading rule, the claims gate: unchanged from v2.
   "Open source. Self-hosted. Apache-2.0." exists only as the `RELEASE_CLAIMS=1` end card.

Research notes (patterns, nothing copied): tech product launch films open on a card or type far more often than on the product
(86 of 149 in a 2026 corpus; product on screen at a median of 12 s; 98 of 149 end on a logo card; "not one video ends on its
result shot": [advids corpus](https://advids.co/blog/aha-moment-tech-product-videos)). Screen Studio's framing vocabulary is
padding, corner radius, an inset frame, a shadow and a flat/gradient background, with auto-zoom on the cursor and smoothed
pointer motion ([Screen Studio background guide](https://screen.studio/guide/background), [review](https://daveswift.com/screen-studio/)).
Launch-film cursor practice: one arrow geometry for the whole film, a decelerating entry of 0.4-0.9 s, a press to 0.84 scale and
back, drift aside during beats it does not own, never idle ([Hyperframes oversized-cursor notes](https://tessl.io/registry/skills/github/heygen-com/hyperframes/oversized-cursor--oversized-cursor)).
Silent-edit pacing: test the cut muted, vary shot length, hold on the hero moment two to four times the average, cut on motion
([Dream Engine](https://www.dreamengine.com.au/tips-and-resources/learning-the-art-of-video-editing/), [Nemo](https://www.nemovideo.com/blog/ai-video-editing/how-to-edit-video-for-sound-off-viewing)).
Lower-third safe area: keep text out of the outer 10 % of the frame; a 1080p working figure is about 100 px from the bottom
edge ([TechSmith](https://www.techsmith.com/blog/edit-videos-lower-thirds/)). Earlier research in v2 (Raycast's cropped real UI,
Warp's keystroke-level workflow, Netflix/BBC reading speeds, WCAG 1.2.1) still applies.

---

## 2. Composition spec (numbers)

Colours are tokens from `apps/web/design-tokens/tokens.json` by name, with the rendered hex; two film-only values are declared
below and nowhere else. The product's dark scheme is the one filmed (the light-scheme beat is cut, section 3).

### 2.1 Film tokens

| Name | Value | Source | Use |
| --- | --- | --- | --- |
| `film.canvas` | `oklch(13% 0.010 255)` ≈ `#0d1015` | film-only (one step under `neutral.900`) | the canvas, the card backgrounds, the dip colour of every transition |
| `film.app` | `#15191e` | `color.bg.canvas` dark (`neutral.900`) | what shows inside the window before the first frame arrives (never visible in the cut) |
| `film.hairline` | `rgba(74,82,92,0.55)` | `neutral.600` `#4a525c` at 55 % | the 1 px window edge |
| `film.ink` | `#e4e8ec` | `color.text.primary` dark (`neutral.100`) | caption text, card name, card mark |
| `film.ink2` | `#a0a8b2` | `color.text.secondary` dark (`neutral.300`) | card tagline, URL |
| `film.fail` | `#ff8a80` | `red.300` | the red words in captions only |
| `film.dim` | `rgba(0,0,0,0.38)` | film-only | the canvas darkening during zooms (never over the window) |

No orange in the film outside the app (the mark's cut is orange in the favicon; on the cards the mark is `film.ink`, as the
closing card of the v2 cut already did). No accent blue outside the app.

### 2.2 Main cut, 1920x1080

```text
┌────────────────────────────────────────── 1920 ──────────────────────────────────────────┐
│ 36                                                                                       │
│    ┌──────────────────────────── window 1696 x 954 (88.3 %) ────────────────────────┐    │
│112 │  the app, recorded at 1920x1080 CSS px @ 2x, scaled 0.8833 (lanczos)            │112 │ 1080
│    │  radius 6, 1 px film.hairline, shadow 0 32px 80px rgba(0,0,0,.70) + 0 6px 16px .50 │    │
│    └──────────────────────────────────────────────────────────────────────────────┘    │
│ 990 ─ caption band 1920 x 90: Plex Sans 600 32/40, film.ink, centred, baseline band centre y=1035 │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

| Element | Numbers |
| --- | --- |
| Canvas | 1920x1080, `film.canvas`, flat (no vignette: it banded in PoC iteration 1 and gradients are a brand non-goal) |
| Window | x 112, y 36, w 1696, h 954 (88.3 % width, 16:9 exact, scale 0.8833 of the 1920x1080 recording); radius 6 px; `box-shadow: 0 0 0 1px film.hairline, 0 32px 80px rgba(0,0,0,.70), 0 6px 16px rgba(0,0,0,.50)`; the window never moves or resizes |
| Caption band | y 990-1080 (90 px); text centred horizontally, vertically centred in the band (text block 1015-1055, which keeps the 32 px glyphs 25 px above the frame edge: inside the 5 % action-safe band and above player chrome); Plex Sans 600 32 px / 40 px line, `letter-spacing: 0`, one line, max 9 words (a 9-word caption is about 700 px); no box, no border, no shadow; `film.ink`; the fault words in `film.fail`; in 160 ms, out 120 ms, opacity only |
| Why 32 and not 34 | the band is 90 px; 34/44 would leave 23 px to the edge. 32 px is 3.0 % of the frame height, the same optical size as v2's 34 px inside a box |
| Cursor | unchanged from v2 3.3 (32 px arrow, bowed eased travel, ripple, press scale 0.88), recorded inside the window, so it scales to 28 px on screen; hidden during zooms and transits |
| Camera | in-window zoom (CSS transform on `<body>`, v2 3.1) with the v2 origin rule; levels 1.0 / 1.25 / 1.6; in 700 ms, out 600 ms, `cubic-bezier(0.65, 0, 0.35, 1)`; min hold 2.5 s; the inspector zoom drops from 1.4 to 1.25 so the headline is not cut mid-word (QA report section 6) |
| Dim | when any zoom is above 1.0, `film.dim` over the canvas outside the window (a plate with the window cut out), alpha in over the zoom-in (700 ms), out over the zoom-out (600 ms). Measured on the PoC: canvas luma 28.3 to 23.5. Never over the window, never during a spotlight |
| Spotlight | unchanged (v2 3.2), inside the window |
| Transitions between pages | a hard cut on a navigation, inside a caption gap (no caption on screen across a cut); the window stays; the cursor is hidden for the first 300 ms of the new page |
| Title card | 0.0-1.4 s; card = `film.canvas`; the lockup centred at the optical centre (y 540 minus 14): mark 72 px (`brand/pulse-mark.svg`, stroke `film.ink`), 22 px gap, "Agent BlackBox" Plex Sans 600 56/64 `film.ink`, 14 px gap, "A flight recorder for AI agents." Plex Sans 400 24/32 `film.ink2` (positioning brief headline option 3, allowed as the one-line description); dip out to `film.canvas` over the last 250 ms |
| Hook | the brand is on screen from frame 1 (goal 2 solved by the title card); the Runs list dips in at 1.4-1.65 s; the first caption lands at 2.6 s; the failed run is open by 5.4 s |
| End card | the same lockup; dip in 250 ms; hold 3.0 s; the film ends on it (no fade to black: the last frame is the card) |
| End card, claims variant | only with `RELEASE_CLAIMS=1` (section 5): the lockup plus, 40 px below the tagline, "Open source. Self-hosted. Apache-2.0." Plex Sans 400 26/34 `film.ink`, then 12 px below, `github.com/abdullahimohamed101/agent-black-box` Plex Mono 400 20/28 `film.ink2`; hold 3.7 s (the URL's reading time) |
| Window entry (optional) | a settle from scale 1.012 to 1.0 over 600 ms on the whole composite as the Runs list dips in (ffmpeg `zoompan` with `d=1`); not in the PoC; use only if the dip alone feels static |
| Output | 1920x1080, 30 fps constant, yuv420p tv range, H.264 crf 20 + VP9 + AV1 as in v2 section 5; the master is the composite, not the raw frames |

### 2.3 Teaser loop, 1920x1080, 18 s

Same window, band and dim as 2.2; no title card and no end card (the landing hero around it carries the name); seam = 250 ms
dip to `film.canvas` at both ends, so the loop is a blink. The site must wrap the hero `<video>` in a box whose background is
`film.canvas` (one CSS rule in `site/`; otherwise the dip shows as a dark flash against `bg.canvas`). Captions 3, 4, 7, 8, 9.

### 2.4 Social cut, 1080x1350 (4:5), separate take

```text
┌──────────── 1080 ────────────┐
│ 40                            │
│  ┌── window 1000 x 1000 ──┐   │
│40│ app at 1000x1000 CSS px│40 │ 1350
│  │ @2x, scale 1.0          │   │
│  └─────────────────────────┘   │
│ 1040 ─ band 1080 x 310: up to two lines, Plex Sans 600 44/54 │
└───────────────────────────────┘
```

| Element | Numbers |
| --- | --- |
| Recording | viewport 1000x1000 CSS px at 2x (the app's 768-1080 layout: one column, the inspector as an overlay). Not a crop of the 16:9 take: a crop of 13 px rows would be unreadable at 4:5 |
| Window | x 40, y 40, 1000x1000, radius 6, same hairline and shadow; scale 1.0 (no downscale: the app's 13 px rows are 13 px on a 1080-wide frame) |
| Band | y 1040-1350; text block centred at y 1180; 44/54 px, two lines allowed (the reading rule unchanged; a two-line caption is max 9 words still); 60 px side margin |
| Camera | levels 1.0 / 1.4 only (the inspector overlay already fills the window); the dim as 2.2 |
| Cards | the same lockup, mark 64, name 48/56, tagline 22/30; title 1.4 s, end 3.0 s |
| Length | 40-45 s (section 3.3); H.264 <= 8 MB, plus a 1080x1080 version made by cropping the 4:5 composite to y 135-1215 only if the band fits (it does: the band centre is at 1180; the window would lose 95 px top and bottom, so prefer re-rendering plates at 1080x1080 with a 900x900 window if the square version is wanted) |

---

## 3. Shot lists

Conventions as v2 section 2: `t` from the first frame of the cut; selectors the recorder already uses; caption numbers are the
positioning brief's bank; caption durations are the reading rule `max(2.0, chars/15 + 0.6)`; "cut" is a navigation inside a
caption gap. Camera moves are named in the Camera column; "dim" is implied by any zoom above 1.0. Demo names are the ones
`scripts/demo.sh` seeds today (`DATA.demo` in the recorder); the section 8 renames change them in one place.

### 3.1 Main cut (72.5 s; 76.2 s with the claims end card)

| # | t | Screen | Action and cursor | Camera | Caption (bank, chars, duration) | Out |
| --- | --- | --- | --- | --- | --- | --- |
| T | 0.0-1.4 | Title card | none; cursor hidden | static | none (the card is the text) | dip 250 ms |
| 1 | 1.4-6.0 | `/runs`, dark | hold 1.2 s with the cursor parked at the Runs parking spot; 2.6 s: travel to the failed run (900 ms), dwell 400, click at 4.4; run page ready by 5.4 with the fault line drawing; park | 1.0 | 2.6-4.7 "A [failed] run. Open it." (3; 22; 2.1) | hold (same page) |
| 2 | 6.0-10.6 | run page, Timeline | cursor hidden at 6.3 | 6.3: zoom 1.6 on `headline`; hold 3.0; zoom out at 10.0 | 6.8-9.6 "One sentence says where it broke." (4; 33; 2.8) | hold |
| 3 | 10.6-14.0 | same | none | spotlight on `headline-detail` (pad 3) 10.8-13.8 | 10.8-13.9 "Spend after the first error, counted." (6; 37; 3.1) | spot out 200 ms |
| 4 | 14.0-17.6 | same | cursor shown at 14.0; travel to 60 px left of the fault label, 24 px under the rule | spotlight on the fault row + label 14.3-17.3 | 14.3-17.2 "The red line is the [first error]." (7; 34; 2.9) | spot out |
| 5 | 17.6-24.8 | inspector | focus `.timeline`; press `e` at 17.8 with the key chip; dialog slides in | 18.9: zoom 1.25 on the "What went wrong" block (not 1.4: no mid-word crop); hold 4.6; out at 24.2 | 17.8-20.9 "Press e. The [failed] tool call opens." (8; 37; 3.1); 21.2-24.5 "What went wrong, from the event itself." (9; 41; 3.3) | Escape at 24.7 |
| 6 | 24.8-31.9 | Timeline | travel to the `llm.request.completed` row, then at 28.5 to the `retry.attempted` row | spotlight follows the row (one box slides 260 ms) | 25.1-28.4 "Every model call carries tokens and cost." (10; 41; 3.3); 28.7-31.6 "Retries are retries, not log lines." (11; 35; 2.9) | spot out |
| 7 | 31.9-39.2 | Findings tab | click the tab at 32.2; click "Show evidence" at 35.7; the jump outline lands | 1.0 | 32.4-35.6 "Findings, from deterministic detectors." (12; 39; 3.2); 35.9-38.9 "Each finding links to its evidence." (13; 36; 3.0) | cut at 39.2 |
| 8 | 39.2-46.9 | multi-agent run, Waterfall, Lanes | the recorder navigates by URL (`goto` the run's Waterfall route): no cursor transit through Runs; cursor hidden until 39.5, then travels along the critical-path bar 43.0-44.5 | spotlight on the critical-path row 43.4-46.6 | 40.0-42.8 "Five agents, one run, one picture." (16; 33; 2.8); 43.6-46.6 "The thick edge is the critical path." (17; 36; 3.0) | cut at 46.9 |
| 9 | 46.9-54.0 | Review, Approvals | `goto`; cursor to the waiting row at 47.4, click at 48.0; panel opens; park | spotlight on the capability/resource/risk block 50.8-53.6 | 47.8-50.7 "The agent asked. A person decides." (22; 35; 2.9); 51.0-53.7 "One approval, one action, once." (23; 32; 2.7) | cut at 54.0 |
| 10 | 54.0-61.2 | Analytics, Cost | `goto`; park | spotlight on "Where the money was wasted" 57.4-60.9 | 54.8-57.3 "What the week cost, per run." (20; 28; 2.5); 57.6-60.9 "Failed runs, retries, spend after errors." (21; 41; 3.3) | cut at 61.2 |
| 11 | 61.2-69.0 | Review, Security findings | `goto`; cursor to the first row at 62.0 | spotlight on the first row 62.3-68.6 (one rect; the "Rule:" line is inside it) | 62.0-65.7 "Destructive commands, secret reads, risky git." (14; 46; 3.7); 66.0-68.7 "Every finding states its rule." (15; 31; 2.7) | dip 250 ms at 69.0 |
| E | 69.25-72.5 | End card | none | static | none (default card: lockup + tagline). With `RELEASE_CLAIMS=1`: the claims card, hold 3.7 s plus 0.25 (ends 76.2) | end |

Removed from v2: beat 1's brand caption (the title card carries it), beat 13 (light scheme: the one-frame flash was the QA
report's "hard cut to light", and the landing page shows both schemes in stills), and the three cursor transits through the
Runs list (v2 beats 9, 10, 12 spent about 3 s each travelling before the first caption; the cut now navigates by URL inside a
caption gap). Caption count 19, total reading time 56.2 s of 72.5.

Holds and cadence (the sound-free rhythm): the cut has four lengths of thing. Cards 1.4 and 3.0 s. Pages before the first
caption: 1.2 s (Runs), 0.8 s (waterfall, approvals, analytics, security). Captions 2.1-3.7 s with 0.25-0.35 s gaps. Camera holds
3.0 s (headline), 4.6 s (inspector), the spotlights 3.0-6.3 s. The longest unbroken hold is the inspector (beat 5), the film's
centre; the fastest stretch is beats 7-10 (four pages in 22 s), after which beat 11 slows and the end card stops.

### 3.2 Teaser (18.0 s)

| # | t | Screen | Action | Camera | Caption |
| --- | --- | --- | --- | --- | --- |
| T1 | 0.0-4.3 | Runs | dip in 250 ms; hold 0.9; travel from 1.2, click at 2.7; run page ready by 3.7 | 1.0 | 0.6-2.7 "A [failed] run. Open it." (3) |
| T2 | 4.3-8.6 | run page | cursor hidden | zoom 1.6 on the headline 4.5-8.0 | 5.0-7.8 "One sentence says where it broke." (4) |
| T3 | 8.6-12.0 | run page | cursor to the fault line | spotlight on the fault row 8.9-11.7 | 8.9-11.8 "The red line is the [first error]." (7) |
| T4 | 12.0-17.75 | inspector | press e at 12.2 (chip) | zoom 1.25 on "What went wrong" 13.0-17.2 | 12.4-15.5 (8); 15.8-17.5 "What went wrong, from the event itself." (9) |
| seam | 17.75-18.0 | | dip out 250 ms to `film.canvas`; loops to T1 | | |

### 3.3 Social cut, 1080x1350 (43 s), separate take at 1000x1000 CSS px

The narrow layout stacks the run header (title, headline, detail, figures) over the timeline, and the inspector opens as an
overlay that covers the list; each beat says what is framed. Captions may wrap to two lines in the 310 px band.

| # | t | Screen (1000x1000 viewport) | Framing and camera | Caption |
| --- | --- | --- | --- | --- |
| T | 0.0-1.4 | Title card | lockup, mark 64 | none |
| 1 | 1.4-6.4 | Runs: the narrow list shows status, name and the failure line; the facet column is hidden at this width (confirm) | 1.0; the failed run is in the first screen (demo order); travel, click at 4.6 | 2.6-4.7 "A [failed] run. Open it." (3) |
| 2 | 6.4-11.0 | run page: header stacked; the headline wraps to two lines at 1000 px (confirm with the fixtures run: 64 chars at 24 px is about 820 px, so one line; the demo's 66-char headline likewise) | zoom 1.4 on the headline block (origin rule); hold 3.0 | 7.0-9.8 (4) |
| 3 | 11.0-14.4 | same | spotlight on the detail line | 11.2-14.3 (6) |
| 4 | 14.4-18.0 | same, timeline below the header | the fault line sits lower on a stacked page: `scrollIntoViewIfNeeded` on the fault row before the beat (off camera, during the previous spotlight is wrong; do it at 14.2 inside the caption gap); spotlight on the row + label | 14.7-17.6 (7) |
| 5 | 18.0-25.2 | inspector overlay (covers the list at this width) | press e; no zoom (the overlay is the frame); spotlight on "What went wrong" | 18.2-21.3 (8); 21.6-24.9 (9) |
| 6 | 25.2-32.3 | timeline rows | spotlight walks model-call row then retry row (the rows are narrower: the cost figures may wrap or truncate; confirm `llm.request.completed` still shows tokens and cost at 1000 px, else drop caption 10 and keep 11) | 25.5-28.8 (10); 29.1-32.0 (11) |
| 7 | 32.3-39.6 | Findings tab | tab click; Show evidence; 1.0 | 32.8-36.0 (12); 36.3-39.3 (13) |
| 8 | 39.6-39.85 | | dip out | |
| E | 39.85-43.0 | End card | lockup | none (claims variant with `RELEASE_CLAIMS=1`, 3.7 s) |

Not in the social cut: the waterfall (five lanes do not fit 1000 px), approvals (the panel's foot sentence is below the fold at
this width), analytics, security. A 1080x1080 square is the same take with plates at 1080x1080 and a 900x900 window
(x 90, y 40; band y 980-1080, 40/50 px, one line).

---

## 4. Rhythm without sound (the devices, stated as rules)

1. **Cut on the gap.** Every route change happens inside a caption gap (>= 0.25 s with nothing in the band); the new page
   gets 0.8-1.2 s before its first caption. A cut never happens under a caption and a caption never spans a cut (v2 rule).
2. **Three tempos.** Fast: the cursor (350-1100 ms travels, 90 ms press). Medium: captions (2.1-3.7 s) and camera moves
   (600-700 ms). Slow: holds (3.0-6.3 s) and cards (1.4 / 3.0 s). A beat uses one fast thing, one medium thing and one hold.
3. **Ease, never linear, never spring.** Camera `cubic-bezier(0.65, 0, 0.35, 1)`; cursor `cubic-bezier(0.22, 1, 0.36, 1)`;
   caption opacity linear 160/120 ms (opacity is the one place linear reads as neutral); dips 250 ms linear in colour.
   No overshoot anywhere (the product's motion contract has none).
4. **The dim is the drum.** The canvas darkens with each zoom and returns with the zoom-out. Three times in 72 s, on the
   headline, the inspector and nothing else (the spotlights do not dim the canvas). Viewers feel the "close / wide" alternation
   without noticing the device.
5. **Hold the hero.** The inspector zoom (4.6 s) is the longest hold and sits at the film's centre (17.6-24.8 of 72.5).
6. **Bookends.** The same lockup, the same dip length, opens and closes; the end card holds 3.0 s, twice the title card.
7. **No idle cursor.** The cursor is either travelling, dwelling before a click (400 ms), pointing (beat 4, 6, 8) or hidden.
   Parked positions are reached by a travel, never by a jump, and never during a zoom.

---

## 5. The claims end card (`RELEASE_CLAIMS=1`)

- The plate renderer writes `end.png` always and `end-claims.png` only when `RELEASE_CLAIMS=1` is in the environment; the
  compositor picks `end-claims.png` only when both the flag is set and the file exists, otherwise `end.png`. The default build
  therefore cannot contain the claims card even by accident (there is no file to pick). VERIFIED in the PoC: `plates.mjs`
  with the flag wrote `end-claims.png`; `composite.sh` used `end.png` (the clip ends on the default card).
- The recorder's existing plan check (`checkPlan`: gated bank 26/28 refused without the flag) stays, and the `.vtt` gains the
  two cues only with the flag (they are text on a card, still captions for the sidecar).
- The claims line is the bank's caption 26 verbatim; the URL is caption 28; both wait for G2 and AC1 as the claims table says.
- `meta.json` already records `releaseClaims`; `video-qa.mjs` adds a check that the last frame contains no text below the
  tagline unless `releaseClaims` is true (luma scan of the band y 640-760 on the end card: dark unless gated).

---

## 6. Approach: composite with ffmpeg from the 2x recording (decision and why)

Two ways were on the table.

**A. Record the framed composition live** (a stage page that embeds the app in an `<iframe>` scaled into the window, with the
band and the cards as DOM). Rejected: the app's CSP and `frame-ancestors`/session cookie handling would need bypassing for the
iframe, Playwright would have to drive the iframe's frame locator with coordinates mapped through a transform, the caption and
card timing would be subject to the same browser scheduling jitter the QA report measured in frame gaps (1.6-2.7 s stalls on a
loaded machine), and every re-cut (a caption change, a different hold) means a re-record.

**B. Keep the recorder as it is (the app at 1920x1080 @ 2x, cursor, camera, spotlight in-page), turn its in-page caption and
card off, and composite with ffmpeg** from the frames plus plates rendered once from HTML with Playwright. Chosen:

- Deterministic: every overlay is an expression on `t` (fade, enable, concat), so two composites of the same take are
  byte-identical except encoder nondeterminism, and the QA can assert caption rectangles from `meta.json` exactly.
- Crisp: the window is a lanczos downscale of 3840x2160 frames to 1696x954 (0.44 of the source), sharper than the v2 1:2;
  zooms stay in-page at 2x, so 1.6x glyphs are rasterised at 1.25x their output size (crop VERIFIED, section 7).
- Re-cuttable: a caption change is a new PNG and a number; a different hold is a `trim`; the take is not touched.
- Cards use the real Plex files through `@font-face` from `apps/web/src/fonts` (no third-party fonts), rendered at 1x PNG.
- Cost: the plates are 5-25 KB PNGs; the composite of 10.8 s took 2.6 s wall on this machine at `nice 15`; a 72 s cut is
  under 30 s of ffmpeg before the final encodes.

What stays in the browser: the cursor, the ripple, the camera zoom, the spotlight, the key chip (they must track the DOM).
What moves out: captions, the cards, the fades, the dim. The recorder keeps writing `meta.json` cue times; the compositor reads
them. Optional window settle: `zoompan` on the composite (section 2.2).

Pipeline (the builder's `scripts/encode-demo.sh` grows a `compose` step before the master):

```text
record-demo.mjs  ->  frames/ (2x JPEG) + frames.ffconcat + meta.json (cues, zooms, beats)
plates.mjs       ->  canvas.png, dim.png, cap-<cue>.png, title.png, end.png [, end-claims.png]
compose          ->  ffmpeg filter graph (section 7.2): scale app -> overlay on canvas -> dim (fade alpha at zoom marks)
                     -> caption overlays (enable + alpha fades at cue times) -> dip joins -> title/end concat -> master
encode           ->  v2 section 5 unchanged (H.264, VP9, AV1, poster, teaser seam, GIF, sidecars)
```

---

## 7. Proof of concept (run here, looked at, iterated twice)

Files: `docs/design/launch/video-concepts/cinematic/` (`record.mjs`, `plates.mjs`, `spec.json`, `composite.sh`, `README.txt`;
`out/` is gitignored and holds the clip, frames and plates of the last run). Run against this worktree's fixtures build of
`apps/web` (`next start --port 3102`, `ABB_WEB_DATA_SOURCE=fixtures ABB_WEB_ALLOW_FIXTURES=1`, build current: `.next/BUILD_ID`
newer than the last `apps/web` commit) under the heavy mutex, `nice -n 15`, server stopped afterwards; the owner's stack on
3000/8000/5433 was not touched. Nothing was pushed or committed.

### 7.1 What was recorded and rendered

- `record.mjs`: 9.0 s, 92 frames at 3840x2160 JPEG q92: Runs list hold, cursor travel and click on "Migrate billing schema"
  (click at 2.13 s), run page with the fault-line draw (ready 2.70 s), cursor parked and hidden, zoom 1.6 on the headline
  (in 3.71 s, out 7.41 s), zoom out, hold. VERIFIED (`marks.json`).
- `plates.mjs` (Playwright, 1x, PNG with alpha where needed): `canvas.png` (window edge + shadow on the flat canvas),
  `dim.png` (black 38 % with the window cut out), `cap-3.png`, `cap-4.png` (band text with the red word), `title.png`,
  `end.png`, and with `RELEASE_CLAIMS=1` `end-claims.png`. VERIFIED (eight files, viewed as a 2x2 review sheet).
- `composite.sh`: title (1.4 s) -> app segment 8.3 s with the dim and the two captions -> end card; 10.8 s in iteration 1
  (xfade dissolves), 11.9 s in iteration 2 (dip joins, end card 2.2 s). Output 1920x1080 30 fps yuv420p H.264 crf 18:
  1.79 MB (v1) and 1.39 MB (v2), faststart. VERIFIED (`ffprobe`).

### 7.2 Iterations and what the frames showed

| Iteration | Change | Seen in the frames |
| --- | --- | --- |
| 1 | radial vignette on the canvas (`#161a20` centre to `#0d1015`), window 1696x954 at (112, 36), band 90 px with 32/40 captions, xfade dissolves of 0.4 / 0.5 s between cards and the shot, dim 0.38 | The window, hairline and band read as intended (`out/frame-runs-window.png`, iteration-2 copy): captions clear of the UI, the red word the only red outside the window. Two faults: the vignette **banded** into visible rings on the cards (8-bit gradient on a dark field), and the title-to-shot **dissolve** put the wordmark over table rows for 0.4 s, which read as a double exposure (`chk1/t1.25`). |
| 2 | flat canvas `#0d1015`, no gradient; shadow raised to `0 32px 80px .70 + 0 6px 16px .50` so depth comes from the shadow alone; dips through `film.canvas` (250 ms out, 250 ms in) instead of dissolves; end card 2.2 s | No banding (`out/frame-end-card.png`); the shot now arrives from the canvas colour, so the first thing that appears is the window edge, then the page. Zoom crispness: a 900x80 crop of the 1.6x headline at 3x (`out/crop-zoom-1.6x.png`) shows clean stems and counters, no ringing. Dim measured with `signalstats` on an 80x300 canvas patch: YAVG 28.3 before the zoom, 23.5 during the hold (17 % darker), visible but quiet. Contact sheet (`out/contact-sheet.jpg`, 2 fps): the window never moves; the band is empty between captions; the cursor is absent during the zoom. |

Things the frames also show, for the builder: the app's top bar reads "Agent Black Box" while the cards say "Agent BlackBox"
(the rename is scheduled; the film will contradict itself until it lands, as the QA report said); at 1.6x the zoom pins to
the left edge and the top bar's right half leaves the window, which is the camera and acceptable; the window's bottom edge is
30 px above the caption glyphs, which is enough, and the shadow's lower lobe darkens the band's top, which reads as depth.

### 7.3 Numbers from the PoC (VERIFIED)

| Item | Value |
| --- | --- |
| Clip v2 | 1920x1080, 30/1, yuv420p, 11.9 s, 1,385,827 B, H.264 crf 18 preset slow |
| Composite time | 2.6 s wall (20.4 s CPU across threads) at `nice 15` for 11.9 s of output |
| Record | 9.0 s, 92 frames (10 fps average: most of the clip is holds; 28-30 fps during travel) |
| Canvas luma | 28.3 (1.0x) -> 23.5 (zoom hold) |
| Plates | canvas 15.9 KB, title/end 25.9 KB, end-claims 40.4 KB |

Not done in the PoC (left to the builder): the spotlight beats, the key chip, the teaser seam, the social take, the
`meta.json`-driven caption placement, the settle. The recorder mechanics for all of those exist in `scripts/record-demo.mjs`.

---

## 8. Data asks for the demo builder

### 8.1 Twelve runs that read like a team's week

Today `scripts/demo.sh` records seven `scripted_agent.py` runs named "Answer support ticket T-104x", two coding runs, one ops
run, one multi-agent run and one waiting agent; the Runs list shows the same sentence seven times with different numbers
(QA report section 6). The ask is names and agents only; the event stories, variants, token scales and `--started-ago` stay
what they are (determinism, AC8). Durations and costs below are the targets the existing `--tokens-scale` and `--pace 0`
virtual clock already produce (10-90 s, 3-60 cents): confirm after one `make demo`, do not tune to the cent.

| # | Run name (<= 40 chars) | Agent | Model | Status | Cost, duration (target) | Age | What produces it |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Add retry to webhook sender | coding-agent | scripted-small | Success | $0.09, 48 s | 6 d | `run_demo.py` baseline, `--name` |
| 2 | Answer ticket: refund not received | support-agent | scripted-small | Success, 1 retry | $0.05, 44 s | 5 d | `scripted_agent.py` recovers variant |
| 3 | Answer ticket: login loop on iOS | support-agent | scripted-small | Success | $0.06, 31 s | 4 d | clean |
| 4 | Answer ticket: duplicate order emails | support-agent | scripted-small | Success, 1 retry | $0.04, 38 s | 4 d | flaky |
| 5 | Fix session refresh bug | coding-agent | scripted-small | Failed at step 24 | $0.14, 62 s | 3 d | `--variant wrong-fix` (exists) |
| 6 | Answer ticket: export stuck at 90 % | support-agent | scripted-small | Success | $0.07, 35 s | 2 d | clean |
| 7 | Answer ticket: card declined twice | support-agent | scripted-small | Blocked | $0.08, 22 s | 2 d | denied variant |
| 8 | Clean up old releases | ops-agent | scripted-large | Success | $0.31, 80 s | 1 d | `ops_agent.py` (exists) |
| 9 | Ship feature: multi-agent | planner (+4 lanes) | scripted-small | Success after an error | $0.06, 11 s | now | `multi-agent/agent.py` (exists; name stays: it is what the waterfall caption needs) |
| 10 | Push session hotfix | coding-agent | scripted-small | Awaiting approval | $0.00 | now | the waiter (exists) |
| 11 | Answer ticket: invoice shows wrong VAT | support-agent | scripted-small | Failed at step 6, ConnectionTimeout, 2 retries | $0.07, 57 s | 3 h | outage variant: **the hero run** (today "Answer support ticket T-1047") |
| 12 | Answer ticket: password reset not sent | support-agent | scripted-small | Success | $0.07, 40 s | 1 h | clean |

Changes needed (all demo-only, under `examples/` and `scripts/`, no product or API change): `examples/python/scripted_agent.py`
gains `--name` (as `run_demo.py` has) or, better, the `TICKETS` table gains a `subject` per ticket and the run is named
`f"Answer ticket: {subject}"`; `scripts/demo.sh` passes the names; `scripts/demo-e2e.sh`'s determinism check and the recorder's
`DATA.demo.failedRun` are updated in one place. Rules kept: no ticket id longer than 6 characters in a name (the ids move into
the ticket lookup payload, where they belong), no emails, no real company or person, no "test"/"foo", agents are role words,
models are the scripted ones. The hero headline becomes "Failed at step 6 after 17 s: lookup-ticket, ConnectionTimeout." (66
chars, fits 1.6x) unchanged.

Also from the QA report: the "Daily spend" chart is one bar because every run happened today. The `--started-ago` spread above
(6 d to 1 h) is what `demo.sh` already does for most runs; make sure the two "now" runs are the only ones on today's bar.

### 8.2 The approval's risk class: why it says "Unclassified", and what is needed

How the class gets to the screen (read in the phase-20 worktree):

- `apps/web/src/lib/policy.ts` `riskMeta()` renders `approval.risk_class`: "Risk R0-R4" when set, else the word
  **"Unclassified"**. The approvals API (`apps/api/src/abb_api/approvals/intent.py`) accepts `risk_class: RiskClass | None`
  on any intent ("The SDK classifier's class"); `policies/mapping.py` only derives a class from `shell.risk_class` attributes
  when mapping events, and `approvals/service.py` uses the class for the fail mode default (R3/R4 closed).
- The SDK (`packages/sdk-python/src/blackbox/coding.py`): `guard_command()` sends the classifier's class for `shell.execute`
  (a plain `git push` classifies **R2**, "git push"; `--force`, `--force-with-lease`, `--delete`, `--mirror` or a `+ref` classify
  **R3**, "force/delete push"). `guard_push()` sends `github.push` with resource `<remote> <branch>`, argument `branch` and
  **`risk_class=None`**. `git_push()` calls `guard_push()` first and then the shell command is guarded too.
- The demo policy (`examples/coding-agent/policies/demo.yaml`) rule `approve-push-main` matches `github.push` with
  `branch: main`, so the Waiting request on screen is the `github.push` intent, which carries no class. Hence "Unclassified".
  It is not a bug: the rule matched on capability and branch, and the SDK has no classifier for pushes.

Options, with what each is:

| Option | Change | Kind | Shows | Honest? |
| --- | --- | --- | --- | --- |
| A | `guard_push(remote, branch)` derives the class from the same git table the shell classifier uses: R2 for a plain push, R3 for a forced or deleting push; `git_push()` gains a `force: bool` to express the latter. The API needs nothing: `risk_class` is already optional on every intent and the policy rule does not match on it, so no decision changes. | **Product change in the SDK** (a public-API addition with a default; not an HTTP contract change; needs its unit test and a line in the SDK changelog) | "Risk R2" for the demo's push to main | Yes: it is the classifier's own class for `git push`, now attached to the `github.push` intent too |
| B | The demo waiter asks for a forced push (`git_push(..., force=True)` after A, or a `run_command("git push --force-with-lease origin main")` through `guard_command`) and `demo.yaml`'s rule stays on `branch: main` (A) or adds `risk_class: [R3]` (shell path). | Demo-only after A; demo-only on its own if the shell path is used (then the panel shows capability `shell.execute`, command `git push --force-with-lease origin main`, and the rule `approve-risky-shell` with a 15 min expiry, which must be raised to hours for the take) | "Risk R3" | Yes, and a better story ("the agent asked to force-push to main"), but it changes the demo scenario's README wording ("a push to main that waits for approval") |
| C | Hard-code `risk_class="R3"` in the demo's guard call | demo-only | "Risk R3" | **No**: a class the classifier did not compute, shown as if it had. Refused. |

Recommendation: A now (small, honest, product-wide benefit: every push approval gets a class), then B only if the owner wants
R3 in frame. Until A lands, the approvals beat keeps the panel but the spotlight rect excludes the risk row (the capability and
resource lines are the proof; "Unclassified" is true and merely unflattering). This is stated here so the builder does not
quietly change a contract: option A touches the SDK's public surface and must go through `review-change`.

---

## 9. QA additions (on top of v2 section 7.1) and risks

QA additions, each a check in `scripts/video-qa.mjs` or a frame to look at:

- [ ] **Band is clean**: at every cue midpoint the band region (y 990-1080) contains only the caption; at every gap it is
      within 2 luma of `film.canvas`. Outside cues, no pixel in the band is brighter than luma 40.
- [ ] **Window is still**: the hairline's top edge (y 36) and left edge (x 112) are detected at the same position in one frame
      per beat (edge scan; tolerance 1 px).
- [ ] **Dim happens**: canvas patch luma drops by >= 4 during each zoom hold and returns within 2 of the baseline after the
      zoom-out; the window region's luma does not change with the dim (sample a 100x100 patch inside the window on a hold).
- [ ] **Red outside the window**: zero pixels within 12 units of `#ff8a80` outside the window rect except inside the band
      during the fault captions (3, 7, 8). The v2 in-window red check continues unchanged.
- [ ] **Cards**: frame 1 and the last frame are the cards; the title card's lockup is centred within 2 px; the last frame has
      no text below the tagline unless `releaseClaims` (section 5).
- [ ] **Dips**: the frame at each dip midpoint is within 2 luma of `film.canvas` everywhere; no dip is longer than 300 ms.
- [ ] **Cuts in gaps**: for every beat boundary that is a navigation, the band is empty from 0.25 s before to 0.8 s after.
- [ ] **Teaser seam on the site**: the hero box background equals `film.canvas` in both schemes (a `check-site.sh` assertion).
- [ ] **Social**: every caption fits two lines at 44/54 within 960 px; the inspector overlay beat has no zoom; the fault row
      was scrolled into view before its caption (frame check).
- [ ] **Fonts**: the cards' and captions' glyphs come from the Plex files (`plates.mjs` fails if `document.fonts.check('600 32px Plex')` is false before the screenshot).

Risks:

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| The hero run's name change breaks the recorder's `DATA` and the e2e determinism check at once | certain | one-place edit in each; the recorder fails fast on a missing name |
| Banding on the flat canvas at crf 20 in dark areas (x264 on near-black) | medium | the PoC at crf 18 shows none; if the final shows banding, `-crf 19` or `-tune film` on the master only, never dithering (noise would ruin the UI crispness) |
| The flat canvas is "too empty" for the owner | medium | the shadow is the depth; the alternative (a vignette) banded in iteration 1 and conflicts with the brand's non-goals; show the owner `out/frame-runs-window.png` before changing it |
| The 0.8833 downscale softens 12 px column heads | low-medium | VERIFIED crisp at 1.0x in `frame-runs-window.png`; the zooms carry the small type; if a 12 px head is the subject of a caption, zoom 1.25 on it |
| In-page zoom pins at the window edge and shows the app's own edge, which could read as a canvas leak | low | the v2 origin rule keeps the page inside the viewport (unit-tested); the window hairline hides any 1 px seam |
| `zoompan` settle introduces a sub-pixel shimmer on hairlines | medium | the settle is optional; if used, scale on the composite in 1/30 px steps is not possible, so prefer no settle (the dip is enough) |
| The social take's narrow layout differs from the one the selectors expect | medium | the beats mark the three things to confirm (facet column, headline wrap, row truncation); dry-run the take at 1000x1000 before the shot list is frozen |
| SDK option A slips | medium | the approvals spotlight excludes the risk row; the captions never mention a class |
| "Agent Black Box" in the app's top bar vs "Agent BlackBox" on the cards | certain until the rename | the title card at least makes the intended casing the first thing seen; the rename is in the plan |
| The owner's two-CPU machine stalls the screencast during a long test run | medium (QA report: 1.6-2.7 s gaps) | record under the heavy mutex; motion is time-based; the compositor's timing comes from `meta.json`, not from frame counts |

---

## 10. Handoff for the Sonnet builder (ordered, each step testable)

Work in the `phase-20-video` worktree on `scripts/record-demo.mjs`, `scripts/encode-demo.sh`, `scripts/video-qa.mjs`; take
the PoC files from `docs/design/launch/video-concepts/cinematic/` as reference (lift, do not import). No new dependency.

1. **Recorder flags.** Add `--captions=off --card=off` (default on, so the old pipeline keeps working): the overlay no longer
   draws the caption div or the closing card; cue timing is still scheduled and logged to `meta.json` exactly as now, and the
   zoom acts log `zoomIn`/`zoomOut` marks with level. Test: `--dry-run --captions=off`; `meta.json` has cues and zoom marks;
   no frame shows a caption (QA "band is clean" on the raw frames' lower third).
2. **Beat table v3.** Encode section 3.1 (title/end are not beats: the recorder starts at the Runs list and ends on the last
   security beat), 3.2 and 3.3; the inspector zoom at 1.25; the `goto` transits for beats 8-11 with an 0.8 s hold before the
   first caption; the light-scheme beat removed. Test: `--plan` prints 72.5 +- 0.5 s, 19 captions, rules hold; with
   `RELEASE_CLAIMS=1` 76.2 s.
3. **Plates.** `scripts/video-plates.mjs` from the PoC's `plates.mjs`: reads `spec.json` (move the PoC's spec to
   `scripts/video-spec.json`, one source of numbers), the Plex files from `apps/web/src/fonts`, the mark from `brand/`, the
   cues from a take's `meta.json`; writes `plates/` into the take directory; `end-claims.png` only with `RELEASE_CLAIMS=1`.
   Test: run on the dry-run take; `ls plates/`; open `title.png` and one `cap-*.png`; the fonts check passes.
4. **Compose step.** `scripts/encode-demo.sh <take>` gains `compose`: the filter graph of the PoC's `composite.sh`
   generalised (N captions from `meta.json`, N dim windows from the zoom marks, dips at beat boundaries that are navigations,
   title and end concat; `--cut=teaser` adds the seam fades instead of the cards; `--cut=social` reads the social spec block).
   The master is the composite. Test: compose the dry-run take; `ffprobe` 1920x1080 30/1 yuv420p; extract the frame at each
   cue midpoint and look.
5. **QA additions.** Section 9's checks in `video-qa.mjs` (band clean, window still, dim happens, red outside the window,
   cards, dips, cuts in gaps, fonts). Negative tests: shift one caption 0.3 s so it crosses a cut; plant a 1 px window offset
   in one frame; both must fail.
6. **Demo names.** Section 8.1 in `examples/python/scripted_agent.py` and `scripts/demo.sh`; update `DATA.demo`,
   `scripts/demo-e2e.sh`. Test: `demo-e2e.sh` passes twice (determinism); the Runs list shows twelve distinct names.
7. **Risk class, option A.** `guard_push()` derives R2/R3 from the git table; `git_push(force=False)`; unit test in
   `packages/sdk-python/tests` (plain push -> R2, `force=True` -> R3; the intent carries it); `review-change` pass (SDK public
   surface). Test: `make demo`, open Review: the waiting request shows "Risk R2".
8. **Real takes.** Main, teaser, social against a private demo stack (`scripts/demo-stack.sh up`, never the owner's stack),
   twice each, QA on both, `--compare`. Look at every QA frame. Attach `ffprobe` lines and the frames to the plan's evidence.
9. **Site wiring.** The hero box background `film.canvas` for the teaser (`site/`), the full cut with `controls` and the
   `<track>`, transcript under it; `check-site.sh` asserts the box colour.
10. **Owner review.** Show the owner `frame-runs-window.png`, the inspector frame and the end card from the real take before
    the encodes; the two decisions they may want to make are the flat canvas (vs a vignette) and the optional settle.

Not in scope: music, voice-over, `--live`, a vignette, window moves, any UI drawn by the film.

---

## Sources

- Tech product launch films, opening and closing patterns (149 videos, 2026): https://advids.co/blog/aha-moment-tech-product-videos
- Screen Studio framing (padding, inset, radius, shadow, backgrounds) and zoom/cursor conventions: https://screen.studio/guide/background ; https://daveswift.com/screen-studio/ ; https://screen.studio/guide/auto-zoom
- Cursor practice in launch films (size, entry, press scale, drift): https://tessl.io/registry/skills/github/heygen-com/hyperframes/oversized-cursor--oversized-cursor
- Silent-edit pacing and the muted test: https://www.dreamengine.com.au/tips-and-resources/learning-the-art-of-video-editing/ ; https://www.nemovideo.com/blog/ai-video-editing/how-to-edit-video-for-sound-off-viewing
- Lower-third safe areas at 1080p: https://www.techsmith.com/blog/edit-videos-lower-thirds/
- Launch-week practice (Resend, video reach): https://resend.com/blog/launch-week-behind-the-scenes
- Everything in the v2 brief's source list (Playwright screencast, Netflix/BBC reading speeds, VP9/AV1 settings, xfade, WCAG 1.2.1): `docs/design/launch/video-brief.md`
- Product: `docs/design/pass-2/01-direction.md`; ADR-166; `docs/design/launch/positioning-brief.md`; `docs/design/launch/landing-site-brief.md`; `docs/design/launch/video-qa-report.md`; `scripts/record-demo.mjs`; `scripts/record-demo-lib.mjs`; `apps/web/design-tokens/tokens.json`; `packages/sdk-python/src/blackbox/coding.py`; `apps/web/src/lib/policy.ts`; `apps/api/src/abb_api/approvals/intent.py`; `examples/coding-agent/policies/demo.yaml`; `scripts/demo.sh`
