# Demo video storyboard: Agent BlackBox (design pass 2 app)

Status: rewritten 2026-10-18 for the redesigned app (ADR-166: one top bar, Runs as home, the fault line). Replaces the Phase 21
storyboard, which described the left rail, stat strips and readout bars that no longer exist.

Length: 78 seconds (range 60-90). Format: 1920x1080, 25 fps, dark theme with a closing light-theme scene. **No voice-over and no
music (D17).** Captions sit in the lower third and carry the story. Casing in captions is "Agent BlackBox" (D15). Audience:
people who have never seen the product.

Rules for the captions: plain language, no hype, nothing shown that is not built. Every caption is checked against the claims
table in `docs/plans/active/phase-20-release-polish.md` ("Claims to verify"). Specifically:

- "Open source" and "Self-hosted" are **blocked** until G2 (the repository is public) and AC1/AC12 pass. Scene 10 therefore carries
  a neutral caption now; the blocked line is listed in the scene table and must not be used before G2.
- Nothing says that `--live` (real model mode) works: the project has never run it (KI-044). The video shows only scripted runs.
- No "production ready", "hosted", "SaaS", "enterprise", no numbers of tests, phases or checks.
- Policy enforcement is cooperative (it applies to agents that ask through the SDK guard); the Approvals caption says only what
  the page says.

## Data

The recording runs against the stack that `make demo` seeds (plan step 10: `scripts/demo.sh`). Step 10 is **not merged into this
branch yet**, so the names below come from the example sources and are marked **[CONFIRM AFTER STEP 10]**; the recorder reads
every name from one table (`DATA` in `scripts/record-demo.mjs`) so confirming them is a one-place edit.

| Role in the video | Demo-seeded name (from the examples) | Fixtures build name (dry runs only) |
| --- | --- | --- |
| Failed run | "Fix OAuth session expiry" by `coding-agent`, the `--variant wrong-fix` run (ends `run.failed`) [CONFIRM AFTER STEP 10: does `make demo` record the wrong-fix variant?] | "Migrate billing schema" |
| Multi-agent run | "Ship feature: multi-agent" by `planner`, five lanes, `--skew-ms` run [CONFIRM AFTER STEP 10: skew value used] | "Ship feature: multi-agent" |
| Run with a retry that succeeds | "Answer support ticket T-1042" by `support-agent` (tool timeout, one retry) | none used |
| Approvals | the coding example's `--policy-demo` approval (`git push` to `main`) [CONFIRM AFTER STEP 10: only if `make demo` runs the policy scenario; otherwise scene 8 is cut] | "Deploy to staging" pending approvals |

Ids and times differ on every `make demo`; the recorder never types an id, it clicks the run by its visible name.

## Scenes

| # | Time | On screen | Caption (lower third) | Notes |
| --- | --- | --- | --- | --- |
| 1 | 0:00-0:07 | Runs, dark, top bar with five items (Runs, Review, Analytics, Experiments, Settings) and the scope control. Under the h1 the one-line sentence "Last 7 days: N runs, ...". Cursor rests. | Agent BlackBox records what your AI agents actually did. | Hold still 2 s. The home page is Runs; there is no Overview. |
| 2 | 0:07-0:14 | Cursor moves down the table to the failed run; its second line "Failed at step N ..." with the red dash; click it. | One run failed. Open it to see why. | The red dash and the word Failed are the only red. |
| 3 | 0:14-0:26 | Run page: title line with Failed, the one-sentence headline ("Failed at step N after T: ..."), the figures line, the fault line above the first error row (label "First error, step N"). Cursor stays still. | One sentence says what happened. One red line marks where it went wrong. | Hold 5 s with no motion so the sentence can be read. |
| 4 | 0:26-0:36 | Click the Findings tab; click Show evidence on the first finding; the timeline opens with the cited event selected and the inspector. | Findings point to the exact events they came from. | Real `Show evidence` jump, not a cut. Skipped by a bounded wait if the run has no finding [CONFIRM AFTER STEP 10: the wrong-fix run's findings]. |
| 5 | 0:36-0:46 | Close the inspector (Escape), Timeline tab, press `j` twice, then `e`: the first error row is selected and the inspector shows the error. | Keyboard first: j and k move, e jumps to the first error. | No key caps are drawn; the app's foot hint says the same. |
| 6 | 0:46-0:52 | **Live run health "as of N s ago" (KI-097).** A running run's health label reads "as of 12 s ago" instead of "no data". | While a run is live, health shows the last analysis and its age. | **Disabled in the scene table until plan steps 3-6 are merged and the label's location is known** (the health dimensions are no longer a panel on the run page, ADR-166). It needs a run that is still running while recording; `make demo` must leave one or the recorder must start one. [CONFIRM AFTER STEPS 3-6 AND 10] |
| 7 | 0:52-1:02 | Back to Runs, open "Ship feature: multi-agent", Waterfall tab: five lanes, the error bar, the critical path, the clock-skew outline if the run has one. Click the error bar: the inspector opens. | Five agents in one picture. The thick edge is the critical path. | Hold 3 s without motion. Show only what the Waterfall draws on the day (see `apps/web/e2e/multiagent.spec.ts`). |
| 8 | 1:02-1:08 | Review, Approvals tab (Pending): the request row with capability and risk, the panel with Approve and Deny (no click). | When an agent asks to act, a person approves or denies it. | Do not decide. The page foot "Policies apply to agents that ask through the SDK guard." must be in frame [CONFIRM AFTER STEP 10]. |
| 9 | 1:08-1:13 | Analytics, Cost: the headline sentence ("$X spent in the last 7 days, ..."). | Cost, reliability and speed, each in one sentence. | Reliability and Performance tabs each shown 1 s. |
| 10 | 1:13-1:18 | The Runs page switches to the light theme (system preference, there is no toggle), then a closing hold. | Agent BlackBox: a flight recorder for AI agents. | **Blocked line:** "Open source. Self-hosted." may replace this caption only after G2 (repository public) and AC1 and AC12 are VERIFIED. Until then the caption above is the one in the table. |

Total 78 s. If scene 6 is cut the total is 72 s and the other holds may grow by 1 s each. If it runs long, shorten scene 9 first.

## Recording method

Playwright records one WebM per browser context; no screen-recording software is needed. The recorder is
`scripts/record-demo.mjs` and the encode step is `scripts/encode-demo.sh` (needs ffmpeg, installed and approved 2026-10-18).

1. Start the stack the video shows: `make demo` against a running stack (see plan step 10), or for a mechanics check the
   fixtures build of `apps/web` on port 3100 (`ABB_WEB_DATA_SOURCE=fixtures ABB_WEB_ALLOW_FIXTURES=1 pnpm exec next start --port 3100`),
   foreground, bounded, stopped afterwards.
2. `ABB_VIDEO_BASE_URL=... node scripts/record-demo.mjs` (sign-in material, if the stack needs it, comes from environment
   variables named in the script header and is never printed). `--dry-run` records scenes 1-3 only (about 20 s).
3. `scripts/encode-demo.sh video-out/<file>.webm` writes an H.264 MP4 (yuv420p, faststart) and a poster PNG next to it.
4. The script is a pure function of the scene table: a UI change means editing selectors in the table, not the driver.

Context settings: 1920x1080, `deviceScaleFactor` 1, `colorScheme: "dark"`, reduced motion **off** (so the inspector's slide and the
fault line's single draw show), cursor simulated by an injected round `div` following `pointermove` (Playwright cannot draw the
real cursor), captions injected as a fixed lower-third `div` in IBM Plex Sans 28 px. Output goes to `video-out/` (gitignored).

## Checklist before the real recording

- Plan steps 3-6 (KI-097) and 10 (`make demo`) merged, so scene 6 and the data names can be confirmed; replace every
  [CONFIRM ...] above with the checked fact.
- Every caption re-read against the claims table; scene 10 stays neutral until G2.
- No real data: the demo workspace uses `owner@local.test` (the person menu is not opened in the video); no key, token or `.env`
  content is on screen; the scope control reads `demo`.
- Look at a frame from every scene as an image (captions not clipped, cursor visible, no text overlap) before publishing.
- The user approves where the video is published (G2 territory); the recorder never uploads anything.
