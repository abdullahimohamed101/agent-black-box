// The gallery island: a real tablist (arrow keys, Home/End), one visible panel, callouts that land on the same source pixels at
// every width, and the colour scheme chooses the image.
import { readFileSync } from "node:fs";
const PANELS = JSON.parse(readFileSync(new URL("../../site/screenshots.json", import.meta.url), "utf8")).panels.length; // the manifest decides how many views there are
export default async function gallery({ srv, browser, rep }) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });
  const page = await context.newPage();
  await page.goto(srv.url, { waitUntil: "networkidle" });
  const tabs = page.getByRole("tab");
  const n = await tabs.count();
  if (n !== PANELS) rep.fail("gallery", `${n} tabs, expected ${PANELS}`);
  const visible = async () => page.locator('#screens [role="tabpanel"]:not([hidden])').count();
  if ((await visible()) !== 1) rep.fail("gallery", "expected exactly one visible panel after hydration");
  await tabs.first().focus();
  await page.keyboard.press("ArrowRight");
  if ((await tabs.nth(1).getAttribute("aria-selected")) !== "true") rep.fail("gallery", "ArrowRight did not select the second tab");
  await page.keyboard.press("End");
  if ((await tabs.nth(n - 1).getAttribute("aria-selected")) !== "true") rep.fail("gallery", "End did not select the last tab");
  await page.keyboard.press("ArrowRight");
  if ((await tabs.first().getAttribute("aria-selected")) !== "true") rep.fail("gallery", "ArrowRight on the last tab did not wrap");
  const sel = await page.locator('#screens [role="tabpanel"]:not([hidden]) img').getAttribute("src");
  if (!/-light\.png$/.test(sel ?? "")) rep.fail("gallery", `light scheme img src is ${sel}`);
  const img = page.locator('#screens [role="tabpanel"]:not([hidden]) img');
  await img.scrollIntoViewIfNeeded();
  await img.evaluate((i) => (i.complete ? null : new Promise((r) => i.addEventListener("load", r, { once: true }))));
  const current = await img.evaluate((i) => i.currentSrc);
  if (!/-light\.webp$/.test(current)) rep.fail("gallery", `light scheme shows ${current}, expected the WebP`);
  await context.close();

  const dark = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
  const dp = await dark.newPage();
  await dp.goto(srv.url, { waitUntil: "networkidle" });
  const dimg = dp.locator('#screens [role="tabpanel"]:not([hidden]) img');
  await dimg.scrollIntoViewIfNeeded();
  await dimg.evaluate((i) => (i.complete ? null : new Promise((r) => i.addEventListener("load", r, { once: true }))));
  const dcur = await dimg.evaluate((i) => i.currentSrc);
  if (!/-dark\.webp$/.test(dcur)) rep.fail("gallery", `dark scheme shows ${dcur}, expected the dark WebP`);
  await dark.close();

  // Callout geometry: the same source-pixel box lands on the same fraction of the image at 1440 and 375.
  const frac = {};
  for (const w of [1440, 375]) {
    const c = await browser.newContext({ viewport: { width: w, height: 800 } });
    const p = await c.newPage();
    await p.goto(srv.url, { waitUntil: "networkidle" });
    frac[w] = await p.evaluate(() => {
      const shot = document.querySelector('#screens [role="tabpanel"]:not([hidden]) .shot');
      const img = shot.querySelector("img").getBoundingClientRect();
      const co = shot.querySelector(".co").getBoundingClientRect();
      return [(co.left - img.left) / img.width, (co.top - img.top) / img.width, co.width / img.width];
    });
    await c.close();
  }
  for (let i = 0; i < 3; i++) {
    const a = frac[1440][i], b = frac[375][i];
    if (Math.abs(a - b) > 0.01) rep.fail("gallery", `callout is at ${a.toFixed(3)} of the image at 1440 but ${b.toFixed(3)} at 375`);
  }
  rep.fact("tablist keys, one visible panel, scheme-chosen WebP, callout geometry identical at 1440 and 375 (relative to the image)");
}
