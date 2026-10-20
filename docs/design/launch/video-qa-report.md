# Launch film QA report (Phase 20, G1), draft 3 (the framed, cinematic cut)

Status: written 2026-10-19 after building the cinematic brief (`docs/design/launch/video-cinematic-brief.md`) on the Phase 20 video branch.
Evidence words as in AGENTS.md: VERIFIED (a command was run here and its output observed), UNVERIFIED (env), ASSUMED. Take directories under `video-out/`
are gitignored; the final files are in `video-out-final/` next to the repository's worktrees (outside git) (draft 1 in `draft-1/`, the polished draft 2 in `polish-2/`).

## 1. The owner's criticisms of draft 2, and what was done

| # | Criticism | Resolution | Evidence |
| --- | --- | --- | --- |
| 1 | Not cinematic (window on a flat canvas, cards, caption band, dim, dips, no dissolves over UI) | The recorder films only the app (2x, cursor, camera, spotlight in the page). Everything else is composed from `meta.json` times: `scripts/video-plates.mjs` (canvas with window frame and shadow, dim plate with the window cut out, caption bands, title and end cards, Plex files, numbers in `scripts/video-spec.json`) and `scripts/video-compose.mjs` (one ffmpeg graph). Window 1696x954 at (112, 36); band y 990-1080; title 1.4 s, end card 3.25 s; 250 ms dips through `#0d1015` only at the ends; no dissolve over UI; the canvas darkens 38 % while the camera is close. | frames viewed (section 5); QA: dips, dim, window-still, cards. VERIFIED |
| 2 | The hook has no wordmark | Frame 1 is the lockup (mark, "Agent BlackBox", "A flight recorder for AI agents."); it dips into the Runs list at 1.4 s; the first caption is at 2.6 s. | QA cards (lockup centred within 2 px; first caption within 3 s); first 90 frames viewed. VERIFIED |
| 3 | Captions cover rows | The caption band is outside the window, always: geometry, not placement. | QA "band is clean": text at all 18 midpoints, empty (luma < 40) in every gap, red only in the failure captions; planted `--plant band` fails. VERIFIED |
| 4 | Data looks scripted | Twelve runs named like a team's week ("Answer ticket: refund not received", "Add retry to webhook sender", ...; ticket ids moved into the lookup payload), see section 4. | `demo-e2e.sh`: `DEMO-E2E PASSED`, two demo runs equal after normalising; new ask "every run name is different and none carries a ticket id". VERIFIED |
| 5 | The approval's risk says "Unclassified" | SDK public-API addition (ADR-176): `guard_push(remote, branch, *, force=False)` derives the class from the shell classifier (plain push R2, `force=True` R3); `git_push(..., force=)`. The demo's real push waits as `github.push` and the panel reads **Risk R2** (frame viewed). | SDK tests (failing first, then 1274 passed), `demo_e2e_check approvals` ask "carries ... R2". VERIFIED |
| 6 | The social cut becomes a real cut | `--cut=social`: a separate 1000x1000 CSS px take at 2x, composited into 1080x1350 (window 1000x1000 at (40, 40), band 310 px, Plex 44/54), title card, beats 1-7, end card: 43.5 s, 3.41 MB. | QA 31 of 31. VERIFIED |
| 7 | Claims end card only with `RELEASE_CLAIMS=1`, separate folder | Plates write `end-claims.png` only with the flag; the default compose never names it; the claims build writes only to `<take>/release-claims/` with `README.txt` "DO NOT PUBLISH BEFORE G2" and a vtt with the two card lines. The default build's `demo.vtt` has 0 occurrences of "Open source". | QA on the default take (nothing below the tagline on the last frame) and `--claims` on the folder (30 of 30). VERIFIED |

Also: the app wordmark reads "Agent BlackBox" (QA reads the page text of every beat; the recorder exits 4 on "Agent Black Box").

## 2. How the final takes were made

| Step | Command (repo root of the `phase-20-video` worktree) | Result |
| --- | --- | --- |
| Private stack | production build of `apps/web`, own database `abb_video_final` on the disposable test Postgres container (:5434, not the owner's :5433), `scripts/demo-stack.sh up` (API :8180, web :3180) | `demo.sh` recorded 12 runs. The owner's stack (:3000/:8000/:5433), its dev key and `seed` were not touched. VERIFIED |
| Main, take A and B | `node scripts/record-demo.mjs --captions=off --card=off`, `scripts/encode-demo.sh <take>`, `node scripts/video-qa.mjs <B> --compare <A>` | 73.4 s (68.8 s of app); **34 of 34** on B including the cross-take checks (beat lengths within 3 ms, a frame of every beat SSIM >= 0.97). VERIFIED |
| Teaser | `--cut=teaser`, encode, QA (two takes) | 19.4 s; 28 of 28 on take A; take B (delivered) 31 of 32: the only failure is the cross-take frame count (88 against 172: take A's screencast was sparse during cursor travel on a loaded machine). VERIFIED |
| Social | `--cut=social` (two takes) | 43.5 s; **31 of 31** with `--compare`. VERIFIED |
| Claims variant | `RELEASE_CLAIMS=1 scripts/encode-demo.sh <main B>`, `node scripts/video-qa.mjs <B> --claims` | 74.1 s, `release-claims/`; **30 of 30**. VERIFIED |
| Unit tests | `node --test scripts/` | 17 passed (applyHolds, the hop cut, is new). VERIFIED |
| SDK | `packages/sdk-python`: ruff, mypy, `pytest` | 1274 passed (4 new tests failing first: `guard_push` sent `risk_class=None`). VERIFIED |
| Other suites | `examples/coding-agent/tests` 23 passed 1 skipped; `examples/python/tests` 14 passed; `integrations/conformance` 20 passed; `apps/api/tests/policies` 213 passed (the dry-run table maps `git.push` to R2 so the SDK and the dry run stay equal) | VERIFIED. The full `apps/api` suite and `scripts/quality.sh full` were not run in this pass. |
| Data determinism | `scripts/demo-e2e.sh` (its own database, :8170) | `DEMO-E2E PASSED`, `demo run 1 == demo run 2 (normalised)`. VERIFIED |

Two behaviours of the recorder worth knowing: a **hop** (section change) is cut out of the film (the frames of the transit are dropped and later frames move earlier, `applyHolds`, unit tested) so each section change is a hard cut in a caption gap with no cursor transit; and a take whose beats overran their table by more than 0.4 s (a stalled browser on a loaded machine) exits 5 and is recorded again (the retry wrapper was needed for none of the final takes; two social attempts and one teaser attempt earlier had 6 to 19 s stalls).

## 3. Measured numbers (ffprobe, final files)

| File | Codec | Size | Frame | Rate | Pixel format | Duration | Bitrate | Audio | Budget |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `demo-1080p.mp4` | H.264 High | 5,738,146 B (5.47 MiB) | 1920x1080 | 30/1 | yuv420p | 73.43 s | 0.63 Mbit/s | none | 12 MB, ok, faststart |
| `demo-1080p.webm` | VP9 | 4,604,151 B (4.39 MiB) | 1920x1080 | 30/1 | yuv420p | 73.43 s | 0.50 Mbit/s | none | 8 MB, ok |
| `demo-1080p-av1.mp4` | AV1 | 2,901,102 B (2.77 MiB) | 1920x1080 | 30/1 | yuv420p | 73.43 s | 0.32 Mbit/s | none | 5 MB, ok, faststart |
| `poster.jpg` / `poster.png` | | 126 KB / 284 KB | 1920x1080 | | | | | | 400 KB, ok |
| `teaser-1080p.mp4` | H.264 High, crf 23 | 1,948,697 B | 1920x1080 | 30/1 | yuv420p | 19.43 s | 0.80 Mbit/s | none | 2.5 MB, ok |
| `teaser-1080p.webm` | VP9 | 1,273,475 B | 1920x1080 | 30/1 | yuv420p | 19.43 s | 0.52 Mbit/s | none | 1.5 MB, ok (no AV1: not smaller than the WebM) |
| `teaser-960.gif` | GIF 12 fps | 3,419,683 B | 960x540 | 12/1 | | 19.41 s | | none | 4 MB, ok |
| `social-1080x1350.mp4` | H.264 High, crf 21 | 3,575,712 B | 1080x1350 | 30/1 | yuv420p | 43.47 s | 0.66 Mbit/s | none | 8 MB, ok |
| `release-claims/demo-1080p.mp4` | H.264 | 5,753,852 B | 1920x1080 | 30/1 | yuv420p | 74.13 s | 0.62 Mbit/s | none | DO NOT PUBLISH BEFORE G2 |

Main film: 18 captions, title 1.4 s, 68.8 s of app, end card 3.25 s (draft 2: 69.97 s, 19 captions). Four hard cuts, two camera zooms (1.6x on the headline and on the inspector's "What went wrong"), canvas 6 luma darker in each zoom hold.
The teaser is 19.4 s, not the brief's 18 s: the brief's caption 9 window (1.7 s) broke the reading rule (3.3 s), so the teaser keeps both inspector captions.

## 4. Data (what the Runs list now shows)

Twelve runs, newest first: Push session hotfix (waiting), Ship feature: multi-agent, Answer ticket: password reset not sent, **Answer ticket: invoice shows wrong VAT** (the hero, failed at step 6), Clean up old releases, Answer ticket: export stuck at 90 %, Answer ticket: card declined twice (failed), Fix session refresh bug (failed), Answer ticket: login loop on iOS, Answer ticket: duplicate order emails, Answer ticket: refund not received, Add retry to webhook sender. Demo-only edits: `examples/python/scripted_agent.py` (ticket `subject`), `scripts/demo.sh`, `scripts/demo_e2e_check.py`, `scripts/record-demo.mjs` (`DATA.demo.failedRun`) and `apps/web/e2e/release-shots.spec.ts` (names only).

## 5. Frames reviewed (take B, teaser B, social B; files under `video-out/<take>/qa/` and the scratchpad, not committed)

- **One frame per beat and per caption**: the 18 caption midpoints of main A as three 3x2 sheets and the 11 beat frames (QA writes `beat-NN.png`); the Risk R2 panel, the waterfall without a tooltip, the Analytics page with a seven-day daily-spend chart, the Security findings with their Rule lines.
- **The first 3 seconds frame by frame** (three 30-frame sheets): the lockup holds, the tagline stays legible, the title dips to the canvas colour in about 7 frames, the Runs list fades in on the framed window with its shadow, the cursor rests at the parking spot; no frame has the wordmark over table rows.
- **Transitions**: the headline zoom-in (12 frames at 0.1 s: the canvas darkens as the camera closes), the cut into the waterfall (8 frames, a hard cut between two frames, no dip), the end card (12 frames at 0.3 s: the lockup fades in from the canvas and holds, the film ends on it), the zoom-out.
- **Social** (six frames: title, headline zoom, red line with the failure word in red in the band, inspector, model/retry row, findings, end card): the band keeps one line of 44 px text centred in the 310 px band.

## 6. Issues found and fixed in this pass

| Issue | Found by | Fix |
| --- | --- | --- |
| Cue times were wrong after a hop (a sleep computed before the hop counted its transit) | QA run 1 (negative caption gaps, a caption across a cut) | the film clock is frozen during a hop and cues poll it |
| The inspector zoom at the brief's 1.25x cut the timeline text behind the dialog | QA (zoomed-text check) | fitted at 1.6x down to 1.3x (1.6x was clean) |
| A waterfall tooltip appeared when the cursor travelled to the critical-path bar | frame review | the cursor stays off the bars |
| The warm-up left the browser on the run page, so the film started on it | first dry run | the warm-up ends on the Runs list |
| The window's own hairline is 1 px into the band's top | QA gap check | the band check starts 6 px lower |
| The social dim patch overlapped the window | QA | patch on the right canvas margin |
| ffmpeg 9 has no `-filter_complex_script` | compose | `-/filter_complex <file>` |

Planted-violation proofs (each run on main take A; each must fail its own check and did, VERIFIED): `--plant band` (a caption plate in the band at gaps), `window` (the picture 2 px off), `dim` (zoom hold sampled with no zoom), `red` (a failure-red box on the canvas), `cards` (lockup 12 px off centre), `dips` (a frame 0.12 s after the dip boundary), `cuts` (a caption laid over the band at a cut), `fonts` (plates rendered with a missing font directory: refused), and the earlier `clip` (a camera that cuts text).

## 7. What a skeptical designer would still criticise

- **The first-frame brand moment is short** (1.4 s) and then the product appears small: at 88 % of the frame the 12-13 px rows are readable only because the zooms carry the headline and the inspector; a phone viewer sees the table as texture.
- **The zoomed headline frame has no top bar**, so the name is the cards plus the app's top bar in the unzoomed frames only.
- **The flat canvas is very quiet** (the shadow is the only depth); the vignette alternative was rejected by the brief (it banded).
- **The cursor is synthetic** (eased and bowed, captured at 30 fps); the cursor is hidden through cuts, so a click that causes a section change is implied.
- **Captions come from one bank and are short**; there is no sound, so the pace is carried by caption length (2.1 to 3.7 s) and four cuts; the Analytics and Security beats are the least dynamic (a spotlight on a static page).
- **The social cut is the desktop layout at 1000 px**, not a phone layout: the app does not reflow to one column at this width, so the beats are the brief's list with the same pages, not a different composition. The inspector opens beside the timeline, not over it.
- **The model names `scripted-agent-1` and `model-x` remain** on the coding and multi-agent runs (the brief wants `scripted-small`); renaming would touch the compare fixtures and the builtin price entry for `model-x`.
- **Take-to-take**: the teaser's first take recorded 88 frames against 172 (a loaded machine), so the cross-take frame-count check fails for the teaser pair; the delivered take is the better-sampled one.
- **Relative times** in the app ("3h ago", "7 h 52 min left") differ between takes.

## 8. Unverified, assumed, and not done

- **UNVERIFIED:** the screenshots set (`docs/screenshots/release`, six views) was not regenerated (owner's instruction) and still shows the previous names and "Unclassified"; Safari/Firefox playback of the AV1 and WebM files; the landing page markup in `docs/design/launch/video-embed.md` (written, not exercised in `site/`, which was not edited); `scripts/quality.sh full` and the full `apps/api` suite.
- **ASSUMED:** how GitHub renders the 3.4 MB GIF; that the site's hero box colour rule (section "Film v3" of `video-embed.md`) is applied before the teaser ships.
- Not done by design: music, voice-over, any `--live` footage, the optional window settle, a vignette.
