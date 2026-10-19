#!/usr/bin/env node
// Renders the film plates as PNG with Playwright (dsf 1): canvas (window frame + shadow), dim (zoom depth), caption band
// PNGs, title card, end card, and (only with RELEASE_CLAIMS=1) the claims end card.
// Usage: ABB_WEB_DIR=<apps/web> FONTS=<apps/web/src/fonts> MARK=<brand/pulse-mark.svg> OUT=<dir> node plates.mjs
import { createRequire } from "node:module";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";

const webDir = process.env.ABB_WEB_DIR;
const { chromium } = createRequire(path.join(webDir, "package.json"))("@playwright/test");
const fonts = process.env.FONTS, out = process.env.OUT;
const mark = readFileSync(process.env.MARK, "utf8").replace(/ width="24" height="24"/, "");
mkdirSync(out, { recursive: true });

// ---- composition spec (the numbers the brief states) ----------------------------------------------------------
const SPEC = JSON.parse(readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), "spec.json"), "utf8"));
const S = SPEC.main;

const css = `
@font-face { font-family: "Plex"; font-weight: 400; src: url("file://${fonts}/IBMPlexSans-Regular-Latin1.woff2") format("woff2"); }
@font-face { font-family: "Plex"; font-weight: 600; src: url("file://${fonts}/IBMPlexSans-SemiBold-Latin1.woff2") format("woff2"); }
@font-face { font-family: "PlexMono"; font-weight: 400; src: url("file://${fonts}/IBMPlexMono-Regular-Latin1.woff2") format("woff2"); }
* { margin: 0; box-sizing: border-box; }
html, body { width: ${S.canvas.w}px; height: ${S.canvas.h}px; overflow: hidden; background: transparent; }
body { font-family: "Plex", sans-serif; color: ${S.color.ink}; letter-spacing: 0; -webkit-font-smoothing: antialiased; }
.canvas { position: absolute; inset: 0; background: ${S.color.canvas}; }
.canvas::before { content: ""; position: absolute; inset: 0;
  background: ${S.color.canvas}; }
.win { position: absolute; left: ${S.window.x}px; top: ${S.window.y}px; width: ${S.window.w}px; height: ${S.window.h}px;
  border-radius: ${S.window.radius}px; background: ${S.color.appCanvas};
  box-shadow: 0 0 0 1px ${S.color.hairline}, ${S.window.shadow}; }
.dim { position: absolute; inset: 0; overflow: hidden; }
.dim .hole { position: absolute; left: ${S.window.x}px; top: ${S.window.y}px; width: ${S.window.w}px; height: ${S.window.h}px;
  border-radius: ${S.window.radius}px; box-shadow: 0 0 0 9999px rgba(0,0,0,${S.zoom.dim}); }
.band { position: absolute; left: 0; right: 0; top: ${S.band.y}px; height: ${S.band.h}px; display: flex; align-items: center; justify-content: center; }
.band span { font-weight: 600; font-size: ${S.band.size}px; line-height: ${S.band.line}px; white-space: nowrap; color: ${S.color.ink}; }
.band em { font-style: normal; color: ${S.color.fail}; }
.card { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0; }
.card svg { width: ${S.card.mark}px; height: ${S.card.mark}px; color: ${S.color.ink}; }
.card .name { margin-top: ${S.card.gapMarkName}px; font-weight: 600; font-size: ${S.card.name}px; line-height: ${S.card.nameLine}px; }
.card .line { margin-top: ${S.card.gapNameLine}px; font-weight: 400; font-size: ${S.card.line}px; line-height: ${S.card.lineLine}px; color: ${S.color.ink2}; }
.card .claims { margin-top: ${S.card.gapClaims}px; font-weight: 400; font-size: ${S.card.claims}px; line-height: ${S.card.claimsLine}px; color: ${S.color.ink}; }
.card .url { margin-top: 12px; font-family: "PlexMono", monospace; font-size: ${S.card.url}px; line-height: ${S.card.urlLine}px; color: ${S.color.ink2}; }
`;
const page1 = (body, bg = "") => `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body>${bg}${body}</body></html>`;

const plates = {
  "canvas.png": page1(`<div class="canvas"></div><div class="win"></div>`),
  "dim.png": page1(`<div class="dim"><div class="hole"></div></div>`), // the hole is cut out below (black -> transparent)
  "title.png": page1(`<div class="canvas"></div><div class="card">${mark}<div class="name">Agent BlackBox</div><div class="line">${S.card.tagline}</div></div>`),
  "end.png": page1(`<div class="canvas"></div><div class="card">${mark}<div class="name">Agent BlackBox</div><div class="line">${S.card.tagline}</div></div>`),
};
if (process.env.RELEASE_CLAIMS === "1") {
  plates["end-claims.png"] = page1(`<div class="canvas"></div><div class="card">${mark}<div class="name">Agent BlackBox</div><div class="line">${S.card.tagline}</div><div class="claims">Open source. Self-hosted. Apache-2.0.</div><div class="url">github.com/abdullahimohamed101/agent-black-box</div></div>`);
}
const captions = JSON.parse(process.env.CAPTIONS ?? "[]"); // [{id, html}]
for (const c of captions) plates[`cap-${c.id}.png`] = page1(`<div class="band"><span>${c.html}</span></div>`);

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: S.canvas.w, height: S.canvas.h }, deviceScaleFactor: 1 });
for (const [name, html] of Object.entries(plates)) {
  const f = path.join(out, name.replace(".png", ".html"));
  writeFileSync(f, html);
  await page.goto("file://" + f, { waitUntil: "load" });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(out, name), omitBackground: true, type: "png" });
  console.log("plate", name);
}
await browser.close();
