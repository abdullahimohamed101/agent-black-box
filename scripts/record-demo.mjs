#!/usr/bin/env node
/*
 * record-demo.mjs (v3, cinematic): records the app of the Agent BlackBox demo video from a RUNNING, demo-seeded stack.
 * Direction: docs/design/launch/video-cinematic-brief.md (and video-brief.md for what it keeps). Record with --captions=off --card=off:
 * the page then carries only the cursor, camera, spotlight and key chip; scripts/video-plates.mjs and scripts/video-compose.mjs
 * (run by scripts/encode-demo.sh) add the caption band, cards, dips and dim from meta.json. Captions: docs/design/launch/positioning-brief.md (caption bank, section 7).
 *
 * What it does. Drives system Chrome with Playwright at 1920x1080 CSS px and deviceScaleFactor 2, captures the page with the
 * CDP screencast (3840x2160 JPEG frames with real arrival times; Playwright's own recorder is VP8 at a fixed 1 Mbit/s), and
 * draws everything a screen recorder would add itself: an arrow cursor with eased travel and a click ripple, a camera zoom on
 * <body>, a dim spotlight, a key chip, burned-in captions and a closing card. The whole run is a pure function of the BEATS
 * tables below: a UI change means editing a selector in a table, never the driver. The pure maths (easing, caption timing,
 * subtitles, frame list) is in record-demo-lib.mjs and tested by `node --test scripts/`.
 *
 * Output (gitignored video-out/<cut>-<stamp>/): frames/ + frames.ffconcat, meta.json, demo.vtt, demo.srt, transcript.md,
 * pagetext/ (the visible text of every beat, for the QA grep). Then: scripts/encode-demo.sh <that directory>.
 *
 * Usage (repo root, `pnpm install` done so @playwright/test resolves from apps/web):
 *   scripts/demo-stack.sh up                                  # a private demo-seeded stack on :3180 (never your dev stack)
 *   node scripts/record-demo.mjs --captions=off --card=off    # main cut, 68.8 s of app (73 s with the cards)
 *   node scripts/record-demo.mjs --cut=teaser                 # 18 s loop for the landing hero and the README
 *   node scripts/record-demo.mjs --cut=social                 # 1000x1000 take composited to 1080x1350, beats 1-7
 *   node scripts/record-demo.mjs --plan                       # no browser: print the beats and cues, write demo.vtt, check the rules
 *   node scripts/record-demo.mjs --dry-run                    # beats 1-3 only (about 15 s): checks the mechanics
 *
 * Environment (nothing is hard-coded; no credential is ever printed):
 *   ABB_VIDEO_BASE_URL        required for a recording, e.g. http://localhost:3180
 *   ABB_VIDEO_SESSION_FILE    file with a development session token (0600; scripts/demo-stack.sh writes one), or
 *   ABB_VIDEO_SESSION_TOKEN   the token itself; either is set as the HttpOnly abb_session cookie (as apps/web/e2e/session.ts does)
 *   ABB_VIDEO_PROFILE         demo (default; the data `make demo` seeds) | fixtures (the apps/web fixtures build)
 *   ABB_VIDEO_WORKSPACE       workspace slug (profile default: demo -> local, fixtures -> demo)
 *   ABB_VIDEO_OUT             output directory (default video-out/)
 *   ABB_VIDEO_TIMEOUT_MS      hard timeout for the whole run (default cut length + 180 s)
 *   RELEASE_CLAIMS=1          only after G2 (repository public) and AC1/AC12 are VERIFIED: the closing card says "Open source.
 *                             Self-hosted. Apache-2.0." and shows the repository URL (captions 26 and 28). Off by default.
 * Flags: --cut=main|teaser|social, --plan, --dry-run, --headed, --profile=<demo|fixtures>, --captions=off, --card=off.
 * ABB_VIDEO_TRACE=1 prints how long each act took. Exit 4: forbidden text ("Agent Black Box", keys, emails) on screen; exit 5: a stalled take
 * (a beat overran its table by 0.4 s): record again.
 * Nothing is uploaded; the script only talks to ABB_VIDEO_BASE_URL. It never passes or shows `--live`.
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  CAMERA_EASE_CSS, MAX_CAPTION_WORDS, applyHolds, MIN_CAPTION_GAP, buildConcat, buildSrt, buildVtt, captionHtml, captionOverlap, clippedLines,
  fitZoom, maxFrameGap, minCaptionSeconds, parseVtt, placeCaption, plain, planCut, travelMs, travelPoint, wordCount,
} from "./record-demo-lib.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// ---------------------------------------------------------------------------------------------------------------------
// Names read from the seeded demo (scripts/demo.sh). Run pages are found by visible name, never by id.
// ---------------------------------------------------------------------------------------------------------------------
const DATA = {
  demo: { workspace: "local", failedRun: "Answer ticket: invoice shows wrong VAT", multiRun: "Ship feature: multi-agent", approval: "github.push" },
  fixtures: { workspace: "demo", failedRun: "Migrate billing schema", multiRun: "Ship feature: multi-agent", approval: "github.push" },
};

// ---------------------------------------------------------------------------------------------------------------------
// Captions: the bank of docs/design/launch/positioning-brief.md section 7, by number. `[[x]]` is the one red word of a caption.
// Gated captions need RELEASE_CLAIMS=1 (G2). Nothing here says live, production, SaaS or enterprise.
// ---------------------------------------------------------------------------------------------------------------------
const BANK = {
  1: "Agent BlackBox records what your agents did.",
  3: "A [[failed]] run. Open it.",
  4: "One sentence says where it broke.",
  6: "Spend after the first error, counted.",
  7: "The red line is the [[first error]].",
  8: "Press e. The [[failed]] tool call opens.",
  9: "What went wrong, from the event itself.",
  10: "Every model call carries tokens and cost.",
  11: "Retries are retries, not log lines.",
  12: "Findings, from deterministic detectors.",
  13: "Each finding links to its evidence.",
  14: "Destructive commands, secret reads, risky git.",
  15: "Every finding states its rule.",
  16: "Five agents, one run, one picture.",
  17: "The thick edge is the critical path.",
  20: "What the week cost, per run.",
  21: "Failed runs, retries, spend after errors.",
  22: "The agent asked. A person decides.",
  23: "One approval, one action, once.",
  26: "Open source. Self-hosted. Apache-2.0.",
  27: "Light and dark follow your system.",
  28: "github.com/abdullahimohamed101/agent-black-box",
};
// Not a bank caption: positioning brief headline option 3, allowed as the one-line description before G2.
const CLOSING = "Agent BlackBox: a flight recorder for AI agents.";
const GATED_BANK = new Set([25, 26, 28]); // G2 (repository public), AC1/AC12, AC8
const FORBIDDEN_WORDS = /\b(live|production|saas|enterprise|real model|openai|anthropic|claude|gpt)\b|--live/i;
const releaseClaims = process.env.RELEASE_CLAIMS === "1";
const cap = (bank, at, extra = {}) => ({ at, bank, text: BANK[bank], ...extra });

// ---------------------------------------------------------------------------------------------------------------------
// The beat tables. Times are seconds from the beat start; a beat lasts max(len, its last caption + 0.35 s), and a caption lasts
// at least max(2 s, characters / 15 + 0.6 s) (video-brief.md section 4), so the reading rule, not the table, sets the pace.
//
// Selector specs `to`/`on`/`for`: {role,name,exact} | {testid} | {css, has?} | {text} | {row: "<text>"} (a timeline row) |
// {heading: "<text>"} (the heading's parent block) | {css, widest: true, row?: true} (the widest match, or its waterfall row).
// A string value beginning "$" is read from DATA; a name beginning "re:" is a case-insensitive regular expression.
// Acts (in order, each waits for its `at`): goto {path} | waitFor {for} | wait {ms} | scroll {to} | travel {to, fx, fy, dx, dy} |
//   dwell {ms} | click | park {name} | press {key, chip} | focus {css} | zoom {on, level} | zoomOut | spot {on: spec | [spec], pad} |
//   spotOff | scheme {value} | card {show} | fade {to: 0|1, ms} | cursor {show} | scrape {key, of} | optional: true on any act.
// ---------------------------------------------------------------------------------------------------------------------
const NAV = (name) => ({ role: "link", name, exact: true });
const RUNS_HEADING = { role: "heading", name: "Runs", exact: true };
const FAILED_LINK = { role: "link", name: "$failedRun" };
const TIMELINE_TAB = { role: "tab", name: "Timeline", exact: true };
const FAULT_ROW = { css: ".timeline [data-first-error]" };
const WHAT_WENT_WRONG = { css: '[role="dialog"] h3', has: "What went wrong", parent: true };
// The hook frames the headline and the red line together: the headline, plus the left 760 px of the first-error row (its text).
const HOOK_FOCUS = { union: [{ testid: "headline" }, { ...FAULT_ROW, clipW: 760 }] };

// v3 (cinematic): the recorder films the app only. The caption band, the title card, the end card, the dips and the dim are made
// afterwards by scripts/video-compose.mjs from meta.json (docs/design/launch/video-cinematic-brief.md). Times below are seconds
// from the beat start; the film clock the recorder keeps excludes the time spent in a `hop` (see below), so a hop is a hard cut.
//
// `hop {via, waitFor, park}`: a section change. The cursor fades out, then everything between "now" and "the new page is ready and
// settled" is cut out of the film (the frames in between are dropped, the old page is followed at once by the new one), so the
// viewer sees one hard cut at beat start + HOP_LEAD, inside a caption gap, never a dip and never a cursor transit. The first caption
// of a beat that starts with a hop comes at least HOP_QUIET after the cut; the previous beat's last caption ends before it.
const HOP_LEAD = 0.25;
const HOP_QUIET = 0.8;
const FIRST_CAP = HOP_LEAD + HOP_QUIET; // 1.05 s into a beat that starts with a hop

const MAIN = [
  { // 1. The Runs list, the failed run opened with a click (the fault line draws as the page opens).
    id: 1, len: 4.6, tx: "The Runs list. The cursor goes to the failed run {failedRun} and opens it; the red first-error line draws on its timeline.",
    caps: [cap(3, 1.2)],
    acts: [
      { at: 1.2, do: "travel", to: FAILED_LINK },
      { do: "dwell", ms: 400 },
      { do: "click" },
      { do: "waitFor", for: { testid: "headline" } },
      { do: "park", name: "run" },
    ],
  },
  { // 2. The sentence: camera 1.6x on the headline.
    id: 2, len: 4.6, tx: "The run page says in one sentence where it broke: {headline}",
    caps: [cap(4, 0.8)],
    acts: [
      { at: 0.0, do: "scrape", key: "headline", of: { testid: "headline" } },
      { at: 0.3, do: "zoom", on: { testid: "headline" }, level: 1.6, min: 1.3 },
      { at: 4.0, do: "zoomOut" },
    ],
  },
  { // 3. The line under it, lit.
    id: 3, len: 3.4, tx: "Under it: {detail}",
    caps: [cap(6, 0.2)],
    acts: [
      { at: 0.0, do: "scrape", key: "detail", of: { testid: "headline-detail" } },
      { at: 0.2, do: "spot", on: { testid: "headline-detail" }, pad: 3 },
      { at: 3.2, do: "spotOff" },
    ],
  },
  { // 4. The red line.
    id: 4, len: 3.6, tx: "On the timeline one red line crosses the rows and is labelled First error.",
    caps: [cap(7, 0.3)],
    acts: [
      { at: 0.0, do: "travel", to: { css: ".tl-fault-label" }, fx: 0, fy: 1, dx: -60, dy: 24 },
      { at: 0.3, do: "spot", on: [FAULT_ROW, { css: ".tl-fault-label" }] },
      { at: 3.3, do: "spotOff" },
    ],
  },
  { // 5. e opens the failed event in the inspector; the camera frames "What went wrong" at the largest zoom (1.6x down to 1.3x) at which no line of text is cut
    // (the brief's 1.25x cut the timeline text behind the inspector at the window's left edge; fitZoom decides, and the QA measures it).
    id: 5, len: 7.2, tx: "Pressing e opens the failed tool call in the inspector. Under What went wrong it reads: {wrong}",
    caps: [cap(8, 0.2), cap(9, 3.6)],
    acts: [
      { at: 0.0, do: "cursor", show: false },
      { at: 0.0, do: "focus", css: ".timeline" },
      { at: 0.2, do: "press", key: "e", chip: "e" },
      { do: "waitFor", for: { role: "dialog" } },
      { at: 1.0, do: "scrape", key: "wrong", of: { css: '[role="dialog"] .drawer-error p' } },
      { at: 1.3, do: "zoom", on: WHAT_WENT_WRONG, level: 1.6, min: 1.3 },
      { at: 6.6, do: "zoomOut" },
      { at: 7.05, do: "press", key: "Escape" },
    ],
  },
  { // 6. Typed events: a model call with tokens and cost, then a retry.
    id: 6, len: 7.1, tx: "Back on the timeline, a model call row shows its tokens and cost, and a retry row says retry attempted.",
    caps: [cap(10, 0.3), cap(11, 3.9)],
    acts: [
      { at: 0.0, do: "travel", to: { row: "llm.request.completed" }, fx: 0.52, fy: 0.5 },
      { at: 0.3, do: "spot", on: { row: "llm.request.completed" }, pad: 6 },
      { at: 3.6, do: "travel", to: { row: "retry.attempted" }, fx: 0.62, fy: 0.5 },
      { at: 3.7, do: "spot", on: { row: "retry.attempted" }, pad: 6 },
      { at: 6.85, do: "spotOff" },
    ],
  },
  { // 7. Findings and their evidence.
    id: 7, len: 7.5, tx: "On the Findings tab: critical findings. Show evidence jumps to the exact rows.",
    caps: [cap(12, 1.0), cap(13, 4.45)],
    acts: [
      { at: 0.0, do: "travel", to: { role: "tab", name: "Findings", exact: true } },
      { do: "dwell", ms: 200 },
      { do: "click" },
      { at: 2.4, do: "travel", to: { role: "button", name: "Show evidence" } },
      { do: "dwell", ms: 300 },
      { at: 4.3, do: "click" },
      { do: "wait", ms: 450 },
      { do: "park", name: "run" },
    ],
  },
  { // 8. Five agents in one waterfall (a cut: the multi-agent run opens on its Waterfall in Lanes).
    id: 8, len: 7.7, cut: true, tx: "The multi-agent run {multiRun}: the Waterfall in lanes shows five agents, with the critical path as the thick edge.",
    caps: [cap(16, FIRST_CAP), cap(17, 4.4)],
    acts: [
      { at: 0.0, do: "hop", via: [NAV("Runs"), { role: "link", name: "$multiRun" }, { role: "tab", name: "Waterfall", exact: true }, { role: "button", name: "Lanes", exact: true }],
        waitFor: { testid: "waterfall" }, park: "run" },
      // the cursor stays off the bars: the app answers a hover with a tooltip that covers the lane labels
      { at: 4.2, do: "spot", on: { css: ".wf-bar[data-critical]", widest: true, row: true } },
      { at: 7.4, do: "spotOff" },
    ],
  },
  { // 9. A person decides. Never clicks Approve or Deny.
    id: 9, len: 7.1, cut: true, tx: "Review: one request is Waiting. Its panel shows the capability github.push, the resource, the risk class R2, the arguments and Approve and Deny buttons, which are never clicked.",
    caps: [cap(22, FIRST_CAP), cap(23, 4.25)],
    acts: [
      { at: 0.0, do: "hop", via: [{ role: "link", name: "re:^Review" }], waitFor: { testid: "approval-row" }, park: "review" },
      { at: 0.5, do: "travel", to: { role: "link", name: "$approval" }, fx: 0.95, fy: 0.3 }, // the arrow beside the link, not over the line under it
      { do: "dwell", ms: 350 },
      { do: "click" },
      { do: "waitFor", for: { role: "button", name: "Approve", exact: true } },
      { do: "park", name: "review" },
      { at: 3.9, do: "spot", on: { css: "dl", has: "Capability" }, pad: 10 },
      { at: 6.75, do: "spotOff" },
    ],
  },
  { // 10. What the week cost.
    id: 10, len: 7.4, cut: true, tx: "Analytics, Cost: {costLine} Below it, where the money was wasted: failed runs, retries and spend after errors.",
    caps: [cap(20, FIRST_CAP), cap(21, 3.8)],
    acts: [
      { at: 0.0, do: "hop", via: [NAV("Analytics")], waitFor: { testid: "report-headline" }, park: "analytics" },
      { do: "scrape", key: "costLine", of: { testid: "report-headline" } },
      { at: 3.6, do: "spot", on: { heading: "Where the money was wasted" }, pad: 10 },
      { at: 7.0, do: "spotOff" },
    ],
  },
  { // 11. Security findings, each with its rule.
    id: 11, len: 8.0, cut: true, tx: "Review, Security findings: a destructive command, a risky git operation and a secret file read, each with a Rule line.",
    caps: [cap(14, FIRST_CAP), cap(15, 5.0)],
    acts: [
      { at: 0.0, do: "hop", via: [{ role: "link", name: "re:^Review" }, { role: "tab", name: "Security findings", exact: true }], waitFor: { testid: "security-row" }, park: "review" },
      { at: 0.8, do: "travel", to: { testid: "security-row" }, fx: 0.55, fy: 0.5 },
      { at: 1.1, do: "spot", on: { testid: "security-row" }, pad: 6 },
      { at: 7.4, do: "spotOff" },
    ],
  },
];

// The teaser loop: the first five beats' story (hook, headline, red line, inspector) with the dip to the canvas at both ends (compose).
const TEASER = [
  { id: "T1", len: 4.3, tx: "The Runs list; the cursor opens the failed run.", caps: [cap(3, 0.6)],
    acts: [
      { at: 1.2, do: "travel", to: FAILED_LINK }, { do: "dwell", ms: 400 }, { do: "click" },
      { do: "waitFor", for: { testid: "headline" } }, { do: "park", name: "run" },
    ] },
  { id: "T2", len: 4.3, tx: "The run page says in one sentence where it broke: {headline}", caps: [cap(4, 0.7)],
    acts: [
      { at: 0.0, do: "scrape", key: "headline", of: { testid: "headline" } },
      { at: 0.2, do: "zoom", on: { testid: "headline" }, level: 1.6, min: 1.3 },
      { at: 3.7, do: "zoomOut" },
    ] },
  { id: "T3", len: 3.4, tx: "On the timeline one red line crosses the rows and is labelled First error.", caps: [cap(7, 0.3)],
    acts: [
      { at: 0.0, do: "travel", to: { css: ".tl-fault-label" }, fx: 0, fy: 1, dx: -60, dy: 24 },
      { at: 0.3, do: "spot", on: [FAULT_ROW, { css: ".tl-fault-label" }] },
      { at: 3.15, do: "spotOff" },
    ] },
  { id: "T4", len: 7.4, tx: "Pressing e opens the failed tool call in the inspector. Under What went wrong it reads: {wrong}", caps: [cap(8, 0.2), cap(9, 3.6)],
    acts: [
      { at: 0.0, do: "cursor", show: false },
      { at: 0.0, do: "focus", css: ".timeline" },
      { at: 0.2, do: "press", key: "e", chip: "e" },
      { do: "waitFor", for: { role: "dialog" } },
      { at: 1.0, do: "scrape", key: "wrong", of: { css: '[role="dialog"] .drawer-error p' } },
      { at: 1.0, do: "zoom", on: WHAT_WENT_WRONG, level: 1.6, min: 1.3 },
    ] },
];

// Social cut: a separate take at 1000x1000 CSS px (the app's one-column layout), beats 1-7, zoom at most 1.4. Written after a dry
// run of the narrow layout (docs/design/launch/video-cinematic-brief.md 3.3): the rows it names are the ones it showed.
const SOCIAL = [
  { id: 1, len: 5.0, tx: "The Runs list at phone width; the failed run {failedRun} is opened.", caps: [cap(3, 1.2)],
    acts: [
      { at: 1.2, do: "travel", to: FAILED_LINK }, { do: "dwell", ms: 400 }, { do: "click" },
      { do: "waitFor", for: { testid: "headline" } }, { do: "park", name: "run" },
    ] },
  { id: 2, len: 4.6, tx: "The headline of the run: {headline}", caps: [cap(4, 0.6)],
    acts: [
      { at: 0.0, do: "scrape", key: "headline", of: { testid: "headline" } },
      { at: 0.2, do: "zoom", on: { testid: "headline" }, level: 1.4, min: 1.1 },
      { at: 4.0, do: "zoomOut" },
    ] },
  { id: 3, len: 3.4, tx: "Under it: {detail}", caps: [cap(6, 0.2)],
    acts: [
      { at: 0.0, do: "scrape", key: "detail", of: { testid: "headline-detail" } },
      { at: 0.2, do: "spot", on: { testid: "headline-detail" }, pad: 3 },
      { at: 3.2, do: "spotOff" },
    ] },
  { id: 4, len: 3.6, tx: "The red first-error line on the timeline.", caps: [cap(7, 0.3)],
    acts: [
      { at: 0.0, do: "scroll", to: FAULT_ROW },
      { at: 0.1, do: "travel", to: { css: ".tl-fault-label" }, fx: 0, fy: 1, dx: -40, dy: 20 },
      { at: 0.4, do: "spot", on: [FAULT_ROW, { css: ".tl-fault-label" }] },
      { at: 3.3, do: "spotOff" },
    ] },
  { id: 5, len: 7.2, tx: "The inspector opens over the list: {wrong}", caps: [cap(8, 0.2), cap(9, 3.6)],
    acts: [
      { at: 0.0, do: "cursor", show: false },
      { at: 0.0, do: "focus", css: ".timeline" },
      { at: 0.2, do: "press", key: "e", chip: "e" },
      { do: "waitFor", for: { role: "dialog" } },
      { at: 1.0, do: "scrape", key: "wrong", of: { css: '[role="dialog"] .drawer-error p' } },
      { at: 1.3, do: "spot", on: WHAT_WENT_WRONG, pad: 6 },
      { at: 6.7, do: "spotOff" },
      { at: 7.05, do: "press", key: "Escape" },
    ] },
  { id: 6, len: 7.1, tx: "A model call row and a retry row on the timeline.", caps: [cap(10, 0.3), cap(11, 3.9)],
    acts: [
      { at: 0.0, do: "travel", to: { row: "llm.request.completed" }, fx: 0.5, fy: 0.5 },
      { at: 0.3, do: "spot", on: { row: "llm.request.completed" }, pad: 4 },
      { at: 3.6, do: "travel", to: { row: "retry.attempted" }, fx: 0.5, fy: 0.5 },
      { at: 3.7, do: "spot", on: { row: "retry.attempted" }, pad: 4 },
      { at: 6.85, do: "spotOff" },
    ] },
  { id: 7, len: 7.5, tx: "On the Findings tab: findings, and Show evidence.", caps: [cap(12, 1.0), cap(13, 4.45)],
    acts: [
      { at: 0.0, do: "travel", to: { role: "tab", name: "Findings", exact: true } }, { do: "dwell", ms: 200 }, { do: "click" },
      { at: 2.4, do: "travel", to: { role: "button", name: "Show evidence" } }, { do: "dwell", ms: 300 },
      { at: 4.3, do: "click" }, { do: "wait", ms: 450 }, { do: "park", name: "run" },
    ] },
];

// Parking spots are fractions of the viewport (never over text being read; video-brief.md 3.3).
const PARKS = { runs: [0.625, 0.2], run: [0.78, 0.28], review: [0.36, 0.88], analytics: [0.78, 0.28] };
// `cap` is the size of the caption when it is drawn in the page (--captions=on, a preview only); the film's captions are plates.
const CUTS = {
  main: { beats: MAIN, w: 1920, h: 1080, name: "main", cap: { size: 34, line: 44 }, parks: PARKS, start: "runs" },
  teaser: { beats: TEASER, w: 1920, h: 1080, name: "teaser", cap: { size: 34, line: 44 }, parks: PARKS, start: "runs" },
  social: { beats: SOCIAL, w: 1000, h: 1000, name: "social", cap: { size: 40, line: 50 }, parks: { runs: [0.6, 0.2], run: [0.7, 0.2], review: [0.4, 0.9], analytics: [0.7, 0.3] }, start: "runs" },
};

// ---------------------------------------------------------------------------------------------------------------------
// Flags, planning and the rules every cut must satisfy before a browser is started.
// ---------------------------------------------------------------------------------------------------------------------
const args = process.argv.slice(2);
const flag = (n) => args.includes(`--${n}`);
const opt = (n) => args.find((a) => a.startsWith(`--${n}=`))?.split("=")[1];

function fail(msg) {
  console.error(`record-demo: ${msg}`);
  process.exit(2);
}

const cutName = opt("cut") ?? "main";
if (!CUTS[cutName]) fail(`unknown cut "${cutName}" (main, teaser, social)`);
const cut = CUTS[cutName];
const dry = flag("dry-run");
// The cinematic film (the default way to make a cut): the page carries no caption and no card; scripts/video-compose.mjs draws them.
const captionsOff = opt("captions") === "off";
const cardOff = opt("card") === "off";
const cinematic = captionsOff && cardOff;
const profile = opt("profile") ?? process.env.ABB_VIDEO_PROFILE ?? "demo";
if (!DATA[profile]) fail(`unknown profile "${profile}" (demo or fixtures)`);
const data = DATA[profile];
const workspace = process.env.ABB_VIDEO_WORKSPACE ?? data.workspace;
const beats = cut.beats.filter((b) => !dry || ["1", "2", "3", "T1", "T2", "T3"].includes(String(b.id)));
const plan = planCut(beats);
const totalPlanned = plan.length ? plan[plan.length - 1].start + plan[plan.length - 1].len : 0;
const outRoot = path.resolve(root, process.env.ABB_VIDEO_OUT ?? "video-out");

/** The rules of the brief that can be checked without a browser. Returns a list of problems (empty = fine). */
function checkPlan(planned) {
  const problems = [];
  const all = planned.flatMap((p) => p.cues.map((c) => ({ ...c, beat: p.id, abs: { start: p.start + c.start, end: p.start + c.end } })));
  for (const c of all) {
    const words = wordCount(c.text);
    if (words > MAX_CAPTION_WORDS) problems.push(`beat ${c.beat}: "${plain(c.text)}" has ${words} words (max ${MAX_CAPTION_WORDS})`);
    if (c.end - c.start < minCaptionSeconds(c.text) - 1e-9) problems.push(`beat ${c.beat}: "${plain(c.text)}" is on screen ${(c.end - c.start).toFixed(2)} s, the rule needs ${minCaptionSeconds(c.text).toFixed(2)} s`);
    if (c.end - c.start > 6.0001) problems.push(`beat ${c.beat}: "${plain(c.text)}" stays longer than 6 s`);
    if (FORBIDDEN_WORDS.test(c.text)) problems.push(`beat ${c.beat}: "${plain(c.text)}" uses a word the video must not use`);
    if (!releaseClaims && ((c.bank && GATED_BANK.has(c.bank)) || /open source|self-hosted|apache|github\.com/i.test(c.text))) {
      problems.push(`beat ${c.beat}: "${plain(c.text)}" is gated on G2 and RELEASE_CLAIMS is not set`);
    }
  }
  for (let i = 1; i < all.length; i++) {
    const gap = all[i].abs.start - all[i - 1].abs.end;
    if (gap < MIN_CAPTION_GAP - 1e-9) problems.push(`captions "${plain(all[i - 1].text)}" and "${plain(all[i].text)}" are ${gap.toFixed(2)} s apart (minimum ${MIN_CAPTION_GAP})`);
  }
  // A cut (a beat that starts with a hop) comes HOP_LEAD into the beat: the previous beat's last caption must be over by then, and
  // the first caption of the beat comes at least HOP_QUIET after it (the band is empty 0.25 s before to 0.8 s after a cut).
  planned.forEach((p, i) => {
    const beat = beats.find((b) => b.id === p.id);
    if (i + 1 < planned.length && beats.find((b) => b.id === planned[i + 1].id)?.cut) {
      for (const c of p.cues) if (c.end > p.len + 1e-9) problems.push(`beat ${p.id}: a caption runs into the cut that opens beat ${planned[i + 1].id}`);
    } else if (i + 1 === planned.length || !beats.find((b) => b.id === planned[i + 1].id)?.cut) {
      for (const c of p.cues) if (c.end > p.len - 0.2) problems.push(`beat ${p.id}: a caption runs to the end of the beat`);
    }
    if (beat?.cut && p.cues[0] && p.cues[0].start < HOP_LEAD + HOP_QUIET - 1e-9) problems.push(`beat ${p.id}: the first caption comes ${p.cues[0].start.toFixed(2)} s after the beat start, less than ${(HOP_LEAD + HOP_QUIET).toFixed(2)} s (cut at ${HOP_LEAD} s plus ${HOP_QUIET} s of quiet)`);
  });
  return problems;
}

function writePlan(dir, planned) {
  const cues = planned.flatMap((p) => p.cues.map((c) => ({ start: p.start + c.start, end: p.start + c.end, text: c.text })));
  const vtt = buildVtt(cues);
  parseVtt(vtt); // throws when it is not valid WebVTT
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, "demo.vtt"), vtt);
}

const problems = checkPlan(plan);
if (flag("plan")) {
  console.log(`cut ${cutName}: ${plan.length} beats, ${totalPlanned.toFixed(1)} s planned, ${plan.flatMap((p) => p.cues).length} captions (RELEASE_CLAIMS ${releaseClaims ? "on" : "off"})`);
  for (const p of plan) {
    console.log(`beat ${String(p.id).padEnd(2)} ${p.start.toFixed(1).padStart(5)} s  len ${p.len.toFixed(1)} s`);
    for (const c of p.cues) console.log(`    ${(p.start + c.start).toFixed(2).padStart(6)} - ${(p.start + c.end).toFixed(2).padStart(6)}  ${plain(c.text)}${c.bank ? `  [bank ${c.bank}]` : ""}`);
  }
  const dir = path.join(outRoot, `plan-${cutName}`);
  writePlan(dir, plan);
  console.log(`wrote ${path.relative(root, path.join(dir, "demo.vtt"))} (valid WebVTT)`);
  if (problems.length) {
    console.error(`\n${problems.length} problem(s):\n- ${problems.join("\n- ")}`);
    process.exit(1);
  }
  console.log("plan rules: all hold");
  process.exit(0);
}
if (problems.length) fail(`the plan breaks the brief's rules:\n- ${problems.join("\n- ")}`);

const base = process.env.ABB_VIDEO_BASE_URL;
if (!base) fail("ABB_VIDEO_BASE_URL is required (e.g. http://localhost:3180)");
let sessionToken = process.env.ABB_VIDEO_SESSION_TOKEN;
if (!sessionToken && process.env.ABB_VIDEO_SESSION_FILE) {
  if (statSync(process.env.ABB_VIDEO_SESSION_FILE).mode & 0o077) fail("the session file must be owner-only (chmod 600)");
  try {
    sessionToken = readFileSync(process.env.ABB_VIDEO_SESSION_FILE, "utf8").trim();
  } catch {
    fail("ABB_VIDEO_SESSION_FILE is set but cannot be read");
  }
}
if (!sessionToken) fail("a development session is needed: ABB_VIDEO_SESSION_FILE or ABB_VIDEO_SESSION_TOKEN (scripts/demo-stack.sh up writes one)");

const { chromium } = createRequire(path.join(root, "apps/web/package.json"))("@playwright/test");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const DSF = 2;

// ---------------------------------------------------------------------------------------------------------------------
// The overlay: runs in every document. Everything lives on <html>, outside <body>, so the zoom (a transform on <body>) never
// scales the cursor, the spotlight, the chip or the captions. React drops unknown children of <html> on hydration, so the
// elements are kept by reference and put back by a MutationObserver and a 250 ms interval. The app's CSP refuses injected
// <style> elements, so the context bypasses CSP and the nonce is copied as well.
// ---------------------------------------------------------------------------------------------------------------------
function overlay(cfg) {
  const mount = () => {
    if (document.getElementById("abb-cur")) return;
    const W = cfg.w, H = cfg.h;
    const style = document.createElement("style");
    const nonced = document.querySelector("script[nonce],style[nonce],link[nonce]");
    if (nonced?.nonce) style.nonce = nonced.nonce;
    style.textContent = `
      html { overflow: hidden !important; background: #15191e; }
      .timeline:focus-visible { outline: none !important; } /* the listbox ring is a keyboard affordance, not part of the story */
      body { transform-origin: 0 0; transition: transform ${cfg.zoomIn}ms ${cfg.camera}; }
      #abb-cur { position: fixed; left: 0; top: 0; width: 32px; height: 32px; pointer-events: none; z-index: 2147483640;
        transition: opacity 200ms linear; will-change: transform; }
      #abb-cur svg { display: block; width: 32px; height: 32px; overflow: visible; filter: drop-shadow(0 1px 1.5px rgba(0,0,0,.6)); }
      #abb-ripple { position: fixed; left: 0; top: 0; width: 44px; height: 44px; margin: -22px 0 0 -22px; border-radius: 50%;
        border: 2px solid rgba(255,255,255,.85); pointer-events: none; z-index: 2147483639; opacity: 0; transform: scale(.3); }
      #abb-ripple.go { animation: abb-rip 380ms cubic-bezier(.2,.7,.3,1) forwards; }
      @keyframes abb-rip { 0% { opacity: .9; transform: scale(.3) } 100% { opacity: 0; transform: scale(1.15) } }
      #abb-cap { position: fixed; left: 50%; top: 0; transform: translateX(-50%); max-width: ${Math.round(W * 0.9)}px;
        padding: 14px 26px; border-radius: 4px; background: rgba(16,20,25,.92); border: 1px solid rgba(255,255,255,.14);
        color: #e2e5e8; font: 600 ${cfg.capSize}px/${cfg.capLine}px var(--abb-cap-font, "IBM Plex Sans", system-ui, sans-serif);
        letter-spacing: 0; text-align: center; pointer-events: none; z-index: 2147483645; opacity: 0;
        transition: opacity 160ms linear; white-space: nowrap; }
      #abb-cap.on { opacity: 1; }
      #abb-cap.off { transition-duration: 120ms; }
      #abb-cap em { font-style: normal; color: #ff8a80; }
      #abb-spot { position: fixed; left: 0; top: 0; width: 0; height: 0; border-radius: 4px; pointer-events: none; z-index: 2147483643;
        box-shadow: 0 0 0 9999px rgba(16,20,25,.55); opacity: 0;
        transition: opacity 200ms linear, left 260ms ${cfg.camera}, top 260ms ${cfg.camera}, width 260ms ${cfg.camera}, height 260ms ${cfg.camera}; }
      @media (prefers-color-scheme: light) { #abb-spot { box-shadow: 0 0 0 9999px rgba(29,34,41,.45); } }
      #abb-chip { position: fixed; right: 60px; bottom: 84px; width: 44px; height: 44px; border-radius: 4px;
        background: rgba(16,20,25,.92); border: 1px solid rgba(255,255,255,.14); color: #e2e5e8; display: flex; align-items: center;
        justify-content: center; font: 500 22px/1 "IBM Plex Mono", ui-monospace, monospace; pointer-events: none; z-index: 2147483644;
        opacity: 0; transition: opacity 120ms linear; }
      #abb-chip.on { opacity: 1; }
    `;
    document.documentElement.append(style);
    const mk = (tag, id, html) => { const e = document.createElement(tag); e.id = id; if (html) e.innerHTML = html; return e; };
    const cur = mk("div", "abb-cur",
      '<svg viewBox="0 0 24 24"><path d="M5 3l14 9-6.2 1.2L16.5 20l-2.6 1.2-3.7-6.6L5 19z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>');
    const rip = mk("div", "abb-ripple");
    const capEl = mk("div", "abb-cap"); capEl.setAttribute("aria-hidden", "true");
    const spot = mk("div", "abb-spot");
    const chip = mk("div", "abb-chip");
    document.documentElement.append(spot, capEl, chip, rip, cur);
    const font = getComputedStyle(document.body).getPropertyValue("--font-plex-sans").trim();
    if (font) document.documentElement.style.setProperty("--abb-cap-font", font);
    const store = {
      get: (k) => { try { return sessionStorage.getItem(k); } catch { return null; } },
      set: (k, v) => { try { sessionStorage.setItem(k, v); } catch { /* storage can be blocked */ } },
    };
    let pos = { x: cfg.start.x, y: cfg.start.y };
    try { pos = JSON.parse(store.get("__abbCur") ?? "null") ?? pos; } catch { /* keep the default */ }
    let scale = 1;
    const place = (x, y) => { cur.style.transformOrigin = "0 0"; cur.style.transform = `translate(${x}px,${y}px) scale(${scale})`; };
    place(pos.x, pos.y);
    addEventListener("pointermove", (e) => { pos = { x: e.clientX, y: e.clientY }; place(pos.x, pos.y); store.set("__abbCur", JSON.stringify(pos)); }, { capture: true, passive: true });
    addEventListener("pointerdown", (e) => {
      rip.style.left = e.clientX + "px"; rip.style.top = e.clientY + "px";
      rip.classList.remove("go"); void rip.offsetWidth; rip.classList.add("go");
      scale = 0.88; place(e.clientX, e.clientY);
    }, { capture: true, passive: true });
    addEventListener("pointerup", (e) => { scale = 1; place(e.clientX, e.clientY); }, { capture: true, passive: true });
    if (store.get("__abbCurHidden") === "1") cur.style.opacity = "0";
    try {
      const saved = JSON.parse(store.get("__abbCap") ?? "null");
      if (saved && saved.until > Date.now()) { capEl.innerHTML = saved.html; capEl.style.top = saved.top + "px"; capEl.classList.add("on"); }
    } catch { /* none */ }
    const keep = [style, spot, capEl, chip, rip, cur];
    const ensure = () => { for (const el of keep) if (!el.isConnected) document.documentElement.append(el); };
    new MutationObserver(ensure).observe(document.documentElement, { childList: true });
    setInterval(ensure, 250);
    window.__abb = {
      // The caption is placed by the recorder (it measures the page's text lines and picks a band that covers none): prep sets the
      // text and reports its size, show puts it at `top` and fades it in.
      captionPrep(html) {
        capEl.classList.remove("on"); capEl.classList.add("off"); capEl.innerHTML = html;
        const r = capEl.getBoundingClientRect();
        return { w: r.width, h: r.height };
      },
      captionShow(top, html, ttl) {
        store.set("__abbCap", JSON.stringify({ html, top, until: Date.now() + ttl }));
        capEl.style.top = top + "px"; capEl.classList.remove("off"); capEl.innerHTML = html; capEl.classList.add("on");
      },
      captionOff() { store.set("__abbCap", "null"); capEl.classList.add("off"); capEl.classList.remove("on"); },
      spot(rect, pad) {
        if (!rect) { spot.style.opacity = "0"; return; }
        Object.assign(spot.style, { left: rect.x - pad + "px", top: rect.y - pad + "px", width: rect.w + 2 * pad + "px", height: rect.h + 2 * pad + "px", opacity: "1" });
      },
      // Camera: the window of the page shown has its top-left at (x0, y0) and is W/s by H/s; transform-origin O = x0 * s / (s - 1).
      zoom(s, x0, y0, ms) {
        document.body.style.transitionDuration = ms + "ms";
        if (s <= 1) { document.body.style.transform = ""; return; }
        const ox = Math.max(0, Math.min(W, (x0 * s) / (s - 1)));
        const oy = Math.max(0, Math.min(H, (y0 * s) / (s - 1)));
        document.body.style.transformOrigin = `${ox}px ${oy}px`;
        document.body.style.transform = `scale(${s})`;
      },
      // Every line of visible text of the page as a viewport rect (transforms included): what the camera must not cut and the
      // caption must not cover.
      lines() {
        const out = [];
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
          const text = n.nodeValue.trim();
          const el = n.parentElement;
          if (!text || !el || /^(SCRIPT|STYLE|NOSCRIPT)$/.test(el.tagName)) continue;
          if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
          const range = document.createRange();
          range.selectNodeContents(n);
          for (const q of range.getClientRects()) if (q.width > 1 && q.height > 1) out.push({ x: q.x, y: q.y, w: q.width, h: q.height, t: text.slice(0, 40) });
        }
        return out;
      },
      chip(label, ms) { chip.textContent = label; chip.classList.add("on"); setTimeout(() => chip.classList.remove("on"), ms); },
      cursor(show) { store.set("__abbCurHidden", show ? "0" : "1"); cur.style.opacity = show ? "1" : "0"; },
      captionInfo() {
        const r = capEl.getBoundingClientRect();
        return { opacity: Number(getComputedStyle(capEl).opacity), on: capEl.classList.contains("on"), rect: { x: r.x, y: r.y, w: r.width, h: r.height } };
      },
      overlayState() { return { cur: !!document.getElementById("abb-cur"), cap: !!document.getElementById("abb-cap"), htmlChildren: document.documentElement.childElementCount }; },
    };
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
}

// ---------------------------------------------------------------------------------------------------------------------
// Recording
// ---------------------------------------------------------------------------------------------------------------------
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const takeDir = path.join(outRoot, `${cut.name}-${dry ? "dry-" : ""}${stamp}`);
const framesDir = path.join(takeDir, "frames");
const hardTimeout = Number(process.env.ABB_VIDEO_TIMEOUT_MS ?? (totalPlanned + 180) * 1000);

const resolveToken = (v) => (typeof v === "string" && v.startsWith("$") ? data[v.slice(1)] : v);
const nameOf = (n) => {
  const v = resolveToken(n);
  return typeof v === "string" && v.startsWith("re:") ? new RegExp(v.slice(3), "i") : v;
};

let page;
let T0 = 0;
// The film clock: wall time since the first frame minus the time cut out by hops (the frames of those intervals are dropped, so
// film time and playback time are the same thing). `holds` are the cut-out intervals in raw performance.now() milliseconds.
let skipMs = 0;
const holds = [];
let frozenAt = null; // film seconds, set while a hop is in progress: the film clock does not run during the cut
const clock = () => frozenAt ?? (performance.now() - T0 - skipMs) / 1000;
async function sleepUntil(sec) {
  // polled, not slept once: a sleep computed before a hop would count the hop's transit as film time
  for (let rem = sec - clock(); rem > 0; rem = sec - clock()) await sleep(Math.min(rem * 1000, 40));
}

/** A Playwright locator for a selector spec (first match; `has` filters by text, `row` selects a timeline row). */
function locate(s) {
  if (s.testid) return page.getByTestId(s.testid).first();
  if (s.row) return page.locator(".timeline .tl-row").filter({ hasText: s.row }).first();
  if (s.css) {
    let l = page.locator(s.css);
    if (s.has) l = l.filter({ hasText: s.has });
    return l.first();
  }
  if (s.text) return page.getByText(resolveToken(s.text)).first();
  return page.getByRole(s.role, { name: nameOf(s.name), exact: s.exact }).first();
}

const rectOf = (r) => (r ? { x: r.x, y: r.y, width: r.width, height: r.height } : null);

/** The bounding box of a spec in viewport CSS px (the camera transform is already included). */
async function boxOf(s) {
  if (s.union) {
    const boxes = [];
    for (const u of s.union) { const b = await boxOf(u); if (b) boxes.push(b); }
    if (!boxes.length) return null;
    const x0 = Math.min(...boxes.map((b) => b.x)), y0 = Math.min(...boxes.map((b) => b.y));
    return { x: x0, y: y0, width: Math.max(...boxes.map((b) => b.x + b.width)) - x0, height: Math.max(...boxes.map((b) => b.y + b.height)) - y0 };
  }
  if (s.widest) {
    return page.evaluate(({ css, row }) => {
      const bars = [...document.querySelectorAll(css)];
      if (!bars.length) return null;
      const bar = bars.sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
      const r = (row ? bar.closest('[role="row"]') ?? bar : bar).getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height };
    }, { css: s.css, row: !!s.row });
  }
  if (s.heading) {
    return page.evaluate((text) => {
      const h = [...document.querySelectorAll("h1,h2,h3,h4")].find((e) => e.textContent?.includes(text));
      const r = h?.parentElement?.getBoundingClientRect();
      return r ? { x: r.x, y: r.y, width: r.width, height: r.height } : null;
    }, s.heading);
  }
  if (s.parent) {
    await locate(s).waitFor({ state: "visible", timeout: 15000 });
    return page.evaluate(({ css, has }) => {
      const h = [...document.querySelectorAll(css)].find((e) => e.textContent?.includes(has));
      const r = h?.parentElement?.getBoundingClientRect();
      return r ? { x: r.x, y: r.y, width: r.width, height: r.height } : null;
    }, { css: s.css, has: s.has });
  }
  const el = locate(s);
  await el.waitFor({ state: "visible", timeout: 15000 });
  const r = rectOf(await el.boundingBox());
  if (r && s.clipW) r.width = Math.min(r.width, s.clipW); // the text of a full-width row, not its whole width
  return r;
}

let cur = { x: 0, y: 0 };
const view = { w: cut.w, h: cut.h };
const log = { overlay: null, console: [], warnings: [], cues: [], beats: [], scrape: {}, textHits: [], zoomStates: [], zoomMarks: [], hops: [], faultInView: [] };
let cursorShown = true;
const setCursor = async (show) => { cursorShown = show; await evalOverlay((v) => window.__abb?.cursor(v), show); };
// What the story is about right now (a spotlight, a zoom's target, the thing the cursor points at), in viewport px: a caption
// must never sit on it. The camera scale is kept for the caption measurements.
let focusNow = [];
let cameraScale = 1;
let layoutLines = []; // the page's lines at normal scale, kept while the camera is zoomed: a caption must also clear them for when it pulls back
const asRect = (b) => ({ x: b.x, y: b.y, w: b.width, h: b.height });
const readLines = () => evalOverlay(() => window.__abb?.lines());

async function travelTo(target) {
  const dist = Math.hypot(target.x - cur.x, target.y - cur.y);
  const ms = travelMs(dist);
  const from = { ...cur };
  const t0 = performance.now();
  for (;;) {
    const p = Math.min(1, (performance.now() - t0) / ms);
    const pt = travelPoint(from, target, p);
    await page.mouse.move(pt.x, pt.y);
    if (p >= 1) break;
    await sleep(1000 / 60);
  }
  cur = { x: target.x, y: target.y };
  await sleep(120); // arrival settle
}

const evalOverlay = async (fn, arg) => {
  for (let i = 0; i < 6; i++) {
    try {
      return await page.evaluate(fn, arg);
    } catch (e) {
      if (i === 5) throw e;
      await sleep(100); // the document is changing: try again on the new one
    }
  }
};

async function act(a, ctx) {
  const timeout = a.optional ? 3000 : 15000;
  const traceAt = performance.now();
  try {
    return await actInner(a, ctx, timeout);
  } finally {
    if (process.env.ABB_VIDEO_TRACE) console.log(`  trace beat ${ctx.id} ${a.do} at ${clock().toFixed(2)} s took ${((performance.now() - traceAt) / 1000).toFixed(2)} s wall`);
  }
}

async function actInner(a, ctx, timeout) {
  try {
    switch (a.do) {
      case "goto":
        await page.goto(new URL(a.path.replace("{ws}", workspace), base).toString(), { waitUntil: "load", timeout: 20000 });
        break;
      case "waitFor":
        await locate(a.for).waitFor({ state: "visible", timeout });
        break;
      case "wait":
      case "dwell":
        await sleep(a.ms);
        break;
      case "scroll":
        await locate(a.to).scrollIntoViewIfNeeded({ timeout });
        {
          const b = await boxOf(a.to);
          log.faultInView.push({ beat: ctx.id, inView: !!b && b.y >= 0 && b.y + b.height <= view.h });
        }
        break;
      case "travel": {
        const b = await boxOf(a.to);
        if (!b) throw new Error("no box for " + JSON.stringify(a.to));
        if (a.focus !== false) focusNow = [asRect(b)];
        if (!cursorShown) { await setCursor(true); await sleep(60); }
        await travelTo({ x: b.x + b.width * (a.fx ?? 0.5) + (a.dx ?? 0), y: b.y + b.height * (a.fy ?? 0.5) + (a.dy ?? 0) });
        break;
      }
      case "click":
        await page.mouse.down();
        await sleep(90);
        await page.mouse.up();
        break;
      case "park": {
        const [fx, fy] = cut.parks[a.name];
        await travelTo({ x: Math.round(cut.w * fx), y: Math.round(cut.h * fy) });
        break;
      }
      case "press":
        if (a.chip) await evalOverlay(([l, ms]) => window.__abb?.chip(l, ms), [a.chip, 600]);
        await page.keyboard.press(a.key);
        break;
      case "focus":
        await page.locator(a.css).first().focus();
        break;
      case "zoom": {
        // Frame the target with the largest zoom (at most a.level, at least a.min) and the window position at which the edges of
        // the picture cut no line of text (fitZoom, pure and tested); the state reached is measured and logged for the QA.
        const b = await boxOf(a.on);
        if (!b) throw new Error("no box for zoom target");
        const lines = await readLines();
        layoutLines = lines;
        const fit = fitZoom({ lines, focus: asRect(b), s0: a.level, sMin: a.min ?? 1.3, view });
        if (!fit) throw new Error(`the zoom target does not fit a window of at least ${a.min ?? 1.3}x`);
        const ms = a.instant ? 0 : 700;
        await setCursor(false);
        const zoomAt = clock();
        await evalOverlay(([s, x0, y0, t]) => window.__abb?.zoom(s, x0, y0, t), [fit.s, fit.x0, fit.y0, ms]);
        log.zoomMarks.push({ beat: ctx.id, level: fit.s, in: +zoomAt.toFixed(3), inEnd: +(zoomAt + ms / 1000).toFixed(3), out: null, outEnd: null });
        await sleep(ms + 80);
        cameraScale = fit.s;
        const after = await readLines();
        const clippedNow = clippedLines(after, view);
        const nb = await boxOf(a.on);
        focusNow = nb ? [asRect(nb)] : [];
        log.zoomStates.push({
          beat: ctx.id, at: T0 ? +clock().toFixed(2) : 0, asked: a.level, scale: fit.s, window: { x0: Math.round(fit.x0), y0: Math.round(fit.y0), x1: Math.round(fit.x0 + view.w / fit.s), y1: Math.round(fit.y0 + view.h / fit.s) },
          lines: after.length, clipped: clippedNow.map((c) => c.t), predictedClipped: fit.clipped,
        });
        break;
      }
      case "zoomOut": {
        const outAt = clock();
        await evalOverlay(([ms]) => window.__abb?.zoom(1, 0, 0, ms), [600]);
        await sleep(600);
        const open = [...log.zoomMarks].reverse().find((m) => m.out === null);
        if (open) { open.out = +outAt.toFixed(3); open.outEnd = +(outAt + 0.6).toFixed(3); }
        cameraScale = 1;
        layoutLines = [];
        focusNow = [];
        break; // the cursor stays hidden until a beat travels it (a camera move is never shared with the cursor)
      }
      case "spot": {
        const specs = Array.isArray(a.on) ? a.on : [a.on];
        const boxes = [];
        for (const s of specs) {
          const b = await boxOf(s);
          if (b) boxes.push(b);
        }
        if (!boxes.length) throw new Error("no box for spotlight");
        const x0 = Math.min(...boxes.map((b) => b.x)), y0 = Math.min(...boxes.map((b) => b.y));
        const x1 = Math.max(...boxes.map((b) => b.x + b.width)), y1 = Math.max(...boxes.map((b) => b.y + b.height));
        const grow = specs.length > 1 ? 6 : 0; // the rule above a fault row belongs to the row
        const pad = a.pad ?? 8;
        await evalOverlay(([r, p]) => window.__abb?.spot(r, p), [{ x: x0, y: y0 - grow, w: x1 - x0, h: y1 - y0 + grow }, pad]);
        focusNow = [{ x: x0 - pad, y: y0 - grow - pad, w: x1 - x0 + 2 * pad, h: y1 - y0 + grow + 2 * pad }];
        break;
      }
      case "spotOff":
        await evalOverlay(() => window.__abb?.spot(null, 0));
        focusNow = [];
        break;
      case "hop": {
        // A section change as a hard cut: the cursor fades, then the whole transit (clicks, loading, settling) is cut out of the
        // film. The first frame of the new page follows the last frame of the old one at film time `cutAt`.
        await setCursor(false);
        await sleep(HOP_LEAD * 1000);
        focusNow = [];
        const cutAt = clock();
        frozenAt = cutAt;
        const h0 = performance.now();
        for (const step of a.via) await locate(step).click({ timeout });
        await locate(a.waitFor).waitFor({ state: "visible", timeout });
        const [fx, fy] = cut.parks[a.park];
        cur = { x: Math.round(cut.w * fx), y: Math.round(cut.h * fy) };
        await page.mouse.move(cur.x, cur.y);
        await sleep(700); // the page's own entry motion (a drawing waterfall, a sliding panel) is over before the cut shows it
        const h1 = performance.now();
        holds.push({ from: h0, to: h1 });
        skipMs += h1 - h0;
        frozenAt = null;
        log.hops.push({ beat: ctx.id, at: +cutAt.toFixed(3), wallSeconds: +((h1 - h0) / 1000).toFixed(2) });
        break;
      }
      case "cursor":
        await setCursor(a.show);
        break;
      case "scrape": {
        log.scrape[a.key] = (await locate(a.of).innerText({ timeout })).replace(/\s+/g, " ").trim();
        break;
      }
      default:
        throw new Error(`unknown act ${a.do}`);
    }
  } catch (e) {
    if (a.optional) {
      log.warnings.push(`optional act ${a.do} skipped: ${String(e.message).split("\n")[0]}`);
      return;
    }
    throw new Error(`beat ${ctx.id}, act ${a.do}: ${String(e.message).split("\n")[0]}`);
  }
}

/** What a caption box covers right now: lines of text and the story's focus rects (measured in the page, read back by the QA). */
async function measureCaption() {
  const info = await evalOverlay(() => window.__abb?.captionInfo());
  const lines = await readLines();
  return { ...info, camera: cameraScale, overlap: captionOverlap({ x: info.rect.x, y: info.rect.y, w: info.rect.w, h: info.rect.h }, lines, focusNow, view) };
}

async function runCue(beatStart, cue, beatId) {
  if (captionsOff) {
    // The caption is a plate composed afterwards; the cue is only a time here (the page is never touched).
    await sleepUntil(beatStart + cue.start);
    const start = clock();
    await sleepUntil(beatStart + cue.end);
    log.cues.push({ beat: beatId, bank: cue.bank ?? null, text: cue.text, start, end: clock() });
    return;
  }
  await sleepUntil(beatStart + cue.start);
  const ttl = Math.round((cue.end - cue.start) * 1000) + 500; // survives a full page load; the explicit off below ends it
  const html = captionHtml(cue.text);
  const size = await evalOverlay((h) => window.__abb?.captionPrep(h), html);
  const seen = [...(await readLines()), ...layoutLines];
  const place = cue.top != null // a fixed place for a page with no free band at the bottom; measured and checked like any other
    ? { top: cue.top, ...captionOverlap({ x: (view.w - size.w) / 2, y: cue.top, w: size.w, h: size.h }, seen, focusNow, view) }
    : placeCaption({ w: size.w, h: size.h, lines: seen, focus: focusNow, view });
  await evalOverlay(([top, h, t]) => window.__abb?.captionShow(top, h, t), [place.top, html, ttl]);
  const start = clock();
  await sleepUntil(beatStart + (cue.start + cue.end) / 2);
  const mid = await measureCaption(); // measured in the page, read back by video-qa.mjs
  await sleepUntil(beatStart + cue.end - 0.12);
  const endM = await measureCaption();
  await sleepUntil(beatStart + cue.end);
  await evalOverlay(() => window.__abb?.captionOff());
  log.cues.push({ beat: beatId, bank: cue.bank ?? null, text: cue.text, start, end: clock(), placed: place, mid, endMeasure: { overlap: endM.overlap, camera: endM.camera } });
}

const FORBIDDEN_TEXT = [/Agent Black Box/, /sk-[A-Za-z0-9]{8,}/, /AKIA[A-Z0-9]{8,}/, /@gmail/i, /anthropic/i, /openai/i, /claude/i, /\bgpt/i, /--live/, /BEGIN [A-Z ]*PRIVATE KEY/];
async function scanText(beatId) {
  let text = "";
  try {
    text = await page.evaluate(() => document.body.innerText);
  } catch {
    return;
  }
  mkdirSync(path.join(takeDir, "pagetext"), { recursive: true });
  writeFileSync(path.join(takeDir, "pagetext", `beat-${beatId}.txt`), text.slice(0, 20000));
  const hits = FORBIDDEN_TEXT.filter((r) => r.test(text)).map(String);
  const emails = [...new Set(text.match(/[\w.+-]+@[\w-]+\.[\w.-]+/g) ?? [])].filter((e) => e !== "owner@local.test");
  if (emails.length) hits.push(`email: ${emails.join(", ")}`);
  if (hits.length) log.textHits.push({ beat: beatId, hits });
}

async function startScreencast() {
  const cdp = await page.context().newCDPSession(page);
  const frames = [];
  let n = 0;
  let lastT = -Infinity;
  let pending = null;
  let timer = null;
  const minGap = 1000 / 30 - 2;
  const write = (f) => {
    const file = path.join(framesDir, `f${String(n++).padStart(5, "0")}.jpg`);
    writeFileSync(file, f.buf);
    frames.push({ file, t: f.t });
    lastT = f.t;
  };
  cdp.on("Page.screencastFrame", async ({ data, sessionId }) => {
    const f = { buf: Buffer.from(data, "base64"), t: performance.now() };
    if (f.t - lastT >= minGap) {
      pending = null;
      write(f);
    } else {
      pending = f; // the newest frame is never lost: it is written when the gap is over
      timer ??= setTimeout(() => { timer = null; if (pending) { const p = pending; pending = null; write(p); } }, minGap);
    }
    await cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: cut.w * DSF, maxHeight: cut.h * DSF, everyNthFrame: 1 });
  return {
    frames,
    async stop() {
      await sleep(80);
      if (pending) { write(pending); pending = null; }
      await cdp.send("Page.stopScreencast").catch(() => {});
      await cdp.detach().catch(() => {});
    },
  };
}

/**
 * Off camera: visit every page the cut opens once, by clicking through the app (no reload), so its code and data are cached and the
 * recording shows pages, not "Loading run..." states. This is what a person who has used the app for a minute would see too.
 */
async function warmUp() {
  const click = (spec) => locate(spec).click({ timeout: 15000 });
  const step = async (name, fn) => {
    try {
      await fn();
    } catch (e) {
      log.warnings.push(`warm-up ${name} failed: ${String(e.message).split("\n")[0]}`);
    }
  };
  await step("failed run", async () => {
    await click(FAILED_LINK);
    await locate(TIMELINE_TAB).waitFor({ timeout: 15000 });
    await locate({ testid: "headline" }).waitFor({ timeout: 15000 });
    await click({ role: "tab", name: "Findings", exact: true });
    await locate({ role: "button", name: "Show evidence" }).waitFor({ timeout: 15000 });
    await click(NAV("Runs"));
    await locate(RUNS_HEADING).waitFor({ timeout: 15000 });
  });
  await step("multi-agent run", async () => {
    await click({ role: "link", name: "$multiRun" });
    await click({ role: "tab", name: "Waterfall", exact: true });
    await locate({ testid: "waterfall" }).waitFor({ timeout: 15000 });
    await click(NAV("Runs"));
    await locate(RUNS_HEADING).waitFor({ timeout: 15000 });
  });
  await step("review", async () => {
    await click({ role: "link", name: "re:^Review" });
    await locate({ testid: "approval-row" }).waitFor({ timeout: 15000 });
    await click({ role: "link", name: "$approval" });
    await locate({ role: "button", name: "Approve", exact: true }).waitFor({ timeout: 15000 });
    await click({ role: "tab", name: "Security findings", exact: true });
    await locate({ testid: "security-row" }).waitFor({ timeout: 15000 });
  });
  await step("analytics", async () => {
    await click(NAV("Analytics"));
    await locate({ testid: "report-headline" }).waitFor({ timeout: 15000 });
  });
  await step("back to runs", async () => {
    await click(NAV("Runs"));
    await locate(RUNS_HEADING).waitFor({ timeout: 15000 });
    await locate({ testid: "week-sentence" }).waitFor({ timeout: 15000 }).catch(() => {});
  });
  // The film opens on the Runs list (beat 1): the warm-up ends there.
}

async function main() {
  mkdirSync(framesDir, { recursive: true });
  const mark = readFileSync(path.join(root, "brand/pulse-mark.svg"), "utf8").replace(/ width="24" height="24"/, "");
  const cfg = {
    w: cut.w, h: cut.h, capSize: cut.cap.size, capLine: cut.cap.line, camera: CAMERA_EASE_CSS, zoomIn: 700, mark,
    start: { x: Math.round(cut.w * cut.parks[cut.start][0]), y: Math.round(cut.h * cut.parks[cut.start][1]) },
  };
  const browser = await chromium.launch({
    channel: "chrome",
    headless: !flag("headed"),
    args: [`--force-device-scale-factor=${DSF}`, "--hide-scrollbars", "--disable-lcd-text"],
  });
  const context = await browser.newContext({
    viewport: { width: cut.w, height: cut.h },
    deviceScaleFactor: DSF,
    colorScheme: "dark",
    reducedMotion: "no-preference", // the fault line draws and the inspector slides: both are part of the story
    bypassCSP: true,
  });
  await context.addCookies([{ name: "abb_session", value: sessionToken, url: base, httpOnly: true, sameSite: "Lax" }]);
  await context.addInitScript(`(${overlay.toString()})(${JSON.stringify(cfg)})`);
  page = await context.newPage();
  page.on("pageerror", (e) => log.console.push(`pageerror: ${e.message.slice(0, 200)}`));
  page.on("console", (m) => { if (m.type() === "error") log.console.push(`console.error: ${m.text().slice(0, 200)}`); });

  await page.goto(new URL(`/w/${workspace}/projects/all/runs`, base).toString(), { waitUntil: "load", timeout: 30000 });
  await page.getByRole("heading", RUNS_HEADING).waitFor({ timeout: 20000 });
  await page.evaluate(() => document.fonts.ready);
  await warmUp();
  await sleep(400);
  cur = { x: cfg.start.x, y: cfg.start.y };
  await page.mouse.move(cur.x, cur.y);
  log.overlay = await page.evaluate(() => window.__abb?.overlayState());
  if (!log.overlay?.cur || !log.overlay?.cap) fail(`the overlay did not mount: ${JSON.stringify(log.overlay)}`);
  await sleep(150);

  const rec = await startScreencast();
  T0 = performance.now();
  const startMs = T0;
  for (const planned of plan) {
    const beat = beats.find((b) => b.id === planned.id);
    await sleepUntil(planned.start);
    const bs = clock();
    const entry = { id: planned.id, plannedStart: planned.start, start: bs, plannedLen: planned.len };
    const ctx = { id: planned.id };
    const cues = planned.cues.map((c) => runCue(bs, c, planned.id));
    try {
      for (const a of beat.acts) {
        if (a.at !== undefined) await sleepUntil(bs + a.at);
        await act(a, ctx);
      }
      await sleepUntil(bs + planned.len);
      await Promise.all(cues);
    } catch (e) {
      await page.screenshot({ path: path.join(takeDir, `failed-beat-${planned.id}.png`) }).catch(() => {});
      await rec.stop();
      throw e;
    }
    entry.end = clock();
    entry.captionOnAtEnd = captionsOff ? false : !!(await evalOverlay(() => window.__abb?.captionInfo().on));
    log.beats.push(entry);
    await scanText(planned.id);
    console.log(`beat ${String(planned.id).padEnd(2)} ${bs.toFixed(2)} - ${entry.end.toFixed(2)} s (planned ${planned.start.toFixed(2)} + ${planned.len.toFixed(2)})`);
  }
  const endMs = performance.now();
  await rec.stop();
  await context.close();
  await browser.close();

  // ---- outputs ----------------------------------------------------------------------------------------------------------
  const filmFrames = applyHolds(rec.frames, holds); // the hops' transits are cut out: film time is playback time
  const filmEnd = endMs - skipMs;
  const seconds = (filmEnd - startMs) / 1000;
  for (const b of log.beats) b.maxFrameGapMs = Math.round(maxFrameGap(filmFrames, startMs + b.start * 1000, startMs + b.end * 1000));
  writeFileSync(path.join(takeDir, "frames.ffconcat"), buildConcat(filmFrames, startMs, filmEnd));
  writeFileSync(path.join(takeDir, "demo.vtt"), buildVtt(log.cues));
  writeFileSync(path.join(takeDir, "demo.srt"), buildSrt(log.cues));
  parseVtt(readFileSync(path.join(takeDir, "demo.vtt"), "utf8"));
  const fill = (s) => s.replace(/\{(\w+)\}/g, (_, k) => (k in log.scrape ? log.scrape[k] : data[k] ?? `{${k}}`));
  const transcript = [`# Transcript: Agent BlackBox demo (${cut.name} cut, ${seconds.toFixed(0)} s, no audio)`, "",
    "A text alternative to the silent video, in the order things appear. Captions are quoted as they are burned in.", ""];
  for (const p of plan) {
    transcript.push(`${p.id}. ${fill(beats.find((b) => b.id === p.id).tx)}`);
    for (const c of log.cues.filter((x) => String(x.beat) === String(p.id))) transcript.push(`   Caption: "${plain(c.text)}"`);
    transcript.push("");
  }
  writeFileSync(path.join(takeDir, "transcript.md"), transcript.join("\n"));
  const seedHash = createHash("sha256")
    .update(["scripts/demo.sh", "examples/python/scripted_agent.py", "examples/python/ops_agent.py", "examples/coding-agent/policies/demo.yaml"].map((f) => readFileSync(path.join(root, f))).join("\n"))
    .digest("hex").slice(0, 16);
  let commit = "unknown";
  try { commit = execFileSync("git", ["-C", root, "rev-parse", "--short", "HEAD"]).toString().trim(); } catch { /* not a checkout */ }
  const meta = {
    cut: cut.name, film: cinematic ? "cinematic" : "preview", captionsInPage: !captionsOff, dryRun: dry, profile, releaseClaims, appCommit: commit, demoSeedHash: seedHash,
    viewport: { w: cut.w, h: cut.h, deviceScaleFactor: DSF }, frames: filmFrames.length, seconds: +seconds.toFixed(3), plannedSeconds: +totalPlanned.toFixed(3),
    beats: log.beats, cues: log.cues.map((c) => ({ ...c, start: +c.start.toFixed(3), end: +c.end.toFixed(3) })),
    zoomStates: log.zoomStates, zoomMarks: log.zoomMarks, faultInView: log.faultInView, hops: log.hops, wordmark: "Agent BlackBox",
    scrape: log.scrape, overlay: log.overlay, consoleErrors: log.console, warnings: log.warnings, textHits: log.textHits,
  };
  writeFileSync(path.join(takeDir, "meta.json"), JSON.stringify(meta, null, 2));
  console.log(`overlay: ${JSON.stringify(log.overlay)}; console errors: ${log.console.length}; warnings: ${log.warnings.length}; text hits: ${log.textHits.length}`);
  console.log(`wrote ${path.relative(root, takeDir)} (${filmFrames.length} frames, ${seconds.toFixed(1)} s, ${holds.length} cut(s) of ${(skipMs / 1000).toFixed(1)} s in all)`);
  if (log.textHits.length) {
    console.error(`record-demo: forbidden text on screen: ${JSON.stringify(log.textHits)}`);
    process.exit(4);
  }
  // A loaded machine stalls the browser for seconds (a travel that took 6 s, a gap in the screencast): the take is then slower than
  // its table, and the captions would sit on the wrong picture. Exit 5 tells the caller to record again.
  const late = log.beats.filter((b) => b.end - b.start > b.plannedLen + 0.4);
  if (late.length) {
    console.error(`record-demo: stalled take (beats ${late.map((b) => `${b.id}: ${(b.end - b.start).toFixed(1)} s vs ${b.plannedLen.toFixed(1)} s`).join(", ")}); record again`);
    process.exit(5);
  }
}

const timer = setTimeout(() => {
  console.error(`record-demo: hard timeout after ${hardTimeout} ms`);
  process.exit(3);
}, hardTimeout);
main()
  .then(() => { clearTimeout(timer); process.exit(0); })
  .catch((e) => { clearTimeout(timer); console.error(`record-demo: ${e.message}`); process.exit(1); });
