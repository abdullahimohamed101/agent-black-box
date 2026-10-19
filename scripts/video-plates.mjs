#!/usr/bin/env node
/*
 * video-plates.mjs: renders the film's plates as PNG from HTML with the self-hosted IBM Plex files (docs/design/launch/
 * video-cinematic-brief.md sections 2 and 5). Everything the film draws outside the app window is a plate: the canvas with the
 * window frame and its shadow, the dim (the canvas darkened while the camera is close, the window cut out), one caption band per
 * cue, the title card and the end card. The numbers come from scripts/video-spec.json.
 *
 *   node scripts/video-plates.mjs <take directory>      writes <take>/plates/*.png and plates.json
 *
 * end-claims.png ("Open source. Self-hosted. Apache-2.0." and the repository address, captions 26 and 28 of the bank) is written
 * ONLY when RELEASE_CLAIMS=1 is in the environment (gated on G2, the repository being public); with the flag unset the file does not
 * exist, so the default build cannot pick it up even by accident. The script fails when the Plex faces did not load (a fallback
 * font on a plate would look right and be wrong) and when a caption does not fit its band.
 */
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { captionHtml } from "./record-demo-lib.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const take = process.argv[2] ? path.resolve(process.argv[2]) : undefined;
if (!take || !existsSync(path.join(take, "meta.json"))) {
  console.error("usage: node scripts/video-plates.mjs <take directory written by scripts/record-demo.mjs>");
  process.exit(2);
}
const meta = JSON.parse(readFileSync(path.join(take, "meta.json"), "utf8"));
const SPEC = JSON.parse(readFileSync(path.join(root, "scripts/video-spec.json"), "utf8"));
const S = SPEC[meta.cut === "social" ? "social" : "main"];
const C = SPEC.color;
const claims = process.env.RELEASE_CLAIMS === "1";
const fonts = process.env.VIDEO_FONTS_DIR ?? path.join(root, "apps/web/src/fonts"); // the override exists to prove the missing-font failure
const { chromium } = createRequire(path.join(root, "apps/web/package.json"))("@playwright/test");
const mark = readFileSync(path.join(root, "brand/pulse-mark.svg"), "utf8").replace(/ width="24" height="24"/, "");
const out = path.join(take, "plates");
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

const css = `
@font-face { font-family: "Plex"; font-weight: 400; src: url("file://${fonts}/IBMPlexSans-Regular-Latin1.woff2") format("woff2"); }
@font-face { font-family: "Plex"; font-weight: 600; src: url("file://${fonts}/IBMPlexSans-SemiBold-Latin1.woff2") format("woff2"); }
@font-face { font-family: "PlexMono"; font-weight: 400; src: url("file://${fonts}/IBMPlexMono-Regular-Latin1.woff2") format("woff2"); }
* { margin: 0; box-sizing: border-box; }
html, body { width: ${S.canvas.w}px; height: ${S.canvas.h}px; overflow: hidden; background: transparent; }
body { font-family: "Plex", sans-serif; color: ${C.ink}; letter-spacing: 0; -webkit-font-smoothing: antialiased; }
.canvas { position: absolute; inset: 0; background: ${C.canvas}; }
.win { position: absolute; left: ${S.window.x}px; top: ${S.window.y}px; width: ${S.window.w}px; height: ${S.window.h}px;
  border-radius: ${S.window.radius}px; background: ${C.appCanvas}; box-shadow: 0 0 0 1px ${C.hairline}, ${S.window.shadow}; }
.dim { position: absolute; inset: 0; overflow: hidden; }
.dim .hole { position: absolute; left: ${S.window.x}px; top: ${S.window.y}px; width: ${S.window.w}px; height: ${S.window.h}px;
  border-radius: ${S.window.radius}px; box-shadow: 0 0 0 9999px rgba(0,0,0,${SPEC.dimAlpha}); }
.band { position: absolute; left: 0; right: 0; top: ${S.band.y}px; height: ${S.band.h}px; display: flex; align-items: center; justify-content: center; }
.band span { font-weight: 600; font-size: ${S.band.size}px; line-height: ${S.band.line}px; color: ${C.ink}; text-align: center;
  ${S.band.lines === 1 ? "white-space: nowrap;" : `max-width: ${S.band.maxW}px; text-wrap: balance;`} }
.band em { font-style: normal; color: ${C.fail}; }
.card { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; transform: translateY(-${S.card.lift}px); }
.card svg { width: ${S.card.mark}px; height: ${S.card.mark}px; color: ${C.ink}; }
.card .name { margin-top: ${S.card.gapMarkName}px; font-weight: 600; font-size: ${S.card.name}px; line-height: ${S.card.nameLine}px; }
.card .line { margin-top: ${S.card.gapNameLine}px; font-weight: 400; font-size: ${S.card.line}px; line-height: ${S.card.lineLine}px; color: ${C.ink2}; }
.card .claims { margin-top: ${S.card.gapClaims}px; font-weight: 400; font-size: ${S.card.claims}px; line-height: ${S.card.claimsLine}px; color: ${C.ink}; }
.card .url { margin-top: 12px; font-family: "PlexMono", monospace; font-size: ${S.card.url}px; line-height: ${S.card.urlLine}px; color: ${C.ink2}; }
`;
const page1 = (body) => `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body>${body}</body></html>`;
const lockup = `<div class="canvas"></div><div class="card">${mark}<div class="name">Agent BlackBox</div><div class="line">${SPEC.tagline}</div>`;

const plates = [
  { file: "canvas.png", html: page1(`<div class="canvas"></div><div class="win"></div>`) },
  { file: "dim.png", html: page1(`<div class="dim"><div class="hole"></div></div>`) },
  { file: "title.png", html: page1(`${lockup}</div>`) },
  { file: "end.png", html: page1(`${lockup}</div>`) },
];
if (claims) {
  plates.push({ file: "end-claims.png", html: page1(`${lockup}<div class="claims">${SPEC.claims.line}</div><div class="url">${SPEC.claims.url}</div></div>`) });
}
const band = { x: 0, y: S.band.y, width: S.canvas.w, height: S.band.h };
meta.cues.forEach((c, i) => {
  plates.push({ file: `cap-${String(i + 1).padStart(2, "0")}.png`, html: page1(`<div class="band"><span>${captionHtml(c.text)}</span></div>`), clip: band, caption: i });
});

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: S.canvas.w, height: S.canvas.h }, deviceScaleFactor: 1 });
const written = [];
const problems = [];
for (const p of plates) {
  const f = path.join(out, p.file.replace(".png", ".html"));
  writeFileSync(f, p.html);
  await page.goto("file://" + f, { waitUntil: "load" });
  await page.evaluate(() => Promise.all([document.fonts.load('600 32px Plex'), document.fonts.load('400 24px Plex'), document.fonts.load('400 20px PlexMono')]).then(() => document.fonts.ready));
  const ok = await page.evaluate(() => document.fonts.check('600 32px Plex') && document.fonts.check('400 24px Plex') && document.fonts.check('400 20px PlexMono'));
  if (!ok) problems.push(`${p.file}: the IBM Plex files did not load (document.fonts.check is false)`);
  if (p.caption !== undefined) {
    const box = await page.evaluate(() => { const r = document.querySelector(".band span").getBoundingClientRect(); return { w: r.width, h: r.height }; });
    const lines = Math.round(box.h / S.band.line);
    if (box.w > S.band.maxW + 0.5 || lines > S.band.lines) problems.push(`${p.file}: the caption is ${Math.round(box.w)} px wide and ${lines} line(s); the band allows ${S.band.maxW} px and ${S.band.lines} line(s)`);
    p.box = box;
  }
  await page.screenshot({ path: path.join(out, p.file), omitBackground: true, type: "png", ...(p.clip ? { clip: p.clip } : {}) });
  rmSync(f);
  written.push(p.file);
}
await browser.close();
writeFileSync(path.join(out, "plates.json"), JSON.stringify({ cut: meta.cut, releaseClaims: claims, files: written, captions: plates.filter((p) => p.caption !== undefined).map((p) => ({ file: p.file, box: p.box })) }, null, 2));
if (problems.length) {
  console.error(`video-plates: ${problems.length} problem(s)\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log(`video-plates: ${written.length} plates in ${path.relative(root, out)}${claims ? " (including end-claims.png: RELEASE_CLAIMS=1)" : ""}`);
