// Browser checks for the landing page (Phase 20, AC11, D16). Reuses apps/web's Playwright and axe installs; adds no dependency.
//   node scripts/check_site_browser.mjs BUILT_DIR [--shots DIR]
// Stack-agnostic: BUILT_DIR is any static output (plain HTML, a Next.js export, ...). Serves it under the sub-path /agent-black-box/ on a loopback port (as GitHub Pages serves a project page), then for each
// of 1440, 768 and 375 px in light and dark: loads the page, and fails on any request outside that sub-path (third party, or a
// root-absolute path that would break there), any failed or 4xx/5xx response, any console error or page error, any horizontal
// scroll, or any axe violation (WCAG 2.x A and AA).
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const positional = process.argv.slice(2).filter((a, i, all) => !a.startsWith("--") && all[i - 1] !== "--shots");
const SITE = path.resolve(positional[0] ?? path.join(REPO, "site"));
const BASE = "/agent-black-box/";
const require = createRequire(path.join(REPO, "apps/web/package.json"));
const { chromium } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;

const argv = process.argv.slice(2);
const shotsDir = argv.includes("--shots") ? path.resolve(argv[argv.indexOf("--shots") + 1]) : process.env.SITE_SHOTS_OUT;

const TYPES = { ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8" };
const served = [];
const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  served.push(url.pathname);
  if (!url.pathname.startsWith(BASE)) {
    res.writeHead(404).end("not under the sub-path");
    return;
  }
  let rel = decodeURIComponent(url.pathname.slice(BASE.length)) || "index.html";
  if (rel.endsWith("/")) rel += "index.html";
  const file = path.join(SITE, rel);
  if (!file.startsWith(SITE + path.sep)) {
    res.writeHead(404).end();
    return;
  }
  try {
    const body = await readFile(file);
    res.writeHead(200, { "content-type": TYPES[path.extname(file)] ?? "application/octet-stream" }).end(body);
  } catch {
    res.writeHead(404).end("not found");
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const origin = `http://127.0.0.1:${server.address().port}`;

let browser;
try {
  browser = await chromium.launch({ channel: "chrome" });
} catch {
  browser = await chromium.launch();
}
const failures = [];
const fail = (ctx, msg) => failures.push(`${ctx}: ${msg}`);
let combos = 0;
let requests = 0;
if (shotsDir) await mkdir(shotsDir, { recursive: true });

for (const width of [1440, 768, 375]) {
  for (const scheme of ["light", "dark"]) {
    const ctx = `${width}px ${scheme}`;
    combos++;
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: scheme });
    const page = await context.newPage();
    page.on("request", (r) => {
      requests++;
      const u = new URL(r.url());
      if (u.origin !== origin) fail(ctx, `third-party request ${u.origin}${u.pathname}`);
      else if (!u.pathname.startsWith(BASE)) fail(ctx, `request outside the sub-path: ${u.pathname}`);
    });
    page.on("response", (r) => {
      if (r.status() >= 400) fail(ctx, `HTTP ${r.status()} for ${r.url()}`);
    });
    page.on("requestfailed", (r) => fail(ctx, `request failed: ${r.url()} ${r.failure()?.errorText}`));
    page.on("console", (m) => {
      if (m.type() === "error" || m.type() === "warning") fail(ctx, `console ${m.type()}: ${m.text()}`);
    });
    page.on("pageerror", (e) => fail(ctx, `page error: ${e.message}`));

    await page.goto(`${origin}${BASE}`, { waitUntil: "networkidle" });
    await page.evaluate(() => document.fonts.ready);

    const facts = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      scripts: document.scripts.length,
      fonts: [...document.fonts].filter((f) => f.status === "loaded").map((f) => `${f.family} ${f.weight}`),
      title: document.title,
    }));
    if (facts.overflow > 0) fail(ctx, `horizontal scroll of ${facts.overflow}px`);

    // Under the sub-path the in-page anchors and relative links must resolve (a click on each in-page link lands on its target).
    const hrefs = await page.$$eval("a[href]", (as) => as.map((a) => a.getAttribute("href")));
    for (const h of hrefs.filter((x) => x.startsWith("#"))) {
      if ((await page.locator(h).count()) !== 1) fail(ctx, `anchor ${h} does not match exactly one element`);
    }

    const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]).analyze();
    for (const v of axe.violations) fail(ctx, `axe ${v.id} (${v.impact}): ${v.nodes.length} node(s), e.g. ${v.nodes[0]?.target.join(" ")}`);

    if (shotsDir) await page.screenshot({ path: path.join(shotsDir, `site-${width}-${scheme}.png`), fullPage: true });
    await context.close();
  }
}
await browser.close();
server.close();

const outside = served.filter((p) => !p.startsWith(BASE));
if (outside.length) failures.push(`server saw requests outside the sub-path: ${[...new Set(outside)].join(", ")}`);
if (failures.length) {
  for (const f of [...new Set(failures)]) console.error(`FAIL: ${f}`);
  process.exit(1);
}
console.log(`browser checks passed: ${combos} viewport/scheme combinations, ${requests} requests, all same-origin under ${BASE}, 0 console errors, axe clean`);
