// Shared helpers for the browser checks of a BUILT landing page (scripts/site-checks.mjs, scripts/site-checks/*.mjs).
// Serves a static export on a loopback port under a base path, launches Chromium, collects failures. Reuses apps/web's Playwright
// and axe: no dependency of its own.
import { createServer } from "node:http";
import { readFile, mkdir, stat } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(REPO, "apps/web/package.json"));
export const { chromium } = require("@playwright/test");
export const AxeBuilder = require("@axe-core/playwright").default;

const TYPES = {
  ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json",
  ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8",
  ".webmanifest": "application/manifest+json", ".mp4": "video/mp4", ".webm": "video/webm", ".vtt": "text/vtt",
};

/** Serves DIR under `base`. Returns { origin, url, served, close }. `served` records every path requested (for outside-the-base detection). */
export async function serve(dir, base = "/agent-black-box") {
  const prefix = base === "" ? "/" : base.replace(/\/?$/, "/");
  const served = [];
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    served.push(url.pathname);
    if (!url.pathname.startsWith(prefix)) return void res.writeHead(404).end("not under the base path");
    let rel = decodeURIComponent(url.pathname.slice(prefix.length));
    let file = path.join(dir, rel);
    if (!file.startsWith(dir)) return void res.writeHead(404).end();
    try {
      if ((await stat(file)).isDirectory()) file = path.join(file, "index.html");
      const body = await readFile(file);
      res.writeHead(200, { "content-type": TYPES[path.extname(file)] ?? "application/octet-stream", "cache-control": "no-store" }).end(body);
    } catch {
      res.writeHead(404).end("not found");
    }
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const origin = `http://127.0.0.1:${server.address().port}`;
  return { origin, url: origin + prefix, prefix, served, close: () => new Promise((r) => server.close(r)) };
}

export async function launch() {
  try {
    return await chromium.launch({ channel: "chrome" });
  } catch {
    return await chromium.launch();
  }
}

export function parseArgs(argv) {
  const rest = argv.filter((a, i) => !(a === "--base" || argv[i - 1] === "--base"));
  const baseIdx = argv.indexOf("--base");
  return { cmd: rest[0], dir: path.resolve(rest[1] ?? path.join(REPO, "site/out")), base: baseIdx >= 0 ? argv[baseIdx + 1] : "/agent-black-box", flags: rest.slice(2) };
}

export class Report {
  failures = [];
  facts = [];
  fail(ctx, msg) { this.failures.push(`${ctx}: ${msg}`); }
  fact(msg) { this.facts.push(msg); }
  finish(name) {
    if (this.failures.length) {
      for (const f of [...new Set(this.failures)]) console.error(`FAIL [${name}]: ${f}`);
      process.exit(1);
    }
    for (const f of this.facts) console.log(`  ${f}`);
    console.log(`${name} check passed`);
  }
}

