#!/usr/bin/env node
/*
 * record-demo.mjs: records the Agent BlackBox demo video (docs/design/video-storyboard.md) as a WebM.
 *
 * What it does: drives a RUNNING stack with Playwright (system Chrome), 1920x1080, deviceScaleFactor 1, dark theme with a
 * closing light-theme scene, reduced motion OFF, a simulated cursor (a round div that follows pointermove, because
 * Playwright's recorder cannot draw the real one) and a lower-third caption div (Plex Sans, 28 px). The whole run is a pure
 * function of the SCENES table below: a UI change means editing selectors in the table, never the driver.
 *
 * Usage (from the repo root, with `pnpm install` done so @playwright/test resolves from apps/web):
 *   ABB_VIDEO_BASE_URL=http://localhost:3000 node scripts/record-demo.mjs            # the real recording (about 80 s)
 *   ABB_VIDEO_BASE_URL=http://localhost:3100 node scripts/record-demo.mjs --dry-run  # scenes 1-3 only, fixtures profile
 *   scripts/encode-demo.sh video-out/<file>.webm                                       # H.264 MP4 + poster PNG
 *
 * Environment (nothing is hard-coded and nothing secret is ever printed):
 *   ABB_VIDEO_BASE_URL        required, e.g. http://localhost:3000
 *   ABB_VIDEO_WORKSPACE       workspace slug in the URL (profile default: demo -> "local", fixtures -> "demo")
 *   ABB_VIDEO_PROJECT         project slug or "all" (default "all")
 *   ABB_VIDEO_SESSION_TOKEN   optional development session; set as the HttpOnly abb_session cookie (as e2e/session.ts does)
 *   ABB_VIDEO_PROFILE         "demo" (data seeded by `make demo`) or "fixtures" (the fixtures build); dry-run defaults to fixtures
 *   ABB_VIDEO_OUT             output directory (default video-out/, gitignored)
 *   ABB_VIDEO_TIMEOUT_MS      hard timeout for the whole run (default scene total + 90 s)
 * Flags: --dry-run (scenes 1-3, about 20 s), --profile=<demo|fixtures>.
 * Nothing is uploaded; the script only talks to ABB_VIDEO_BASE_URL.
 */
import { createRequire } from "node:module";
import { mkdirSync, readdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = createRequire(path.join(root, "apps/web/package.json"))("@playwright/test");

// ---------------------------------------------------------------------------------------------------------------------
// Data names per profile. Step 10 of the plan (`make demo`) is not merged yet, so the demo names are [CONFIRM AFTER STEP 10].
// ---------------------------------------------------------------------------------------------------------------------
const DATA = {
  demo: {
    workspace: "local",
    failedRun: "Fix OAuth session expiry", // [CONFIRM AFTER STEP 10] the --variant wrong-fix run
    multiRun: "Ship feature: multi-agent",
  },
  fixtures: { workspace: "demo", failedRun: "Migrate billing schema", multiRun: "Ship feature: multi-agent" },
};

// ---------------------------------------------------------------------------------------------------------------------
// The scene table. Step kinds:
//   goto {path}  move {to}  click {to}  park {x,y}  press {key}  wait {ms | for}  scheme {value}; the caption is per scene.
// Selectors `to`: {role,name,exact} | {testid} | {css} | {text}. A string starting with "$" is read from DATA.
// `optional: true` skips the step (bounded 3 s) when the element is not there instead of failing the recording.
// `ms` is the scene's target length; the driver pads with a bounded wait so the video length is deterministic.
// ---------------------------------------------------------------------------------------------------------------------
const SCENES = [
  {
    id: 1,
    ms: 7000,
    caption: "Agent BlackBox records what your AI agents actually did.",
    steps: [
      { do: "goto", path: "{project}/runs" },
      { do: "wait", for: { role: "heading", name: "Runs" } },
      { do: "move", to: { css: '[data-testid="week-sentence"] a' } }, // points at the sentence's link, covers no text
    ],
  },
  {
    id: 2,
    ms: 7000,
    caption: "One run failed. Open it to see why.",
    steps: [
      { do: "move", to: { role: "link", name: "$failedRun" } },
      { do: "wait", ms: 800 },
      { do: "click", to: { role: "link", name: "$failedRun" } },
    ],
  },
  {
    id: 3,
    ms: 12000,
    caption: "One sentence says what happened. One red line marks where it went wrong.",
    steps: [
      { do: "wait", for: { role: "tab", name: "Timeline", exact: true } },
      { do: "park", x: 1400, y: 900 }, // the cursor rests on empty canvas so it covers nothing the viewer reads
    ],
  },
  {
    id: 4,
    ms: 10000,
    caption: "Findings point to the exact events they came from.",
    steps: [
      { do: "click", to: { role: "tab", name: "Findings", exact: true } },
      { do: "wait", ms: 1200 },
      { do: "click", to: { role: "button", name: "Show evidence" }, optional: true },
    ],
  },
  {
    id: 5,
    ms: 10000,
    caption: "Keyboard first: j and k move, e jumps to the first error.",
    steps: [
      { do: "press", key: "Escape" },
      { do: "click", to: { role: "tab", name: "Timeline", exact: true } },
      { do: "wait", ms: 800 },
      { do: "press", key: "j" },
      { do: "wait", ms: 500 },
      { do: "press", key: "j" },
      { do: "wait", ms: 800 },
      { do: "press", key: "e" },
    ],
  },
  {
    id: 6,
    ms: 6000,
    enabled: false, // KI-097 live health label: enable after plan steps 3-6 and 10 are merged and the label's selector is known
    caption: "While a run is live, health shows the last analysis and its age.",
    steps: [{ do: "goto", path: "{project}/runs" }], // [CONFIRM AFTER STEPS 3-6 AND 10] open a running run, point at the "as of" label
  },
  {
    id: 7,
    ms: 10000,
    caption: "Five agents in one picture. The thick edge is the critical path.",
    steps: [
      { do: "goto", path: "{project}/runs" },
      { do: "click", to: { role: "link", name: "$multiRun" } },
      { do: "click", to: { role: "tab", name: "Waterfall", exact: true } },
      { do: "wait", for: { testid: "waterfall" } },
      { do: "wait", ms: 3000 },
      { do: "click", to: { css: '[data-testid="waterfall"] [role="row"][data-error]' }, optional: true },
    ],
  },
  {
    id: 8,
    ms: 6000,
    caption: "When an agent asks to act, a person approves or denies it.",
    steps: [
      { do: "goto", path: "/w/{workspace}/approvals" },
      { do: "wait", for: { role: "heading", name: "Review" } },
      { do: "click", to: { testid: "approval-row" }, optional: true }, // opens the panel; never decides
    ],
  },
  {
    id: 9,
    ms: 5000,
    caption: "Cost, reliability and speed, each in one sentence.",
    steps: [
      { do: "goto", path: "{project}/analytics" },
      { do: "wait", for: { testid: "report-headline" } },
      { do: "wait", ms: 1500 },
      { do: "click", to: { role: "tab", name: "Reliability", exact: true } },
      { do: "wait", ms: 1000 },
      { do: "click", to: { role: "tab", name: "Performance", exact: true } },
    ],
  },
  {
    id: 10,
    ms: 5000,
    // "Open source. Self-hosted." stays blocked until G2 (repository public) and AC1/AC12 are VERIFIED (storyboard scene 10).
    caption: "Agent BlackBox: a flight recorder for AI agents.",
    steps: [
      { do: "goto", path: "{project}/runs" },
      { do: "wait", for: { testid: "week-sentence" } },
      { do: "scheme", value: "light" },
    ],
  },
];
const DRY_RUN_SCENES = [1, 2, 3];

// ---------------------------------------------------------------------------------------------------------------------
// Driver (no selectors or copy below this line).
// ---------------------------------------------------------------------------------------------------------------------
const args = process.argv.slice(2);
const dry = args.includes("--dry-run");
const profileArg = args.find((a) => a.startsWith("--profile="))?.split("=")[1];
const profile = profileArg ?? process.env.ABB_VIDEO_PROFILE ?? (dry ? "fixtures" : "demo");
if (!DATA[profile]) fail(`unknown profile "${profile}" (use demo or fixtures)`);
const base = process.env.ABB_VIDEO_BASE_URL;
if (!base) fail("ABB_VIDEO_BASE_URL is required (e.g. http://localhost:3000)");
const data = DATA[profile];
const workspace = process.env.ABB_VIDEO_WORKSPACE ?? data.workspace;
const projectSlug = process.env.ABB_VIDEO_PROJECT ?? "all";
const outDir = path.resolve(root, process.env.ABB_VIDEO_OUT ?? "video-out");
const DRY_SCALE = 0.7; // a dry run is about 18 s; it checks mechanics, not pacing
const scenes = SCENES.filter((s) => s.enabled !== false && (!dry || DRY_RUN_SCENES.includes(s.id))).map((s) =>
  dry ? { ...s, ms: Math.round(s.ms * DRY_SCALE) } : s,
);
const total = scenes.reduce((n, s) => n + s.ms, 0);
const hardTimeout = Number(process.env.ABB_VIDEO_TIMEOUT_MS ?? total + 90_000);

function fail(msg) {
  console.error(`record-demo: ${msg}`);
  process.exit(2);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const resolveTokens = (v) => (typeof v === "string" && v.startsWith("$") ? data[v.slice(1)] : v);
const expandPath = (p) =>
  p.replace("{project}", `/w/${workspace}/projects/${projectSlug}`).replace("{workspace}", workspace);

function locate(page, s) {
  if (s.testid) return page.getByTestId(s.testid).first();
  if (s.css) return page.locator(s.css).first();
  if (s.text) return page.getByText(resolveTokens(s.text)).first();
  return page.getByRole(s.role, { name: resolveTokens(s.name), exact: s.exact }).first();
}

async function moveTo(page, target) {
  const box = await target.boundingBox();
  if (!box) throw new Error("element has no box");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 30 });
}

async function runStep(page, step) {
  const timeout = step.optional ? 3000 : 15000;
  try {
    switch (step.do) {
      case "goto":
        await page.goto(new URL(expandPath(step.path), base).toString(), { waitUntil: "load", timeout: 20000 });
        break;
      case "wait":
        if (step.for) await locate(page, step.for).waitFor({ state: "visible", timeout });
        else await sleep(Math.min(step.ms ?? 0, 10000));
        break;
      case "move":
      case "click": {
        const el = locate(page, step.to);
        await el.waitFor({ state: "visible", timeout });
        await el.scrollIntoViewIfNeeded({ timeout });
        await moveTo(page, el);
        if (step.do === "click") await page.mouse.down().then(() => page.mouse.up());
        break;
      }
      case "park":
        await page.mouse.move(step.x, step.y, { steps: 30 });
        break;
      case "press":
        await page.keyboard.press(step.key);
        break;
      case "scheme":
        await page.emulateMedia({ colorScheme: step.value });
        break;
      default:
        throw new Error(`unknown step ${step.do}`);
    }
  } catch (e) {
    if (step.optional) return;
    throw e;
  }
}

// Runs in every page: the cursor and the caption survive navigations because their state is kept in sessionStorage.
const injected = () => {
  const KEY_C = "__abbCur";
  const KEY_T = "__abbCap";
  const get = (k) => {
    try {
      return sessionStorage.getItem(k);
    } catch {
      return null;
    }
  };
  const set = (k, v) => {
    try {
      sessionStorage.setItem(k, v);
    } catch {
      /* storage can be blocked; the overlay then simply resets on navigation */
    }
  };
  const mount = () => {
    if (document.getElementById("abb-rec-cursor")) return;
    const cur = document.createElement("div");
    cur.id = "abb-rec-cursor";
    cur.style.cssText =
      "position:fixed;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;" +
      "background:rgba(255,255,255,.55);border:2px solid #111;box-shadow:0 0 0 2px rgba(255,255,255,.9);" +
      "pointer-events:none;z-index:2147483647;transition:none";
    const pos = JSON.parse(get(KEY_C) ?? '{"x":960,"y":540}');
    cur.style.transform = `translate(${pos.x}px,${pos.y}px)`;
    const cap = document.createElement("div");
    cap.id = "abb-rec-caption";
    cap.setAttribute("aria-hidden", "true");
    cap.style.cssText =
      "position:fixed;left:50%;bottom:72px;transform:translateX(-50%);width:max-content;max-width:1500px;padding:14px 28px;" +
      "border-radius:8px;background:#0a0c10;color:#fff;font-size:28px;line-height:1.3;font-weight:400;" +
      "text-align:center;pointer-events:none;z-index:2147483646;display:none";
    cap.style.fontFamily = getComputedStyle(document.body).fontFamily; // the app's own Plex Sans (next/font hashes the name)
    const t = get(KEY_T);
    if (t) {
      cap.textContent = t;
      cap.style.display = "block";
    }
    document.documentElement.append(cap, cur); // on <html>, not <body>, so React hydration never sees them
    addEventListener(
      "pointermove",
      (e) => {
        cur.style.transform = `translate(${e.clientX}px,${e.clientY}px)`;
        set(KEY_C, JSON.stringify({ x: e.clientX, y: e.clientY }));
      },
      { capture: true, passive: true },
    );
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
  window.__abbCaption = (text) => {
    set(KEY_T, text);
    const cap = document.getElementById("abb-rec-caption");
    if (!cap) return;
    cap.style.fontFamily = getComputedStyle(document.body).fontFamily;
    cap.textContent = text;
    cap.style.display = text ? "block" : "none";
  };
};

async function main() {
  mkdirSync(outDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const tmp = path.join(outDir, `.rec-${stamp}`);
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    reducedMotion: "no-preference",
    recordVideo: { dir: tmp, size: { width: 1920, height: 1080 } },
  });
  if (process.env.ABB_VIDEO_SESSION_TOKEN) {
    await context.addCookies([
      { name: "abb_session", value: process.env.ABB_VIDEO_SESSION_TOKEN, url: base, httpOnly: true, sameSite: "Lax" },
    ]);
  }
  await context.addInitScript(injected);
  const page = await context.newPage();
  const t0 = Date.now();
  let startMs = 0;
  try {
    // Warm the first page off-camera content-wise: the recording starts at the first scene, the encoder trims the lead-in.
    await page.goto(new URL(expandPath("{project}/runs"), base).toString(), { waitUntil: "load", timeout: 20000 });
    await page.waitForTimeout(500);
    startMs = Date.now() - t0;
    for (const scene of scenes) {
      const sceneStart = Date.now();
      await page.evaluate((c) => window.__abbCaption?.(c), scene.caption).catch(() => {});
      for (const step of scene.steps) {
        await runStep(page, step);
        // a navigation clears the injected caption; set it again once the new document exists
        await page.evaluate((c) => window.__abbCaption?.(c), scene.caption).catch(() => {});
      }
      const rest = scene.ms - (Date.now() - sceneStart);
      if (rest > 0) await sleep(Math.min(rest, 20000));
      console.log(`scene ${scene.id} done (${Math.round((Date.now() - sceneStart) / 100) / 10} s)`);
    }
  } finally {
    await context.close(); // writes the WebM
    await browser.close();
  }
  const file = readdirSync(tmp).find((f) => f.endsWith(".webm"));
  if (!file) fail("no video was written");
  const name = `${dry ? "demo-dry" : "demo"}-${stamp}`;
  const webm = path.join(outDir, `${name}.webm`);
  renameSync(path.join(tmp, file), webm);
  rmSync(tmp, { recursive: true, force: true });
  writeFileSync(path.join(outDir, `${name}.meta.json`), JSON.stringify({ startMs, scenes: scenes.map((s) => s.id), dry }));
  console.log(`wrote ${path.relative(root, webm)} (lead-in to trim: ${startMs} ms)`);
}

const timer = setTimeout(() => {
  console.error(`record-demo: hard timeout after ${hardTimeout} ms`);
  process.exit(3);
}, hardTimeout);
main()
  .then(() => {
    clearTimeout(timer);
    process.exit(0);
  })
  .catch((e) => {
    clearTimeout(timer);
    console.error(`record-demo: ${e.message}`);
    process.exit(1);
  });
