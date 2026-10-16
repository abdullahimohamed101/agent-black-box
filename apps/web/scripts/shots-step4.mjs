// Design pass 2 step 4: screenshots of two screens (Runs list, a failed run) in light and dark at 1440 and 375.
// Usage: node scripts/shots-step4.mjs <before|after> [baseUrl]. Needs a running fixtures server (port 3100 by default).
import { chromium } from "@playwright/test";

const phase = process.argv[2] ?? "after";
const base = process.argv[3] ?? "http://localhost:3100";
const run = `run_${String(2).padStart(26, "0")}`;
const pages = [
  ["runs", `/w/demo/projects/all/runs`],
  ["run", `/w/demo/projects/all/runs/${run}`],
];
const browser = await chromium.launch({ channel: "chrome" });
for (const scheme of ["light", "dark"]) {
  for (const [w, h] of [
    [1440, 900],
    [375, 812],
  ]) {
    const ctx = await browser.newContext({
      viewport: { width: w, height: h },
      colorScheme: scheme,
      reducedMotion: "reduce",
    });
    const page = await ctx.newPage();
    for (const [name, url] of pages) {
      await page.goto(base + url);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(400);
      await page.screenshot({
        path: `../../docs/screenshots/pass-2/step4-${phase}-${name}-${w}-${scheme}.png`,
      });
    }
    await ctx.close();
  }
}
await browser.close();
