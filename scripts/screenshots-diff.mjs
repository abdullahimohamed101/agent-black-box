// Compares two directories of release screenshots (scripts/screenshots.sh --verify): identical bytes pass; otherwise both PNGs are
// decoded in headless Chromium (canvas, no image dependency) and the pixels that differ are counted. A pixel differs when any
// channel is off by more than CHANNEL_TOLERANCE (anti-aliasing noise); an image fails when more than MAX_DIFF_PERCENT of its
// pixels differ. Prints, per image, the verdict and the bounding box of the differing pixels.
//   node scripts/screenshots-diff.mjs <dirA> <dirB> [--max-percent=0.05]
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { launch } from "./site-lib.mjs";

const args = process.argv.slice(2);
const [a, b] = args.filter((x) => !x.startsWith("--"));
const maxPercent = Number(args.find((x) => x.startsWith("--max-percent="))?.split("=")[1] ?? 0.05);
const CHANNEL_TOLERANCE = 8;
if (!a || !b) { console.error("usage: node scripts/screenshots-diff.mjs <dirA> <dirB> [--max-percent=0.05]"); process.exit(2); }

const names = [...new Set([...readdirSync(a), ...readdirSync(b)].filter((f) => f.endsWith(".png")))].sort();
const browser = await launch();
const page = await browser.newPage();
await page.goto("about:blank");
let failed = 0;
for (const name of names) {
  const fa = path.join(a, name), fb = path.join(b, name);
  if (!existsSync(fa) || !existsSync(fb)) { console.log(`FAIL ${name}: present in only one directory`); failed++; continue; }
  const ba = readFileSync(fa), bb = readFileSync(fb);
  if (ba.equals(bb)) { console.log(`same ${name} (identical bytes)`); continue; }
  const r = await page.evaluate(async ({ x, y, tol }) => {
    const load = async (b64) => { const i = new Image(); i.src = "data:image/png;base64," + b64; await i.decode(); const c = document.createElement("canvas"); c.width = i.naturalWidth; c.height = i.naturalHeight; const g = c.getContext("2d"); g.drawImage(i, 0, 0); return g.getImageData(0, 0, c.width, c.height); };
    const [p, q] = [await load(x), await load(y)];
    if (p.width !== q.width || p.height !== q.height) return { size: true };
    let n = 0, x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
    for (let i = 0; i < p.data.length; i += 4) {
      if (Math.abs(p.data[i] - q.data[i]) > tol || Math.abs(p.data[i + 1] - q.data[i + 1]) > tol || Math.abs(p.data[i + 2] - q.data[i + 2]) > tol) {
        n++; const px = (i / 4) % p.width, py = Math.floor(i / 4 / p.width);
        x0 = Math.min(x0, px); y0 = Math.min(y0, py); x1 = Math.max(x1, px); y1 = Math.max(y1, py);
      }
    }
    return { n, total: p.width * p.height, box: n ? [x0, y0, x1, y1] : null };
  }, { x: ba.toString("base64"), y: bb.toString("base64"), tol: CHANNEL_TOLERANCE });
  if (r.size) { console.log(`FAIL ${name}: different pixel sizes`); failed++; continue; }
  const pct = (100 * r.n) / r.total;
  const ok = pct <= maxPercent;
  if (!ok) failed++;
  console.log(`${ok ? "near" : "FAIL"} ${name}: ${r.n} of ${r.total} pixels differ (${pct.toFixed(4)}%, limit ${maxPercent}%)${r.box ? ` in box x ${r.box[0]}-${r.box[2]}, y ${r.box[1]}-${r.box[3]}` : ""}`);
}
await browser.close();
console.log(failed ? `${failed} image(s) differ beyond the limit` : `all ${names.length} images identical or within ${maxPercent}% of pixels`);
process.exit(failed ? 1 : 0);
