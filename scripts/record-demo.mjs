#!/usr/bin/env node
/*
 * record-demo.mjs (v2): records the Agent BlackBox demo video from a RUNNING, demo-seeded stack.
 * Direction: docs/design/launch/video-brief.md. Captions: docs/design/launch/positioning-brief.md (caption bank, section 7).
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
 *   node scripts/record-demo.mjs                              # main cut, about 85 s
 *   node scripts/record-demo.mjs --cut=teaser                 # 18 s loop for the landing hero and the README
 *   node scripts/record-demo.mjs --cut=social                 # optional 1080x1350 take, beats 1-8 (not recorded by the project yet)
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
 * Flags: --cut=main|teaser|social, --plan, --dry-run, --headed, --profile=<demo|fixtures>.
 * Nothing is uploaded; the script only talks to ABB_VIDEO_BASE_URL. It never passes or shows `--live`.
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  CAMERA_EASE_CSS, MAX_CAPTION_WORDS, MIN_CAPTION_GAP, buildConcat, buildSrt, buildVtt, captionHtml, maxFrameGap,
  minCaptionSeconds, parseVtt, plain, planCut, travelMs, travelPoint, wordCount,
} from "./record-demo-lib.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// ---------------------------------------------------------------------------------------------------------------------
// Names read from the seeded demo (scripts/demo.sh). Run pages are found by visible name, never by id.
// ---------------------------------------------------------------------------------------------------------------------
const DATA = {
  demo: { workspace: "local", failedRun: "Answer support ticket T-1047", multiRun: "Ship feature: multi-agent", approval: "github.push" },
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

const MAIN = [
  { // 1. The Runs list, still. The only colour is the red Failed words.
    id: 1, len: 3.4, tx: "The Runs list: twelve runs of scripted agents, three of them marked Failed.",
    caps: [cap(1, 0.3)], acts: [],
  },
  { // 2. A failed run: the cursor travels to it and opens it.
    id: 2, len: 4.0, tx: "The cursor opens the failed run {failedRun}.",
    caps: [cap(3, 0.2)],
    acts: [
      { at: 0.0, do: "scroll", to: FAILED_LINK },
      { at: 0.2, do: "travel", to: FAILED_LINK },
      { do: "dwell", ms: 400 },
      { at: 2.3, do: "click" },
      { do: "waitFor", for: TIMELINE_TAB },
      { do: "wait", ms: 450 }, // the fault line's 280 ms draw happens on arrival and belongs in the recording
      { do: "park", name: "run" },
    ],
  },
  { // 3. The sentence, zoomed to fill the frame once.
    id: 3, len: 5.4, tx: "The run page says in one sentence where it broke: {headline}",
    caps: [cap(4, 1.0)],
    acts: [
      { at: 0.0, do: "scrape", key: "headline", of: { testid: "headline" } },
      { at: 0.7, do: "zoom", on: { testid: "headline" }, level: 1.6 },
      { at: 4.4, do: "zoomOut" },
    ],
  },
  { // 4. What it cost after the break.
    id: 4, len: 3.6, tx: "Under it, the detail line: {detail}",
    caps: [cap(6, 0.3)],
    acts: [
      { at: 0.0, do: "scrape", key: "detail", of: { testid: "headline-detail" } },
      { at: 0.2, do: "spot", on: { testid: "headline-detail" } },
      { at: 3.45, do: "spotOff" },
    ],
  },
  { // 5. The red line.
    id: 5, len: 3.3, tx: "On the timeline one red line crosses the rows and is labelled First error.",
    caps: [cap(7, 0.3)],
    acts: [
      { at: 0.0, do: "travel", to: FAULT_ROW, fx: 0.62, fy: 0.5, dx: 0, dy: 34 },
      { at: 0.3, do: "spot", on: [FAULT_ROW, { css: ".tl-fault-label" }] },
      { at: 3.15, do: "spotOff" },
    ],
  },
  { // 6. e opens the failed event in the inspector.
    id: 6, len: 7.2, tx: "Pressing e opens the failed tool call in the inspector. Under What went wrong it reads: {wrong}",
    caps: [cap(8, 0.2), cap(9, 3.5)],
    acts: [
      { at: 0.0, do: "focus", css: ".timeline" },
      { at: 0.3, do: "press", key: "e", chip: "e" },
      { do: "waitFor", for: { role: "dialog" } },
      { at: 1.2, do: "scrape", key: "wrong", of: { css: '[role="dialog"] .drawer-error p' } },
      { at: 1.3, do: "zoom", on: WHAT_WENT_WRONG, level: 1.4 },
      { at: 6.3, do: "zoomOut" },
      { at: 6.9, do: "press", key: "Escape" },
    ],
  },
  { // 7. Typed events: a model call with tokens and cost, then a retry.
    id: 7, len: 6.8, tx: "Back on the timeline, a model call row shows its tokens and cost, and a retry row says retry attempted.",
    caps: [cap(10, 0.3), cap(11, 3.9)],
    acts: [
      { at: 0.1, do: "travel", to: { row: "llm.request.completed" }, fx: 0.52, fy: 0.5 },
      { at: 0.5, do: "spot", on: { row: "llm.request.completed" }, pad: 6 },
      { at: 3.75, do: "travel", to: { row: "retry.attempted" }, fx: 0.62, fy: 0.5 },
      { at: 3.85, do: "spot", on: { row: "retry.attempted" }, pad: 6 },
      { at: 6.65, do: "spotOff" },
    ],
  },
  { // 8. Findings and their evidence.
    id: 8, len: 7.0, tx: "On the Findings tab: two Critical findings and one Warning. Show evidence jumps to the exact rows.",
    caps: [cap(12, 0.5), cap(13, 4.0)],
    acts: [
      { at: 0.1, do: "travel", to: { role: "tab", name: "Findings", exact: true } },
      { do: "dwell", ms: 300 },
      { do: "click" },
      { at: 2.4, do: "travel", to: { role: "button", name: "Show evidence" } },
      { do: "dwell", ms: 400 },
      { at: 3.85, do: "click" },
      { do: "wait", ms: 500 },
      { do: "park", name: "run" },
    ],
  },
  { // 9. Five agents in one waterfall.
    id: 9, len: 9.8, tx: "The multi-agent run {multiRun}: the Waterfall in lanes shows five agents, with the critical path as the thick edge.",
    caps: [cap(16, 3.3), cap(17, 6.5)],
    acts: [
      { at: 0.1, do: "travel", to: NAV("Runs") },
      { do: "click" },
      { do: "waitFor", for: RUNS_HEADING },
      { do: "travel", to: { role: "link", name: "$multiRun" } },
      { do: "click" },
      { do: "waitFor", for: { role: "tab", name: "Waterfall", exact: true } },
      { do: "travel", to: { role: "tab", name: "Waterfall", exact: true } },
      { do: "click" },
      { do: "waitFor", for: { testid: "waterfall" } },
      { at: 4.6, do: "travel", to: { role: "button", name: "Lanes", exact: true } },
      { do: "click" },
      { at: 6.6, do: "travel", to: { css: ".wf-bar[data-critical]", widest: true }, fx: 0.15, fy: 0.5 },
      { at: 6.8, do: "spot", on: { css: ".wf-bar[data-critical]", widest: true, row: true } },
      { at: 7.6, do: "travel", to: { css: ".wf-bar[data-critical]", widest: true }, fx: 0.85, fy: 0.5 },
      { at: 9.5, do: "spotOff" },
    ],
  },
  { // 10. A person decides. Never clicks Approve or Deny.
    id: 10, len: 8.0, tx: "Review: one request is Waiting. Its panel shows the capability github.push, the resource, the arguments and Approve and Deny buttons, which are never clicked.",
    caps: [cap(22, 1.7), cap(23, 4.8)],
    acts: [
      { at: 0.1, do: "travel", to: { role: "link", name: "re:^Review" } },
      { do: "click" },
      { do: "waitFor", for: { testid: "approval-row" } },
      { do: "travel", to: { role: "link", name: "$approval" } },
      { do: "dwell", ms: 300 },
      { at: 3.1, do: "click" },
      { do: "waitFor", for: { role: "button", name: "Approve", exact: true } },
      { at: 3.5, do: "park", name: "review" },
      { at: 4.0, do: "spot", on: { css: "dl", has: "Capability" }, pad: 10 },
      { at: 7.4, do: "spotOff" },
    ],
  },
  { // 11. What the week cost.
    id: 11, len: 8.0, tx: "Analytics, Cost: {costLine} Below it, where the money was wasted: failed runs, retries and spend after errors.",
    caps: [cap(20, 1.4), cap(21, 4.3)],
    acts: [
      { at: 0.1, do: "travel", to: NAV("Analytics") },
      { do: "click" },
      { do: "waitFor", for: { testid: "report-headline" } },
      { do: "scrape", key: "costLine", of: { testid: "report-headline" } },
      { do: "park", name: "analytics" },
      { at: 4.2, do: "spot", on: { heading: "Where the money was wasted" }, pad: 10 },
      { at: 7.4, do: "spotOff" },
    ],
  },
  { // 12. Security findings, each with its rule.
    id: 12, len: 8.4, tx: "Review, Security findings: a destructive command, a risky git operation and a secret file read, each with a Rule line.",
    caps: [cap(14, 1.9), cap(15, 5.8)],
    acts: [
      { at: 0.1, do: "travel", to: { role: "link", name: "re:^Review" } },
      { do: "click" },
      { do: "waitFor", for: { role: "tab", name: "Security findings", exact: true } },
      { do: "travel", to: { role: "tab", name: "Security findings", exact: true } },
      { do: "click" },
      { do: "waitFor", for: { testid: "security-row" } },
      { at: 2.2, do: "travel", to: { testid: "security-row" }, fx: 0.55, fy: 0.5 },
      { at: 2.4, do: "spot", on: { testid: "security-row" }, pad: 6 },
      { at: 8.0, do: "spotOff" },
    ],
  },
  { // 13. Light follows the system.
    id: 13, len: 6.4, tx: "The failed run again, now in the light scheme: the app follows the system setting. The caption box stays dark.",
    caps: [cap(27, 3.1)],
    acts: [
      { at: 0.1, do: "travel", to: NAV("Runs") },
      { do: "click" },
      { do: "waitFor", for: RUNS_HEADING },
      { do: "travel", to: FAILED_LINK },
      { do: "click" },
      { do: "waitFor", for: TIMELINE_TAB },
      { do: "park", name: "run" },
      { at: 2.9, do: "scheme", value: "light" },
    ],
  },
  { // 14. The closing card. Pre-G2 text unless RELEASE_CLAIMS=1.
    id: 14, len: 4.6,
    tx: releaseClaims
      ? "The closing card: the wordmark on the canvas, the text 'Open source. Self-hosted. Apache-2.0.' and the repository address."
      : "The closing card: the wordmark on the canvas and the line 'Agent BlackBox: a flight recorder for AI agents.'",
    caps: releaseClaims ? [cap(26, 0.9), cap(28, 4.15, { d: 2.9 })] : [{ at: 0.7, bank: null, text: CLOSING }],
    acts: [
      { at: 0.0, do: "cursor", show: false },
      { at: 0.0, do: "fade", to: 1, ms: 300 },
      { at: 0.35, do: "card", show: true },
      { at: 0.35, do: "fade", to: 0, ms: 1 },
    ],
  },
];

const TEASER = [
  { id: "T1", len: 2.8, tx: "The Runs list; the cursor opens the failed run {failedRun}.", caps: [cap(3, 0.4)],
    acts: [
      { at: 0.05, do: "fade", to: 0, ms: 250 },
      { at: 0.5, do: "travel", to: FAILED_LINK },
      { do: "dwell", ms: 350 },
      { at: 2.4, do: "click" },
      { do: "waitFor", for: TIMELINE_TAB },
      { do: "wait", ms: 400 },
      { do: "park", name: "run" },
    ] },
  { id: "T2", len: 5.4, tx: "One sentence says where it broke: {headline}", caps: [cap(4, 1.0)],
    acts: [
      { at: 0.0, do: "scrape", key: "headline", of: { testid: "headline" } },
      { at: 0.6, do: "zoom", on: { testid: "headline" }, level: 1.6 },
      { at: 4.3, do: "zoomOut" },
    ] },
  { id: "T3", len: 3.4, tx: "The red line marks the first error.", caps: [cap(7, 0.3)],
    acts: [
      { at: 0.0, do: "travel", to: FAULT_ROW, fx: 0.62, fy: 0.5, dx: 0, dy: 34 },
      { at: 0.3, do: "spot", on: [FAULT_ROW, { css: ".tl-fault-label" }] },
      { at: 3.05, do: "spotOff" },
    ] },
  { id: "T4", len: 6.9, tx: "Press e: the failed tool call opens; What went wrong comes from the event itself.", caps: [cap(8, 0.2), cap(9, 3.5)],
    acts: [
      { at: 0.0, do: "focus", css: ".timeline" },
      { at: 0.3, do: "press", key: "e", chip: "e" },
      { do: "waitFor", for: { role: "dialog" } },
      { at: 1.3, do: "zoom", on: WHAT_WENT_WRONG, level: 1.4 },
      { at: 6.1, do: "zoomOut" },
      { at: 6.6, do: "fade", to: 1, ms: 250 },
    ] },
];

// Social cut: a separate take at 1080x1350 (the app's narrow layout), beats 1-8, captions 40/50 px, zoom at most 1.4.
const SOCIAL = MAIN.filter((b) => b.id <= 8).map((b) => ({
  ...b,
  acts: b.acts.map((a) => (a.do === "zoom" ? { ...a, level: Math.min(a.level, 1.4) } : a)),
}));

// Parking spots are fractions of the viewport (never over text being read; video-brief.md 3.3).
const PARKS = { runs: [0.625, 0.2], run: [0.78, 0.28], review: [0.36, 0.88], analytics: [0.78, 0.28] };
const CUTS = {
  main: { beats: MAIN, w: 1920, h: 1080, name: "main", cap: { size: 34, line: 44 }, parks: PARKS },
  teaser: { beats: TEASER, w: 1920, h: 1080, name: "teaser", cap: { size: 34, line: 44 }, parks: PARKS, startFaded: true },
  social: { beats: SOCIAL, w: 1080, h: 1350, name: "social", cap: { size: 40, line: 50 }, parks: { ...PARKS, runs: [0.6, 0.12], run: [0.7, 0.2] } },
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
const profile = opt("profile") ?? process.env.ABB_VIDEO_PROFILE ?? "demo";
if (!DATA[profile]) fail(`unknown profile "${profile}" (demo or fixtures)`);
const data = DATA[profile];
const workspace = process.env.ABB_VIDEO_WORKSPACE ?? data.workspace;
const beats = cut.beats.filter((b) => !dry || (typeof b.id === "number" && b.id <= 3));
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
  for (const p of planned) for (const c of p.cues) if (c.end > p.len - 0.2) problems.push(`beat ${p.id}: a caption runs to the end of the beat (across a cut)`);
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
      html { overflow: hidden !important; background: #101419; }
      body { transform-origin: 0 0; transition: transform ${cfg.zoomIn}ms ${cfg.camera}; }
      #abb-cur { position: fixed; left: 0; top: 0; width: 32px; height: 32px; pointer-events: none; z-index: 2147483640;
        transition: opacity 200ms linear; will-change: transform; }
      #abb-cur svg { display: block; width: 32px; height: 32px; overflow: visible; filter: drop-shadow(0 1px 1.5px rgba(0,0,0,.6)); }
      #abb-ripple { position: fixed; left: 0; top: 0; width: 44px; height: 44px; margin: -22px 0 0 -22px; border-radius: 50%;
        border: 2px solid rgba(255,255,255,.85); pointer-events: none; z-index: 2147483639; opacity: 0; transform: scale(.3); }
      #abb-ripple.go { animation: abb-rip 380ms cubic-bezier(.2,.7,.3,1) forwards; }
      @keyframes abb-rip { 0% { opacity: .9; transform: scale(.3) } 100% { opacity: 0; transform: scale(1.15) } }
      #abb-cap { position: fixed; left: 50%; bottom: 84px; transform: translateX(-50%); max-width: ${Math.round(W * 0.9)}px;
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
      #abb-chip { position: fixed; right: ${W - 1860}px; bottom: ${H - 996}px; width: 44px; height: 44px; border-radius: 4px;
        background: rgba(16,20,25,.92); border: 1px solid rgba(255,255,255,.14); color: #e2e5e8; display: flex; align-items: center;
        justify-content: center; font: 500 22px/1 "IBM Plex Mono", ui-monospace, monospace; pointer-events: none; z-index: 2147483644;
        opacity: 0; transition: opacity 120ms linear; }
      #abb-chip.on { opacity: 1; }
      #abb-fade { position: fixed; inset: 0; background: #101419; pointer-events: none; z-index: 2147483646; opacity: 0; }
      #abb-card { position: fixed; inset: 0; background: #101419; z-index: 2147483641; display: none; align-items: center; justify-content: center;
        flex-direction: column; gap: 28px; pointer-events: none; color: #e2e5e8; }
      #abb-card.on { display: flex; }
      #abb-card svg { width: 96px; height: 96px; color: #e2e5e8; }
      #abb-card .name { font: 600 56px/1.1 var(--abb-cap-font, "IBM Plex Sans", system-ui, sans-serif); letter-spacing: 0; }
    `;
    document.documentElement.append(style);
    const mk = (tag, id, html) => { const e = document.createElement(tag); e.id = id; if (html) e.innerHTML = html; return e; };
    const cur = mk("div", "abb-cur",
      '<svg viewBox="0 0 24 24"><path d="M5 3l14 9-6.2 1.2L16.5 20l-2.6 1.2-3.7-6.6L5 19z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>');
    const rip = mk("div", "abb-ripple");
    const capEl = mk("div", "abb-cap"); capEl.setAttribute("aria-hidden", "true");
    const spot = mk("div", "abb-spot");
    const chip = mk("div", "abb-chip");
    const fade = mk("div", "abb-fade");
    const card = mk("div", "abb-card", cfg.mark + '<div class="name">Agent BlackBox</div>');
    document.documentElement.append(card, spot, capEl, chip, rip, cur, fade);
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
      if (saved && saved.until > Date.now()) { capEl.innerHTML = saved.html; capEl.classList.add("on"); }
    } catch { /* none */ }
    if (store.get("__abbFade") === "1") fade.style.opacity = "1";
    if (store.get("__abbCard") === "1") card.classList.add("on");
    const keep = [style, card, spot, capEl, chip, rip, cur, fade];
    const ensure = () => { for (const el of keep) if (!el.isConnected) document.documentElement.append(el); };
    new MutationObserver(ensure).observe(document.documentElement, { childList: true });
    setInterval(ensure, 250);
    window.__abb = {
      caption(html, ttl) {
        store.set("__abbCap", JSON.stringify({ html, until: Date.now() + ttl }));
        capEl.classList.remove("off"); capEl.innerHTML = html; capEl.classList.add("on");
      },
      captionOff() { store.set("__abbCap", "null"); capEl.classList.add("off"); capEl.classList.remove("on"); },
      spot(rect, pad) {
        if (!rect) { spot.style.opacity = "0"; return; }
        Object.assign(spot.style, { left: rect.x - pad + "px", top: rect.y - pad + "px", width: rect.w + 2 * pad + "px", height: rect.h + 2 * pad + "px", opacity: "1" });
      },
      zoom(s, x, y, ms) {
        document.body.style.transitionDuration = ms + "ms";
        if (s <= 1) { document.body.style.transform = ""; return; }
        const ox = Math.max(0, Math.min(W, (x - W / (2 * s)) / (1 - 1 / s)));
        const oy = Math.max(0, Math.min(H, (y - H / (2 * s)) / (1 - 1 / s)));
        document.body.style.transformOrigin = `${ox}px ${oy}px`;
        document.body.style.transform = `scale(${s})`;
      },
      chip(label, ms) { chip.textContent = label; chip.classList.add("on"); setTimeout(() => chip.classList.remove("on"), ms); },
      cursor(show) { store.set("__abbCurHidden", show ? "0" : "1"); cur.style.opacity = show ? "1" : "0"; },
      fade(to, ms) { store.set("__abbFade", to ? "1" : "0"); fade.style.transition = `opacity ${ms}ms linear`; fade.style.opacity = String(to); },
      card(show) { store.set("__abbCard", show ? "1" : "0"); card.classList.toggle("on", show); },
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
const clock = () => (performance.now() - T0) / 1000;
async function sleepUntil(sec) {
  const ms = sec * 1000 - (performance.now() - T0);
  if (ms > 0) await sleep(ms);
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
  return rectOf(await el.boundingBox());
}

let cur = { x: 0, y: 0 };
const log = { overlay: null, console: [], warnings: [], cues: [], beats: [], scrape: {}, textHits: [] };

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
        break;
      case "travel": {
        const b = await boxOf(a.to);
        if (!b) throw new Error("no box for " + JSON.stringify(a.to));
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
        const b = await boxOf(a.on);
        if (!b) throw new Error("no box for zoom target");
        await evalOverlay(() => window.__abb?.cursor(false));
        await evalOverlay(([s, x, y, ms]) => window.__abb?.zoom(s, x, y, ms), [a.level, b.x + b.width / 2, b.y + b.height / 2, 700]);
        await sleep(700);
        break;
      }
      case "zoomOut":
        await evalOverlay(([ms]) => window.__abb?.zoom(1, 0, 0, ms), [600]);
        await sleep(600);
        await evalOverlay(() => window.__abb?.cursor(true));
        break;
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
        await evalOverlay(([r, pad]) => window.__abb?.spot(r, pad), [{ x: x0, y: y0 - grow, w: x1 - x0, h: y1 - y0 + grow }, a.pad ?? 8]);
        break;
      }
      case "spotOff":
        await evalOverlay(() => window.__abb?.spot(null, 0));
        break;
      case "scheme":
        await page.emulateMedia({ colorScheme: a.value });
        break;
      case "card":
        await evalOverlay((show) => window.__abb?.card(show), a.show);
        break;
      case "fade":
        await evalOverlay(([to, ms]) => window.__abb?.fade(to, ms), [a.to, a.ms]);
        break;
      case "cursor":
        await evalOverlay((show) => window.__abb?.cursor(show), a.show);
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

async function runCue(beatStart, cue, beatId) {
  await sleepUntil(beatStart + cue.start);
  const ttl = Math.round((cue.end - cue.start) * 1000) + 500; // survives a full page load; the explicit off below ends it
  await evalOverlay(([h, t]) => window.__abb?.caption(h, t), [captionHtml(cue.text), ttl]);
  const start = clock();
  await sleepUntil(beatStart + cue.end);
  await evalOverlay(() => window.__abb?.captionOff());
  log.cues.push({ beat: beatId, bank: cue.bank ?? null, text: cue.text, start, end: clock() });
}

const FORBIDDEN_TEXT = [/sk-[A-Za-z0-9]{8,}/, /AKIA[A-Z0-9]{8,}/, /@gmail/i, /anthropic/i, /openai/i, /claude/i, /\bgpt/i, /--live/, /BEGIN [A-Z ]*PRIVATE KEY/];
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

async function main() {
  mkdirSync(framesDir, { recursive: true });
  const mark = readFileSync(path.join(root, "brand/pulse-mark.svg"), "utf8").replace(/ width="24" height="24"/, "");
  const cfg = {
    w: cut.w, h: cut.h, capSize: cut.cap.size, capLine: cut.cap.line, camera: CAMERA_EASE_CSS, zoomIn: 700, mark,
    start: { x: Math.round(cut.w * cut.parks.runs[0]), y: Math.round(cut.h * cut.parks.runs[1]) },
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
  await sleep(400);
  cur = { x: cfg.start.x, y: cfg.start.y };
  await page.mouse.move(cur.x, cur.y);
  log.overlay = await page.evaluate(() => window.__abb?.overlayState());
  if (!log.overlay?.cur || !log.overlay?.cap) fail(`the overlay did not mount: ${JSON.stringify(log.overlay)}`);
  if (cut.startFaded) await page.evaluate(() => window.__abb.fade(1, 0));
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
    entry.maxFrameGapMs = Math.round(maxFrameGap(rec.frames, startMs + bs * 1000, startMs + entry.end * 1000));
    log.beats.push(entry);
    await scanText(planned.id);
    console.log(`beat ${String(planned.id).padEnd(2)} ${bs.toFixed(2)} - ${entry.end.toFixed(2)} s (planned ${planned.start.toFixed(2)} + ${planned.len.toFixed(2)})`);
  }
  const endMs = performance.now();
  await rec.stop();
  await context.close();
  await browser.close();

  // ---- outputs ----------------------------------------------------------------------------------------------------------
  const seconds = (endMs - startMs) / 1000;
  writeFileSync(path.join(takeDir, "frames.ffconcat"), buildConcat(rec.frames, startMs, endMs));
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
    cut: cut.name, dryRun: dry, profile, releaseClaims, appCommit: commit, demoSeedHash: seedHash,
    viewport: { w: cut.w, h: cut.h, deviceScaleFactor: DSF }, frames: rec.frames.length, seconds: +seconds.toFixed(3), plannedSeconds: +totalPlanned.toFixed(3),
    beats: log.beats, cues: log.cues.map((c) => ({ ...c, start: +c.start.toFixed(3), end: +c.end.toFixed(3) })),
    scrape: log.scrape, overlay: log.overlay, consoleErrors: log.console, warnings: log.warnings, textHits: log.textHits,
  };
  writeFileSync(path.join(takeDir, "meta.json"), JSON.stringify(meta, null, 2));
  console.log(`overlay: ${JSON.stringify(log.overlay)}; console errors: ${log.console.length}; warnings: ${log.warnings.length}; text hits: ${log.textHits.length}`);
  console.log(`wrote ${path.relative(root, takeDir)} (${rec.frames.length} frames, ${seconds.toFixed(1)} s)`);
  if (log.textHits.length) {
    console.error(`record-demo: forbidden text on screen: ${JSON.stringify(log.textHits)}`);
    process.exit(4);
  }
}

const timer = setTimeout(() => {
  console.error(`record-demo: hard timeout after ${hardTimeout} ms`);
  process.exit(3);
}, hardTimeout);
main()
  .then(() => { clearTimeout(timer); process.exit(0); })
  .catch((e) => { clearTimeout(timer); console.error(`record-demo: ${e.message}`); process.exit(1); });
