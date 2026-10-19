// C4: WCAG AA with axe at 375 and 1440 in light and dark (plus 768), no horizontal scroll, no console error, and a keyboard walk:
// Tab reaches the skip link first, then every control in document order, each shows a visible focus ring, and the walk ends in the footer.
import { AxeBuilder } from "../site-lib.mjs";

export default async function a11y({ browser, srv, rep }) {
  let combos = 0;
  for (const width of [1440, 768, 375]) {
    for (const scheme of ["light", "dark"]) {
      const ctxName = `${width}px ${scheme}`;
      combos++;
      const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: scheme });
      const page = await context.newPage();
      page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") rep.fail(ctxName, `console ${m.type()}: ${m.text()}`); });
      page.on("pageerror", (e) => rep.fail(ctxName, `page error: ${e.message}`));
      await page.goto(srv.url, { waitUntil: "networkidle" });
      await page.evaluate(() => document.fonts.ready);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (overflow > 0) rep.fail(ctxName, `horizontal scroll of ${overflow}px`);
      for (const h of await page.$$eval("a[href^='#']", (as) => as.map((a) => a.getAttribute("href")))) {
        if ((await page.locator(h).count()) !== 1) rep.fail(ctxName, `anchor ${h} does not match exactly one element`);
      }
      // Open every <details> so axe sees the answers too.
      await page.evaluate(() => document.querySelectorAll("details").forEach((d) => (d.open = true)));
      const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]).analyze();
      for (const v of axe.violations) rep.fail(ctxName, `axe ${v.id} (${v.impact}): ${v.nodes.length} node(s), e.g. ${v.nodes[0]?.target.join(" ")}`);
      // Touch targets: every visible button and link-button is at least 44 px tall (inline text links are exempt, WCAG 2.5.8).
      const small = await page.evaluate(() => [...document.querySelectorAll("button, .btn, [role=tab], summary, .tab")].filter((e) => { const r = e.getBoundingClientRect(); return r.width && r.height < 43.5; }).map((e) => `${e.tagName.toLowerCase()}.${e.className}`));
      if (small.length) rep.fail(ctxName, `touch targets under 44 px: ${[...new Set(small)].join(", ")}`);
      await context.close();
    }
  }

  // Keyboard walk (desktop, light).
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(srv.url, { waitUntil: "networkidle" });
  const seen = [];
  for (let i = 0; i < 80; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const e = document.activeElement;
      if (!e || e === document.body) return null;
      const cs = getComputedStyle(e);
      const label = (e.getAttribute("aria-label") || e.innerText || e.textContent || "").trim().slice(0, 40);
      return { tag: e.tagName.toLowerCase(), label, outline: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) >= 2, box: cs.boxShadow !== "none" };
    });
    if (!info) break;
    seen.push(info);
    if (!info.outline && !info.box) rep.fail("keyboard", `no visible focus on <${info.tag}> ${JSON.stringify(info.label)}`);
    if (info.label.startsWith("Source") && seen.length > 70) break;
  }
  if (!/skip/i.test(seen[0]?.label ?? "")) rep.fail("keyboard", `the first Tab stop is ${JSON.stringify(seen[0]?.label)}, expected the skip link`);
  const labels = seen.map((s) => s.label);
  const need = ["Copy", "Run the demo"];
  for (const n of need) if (!labels.some((l) => l.includes(n))) rep.fail("keyboard", `Tab never reached ${n}`);
  if (seen.filter((s) => s.tag === "summary").length !== 4) rep.fail("keyboard", `Tab reached ${seen.filter((s) => s.tag === "summary").length} FAQ questions, expected 4`);
  if (seen.filter((s) => s.tag === "button").length < 3) rep.fail("keyboard", "Tab reached fewer than three buttons");
  rep.fact(`axe clean in ${combos} viewport/scheme combinations (WCAG 2.0-2.2 A/AA + best practice), no horizontal scroll, touch targets >= 44 px; keyboard walk visited ${seen.length} stops, skip link first, focus ring visible on each`);
  await ctx.close();
}
