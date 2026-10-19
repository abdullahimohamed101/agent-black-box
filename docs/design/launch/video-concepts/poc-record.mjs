#!/usr/bin/env node
/*
 * poc-record.mjs: proof of concept for the v2 demo recorder (docs/design/launch/video-brief.md, section 3).
 *
 * Records 12-15 s against the fixtures build of apps/web through the CDP screencast at deviceScaleFactor 2
 * (3840x2160 JPEG frames with timestamps, not Playwright's recordVideo, which is VP8 at a fixed 1 Mbit/s), with:
 *   - a simulated arrow cursor (SVG on <html>, outside the zoomed <body>) moved along eased paths from Node,
 *   - a click ripple,
 *   - a lower-third caption (outside the zoom, so it never scales),
 *   - one camera zoom (CSS transform on <body>, eased, origin chosen so no canvas edge is exposed).
 * Writes frames + a concat list to OUT; encode with poc-encode.sh.
 *
 * Usage:  ABB_VIDEO_BASE_URL=http://localhost:3101 ABB_WEB_DIR=/path/to/apps/web OUT=/tmp/poc node poc-record.mjs
 * Nothing is uploaded; nothing secret is read or printed.
 */
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const webDir = process.env.ABB_WEB_DIR ?? path.resolve(process.cwd(), "apps/web");
const { chromium } = createRequire(path.join(webDir, "package.json"))("@playwright/test");
const base = process.env.ABB_VIDEO_BASE_URL ?? "http://localhost:3101";
const out = process.env.OUT ?? path.resolve(process.cwd(), "poc-out");
const FPS_CAP = Number(process.env.FPS_CAP ?? 30);
const DSF = Number(process.env.DSF ?? 2);
mkdirSync(path.join(out, "frames"), { recursive: true });

// ---- motion spec (numbers from the brief, section 3) -------------------------------------------------------------
const EASE = {
  // cubic-bezier(0.22, 1, 0.36, 1): fast start, long settle ("ease-out-quint" family). Used for cursor travel.
  travel: bezier(0.22, 1, 0.36, 1),
  // cubic-bezier(0.65, 0, 0.35, 1): symmetric ease-in-out, used for camera zooms (CSS side uses the same string).
  camera: "cubic-bezier(0.65, 0, 0.35, 1)",
};
const CURSOR = { travelMsPer1000px: 900, minTravelMs: 350, maxTravelMs: 1100, settleMs: 120, clickDownMs: 90 };
const ZOOM = { ms: 700, scale: 1.6 };

function bezier(x1, y1, x2, y2) {
  // Cubic bezier easing solved for t by bisection (good enough at 60 Hz).
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sx = (t) => ((ax * t + bx) * t + cx) * t;
  const sy = (t) => ((ay * t + by) * t + cy) * t;
  return (x) => {
    let lo = 0, hi = 1, t = x;
    for (let i = 0; i < 24; i++) {
      const v = sx(t);
      if (Math.abs(v - x) < 1e-4) break;
      if (v < x) lo = t; else hi = t;
      t = (lo + hi) / 2;
    }
    return sy(t);
  };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- overlay injected into every document (cursor, ripple, caption, camera) -----------------------------------
const overlay = () => {
  const mount = () => {
    if (document.getElementById("abb-cur")) return;
    const style = document.createElement("style");
    style.textContent = `
      html { overflow: hidden !important; background: var(--color-bg-canvas, #101419); }
      body { transform-origin: 0 0; transition: transform ${"__ZOOM_MS__"}ms ${"__ZOOM_EASE__"}; }
      #abb-cur { position: fixed; left: 0; top: 0; width: 24px; height: 24px; pointer-events: none;
        z-index: 2147483647; transform: translate(960px, 540px); will-change: transform; }
      #abb-cur svg { display: block; filter: drop-shadow(0 1px 1.5px rgba(0,0,0,.6)); }
      #abb-ripple { position: fixed; left: 0; top: 0; width: 44px; height: 44px; margin: -22px 0 0 -22px; border-radius: 50%;
        border: 2px solid rgba(255,255,255,.85); pointer-events: none; z-index: 2147483646; opacity: 0; transform: scale(.3); }
      #abb-ripple.go { animation: abb-rip 380ms cubic-bezier(.2,.7,.3,1) forwards; }
      @keyframes abb-rip { 0% { opacity: .9; transform: scale(.3) } 100% { opacity: 0; transform: scale(1.15) } }
      #abb-cap { position: fixed; left: 50%; bottom: 84px; transform: translateX(-50%); max-width: 1440px;
        padding: 14px 26px; border-radius: 4px; background: rgba(16,20,25,.92); border: 1px solid rgba(255,255,255,.14);
        color: #e2e5e8; font: 600 34px/44px var(--abb-cap-font, "IBM Plex Sans", system-ui, sans-serif);
        letter-spacing: 0; text-align: center; pointer-events: none; z-index: 2147483645; opacity: 0;
        transition: opacity 160ms linear; white-space: nowrap; }
      #abb-cap.on { opacity: 1; }
      #abb-cap em { font-style: normal; color: #ff8a80; }
    `;
    document.documentElement.append(style);
    const cur = document.createElement("div");
    cur.id = "abb-cur";
    cur.innerHTML =
      '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M5 3l14 9-6.2 1.2L16.5 20l-2.6 1.2-3.7-6.6L5 19z" ' +
      'fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    const rip = document.createElement("div");
    rip.id = "abb-ripple";
    const cap = document.createElement("div");
    cap.id = "abb-cap";
    cap.setAttribute("aria-hidden", "true");
    document.documentElement.append(cap, rip, cur);
    const font = getComputedStyle(document.body).getPropertyValue("--font-plex-sans").trim();
    if (font) document.documentElement.style.setProperty("--abb-cap-font", font);
    let pos = { x: 960, y: 540 };
    try {
      pos = JSON.parse(sessionStorage.getItem("__abbCur") ?? "null") ?? pos;
    } catch {}
    cur.style.transform = `translate(${pos.x}px,${pos.y}px)`;
    addEventListener(
      "pointermove",
      (e) => {
        cur.style.transform = `translate(${e.clientX}px,${e.clientY}px)`;
        try { sessionStorage.setItem("__abbCur", JSON.stringify({ x: e.clientX, y: e.clientY })); } catch {}
      },
      { capture: true, passive: true },
    );
    addEventListener(
      "pointerdown",
      (e) => {
        rip.style.left = e.clientX + "px";
        rip.style.top = e.clientY + "px";
        rip.classList.remove("go");
        void rip.offsetWidth;
        rip.classList.add("go");
        cur.style.transform = `translate(${e.clientX}px,${e.clientY}px) scale(.88)`;
      },
      { capture: true, passive: true },
    );
    addEventListener("pointerup", (e) => { cur.style.transform = `translate(${e.clientX}px,${e.clientY}px)`; }, { capture: true, passive: true });
    try {
      const t = sessionStorage.getItem("__abbCap");
      if (t) { cap.innerHTML = t; cap.classList.add("on"); }
    } catch {}
  };
  window.__abb = {
    caption(html) {
      const cap = document.getElementById("abb-cap");
      try { sessionStorage.setItem("__abbCap", html ?? ""); } catch {}
      if (!cap) return;
      if (!html) { cap.classList.remove("on"); return; }
      cap.innerHTML = html;
      cap.classList.add("on");
    },
    // Zoom the app (body) so that viewport point (x, y) stays fixed, clamped so no canvas edge shows.
    zoom(scale, x, y) {
      const W = innerWidth, H = innerHeight;
      if (scale <= 1) { document.body.style.transform = ""; return; }
      // Visible rect after scaling about (ox, oy): [ox - ox/s, ox + (W-ox)/s]. Clamp origin into [0, W]/[0, H] band so
      // the chosen focus point is on screen and the scaled body still covers the viewport (origin inside viewport).
      const ox = Math.max(0, Math.min(W, x));
      const oy = Math.max(0, Math.min(H, y));
      document.body.style.transformOrigin = `${ox}px ${oy}px`;
      document.body.style.transform = `scale(${scale})`;
    },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
};

// ---- recorder: CDP screencast -> timestamped JPEG frames ---------------------------------------------------------
async function startScreencast(page) {
  const cdp = await page.context().newCDPSession(page);
  const frames = [];
  let n = 0;
  let lastT = -Infinity;
  const minGap = 1000 / FPS_CAP - 2;
  cdp.on("Page.screencastFrame", async ({ data, sessionId, metadata }) => {
    const t = performance.now();
    if (t - lastT >= minGap) {
      lastT = t;
      const file = path.join(out, "frames", `f${String(n++).padStart(5, "0")}.jpg`);
      writeFileSync(file, Buffer.from(data, "base64"));
      frames.push({ file, t, ts: metadata.timestamp, w: metadata.deviceWidth, h: metadata.deviceHeight });
    }
    await cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: 1920 * DSF, maxHeight: 1080 * DSF, everyNthFrame: 1 });
  return {
    frames,
    async stop() {
      await cdp.send("Page.stopScreencast").catch(() => {});
      await cdp.detach().catch(() => {});
    },
  };
}

// ---- cursor motion from Node ----------------------------------------------------------------------------------
let cur = { x: 960, y: 540 };
async function travel(page, to) {
  const dx = to.x - cur.x, dy = to.y - cur.y;
  const dist = Math.hypot(dx, dy);
  const ms = Math.max(CURSOR.minTravelMs, Math.min(CURSOR.maxTravelMs, (dist / 1000) * CURSOR.travelMsPer1000px));
  // slight arc: perpendicular bow of 4 % of distance, so the path is not a ruler line
  const bow = Math.min(40, dist * 0.04);
  const nx = -dy / (dist || 1), ny = dx / (dist || 1);
  const t0 = performance.now();
  while (true) {
    const p = Math.min(1, (performance.now() - t0) / ms);
    const e = EASE.travel(p);
    const arc = Math.sin(Math.PI * p) * bow;
    await page.mouse.move(cur.x + dx * e + nx * arc, cur.y + dy * e + ny * arc);
    if (p >= 1) break;
    await sleep(1000 / 60);
  }
  cur = { ...to };
  await sleep(CURSOR.settleMs);
}
async function center(loc) {
  const b = await loc.boundingBox();
  if (!b) throw new Error("no box");
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}
async function click(page) {
  await page.mouse.down();
  await sleep(CURSOR.clickDownMs);
  await page.mouse.up();
}
const caption = (page, html) => page.evaluate((h) => window.__abb?.caption(h), html).catch(() => {});
const zoom = (page, s, x, y) => page.evaluate(([s, x, y]) => window.__abb?.zoom(s, x, y), [s, x, y]).catch(() => {});

// ---- the clip ---------------------------------------------------------------------------------------------------
async function main() {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: DSF,
    colorScheme: "dark",
    reducedMotion: "no-preference",
  });
  await context.addInitScript(
    `(${overlay.toString().replace("__ZOOM_MS__", String(ZOOM.ms)).replace("__ZOOM_EASE__", EASE.camera)})()`,
  );
  const page = await context.newPage();
  const marks = [];
  const mark = (name) => marks.push({ name, t: performance.now() });
  await page.goto(`${base}/w/demo/projects/all/runs`, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "Runs" }).waitFor();
  await page.mouse.move(cur.x, cur.y);
  const rec = await startScreencast(page);
  const t0 = performance.now();
  mark("start");

  // Beat 1 (0-3 s): hold on Runs, caption 1.
  await caption(page, "Your agent ran. Something broke. Where?");
  await sleep(2600);

  // Beat 2: travel to the failed run and click it.
  const link = page.getByRole("link", { name: "Migrate billing schema" }).first();
  await link.scrollIntoViewIfNeeded();
  await caption(page, "One run <em>failed</em>. Open it.");
  await travel(page, await center(link));
  await sleep(250);
  mark("click");
  await click(page);
  await page.getByRole("tab", { name: "Timeline", exact: true }).waitFor();
  await page.waitForTimeout(400); // the fault line draws once (280 ms) on arrival
  // park the cursor out of the headline's way
  await travel(page, { x: 1500, y: 300 });

  // Beat 3: zoom on the headline + figures (origin near the headline's left so the sentence stays on screen).
  await caption(page, "One sentence says what happened.");
  const headline = page.locator("h1").first();
  const hb = await headline.boundingBox();
  mark("zoom-in");
  await zoom(page, ZOOM.scale, hb.x, hb.y - 40);
  await sleep(ZOOM.ms + 2400);

  // Beat 4: zoom out, then caption on the red line.
  mark("zoom-out");
  await zoom(page, 1, 0, 0);
  await sleep(ZOOM.ms + 200);
  await caption(page, "One red line marks where it went wrong.");
  const fault = page.locator(".tl-fault-label").first();
  const fb = await fault.boundingBox().catch(() => null);
  if (fb) await travel(page, { x: fb.x - 60, y: fb.y + fb.height / 2 + 24 });
  await sleep(2600);
  await caption(page, "");
  await sleep(400);
  mark("end");

  await rec.stop();
  await context.close();
  await browser.close();

  // concat list with real durations, so playback time == wall time regardless of how many frames repainted
  const fr = rec.frames;
  const lines = ["ffconcat version 1.0"];
  for (let i = 0; i < fr.length; i++) {
    const next = i + 1 < fr.length ? fr[i + 1].t : fr[i].t + 1000 / FPS_CAP;
    lines.push(`file '${fr[i].file}'`, `duration ${((next - fr[i].t) / 1000).toFixed(4)}`);
  }
  if (fr.length) lines.push(`file '${fr[fr.length - 1].file}'`);
  writeFileSync(path.join(out, "frames.ffconcat"), lines.join("\n") + "\n");
  writeFileSync(
    path.join(out, "meta.json"),
    JSON.stringify({ frames: fr.length, seconds: (performance.now() - t0) / 1000, size: fr[0] && { w: fr[0].w, h: fr[0].h }, marks: marks.map((m) => ({ ...m, t: (m.t - t0) / 1000 })) }, null, 2),
  );
  console.log(`frames=${fr.length} seconds=${((performance.now() - t0) / 1000).toFixed(1)} first=${fr[0]?.w}x${fr[0]?.h}`);
}
main().catch((e) => { console.error(e); process.exit(1); });
