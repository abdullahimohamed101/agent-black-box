// Gallery images (Phase 20, brief section 8). For every panel in site/screenshots.json and both colour schemes:
//   source  docs/screenshots/release/<release>-<scheme>.png   (step 12's set), else
//           docs/screenshots/pass-2/<standIn>-<scheme>.png    (a DEV STAND-IN; the panel is marked and the release check fails)
//   output  site/public/assets/shots/<id>-<scheme>.{webp,png} and site/src/content/shots.generated.json
// WebP is encoded by headless Chromium (canvas toBlob, quality 0.82), so no image encoder is a dependency. Fails when a WebP is
// over 120 KB or a PNG over 400 KB (D11's budget), or when a PNG is not 1440 px wide.
//   node scripts/site-images.mjs [--check]     --check only verifies the committed outputs are within budget
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { launch } from "./site-lib.mjs";

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT = path.join(REPO, "site/public/assets/shots");
const GENERATED = path.join(REPO, "site/src/content/shots.generated.json");
const WEBP_MAX = 120 * 1024;
const PNG_MAX = 400 * 1024;

const manifest = JSON.parse(readFileSync(path.join(REPO, "site/screenshots.json"), "utf8"));
const check = process.argv.includes("--check");
const problems = [];

function size(file) {
  const b = readFileSync(file);
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
}
const kb = (n) => `${(n / 1024).toFixed(1)} KB`;

if (check) {
  const gen = JSON.parse(readFileSync(GENERATED, "utf8"));
  for (const p of gen.panels)
    for (const s of ["light", "dark"]) {
      const { webp, png } = p[s];
      const wb = statSync(path.join(OUT, webp)).size, pb = statSync(path.join(OUT, png)).size;
      if (wb > WEBP_MAX) problems.push(`${webp} is ${kb(wb)} (> 120 KB)`);
      if (pb > PNG_MAX) problems.push(`${png} is ${kb(pb)} (> 400 KB)`);
    }
  const stray = existsSync(OUT) ? readdirSync(OUT).filter((f) => !gen.panels.some((p) => [p.light.webp, p.light.png, p.dark.webp, p.dark.png].includes(f))) : [];
  if (stray.length) problems.push(`files in public/assets/shots not in the manifest: ${stray.join(", ")}`);
  report(gen.panels.length * 4);
}

mkdirSync(OUT, { recursive: true });
const browser = await launch();
const page = await browser.newPage();
await page.goto("about:blank");
const panels = [];
let n = 0;
for (const p of manifest.panels) {
  const entry = { id: p.id, standIn: false };
  for (const scheme of ["light", "dark"]) {
    let src = path.join(REPO, "docs/screenshots/release", `${p.release}-${scheme}.png`);
    if (!existsSync(src)) {
      src = path.join(REPO, "docs/screenshots/pass-2", `${p.standIn}-${scheme}.png`);
      entry.standIn = true;
    }
    if (!existsSync(src)) { problems.push(`${p.id}: no ${scheme} source (release or stand-in)`); continue; }
    const { w, h } = size(src);
    if (w !== 1440) problems.push(`${p.id} ${scheme}: source is ${w} px wide, expected 1440`);
    const png = `${p.id}-${scheme}.png`, webp = `${p.id}-${scheme}.webp`;
    copyFileSync(src, path.join(OUT, png));
    const b64 = await page.evaluate(async (data) => {
      const img = new Image();
      img.src = "data:image/png;base64," + data;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.naturalWidth; c.height = img.naturalHeight;
      c.getContext("2d").drawImage(img, 0, 0);
      const blob = await new Promise((r) => c.toBlob(r, "image/webp", 0.82));
      const buf = new Uint8Array(await blob.arrayBuffer());
      let s = ""; for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
      return btoa(s);
    }, readFileSync(src).toString("base64"));
    writeFileSync(path.join(OUT, webp), Buffer.from(b64, "base64"));
    const wb = statSync(path.join(OUT, webp)).size, pb = statSync(path.join(OUT, png)).size;
    if (wb > WEBP_MAX) problems.push(`${webp} is ${kb(wb)} (> 120 KB)`);
    if (pb > PNG_MAX) problems.push(`${png} is ${kb(pb)} (> 400 KB)`);
    console.log(`${p.id.padEnd(16)} ${scheme.padEnd(5)} ${w}x${h}  png ${kb(pb).padStart(9)}  webp ${kb(wb).padStart(8)}${entry.standIn ? "  (stand-in)" : ""}`);
    entry[scheme] = { webp, png, w, h };
    n++;
  }
  panels.push(entry);
}
await browser.close();
writeFileSync(GENERATED, JSON.stringify({ panels }, null, 1) + "\n");
report(n * 2);

function report(count) {
  if (problems.length) {
    for (const p of problems) console.error(`FAIL: ${p}`);
    process.exit(1);
  }
  console.log(`images ok: ${count} files within budget (WebP <= 120 KB, PNG <= 400 KB)`);
  if (check) process.exit(0);
}
