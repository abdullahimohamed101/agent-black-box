# Demo video QA report (Phase 20, G1)

Status: written 2026-10-19 for the first real recording made with recorder v2 (`scripts/record-demo.mjs`, `scripts/encode-demo.sh`,
`scripts/video-qa.mjs`) against a demo-seeded stack. Direction: `docs/design/launch/video-brief.md`. Evidence words as in AGENTS.md:
VERIFIED (a command was run here and its output observed), UNVERIFIED (env), ASSUMED.

Everything below the "Frames reviewed" heading names files under `video-out/` (gitignored, not committed). The final files were copied
to `/Users/abdullahimohamed/Projects/abb-worktrees/video-out-final/` so the owner can watch them.

## 1. How the take was made

| Step | Command (repo root of the `phase-20-video` worktree) | Result |
| --- | --- | --- |
| Private stack | `ABB_DATABASE_URL=<own throwaway Postgres on :5461> scripts/demo-stack.sh up` | API :8180, worker, **production build** of `apps/web` on :3180, database `abb_demo_video`; `scripts/demo.sh` ran against it (12 runs, one agent waiting for approval). The user's dev stack (:3000, :8000, :5433) and `.env` were not touched or read; `seed` was never called. VERIFIED |
| Main cut, take A | `node scripts/record-demo.mjs` then `scripts/encode-demo.sh <take>` then `node scripts/video-qa.mjs <take>` | 85.6 s, 957 frames, 17 of 17 checks. VERIFIED (`video-out/main-2026-10-19T02-54-15-954Z`) |
| Main cut, take B (the delivered one) | same, `video-qa.mjs <B> --compare <A>` | 85.6 s, 946 frames, **21 of 21** checks including the cross-take ones. VERIFIED (`video-out/main-2026-10-19T02-57-11-313Z`) |
| Teaser | `node scripts/record-demo.mjs --cut=teaser`, encode, QA | 18.97 s, 233 frames, **18 of 18** checks. VERIFIED (`video-out/teaser-2026-10-19T02-47-52-993Z`) |
| Unit tests | `node --test scripts/` | 11 passed (easing, travel, camera origin, caption rule, subtitles, frame list). VERIFIED |
| Data determinism | `ABB_DATABASE_URL=<throwaway> scripts/demo-e2e.sh` | `demo run 1 == demo run 2 (normalised)`, all 15 video asks hold, `DEMO-E2E PASSED`. VERIFIED |

The recorder and the demo ran with the heavy mutex held; the private stack and its throwaway Postgres container were removed afterwards.

## 2. Measured numbers (ffprobe, take B and the teaser)

| File | Codec | Size | Frame | Rate | Pixel format | Duration | Bitrate | Audio | Budget |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `demo-1080p.mp4` | H.264 High | 7,648,395 B (7.29 MiB) | 1920x1080 | 30/1 | yuv420p (tv range) | 85.73 s | 0.71 Mbit/s | none | 12 MB, ok; `ftyp`, `moov`, `mdat` in that order (faststart) |
| `demo-1080p.webm` | VP9 profile 0 | 6,099,900 B (5.82 MiB) | 1920x1080 | 30/1 | yuv420p | 85.73 s | 0.57 Mbit/s | none | 8 MB, ok |
| `demo-1080p-av1.mp4` | AV1 Main (SVT-AV1 preset 4, crf 36) | 3,904,216 B (3.72 MiB) | 1920x1080 | 30/1 | yuv420p | 85.73 s | 0.36 Mbit/s | none | 5 MB, ok; faststart |
| `poster.jpg` / `poster.png` | | 137,843 B / 312,576 B | 1920x1080 | | | | | | 400 KB, ok |
| `teaser-1080p.mp4` | H.264 High, crf 23 | 2,568,265 B (2.45 MiB) | 1920x1080 | 30/1 | yuv420p | 18.97 s | 1.08 Mbit/s | none | 2.5 MB, ok by 0.05 MiB; faststart |
| `teaser-1080p.webm` | VP9, 700k crf 36 | 1,497,221 B (1.43 MiB) | 1920x1080 | 30/1 | yuv420p | 18.97 s | 0.63 Mbit/s | none | 1.5 MB, ok by 0.07 MiB |
| `teaser-960.gif` | GIF, 12 fps, 128 colours | 4,157,800 B (3.97 MiB) | 960x540 | 12/1 | | 19.0 s | | none | 4 MB: ok in MiB, **4.16 MB in decimal**: borderline (drop to 10 fps or 800 px to get clear) |
| `teaser-poster.jpg` | | 138,566 B | 1920x1080 | | | | | | 400 KB, ok |

No AV1 teaser was shipped: at crf 36 it was not smaller than the WebM (the encoder deletes it and says so). Captured frames are
3840x2160 JPEG (VERIFIED: `ffprobe frames/f00000.jpg` prints 3840,2160); the 2x capture is lanczos-downscaled in the master.
The main cut is 85.6 s (brief: 60-90 s; its table said 74 s but also required the reading rule `max(2 s, characters / 15 + 0.6 s)`,
and the 21 captions at that rule do not fit in 74 s). With `RELEASE_CLAIMS=1` the closing card lengthens by 3.3 s to a planned
88.9 s (plan only, not recorded).

## 3. The 14-item checklist of the brief (section 7.1)

| # | Item | Result |
| --- | --- | --- |
| 1 | ffprobe of each deliverable: size, 30/1, yuv420p, no audio, faststart, budget | VERIFIED by `encode-demo.sh` and `video-qa.mjs` (section 2). The first encodes came out `yuvj420p` (full range, from the JPEG frames); fixed with `in_range=pc:out_range=tv` in the scale filter. |
| 2 | A frame per caption midpoint, looked at: text complete, box in the safe area, no overlap with app text, no caption across a cut | The recorder measures the caption box in the page at each cue midpoint; the QA reads the pixels inside that rectangle (dark fill at least 55 %, light text pixels 1.5-40 %, opacity 1) and the safe areas (box bottom 996 px of 1080, inside the 5 % band; width under 90 %). 21 of 21 cues pass. No caption is on at the end of any beat. I also looked at the 14 beat frames and the cue frames by eye. A caption does sit over live table rows when the page is zoomed (beats 3 and 6): by design (the box is the same on every beat), see section 6. |
| 3 | A frame per zoom at full level: crisp edges, no canvas edge, no clipped top bar | Crisp: `qa/crop-compare.png` puts a 420x70 crop of the 1.0x headline above the 1.6x one at 3x magnification; no ringing or softening at 1.6x. No canvas edge: the origin formula is unit-tested for 70 focus points at 1.4x and 1.6x (`zoomWindow` stays inside the viewport). The top bar is clipped at the left on the 1.4x inspector zoom (beat 6): that is the camera, not a defect, but see section 6. |
| 4 | Red pixels: only the fault line, the list dash, the failure words and the caption's fault words | `video-qa.mjs` samples every 0.5 s (171 samples), excludes the caption band and any row that is red across more than half its width (the fault line is 0.29 % of the frame at 1.6x on its own), and fails over 0.3 %. Worst remaining share 0.093 % (at 28.0 s). Negative test: a planted red box (`drawbox`) made the check fail (8.385 %). |
| 5 | No secret, key, token, `.env` content, real email, real hostname or provider/model name | The recorder scans the visible text of every beat (`pagetext/beat-N.txt`) for `sk-`, `AKIA`, `@gmail`, `anthropic`, `openai`, `claude`, `gpt`, `--live`, private-key headers and any email other than `owner@local.test`: 0 hits in all takes. By eye: the only identity in frame is `owner@local.test` in the top bar (the person menu is never opened); ids in the inspector are throwaway ULIDs; models read `scripted-small`, `scripted-agent-1`, `model-x`. |
| 6 | The cursor never covers a caption's subject; parked during zooms | By eye on every beat frame. Fixed in the last takes: on the Review beat the arrow covered the "origin main" line under the link (now beside the link at 95 % of its width). The cursor is hidden during the 1.6x and 1.4x zooms. |
| 7 | Caption cues in `demo.vtt` match the burned-in text one for one; the transcript lists every beat | VERIFIED by `video-qa.mjs` (21 cues, times within 2 ms of what the recorder logged; transcript lists beats 1-14). |
| 8 | Two takes diff-equal in `meta.json` except frame counts within 5 % and timestamps | VERIFIED: same beats, captions and scraped page sentences; frames 946 and 957 (1.2 %); every beat lasts the same within 2 ms; SSIM of the middle frame of every beat at least 0.97. |
| 9 | Beat durations within +-150 ms of the table | VERIFIED (all 14, largest difference 2 ms). The first take overran beat 2 by 0.6 s because the table underestimated the page load; the table now says 4.6 s. |
| 10 | The light-scheme beat shows the same run and the caption box is still dark | VERIFIED by eye (`qa/beat-13.png`). The app flips in one frame when the system scheme changes (luma 37 to 222 at 78.07 s). |
| 11 | The closing caption is the pre-G2 variant unless G2 is VERIFIED | VERIFIED: `meta.json` has `releaseClaims: false`; no cue mentions open source, self-hosted, Apache or github.com. Planting caption 26 in a copy of the table without the flag made `--plan` refuse (negative test). With `RELEASE_CLAIMS=1`, `--plan` passes and lays out captions 26 and 28 (plan only, not recorded). |
| 12 | Nothing on screen says "live", "real model", a provider, "production", "SaaS", "enterprise" | Captions are checked by regular expression (refused at plan time). Page text scan as in item 5. The scope control reads "Local development": it contains "development", none of the listed words. |
| 13 | Teaser loops without a visible jump: play it three times in a browser | VERIFIED in Chrome for the MP4 and the WebM: `<video loop muted>` played at 4x for three loops; the luma at the wrap stays 18-26 (the canvas colour), no bright frame. The first and last frames are the flat canvas `#101419` (QA check). |
| 14 | GIF under 4 MB; poster under 400 KB | Poster 135 KB: ok. GIF 3.97 MiB: ok in MiB, borderline in decimal MB (section 2). |

## 4. Frames reviewed (all under `video-out/`, not committed)

Take B, one frame per beat at the middle of its first caption (`video-out/main-2026-10-19T02-57-11-313Z/qa/`):
`beat-01.png` (Runs list, caption 1), `beat-02.png` (cursor on the failed row), `beat-03.png` (1.6x headline), `beat-04.png`
(spotlight on the detail line), `beat-05.png` (spotlight on the fault row), `beat-06.png` (1.4x inspector), `beat-07.png` (model-call
row), `beat-08.png` (Findings tab), `beat-09.png` (waterfall), `beat-10.png` (Review), `beat-11.png` (Analytics), `beat-12.png`
(Security findings), `beat-13.png` (light scheme), `beat-14.png` (closing card); 21 cue frames `cue-01.png` to `cue-21.png`;
montages `montage-01.png`, `montage-05.png`, `montage-09.png`, `montage-13.png`; `crop-compare.png`; the SSIM pairs `det-a-N.png` and
`det-b-N.png`. Earlier takes were reviewed the same way and then deleted or moved to `video-out/superseded/` (metadata only).
Transition frames looked at (takes 2 and 3, `ffmpeg -ss` on the MP4): the key chip on `e` (beat 6 + 0.55 s), the click ripple and the
fault line drawing on arrival (beat 2 + 2.45 to 3.1 s, four frames), the zoom-in mid-ease with the caption fading (beat 3 + 1.05 s),
the critical-path spotlight with the app's own hover tooltip (beat 9 + 7.5 and 8.3 s), the evidence jump (beat 8 + 4.7 s), the
approval panel (beat 10 + 5.5 s), the closing fade, the teaser's first, middle and last frames. A luma scan of the whole MP4 at 15
samples per second found no white flash or blank frame outside the light-scheme beat and the closing fade.

## 5. Issues found and fixed during the takes

| Issue | Found by | Fix |
| --- | --- | --- |
| Run headline read "lookup-ticket, error." and the inspector said "failure without a message" | frame review of take 1 | the scripted agent sets `tool.error_type` (the attribute the app reads); now "Failed at step 6 after 2.3 s: lookup-ticket, ConnectionTimeout." |
| The run page showed "Loading run..." for about 0.4 s after the click | frame review (ripple frame) | `warmUp()` visits every page the cut opens once off camera, by clicking through the app, so the recording shows pages, not spinners |
| A blue focus ring ran across the top and bottom of the timeline from beat 7 on | frame review | the listbox's `:focus-visible` outline is hidden in the recording (row selection stays) |
| Four white dots under the descenders of the headline on beat 4 | 3x crop | the spotlight padding on the detail line is 3 px, not 8 |
| H.264 was `yuvj420p` | `encode-demo.sh` ffprobe line | scale filter converts full range to tv range |
| My first QA caption detector failed on pages with light text behind the caption (Analytics, light scheme) and the red check failed at 1.6x because the fault line alone is 0.29 % of the frame | first QA run | the recorder measures the caption rectangle in the page and the QA reads that rectangle; the red check excludes rows that are red across most of their width |
| Beat 2 overran its table by 0.6 s, so every later beat slipped | `meta.json` | table length raised to 4.6 s; all beats within 2 ms now |
| Social cut and release-claims closing: the URL caption broke the reading rule (2.9 s where 3.67 s is needed) | `--plan` with `RELEASE_CLAIMS=1` | its own duration of 3.7 s |

## 6. What a skeptical designer would still criticise

- **The inspector zoom (beat 6) crops the page mid-word.** At 1.4x on the right-hand drawer the window is pinned to the right edge, so the left 30 % of the timeline and the top bar are cut ("ectionTimeout." in the headline). A spotlight on the drawer instead of a zoom, or a 1.25x zoom, would avoid it; the brief specified 1.4x.
- **The captions sit on live content.** A dark box over the lower third covers table rows on the zoomed beats and the cost list on the Analytics beat. It is one consistent style, as the brief asks, but it is not placed per beat.
- **Pace.** 85.6 s with 21 captions, each held 2.5-3.7 s because of the reading rule. Beats 9-12 each spend about 3 s travelling between pages by cursor before the first caption. A tighter cut would drop the Analytics or the Security beat.
- **The cursor is synthetic.** Eased and bowed, sampled at 60 Hz, but captured at 30 fps without motion blur, so fast travels step slightly. The click ripple and the 0.88 press scale are subtle.
- **The data is visibly scripted.** Runs last 1.4 s, cost $0.004, and "Answer support ticket T-1042" repeats with different numbers. The Analytics "Daily spend" chart is a single bar because every run happened today. The Review panel shows risk "Unclassified" (the SDK's coding guard does not send a risk class for `git push`), where the brief asked for R3.
- **The app wordmark says "Agent Black Box"** (three words, top left, on every frame) while every caption and the closing card say "Agent BlackBox" (D15). The repository rename is a scheduled later step; until then the video contradicts itself in the first second.
- **Hard cut to light.** The scheme switch is one frame from dark to light (luma 37 to 222), a bright flash. It is what the app does, and it is the point of the beat, but it is abrupt.
- **The opening is a dense table, not a hero.** Twelve rows, a search box and a facet column are on screen for 3.5 s while the first caption is read. The one red thing is four small words.
- **Time-dependent text.** "7 h 36 min left", "23m ago" and the 7-day sentence change between takes; no caption quotes them, and the QA compares picture similarity, not those strings.
- **The README GIF is at the size limit** (3.97 MiB) and the teaser MP4 and WebM pass their budgets by 0.05 and 0.07 MiB.

## 7. Unverified, assumed, and not done

- **UNVERIFIED (not run):** the social cut beyond beats 1-3. `--cut=social --dry-run` recorded 1080x1350 frames at 2160x2700 with the 40/50 px captions and the cursor and caption working (frame viewed), then the take was deleted. Beats 4-8 at the narrow layout were not recorded, and the app's narrow layout may need different selectors.
- **UNVERIFIED:** the `RELEASE_CLAIMS=1` closing card was planned (`--plan`), not recorded.
- **UNVERIFIED:** `make demo` against the owner's own dev stack. It was not run there on purpose. If that API does not accept development sessions, `scripts/demo.sh` says so and skips only the waiting agent (the other eleven runs are recorded); the approval beat then has nothing to show.
- **ASSUMED:** playback of the AV1 MP4 and the WebM in Safari and Firefox (only Chrome decoded them here; the loop test used Chrome); how GitHub renders the 4.16 MB GIF; the landing page's `<video>` markup in `docs/design/launch/video-embed.md` (written, not exercised in `site/`).
- **VERIFIED but narrow:** determinism of the pictures is SSIM of one frame per beat (at least 0.97), not a pixel diff of the whole video; relative ages in the app change every second.
- Not done by design: music, voice-over, any `--live` footage (D17, KI-044).
