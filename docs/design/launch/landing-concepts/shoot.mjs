// Screenshots the concept mockups at 1440 and 375, light and dark, with Playwright Chromium (apps/web's install).
//   node docs/design/launch/landing-concepts/shoot.mjs [concept-a concept-b]
// Writes shots/<concept>-<width>-<scheme>.png next to this file. Full page, so the whole narrative is reviewable.
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(new URL("../../../../apps/web/package.json", import.meta.url));
const { chromium } = require("@playwright/test");
const here = path.dirname(fileURLToPath(import.meta.url));
const names = process.argv.slice(2).length ? process.argv.slice(2) : ["concept-a", "concept-b"];

const browser = await chromium.launch();
for (const name of names) {
  for (const [width, height] of [[1440, 900], [375, 812]]) {
    for (const scheme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: scheme, deviceScaleFactor: 1, reducedMotion: "reduce" }); // full page: final static state; motion is checked in the hero shot below
      const page = await ctx.newPage();
      await page.goto(pathToFileURL(path.join(here, `${name}.html`)).href);
      await page.waitForTimeout(1200); // let the fault line draw
      const over = await page.evaluate((w) => [...document.querySelectorAll("body *")].filter(e => e.getBoundingClientRect().right > w + 1).slice(0, 6).map(e => e.tagName + "." + e.className + " r=" + Math.round(e.getBoundingClientRect().right)), width);
      if (over.length) console.log(`WARN ${name} ${width}: overflow`, over);
      await page.screenshot({ path: path.join(here, "shots", `${name}-${width}-${scheme}.png`), fullPage: true });
      await ctx.close();
    }
  }
  // Hero only, motion on, after the fault line has drawn: what a first visitor sees at 1440.
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "light", reducedMotion: "no-preference" });
  const page = await ctx.newPage(); await page.goto(pathToFileURL(path.join(here, `${name}.html`)).href); await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(here, "shots", `${name}-hero-1440-motion.png`) }); await ctx.close();
}
await browser.close();
console.log("done");
