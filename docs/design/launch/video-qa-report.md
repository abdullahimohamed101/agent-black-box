# Demo video QA report (Phase 20, G1), draft 2 (polish pass)

Status: written 2026-10-19 after the polish pass on draft 1 (the owner's review of the 85.6 s cut). Direction: `docs/design/launch/video-brief.md`
(section 0.1 lists what changed). Evidence words as in AGENTS.md: VERIFIED (a command was run here and its output observed), UNVERIFIED
(env), ASSUMED. Everything named under `video-out/` is gitignored; the final files are in
`/Users/abdullahimohamed/Projects/abb-worktrees/video-out-final/` and draft 1, kept for comparison, in its `draft-1/` folder.

## 1. What changed from draft 1, against the owner's list

| # | Owner's point | What was done | Evidence |
| --- | --- | --- | --- |
| 1 | Pacing: about 3 s of cursor travel before captions on beats 9-12; target 60-75 s | 85.6 s to **69.8 s**. A section change is a 0.57-0.69 s dip to the canvas (`hop`: the click happens under the dip, the cursor is placed for free); inside a section the brief's travel clamps are unchanged and captions start while the cursor is still moving. Later beats re-ordered (Waterfall, Approvals, Security, Analytics, Runs in light) so Review is entered once. The "spend after the first error" caption is dropped (the spotlight now lights headline and detail together). Caption text is unchanged bank text; the reading rule `max(2 s, chars / 15 + 0.6 s)` holds for all 19 cues. | QA: main cut 60-75 s, hops at most 1.1 s (4 hops, 0.57-0.69 s), reading rule. VERIFIED |
| 2 | Opening: not a dense table | The film opens on the failed run page in a 1.6x frame of its headline and the red first-error line; caption 1 is on at 0.16 s; the camera pulls back at 1.9 s. | QA: first caption within 0.5 s, first zoom state at 0 s; the first 90 frames reviewed. VERIFIED |
| 3 | Zoom crops text mid-word; caption on rows | The zoom level and window are chosen by `fitZoom` (pure, unit tested): the largest of 1.6 down to 1.3 at which no viewport edge runs through a line of text. The settled state is measured again in the recording (`meta.zoomStates`). Hook window 0,48-1200,723 at 1.6x; inspector window 720,48-1920,723 at 1.6x (draft 1: window at x 549, 1.4x, headline cut). The caption box is placed by `placeCaption`: from its usual place down to the safe line or up, to the first band that covers no text and none of the focus rects; Analytics uses a fixed band beside the chart. | QA checks "no zoomed camera state cuts text mid-glyph" and "no caption sits on the focus or on text at normal scale", with planted-violation proofs (section 4). VERIFIED |
| 4 | Dark to light is a one-frame flash | A 650 ms cross-fade (a screenshot of the dark page is laid over, the scheme changes under it, the screenshot fades away); the caption waits until the fade is over. | QA "no flash": largest frame-to-frame brightness step 31.1 of 255 in draft 2 (draft 1: 216.2 at 77.97 s, same tool). VERIFIED |
| 5 | `demo-e2e.sh` must still pass with the realistic data | Passes; one ask added (the failed support run, the opening shot, lasts 30 s or more: it lasted 57 s). | `DEMO-E2E PASSED`, `demo run 1 == demo run 2 (normalised)`, every ask ok. VERIFIED |
| 6 | Captions per the positioning brief; claims gating unchanged | Same bank numbers; `RELEASE_CLAIMS` off (`meta.releaseClaims: false`); no `--live`. | QA "closing caption is the pre-G2 variant". VERIFIED |
| 7 | Selectors, captions and the wordmark match the current UI | All selectors ran unchanged against the production build of this branch (0 warnings, 0 console errors); the Runs list in the light scene shows `X 3 · critical` and the security shield. The recorder now fails on the three-word "Agent Black Box" anywhere on screen, and the QA checks every beat's page text for "Agent BlackBox". | `meta.warnings: []`, QA wordmark check. VERIFIED |

## 2. How the final take was made

| Step | Command (repo root of the `phase-20-video` worktree) | Result |
| --- | --- | --- |
| Private stack | own Postgres container on :5461, `ABB_DATABASE_URL=<it> scripts/demo-stack.sh up` (the production build of `apps/web` was rebuilt first; the old build predated the rename) | API :8180, worker, web :3180, database `abb_demo_video`; `scripts/demo.sh` recorded 12 runs on 7 days. The owner's dev stack (:3000, :8000, :5433), its key and `.env` were not touched; `seed` was not called. VERIFIED |
| Main cut, take A | `node scripts/record-demo.mjs`, `scripts/encode-demo.sh <take>`, `node scripts/video-qa.mjs <take>` | 69.8 s, 727 frames, 26 of 26 checks. VERIFIED |
| Main cut, take B (delivered) | same, `video-qa.mjs <B> --compare <A>` | 69.8 s, 736 frames, **30 of 30** checks including the cross-take ones (beat lengths within 2 ms, a frame of every beat with SSIM at least 0.97). VERIFIED |
| Teaser | `--cut=teaser`, encode, QA | 17.5 s, 178 frames, 23 of 23 checks. VERIFIED |
| Social cut | `--cut=social --dry-run` (beats 1-3, 1080x1350) | 10.4 s, 22 of 22 checks (zoom 1.25x; at 1.35x the label "First error, step 6" was cut, so the social cut may now zoom down to 1.1x). Beats 4-5 of the social cut were not recorded. VERIFIED for beats 1-3 only |
| Unit tests | `node --test scripts/` | 16 passed (the 11 earlier plus clipped lines, camera fit, the draft-1 camera as a planted violation, caption placement, flash step). VERIFIED |
| Data determinism | `ABB_DATABASE_URL=<throwaway> scripts/demo-e2e.sh` | `DEMO-E2E PASSED`. VERIFIED |

The recorder and the demo ran with the heavy mutex held (the wait for it, behind another worktree's long run, was about 40 minutes).

## 3. Measured numbers (ffprobe, take B and the teaser)

| File | Codec | Size | Frame | Rate | Pixel format | Duration | Bitrate | Audio | Budget |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `demo-1080p.mp4` | H.264 High | 6,256,855 B (5.97 MiB) | 1920x1080 | 30/1 | yuv420p | 69.97 s | 0.72 Mbit/s | none | 12 MB, ok; faststart |
| `demo-1080p.webm` | VP9 | 5,710,739 B (5.45 MiB) | 1920x1080 | 30/1 | yuv420p | 69.97 s | 0.65 Mbit/s | none | 8 MB, ok |
| `demo-1080p-av1.mp4` | AV1 (SVT-AV1 preset 4, crf 36) | 3,079,222 B (2.94 MiB) | 1920x1080 | 30/1 | yuv420p | 69.97 s | 0.35 Mbit/s | none | 5 MB, ok; faststart |
| `poster.jpg` / `poster.png` | | 137,468 B / 291 KB | 1920x1080 | | | | | | 400 KB, ok (the red-line beat, beat 3) |
| `teaser-1080p.mp4` | H.264 High, crf 23 | 2,175,804 B (2.07 MiB) | 1920x1080 | 30/1 | yuv420p | 17.57 s | 0.99 Mbit/s | none | 2.5 MB, ok; faststart |
| `teaser-1080p.webm` | VP9 | 1,391,355 B (1.33 MiB) | 1920x1080 | 30/1 | yuv420p | 17.57 s | 0.63 Mbit/s | none | 1.5 MB, ok |
| `teaser-960.gif` | GIF, 12 fps | 3,567,157 B (3.40 MiB; 3.57 MB decimal) | 960x540 | 12/1 | | 17.58 s | | none | 4 MB, ok in both units (draft 1: 4.16 MB decimal) |
| social dry run `demo-1080p.mp4` | H.264 | 1,396,612 B | 1080x1350 | 30/1 | yuv420p | 10.57 s | | none | n/a |

Captured frames are 3840x2160 JPEG, lanczos-downscaled in the master. No AV1 teaser (it is not smaller than the WebM). For comparison, draft 1:
85.73 s, 7,648,395 B MP4, 21 captions; draft 2: 69.97 s, 6,256,855 B, 19 captions.

## 4. Checks and proofs added in this pass (`scripts/video-qa.mjs`, 30 checks on a main take)

| Check | How it measures | Planted-violation proof (VERIFIED) |
| --- | --- | --- |
| No zoomed camera state cuts text mid-glyph | the recorder lists every text line (a Range rect per line of every text node, camera included) at each settled zoom and logs the lines a viewport edge runs through; the QA fails on any | `--plant clip` (the draft-1 camera, window at x 549, cutting "Failed at step 6 after 17 s: lookup-ti"): FAIL, 25 of 26 checks. Unit test with the same camera: cut lines found; the fitted camera: none. |
| No caption on the story's focus, none on text at normal scale | at the middle and 0.12 s before the end of each cue the recorder measures the caption box against every text line and against the focus rects (spotlight, zoom target, what the cursor points at) | `--plant overlap` (3 lines and the focus): FAIL, 25 of 26 |
| No flash | mean brightness of every frame at 30 fps; largest frame-to-frame step under 40 of 255 | `--plant flash` (185): FAIL; `--luma` on draft 1: 216.2 at 77.97 s, exit 1; on draft 2: 31.1 at 64.40 s (the light page fading to the closing canvas), exit 0 |
| Main cut 60-75 s; hook within 0.5 s; first zoom at the start; each hop at most 1.1 s; zoom 1.3-1.6 | from `meta.json` | by construction (draft 1 was 85.6 s and had no hook zoom) |
| Wordmark | every beat's page text reads "Agent BlackBox", none "Agent Black Box" | the recorder exits 4 on the three-word form |
| Two takes | beat lengths, captions, scraped sentences; SSIM at 25/50/75 % of each beat, best frame at least 0.97 | draft 2 take B vs A: largest beat difference 2 ms. (The first version compared only the middle frame and failed on beat 1 by a few ms of a zoom-out: replaced by the best of three.) |

## 5. Frames reviewed (take B and the teaser; files under `video-out/main-2026-10-19T05-19-43-599Z/qa/`, not committed)

- **Every beat, all 12**, at the middle of the beat (`beat-01.png` to `beat-12.png`, viewed as montages `mont-01.png`, `mont-05.png`, `mont-09.png`) and the 19 cue frames
  (`cue-01.png` to `cue-19.png`; the zoomed ones and the Analytics and waterfall ones were viewed in full size).
- **The first 3 seconds frame by frame**: all 90 frames at 30 fps in three 30-frame sheets (`hook-sheet-0/1/2.png`): frame 1 already shows the headline and the red line
  zoomed; the caption fades in from about frame 7; the pull-back runs from about 1.9 to 2.5 s; the cursor reappears at about 2.8 s. No frame shows the Runs table.
- **Transition frames**: the hop into the Waterfall (`tr-hop1.png`, nine frames at 0.1 s: the dip is three flat canvas frames), the dark-to-light cross-fade
  (`tr-xfade.png`, twelve frames), the closing fade (`tr-close.png`), the zoom-in on the inspector (`tr-zoomin.png`); the teaser's first and last frames are checked by the QA (flat canvas at both ends).
- Against draft 1: the one-frame flash is gone; the headline and top bar are no longer cut (draft 1's "ectionTimeout." crop); the opening is the failed run, not a table;
  the cost list is no longer under a caption.

## 6. Issues found and fixed in this pass

| Issue | Found by | Fix |
| --- | --- | --- |
| The hook caption, placed while the camera was zoomed, covered a text line once the camera pulled back | QA run 1 (end-of-cue measure) | placement also clears the page's lines at normal scale while a zoom is active |
| The "Press e" caption covered 4 timeline rows: placed at 0.1 s, before the inspector had re-flowed the rows | QA run 1 | the caption starts at 0.4 s, after the inspector is open |
| The Analytics caption sat over a cost bar | frame review (cue 17) | a fixed band beside the daily-spend chart for that beat |
| The waterfall's hover tooltip covered the lane labels | frame review (cue 11) | no hover on the bar; the spotlight carries the message |
| The light-to-dark closing fade stepped 50 of 255 | QA flash check | 480 ms fade, card at 0.55 s |
| The cross-fade's grey mid-point sat under the caption | frame review (`tr-xfade.png`) | the caption starts after the fade |
| The social cut at 1.35x cut "First error, step 6" | QA clip check on the dry run | the social cut may zoom down to 1.1x (it took 1.25x) |
| The cross-take SSIM failed on beat 1 | QA | best of three frames per beat |

## 7. What a skeptical designer would still criticise

- **Captions still cover background rows when the camera is zoomed** (hook: 1 line; "Press e": 4 rows left of the inspector). The page is all text at 1.6x, so the box only keeps off the focus there; at normal scale it covers none. Graphics (bars, chart lines) are not measured, only text lines and focus rects.
- **The cross-fade passes through a flat grey** for about 0.3 s: a blend of inverted pictures has no contrast. No flash, but not pretty.
- **The cursor teleports under the dips.** Section changes hide the cursor travel; the viewer never sees the cursor go to the Runs link or the Review tab, so the click is implied, not shown. Inside a section it still travels.
- **The hook shows neither the top bar nor the wordmark** (the 1.6x window starts below it); the product name comes only from the caption at 0.16 s.
- **The zoom is checked at the settled state, not during the 700 ms ease**: mid-ease frames pass through windows that were not tested (they looked fine by eye).
- **The data is still visibly scripted**: the same "Answer support ticket T-104x" six times, the models `scripted-small` and `model-x`, the approval risk "Unclassified". The relative times ("4h ago", "7 h 5 min left") change between takes.
- **The cursor is synthetic** (eased and bowed, but captured at 30 fps without motion blur).
- **The closing fade from the light page is a 31/255 step**, inside the limit but the largest in the film.
- **The social cut is a dry run**: only beats 1-3 at 1080x1350.

## 8. Unverified, assumed, and not done

- **UNVERIFIED:** social-cut beats 4-5 at the narrow layout; the `RELEASE_CLAIMS=1` closing card (planned with `--plan`, not recorded); `make demo` against the owner's own dev stack (not run there on purpose).
- **ASSUMED:** playback of the AV1 MP4 and the WebM in Safari and Firefox (only Chrome decoded them; draft 1's teaser loop test used Chrome); how GitHub renders the 3.57 MB GIF; the landing page's `<video>` markup in `docs/design/launch/video-embed.md` (written, not exercised in `site/`).
- **VERIFIED but narrow:** determinism of the pictures is SSIM of three frames per beat, not a pixel diff of the whole video.
- Not done by design: music, voice-over, any `--live` footage (D17, KI-044).
