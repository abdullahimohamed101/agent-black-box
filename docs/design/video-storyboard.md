# Demo video storyboard: Agent BlackBox (redesigned app)

Length: 75 seconds (range 60-90). Format: 1920×1080 screen capture of the fixtures build after Phase 21, dark theme, no voice-over (captions carry the story), optional quiet music [NEEDS INPUT]. Audience: the owner's portfolio; people who have never seen the product.

Rules for the captions: plain language, no hype, nothing shown as built that is not built. The Compare tab, the Phase 16 search chips, policies and alerts appear only with the on-screen "Planned" tag, and only in scene 7.

## Scenes

| # | Time | On screen | Caption (lower third) | Notes |
| --- | --- | --- | --- | --- |
| 1 | 0:00-0:06 | Runs list, dark theme, rail visible. Cursor rests. The REC indicator shows "2 running". | Agent BlackBox records what your AI agents actually did. | Static for 2 s before any motion; no entrance animation (the app has none). |
| 2 | 0:06-0:14 | Cursor moves down the list; the Failed row "Migrate billing schema" is selected (row tint, Findings "High" pill visible). Click. | A failed run. Let's find out why. | Row selection is instant; keep the cursor path short. |
| 3 | 0:14-0:26 | Run page opens on the Story tab. The header: status Failed, readout strip with Retries 2 (amber) and Errors 4 (red). The headline sentence is read by the viewer. Cursor hovers "Show it". | One sentence says what happened. The readouts say how bad. | Hold 4 s on the header before moving. Zoom (post-production crop) on the readout strip is allowed if ffmpeg is available; otherwise no zoom. |
| 4 | 0:26-0:40 | Click the High finding "Repeated failure, same arguments". The timeline scrolls to evt_0011, the row gets the landing ring, the inspector opens on the right with the ConnectionTimeout callout and "Retry of evt_0008". | Findings link to the exact events they were built from. | This is the finding-to-evidence jump; it must be the real `jumpTo`, not a cut. |
| 5 | 0:40-0:50 | Press Escape (inspector closes, focus returns), click the Timeline tab, press `j` a few times, press `e`. The first-error row is outlined and the inspector opens again. | Keyboard first: j, k, Enter, e for the first error. | Show the keys as small on-screen captions (`j` `j` `e`) in the lower right. |
| 6 | 0:50-0:62 | Back to Runs, open "Ship feature: multi-agent", click the Waterfall tab. Five agent lanes; the hatched error bar; the critical path edges; the tester's clock-skew outline. Click the error bar: inspector shows HttpError 503. | Five agents in one picture. The thick edge is the critical path; the dashed outline is a clock that disagreed. | Use the fixtures multi-agent run; hold 3 s with no cursor movement so viewers read the bars. |
| 7 | 0:62-0:70 | Runs list again. Cursor hovers the search slot and the rail: the "Planned" tags on Experiments, Policies, Alerts and the search chips are visible. | Coming next: search with filter chips, run comparison, policies and alerts. (Planned) | The word "Planned" must be on screen; do not type into the search slot if Phase 16 has not landed. |
| 8 | 0:70-0:75 | Light theme switch (system preference) on the waterfall page, then cut to the wordmark on the dark canvas. | Open source. Self-hosted. Your agents' flight recorder. | [NEEDS INPUT: licence and hosting claims must match the README and the private-hosting guide before this caption is used.] |

Total: 75 s. If it runs long, cut scene 8 to 3 s and scene 5 to 8 s.

## Recording method (Playwright against the fixtures build)

Playwright records WebM video per browser context; no screen-recording software is needed.

1. Build and serve the fixtures app on port 3100 exactly as `apps/web/playwright.config.ts` does (`ABB_WEB_DATA_SOURCE=fixtures ABB_WEB_ALLOW_FIXTURES=1`). Keep it in the foreground in one terminal; stop it with Ctrl-C when done (never by killing by name).
2. A script in this folder's style (`record.mjs`, to be written when Phase 21 lands) does, in one foreground run with a bounded timeout:
   - `chromium.launch({ channel: "chrome" })`, `newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1, colorScheme: "dark", recordVideo: { dir, size: { width: 1920, height: 1080 } } })`.
   - For each scene: `page.goto`, real clicks and key presses with `page.mouse.move(x, y, { steps: 30 })` so the cursor path is visible, `page.waitForTimeout` for the holds, and `page.keyboard.press("j")`, `"e"`, `"Escape"` for scene 5.
   - Captions are injected with `page.evaluate` as a fixed `div` at the bottom (same Plex Sans, 28 px, dark pill) so they are part of the capture and no editor is needed. The "Planned" tag in scene 7 is the app's own.
   - Scene 8's light switch is `context.emulateMedia({ colorScheme: "light" })` [NEEDS INPUT: if the app ships a toggle instead, click it].
   - `await context.close()` writes the `.webm`; `await page.video().path()` gives the file.
3. The result is one WebM at 1920×1080, 25 fps, 75 s. Playwright's recorder cannot draw the cursor, so the cursor is simulated: a small round `div` moved by the same script (`pointermove` listener) so viewers see where the click lands.

### What is needed and what requires approval

- No extra software is needed to produce the WebM.
- **ffmpeg is not installed on this machine** (`which ffmpeg` returns nothing). It is needed only for: converting WebM to MP4 (H.264) for LinkedIn and most portfolio sites, trimming, concatenating scenes recorded as separate contexts, and the optional zoom in scene 3. Installing it (`brew install ffmpeg`) is system software and requires the owner's approval per AGENTS.md "When to Ask". Alternatives without ffmpeg: upload the WebM directly to YouTube or Vimeo (both accept WebM), or convert with a browser-based tool the owner trusts.
- Music, if any, also needs ffmpeg to mux, and a licence [NEEDS INPUT].

### Checklist before recording

- Phase 21 merged; fixtures build green; `prefers-reduced-motion` off in the recording context (so the inspector's 180 ms slide shows), captions readable at 1080p.
- Every caption cross-checked against `docs/IMPLEMENTATION_PLAN.md` for what is built; anything planned carries the tag.
- No real data: the fixtures use `coding-agent`, `planner`, `researcher`, `coder`, `tester`, `reviewer`, "Ship feature: multi-agent", "Migrate billing schema"; the owner's email in the rail footer is the fixture `owner@local.test`, never a real address.
