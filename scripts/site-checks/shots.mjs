// AC11: full-page screenshots of the export at 375 and 1440, light and dark, written as site-<width>-<scheme>.png into
// $SHOTS_DIR (docs/screenshots/release for the committed set; unset in CI, which then only asserts no horizontal overflow).
// The page is scrolled through first so lazy images and content-visibility sections are painted, and the fault line has drawn.
import { mkdir } from "node:fs/promises";
import path from "node:path";

export default async function shots({ browser, srv, rep }) {
  const dir = process.env.SHOTS_DIR ? path.resolve(process.env.SHOTS_DIR) : null;
  if (dir) await mkdir(dir, { recursive: true });
  const written = [];
  for (const [width, height] of [[1440, 900], [375, 812]]) {
    for (const scheme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: scheme, deviceScaleFactor: 1 });
      const page = await ctx.newPage();
      await page.goto(srv.url, { waitUntil: "networkidle" });
      await page.waitForTimeout(1500);
      const total = await page.evaluate(() => document.documentElement.scrollHeight);
      for (let y = 0; y < total; y += height * 0.7) { await page.evaluate((v) => window.scrollTo(0, v), y); await page.waitForTimeout(120); }
      await page.waitForLoadState("networkidle");
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(300);
      const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (over !== 0) rep.fail(`${width}px ${scheme}`, `document is ${over}px wider than the viewport`);
      if (dir) {
        // A full-page capture does not paint sections the page skips with content-visibility: auto; show them for the picture.
        await page.addStyleTag({ content: "section.band { content-visibility: visible !important; }" });
        await page.waitForTimeout(300);
        const file = path.join(dir, `site-${width}-${scheme}.png`);
        await page.screenshot({ path: file, fullPage: true });
        written.push(path.basename(file));
      }
      await ctx.close();
    }
  }
  rep.fact(dir ? `wrote ${written.join(", ")} to ${dir}; no horizontal overflow` : "no horizontal overflow at 375 and 1440, light and dark (SHOTS_DIR unset: no files written)");
}
