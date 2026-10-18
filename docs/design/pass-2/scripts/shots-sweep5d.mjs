// One-off script of design pass 2, kept as a record. It imports @playwright/test, so copy it into apps/web/scripts to run it (paths below are relative to apps/web).
// Design pass 2 step 5d: the cross-app consistency sweep. One screenshot per top-level area at 1440 light (login at its own
// URL), the same frame and wait for each, so the screens can be compared side by side. Usage: node scripts/shots-sweep5d.mjs
// [baseUrl] [scheme]. Needs the fixtures server (port 3110).
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://localhost:3110";
const scheme = process.argv[3] ?? "light";
const OUT = "../../docs/screenshots/pass-2";
import { SCREENS } from "./screens5d.mjs";
const browser = await chromium.launch({ channel: "chrome" });
for (const [name, url] of SCREENS) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: scheme,
    reducedMotion: "reduce",
  });
  const page = await ctx.newPage();
  await page.goto(base + url);
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/step5d-sweep-${name}-1440-${scheme}.png` });
  await ctx.close();
}
await browser.close();
