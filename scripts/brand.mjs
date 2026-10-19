// Brand assets from one source (Phase 20, D5): `brand/pulse-mark.svg` is the pulse glyph (placeholder launch mark). This script writes
//   site/assets/{favicon.svg, favicon-32.png, favicon-180.png, icon-512.png, social-card.png, readme-header.svg}
// Colours come from apps/web/design-tokens/tokens.json (never typed here), fonts from apps/web/src/fonts (IBM Plex, OFL).
// Swapping the mark later means replacing brand/pulse-mark.svg (and the web copy, apps/web/public/brand/option-a.svg) and rerunning
// `scripts/brand.sh`. `--check` only verifies the web copy equals the source and exits.
// Rasterising uses the Chromium that Playwright already provides to apps/web; no new dependency.
import { createRequire } from "node:module";
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const out = path.join(root, "site/assets");
const source = readFileSync(path.join(root, "brand/pulse-mark.svg"), "utf8");

if (process.argv.includes("--check")) {
  const web = readFileSync(path.join(root, "apps/web/public/brand/option-a.svg"), "utf8");
  if (web !== source) {
    console.error("brand: apps/web/public/brand/option-a.svg differs from brand/pulse-mark.svg (copy the source over it)");
    process.exit(1);
  }
  console.log("brand: web copy equals brand/pulse-mark.svg");
  process.exit(0);
}

const tokens = JSON.parse(readFileSync(path.join(root, "apps/web/design-tokens/tokens.json"), "utf8"));
const primitive = tokens.color.primitive;
const pick = (family, step) => primitive[family][step].value;

// The glyph's paths, with the stroke widths used at small sizes (the thin cut would vanish at 16 px otherwise).
const inner = source.slice(source.indexOf(">") + 1, source.lastIndexOf("</svg>")).trim();
if (!inner.startsWith("<path")) throw new Error("brand/pulse-mark.svg: expected path elements");
const sturdy = inner.replace('stroke-width="1.5"', 'stroke-width="1.75"');
const glyph = (stroke, { width = 2, paths = inner, transform = "" } = {}) =>
  `<g${transform ? ` transform="${transform}"` : ""} fill="none" stroke="${stroke}" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round">${paths}</g>`;

const require = createRequire(path.join(root, "apps/web/package.json"));
const { chromium } = require("@playwright/test");
const browser = await chromium.launch();
const context = await browser.newContext({ deviceScaleFactor: 1 });
const page = await context.newPage();

// Token values (hex or oklch) to #rrggbb, resolved by the same engine that renders the assets.
async function hex(value) {
  return page.evaluate((v) => {
    const c = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
    c.fillStyle = "#000"; c.fillStyle = v; c.fillRect(0, 0, 1, 1);
    const [r, g, b] = c.getImageData(0, 0, 1, 1).data;
    return "#" + [r, g, b].map((n) => n.toString(16).padStart(2, "0")).join("");
  }, value);
}
await page.goto("about:blank");
const ink = {
  inkLight: await hex(pick("neutral", "800")),
  inkDark: await hex(pick("neutral", "100")),
  mutedLight: await hex(pick("neutral", "500")),
  mutedDark: await hex(pick("neutral", "300")),
  bgLight: await hex(pick("neutral", "50")),
  bgDark: await hex(pick("neutral", "900")),
  markLight: await hex(pick("orange", "600")),
  markDark: await hex(pick("orange", "400")),
};

const write = (name, text) => writeFileSync(path.join(out, name), text.endsWith("\n") ? text : text + "\n");
mkdirSync(out, { recursive: true });

// favicon.svg: the glyph in the ink colour, light or dark by the visitor's setting (same idea as apps/web/src/app/icon.svg).
write(
  "favicon.svg",
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"><style>path{stroke:${ink.inkLight}}@media (prefers-color-scheme:dark){path{stroke:${ink.inkDark}}}</style>${sturdy}</svg>`,
);

// App tiles: the glyph in the brand colour on the dark canvas, so it reads on any browser chrome. `square` has no rounded corners
// (the platform rounds touch icons itself).
const tile = (size, { square = false } = {}) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24"><rect width="24" height="24" ${square ? "" : 'rx="5.5" '}fill="${ink.bgDark}"/>${glyph(ink.markDark, { width: 2.25, paths: sturdy, transform: "translate(4.2 4.2) scale(0.65)" })}</svg>`;

async function rasterise(svgOrHtml, file, width, height, { transparent = false, html = false } = {}) {
  await page.setViewportSize({ width, height });
  if (html) await page.goto(pathToFileURL(svgOrHtml).href);
  else await page.setContent(`<style>html,body{margin:0;background:transparent}</style>${svgOrHtml}`);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(out, file), omitBackground: transparent, clip: { x: 0, y: 0, width, height } });
}
await rasterise(tile(32), "favicon-32.png", 32, 32, { transparent: true });
await rasterise(tile(180, { square: true }), "favicon-180.png", 180, 180);
await rasterise(tile(512), "icon-512.png", 512, 512, { transparent: true });

const fontFile = (name) => path.join(root, "apps/web/src/fonts", name);
const fontUrl = (name) => pathToFileURL(fontFile(name)).href;
const fontData = (name) => `data:font/woff2;base64,${readFileSync(fontFile(name)).toString("base64")}`;
const NAME = "Agent BlackBox";
const TAGLINE = "Flight recorder and debugger for AI agents";

// Social card 1200x630, rendered from a throwaway HTML page (fonts by file URL) in a temp directory that is removed afterwards.
const scratch = mkdtempSync(path.join(tmpdir(), "abb-brand-"));
const cardHtml = path.join(scratch, "card.html");
writeFileSync(
  cardHtml,
  `<!doctype html><meta charset="utf-8"><style>
@font-face{font-family:"Plex";font-weight:600;src:url("${fontUrl("IBMPlexSans-SemiBold-Latin1.woff2")}") format("woff2")}
@font-face{font-family:"Plex";font-weight:400;src:url("${fontUrl("IBMPlexSans-Regular-Latin1.woff2")}") format("woff2")}
html,body{margin:0;width:1200px;height:630px;background:${ink.bgDark};font-family:"Plex",sans-serif}
.card{box-sizing:border-box;width:1200px;height:630px;padding:96px;display:flex;flex-direction:column;justify-content:center;gap:28px}
.mark{width:176px;height:176px;margin-left:-14px}
h1{margin:0;font-weight:600;font-size:96px;line-height:1.05;color:${ink.inkDark};letter-spacing:-1px}
p{margin:12px 0 0;font-weight:400;font-size:40px;line-height:1.25;color:${ink.mutedDark}}
</style><div class="card"><svg class="mark" viewBox="0 0 24 24">${glyph(ink.markDark, { width: 2, paths: sturdy })}</svg>
<div><h1>${NAME}</h1><p>${TAGLINE}</p></div></div>`,
);
await rasterise(cardHtml, "social-card.png", 1200, 630, { html: true });
await browser.close();
rmSync(scratch, { recursive: true, force: true });

// README header: an SVG shown through <img>, so it cannot load fonts from elsewhere: IBM Plex is embedded as data URIs.
write(
  "readme-header.svg",
  `<svg xmlns="http://www.w3.org/2000/svg" width="880" height="160" viewBox="0 0 880 160" role="img" aria-label="${NAME}: ${TAGLINE}">
<title>${NAME}</title>
<style>
@font-face{font-family:"Plex";font-weight:600;src:url("${fontData("IBMPlexSans-SemiBold-Latin1.woff2")}") format("woff2")}
@font-face{font-family:"Plex";font-weight:400;src:url("${fontData("IBMPlexSans-Regular-Latin1.woff2")}") format("woff2")}
.mark{stroke:${ink.markLight}}.name{fill:${ink.inkLight}}.tag{fill:${ink.mutedLight}}
@media (prefers-color-scheme:dark){.mark{stroke:${ink.markDark}}.name{fill:${ink.inkDark}}.tag{fill:${ink.mutedDark}}}
text{font-family:"Plex",ui-sans-serif,system-ui,sans-serif}
</style>
<g class="mark" transform="translate(8 28) scale(4.2)" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${sturdy}</g>
<text class="name" x="132" y="86" font-size="56" font-weight="600">${NAME}</text>
<text class="tag" x="134" y="124" font-size="26" font-weight="400">${TAGLINE}</text>
</svg>`,
);
console.log("brand: wrote", ["favicon.svg", "favicon-32.png", "favicon-180.png", "icon-512.png", "social-card.png", "readme-header.svg"].join(", "));
