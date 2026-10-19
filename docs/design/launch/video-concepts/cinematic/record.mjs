#!/usr/bin/env node
// Cinematic PoC recorder: records ~9 s of the real app (fixtures build) through the CDP screencast at 2x with the
// cursor + camera overlay only (captions and cards are composited afterwards, outside the window).
// Usage: ABB_VIDEO_BASE_URL=http://localhost:3102 ABB_WEB_DIR=<apps/web> OUT=<dir> node record.mjs
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const webDir = process.env.ABB_WEB_DIR;
const { chromium } = createRequire(path.join(webDir, "package.json"))("@playwright/test");
const base = process.env.ABB_VIDEO_BASE_URL ?? "http://localhost:3102";
const out = process.env.OUT;
const W = 1920, H = 1080, DSF = 2;
mkdirSync(path.join(out, "frames"), { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function bezier(x1, y1, x2, y2) {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sx = (t) => ((ax * t + bx) * t + cx) * t, sy = (t) => ((ay * t + by) * t + cy) * t;
  return (x) => { if (x <= 0) return 0; if (x >= 1) return 1; let lo = 0, hi = 1, t = x; for (let i = 0; i < 40; i++) { const v = sx(t); if (Math.abs(v - x) < 1e-6) break; if (v < x) lo = t; else hi = t; t = (lo + hi) / 2; } return sy(t); };
}
const travelEase = bezier(0.22, 1, 0.36, 1);

function overlay(cfg) {
  const mount = () => {
    if (document.getElementById("abb-cur")) return;
    const style = document.createElement("style");
    const nonced = document.querySelector("script[nonce],style[nonce],link[nonce]");
    if (nonced?.nonce) style.nonce = nonced.nonce;
    style.textContent = `
      html { overflow: hidden !important; background: #15191e; }
      .timeline:focus-visible { outline: none !important; }
      body { transform-origin: 0 0; transition: transform 700ms cubic-bezier(0.65, 0, 0.35, 1); }
      #abb-cur { position: fixed; left: 0; top: 0; width: 32px; height: 32px; pointer-events: none; z-index: 2147483640; transition: opacity 200ms linear; }
      #abb-cur svg { display: block; width: 32px; height: 32px; overflow: visible; filter: drop-shadow(0 1px 1.5px rgba(0,0,0,.6)); }
      #abb-ripple { position: fixed; left: 0; top: 0; width: 44px; height: 44px; margin: -22px 0 0 -22px; border-radius: 50%; border: 2px solid rgba(255,255,255,.85); pointer-events: none; z-index: 2147483639; opacity: 0; transform: scale(.3); }
      #abb-ripple.go { animation: abb-rip 380ms cubic-bezier(.2,.7,.3,1) forwards; }
      @keyframes abb-rip { 0% { opacity: .9; transform: scale(.3) } 100% { opacity: 0; transform: scale(1.15) } }
    `;
    document.documentElement.append(style);
    const cur = document.createElement("div"); cur.id = "abb-cur";
    cur.innerHTML = '<svg viewBox="0 0 24 24"><path d="M5 3l14 9-6.2 1.2L16.5 20l-2.6 1.2-3.7-6.6L5 19z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    const rip = document.createElement("div"); rip.id = "abb-ripple";
    document.documentElement.append(rip, cur);
    let pos = { x: cfg.start.x, y: cfg.start.y };
    try { pos = JSON.parse(sessionStorage.getItem("__abbCur") ?? "null") ?? pos; } catch {}
    let scale = 1;
    const place = (x, y) => { cur.style.transformOrigin = "0 0"; cur.style.transform = `translate(${x}px,${y}px) scale(${scale})`; };
    place(pos.x, pos.y);
    addEventListener("pointermove", (e) => { place(e.clientX, e.clientY); try { sessionStorage.setItem("__abbCur", JSON.stringify({ x: e.clientX, y: e.clientY })); } catch {} }, { capture: true, passive: true });
    addEventListener("pointerdown", (e) => { rip.style.left = e.clientX + "px"; rip.style.top = e.clientY + "px"; rip.classList.remove("go"); void rip.offsetWidth; rip.classList.add("go"); scale = 0.88; place(e.clientX, e.clientY); }, { capture: true, passive: true });
    addEventListener("pointerup", (e) => { scale = 1; place(e.clientX, e.clientY); }, { capture: true, passive: true });
    if (sessionStorage.getItem("__abbCurHidden") === "1") cur.style.opacity = "0";
    const keep = [style, rip, cur];
    const ensure = () => { for (const el of keep) if (!el.isConnected) document.documentElement.append(el); };
    new MutationObserver(ensure).observe(document.documentElement, { childList: true });
    setInterval(ensure, 250);
    window.__abb = {
      zoom(s, x, y, ms) {
        document.body.style.transitionDuration = ms + "ms";
        if (s <= 1) { document.body.style.transform = ""; return; }
        const ox = Math.max(0, Math.min(cfg.w, (x - cfg.w / (2 * s)) / (1 - 1 / s)));
        const oy = Math.max(0, Math.min(cfg.h, (y - cfg.h / (2 * s)) / (1 - 1 / s)));
        document.body.style.transformOrigin = `${ox}px ${oy}px`;
        document.body.style.transform = `scale(${s})`;
      },
      cursor(show) { try { sessionStorage.setItem("__abbCurHidden", show ? "0" : "1"); } catch {} cur.style.opacity = show ? "1" : "0"; },
      state() { return { cur: !!document.getElementById("abb-cur") }; },
    };
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
}

let page, cur = { x: 1200, y: 215 };
async function travelTo(target) {
  const dist = Math.hypot(target.x - cur.x, target.y - cur.y);
  const ms = Math.max(350, Math.min(1100, dist * 0.9));
  const bow = Math.min(40, dist * 0.04);
  const from = { ...cur }, t0 = performance.now();
  for (;;) {
    const p = Math.min(1, (performance.now() - t0) / ms);
    const e = travelEase(p), dx = target.x - from.x, dy = target.y - from.y, d = dist || 1;
    const arc = Math.sin(Math.PI * p) * bow;
    await page.mouse.move(from.x + dx * e + (-dy / d) * arc, from.y + dy * e + (dx / d) * arc);
    if (p >= 1) break;
    await sleep(1000 / 60);
  }
  cur = { ...target };
  await sleep(120);
}

async function main() {
  const browser = await chromium.launch({ headless: true, args: [`--force-device-scale-factor=${DSF}`, "--hide-scrollbars", "--disable-lcd-text"] });
  const context = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: DSF, colorScheme: "dark", reducedMotion: "no-preference", bypassCSP: true });
  await context.addInitScript(`(${overlay.toString()})(${JSON.stringify({ w: W, h: H, start: cur })})`);
  page = await context.newPage();
  page.on("pageerror", (e) => console.log("pageerror:", e.message.slice(0, 200)));
  await page.goto(`${base}/w/demo/projects/all/runs`, { waitUntil: "load", timeout: 30000 });
  await page.getByRole("heading", { name: "Runs", exact: true }).waitFor({ timeout: 20000 });
  await page.evaluate(() => document.fonts.ready);
  const link = () => page.getByRole("link", { name: "Migrate billing schema" }).first();
  // warm-up off camera: open the run once and come back, so the recording shows pages, not spinners
  await link().click();
  await page.getByTestId("headline").waitFor({ timeout: 15000 });
  await page.getByRole("link", { name: "Runs", exact: true }).first().click();
  await page.getByRole("heading", { name: "Runs", exact: true }).waitFor({ timeout: 15000 });
  await sleep(300);
  await page.mouse.move(cur.x, cur.y);
  console.log("overlay:", JSON.stringify(await page.evaluate(() => window.__abb?.state())));

  // screencast
  const cdp = await context.newCDPSession(page);
  const frames = []; let n = 0, lastT = -Infinity, pending = null, timer = null; const minGap = 1000 / 30 - 2;
  const write = (f) => { const file = path.join(out, "frames", `f${String(n++).padStart(5, "0")}.jpg`); writeFileSync(file, f.buf); frames.push({ file, t: f.t }); lastT = f.t; };
  cdp.on("Page.screencastFrame", async ({ data, sessionId }) => {
    const f = { buf: Buffer.from(data, "base64"), t: performance.now() };
    if (f.t - lastT >= minGap) { pending = null; write(f); } else { pending = f; timer ??= setTimeout(() => { timer = null; if (pending) { const p = pending; pending = null; write(p); } }, minGap); }
    await cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: W * DSF, maxHeight: H * DSF, everyNthFrame: 1 });
  const T0 = performance.now(); const clock = () => (performance.now() - T0) / 1000;
  const marks = {}; const mark = (k) => (marks[k] = +clock().toFixed(3));
  const until = async (s) => { const ms = s * 1000 - (performance.now() - T0); if (ms > 0) await sleep(ms); };

  // beat A: hold on Runs
  await until(1.0);
  // beat B: travel to the failed run, click
  const b = await link().boundingBox();
  await travelTo({ x: b.x + b.width * 0.5, y: b.y + b.height / 2 });
  await sleep(350);
  mark("click");
  await page.mouse.down(); await sleep(90); await page.mouse.up();
  await page.getByRole("tab", { name: "Timeline", exact: true }).waitFor({ timeout: 15000 });
  await sleep(450);
  mark("runReady");
  await travelTo({ x: 1500, y: 300 });
  // beat C: zoom 1.6 on the headline
  await until(Math.max(clock(), 3.4));
  const hb = await page.getByTestId("headline").boundingBox();
  await page.evaluate(() => window.__abb.cursor(false));
  mark("zoomIn");
  await page.evaluate(([x, y]) => window.__abb.zoom(1.6, x, y, 700), [hb.x + hb.width / 2, hb.y + hb.height / 2]);
  await sleep(700 + 3000);
  mark("zoomOut");
  await page.evaluate(() => window.__abb.zoom(1, 0, 0, 600));
  await sleep(600);
  await page.evaluate(() => window.__abb.cursor(true));
  await sleep(900);
  mark("end");
  await sleep(80); if (pending) { write(pending); pending = null; }
  const endMs = performance.now();
  await cdp.send("Page.stopScreencast").catch(() => {});
  await context.close(); await browser.close();
  const lines = ["ffconcat version 1.0"];
  for (let i = 0; i < frames.length; i++) {
    const from = i === 0 ? T0 : frames[i].t, to = i + 1 < frames.length ? frames[i + 1].t : endMs;
    lines.push(`file '${frames[i].file}'`, `duration ${Math.max(0.001, (to - from) / 1000).toFixed(4)}`);
  }
  lines.push(`file '${frames[frames.length - 1].file}'`);
  writeFileSync(path.join(out, "frames.ffconcat"), lines.join("\n") + "\n");
  writeFileSync(path.join(out, "marks.json"), JSON.stringify({ marks, frames: frames.length, seconds: +((endMs - T0) / 1000).toFixed(3) }, null, 2));
  console.log("done", JSON.stringify(marks), frames.length, "frames");
}
main().catch((e) => { console.error(e); process.exit(1); });
