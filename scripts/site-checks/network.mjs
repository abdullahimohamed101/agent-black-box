// C1: zero third-party requests. Loads the page and scrolls to the end (so lazy images load) at 1440 and 375, in both schemes, and
// fails on any request whose origin differs from the page's, any request outside the base path, any 4xx/5xx, any console error.
// Also measures what was downloaded (gzip for text, as Pages serves it): initial view and the whole page, light scheme.
// With SITE_ROOT_DIR (an export built with ABB_SITE_BASE_PATH="") it repeats the run served at "/".
import { gzipSync } from "node:zlib";
import { launch, serve } from "../site-lib.mjs";

const TEXT = /text\/|javascript|json|svg|xml/;

async function visit(browser, srv, label, rep, { measure = false } = {}) {
  let total = 0, initial = 0;
  for (const [w, h] of [[1440, 900], [375, 812]]) {
    for (const scheme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme });
      const page = await ctx.newPage();
      const ctxName = `${label} ${w}px ${scheme}`;
      let bytes = 0, requests = 0, afterLoad = false, atLoad = 0;
      page.on("request", (r) => {
        requests++;
        const u = new URL(r.url());
        if (u.origin !== srv.origin) rep.fail(ctxName, `third-party request ${u.origin}${u.pathname}`);
        else if (!u.pathname.startsWith(srv.prefix)) rep.fail(ctxName, `request outside the base path: ${u.pathname}`);
      });
      page.on("response", async (r) => {
        if (r.status() >= 400) rep.fail(ctxName, `HTTP ${r.status()} for ${r.url()}`);
        try {
          const body = await r.body();
          const type = r.headers()["content-type"] ?? "";
          bytes += TEXT.test(type) ? gzipSync(body, { level: 9 }).length : body.length;
        } catch { /* a redirect or an aborted request has no body */ }
        if (!afterLoad) atLoad = bytes;
      });
      page.on("requestfailed", (r) => rep.fail(ctxName, `request failed: ${r.url()} ${r.failure()?.errorText}`));
      page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") rep.fail(ctxName, `console ${m.type()}: ${m.text()}`); });
      page.on("pageerror", (e) => rep.fail(ctxName, `page error: ${e.message}`));
      await page.goto(srv.url, { waitUntil: "load" });
      await page.waitForLoadState("networkidle");
      afterLoad = true;
      const first = bytes;
      for (let y = 0; y < 60; y++) {
        await page.mouse.wheel(0, h * 0.8);
        await page.waitForTimeout(60);
      }
      await page.waitForLoadState("networkidle");
      if (measure && w === 1440 && scheme === "light") { initial = first; total = bytes; }
      await ctx.close();
    }
  }
  return { initial, total };
}

export default async function network({ browser, srv, rep }) {
  const { initial, total } = await visit(browser, srv, srv.prefix, rep, { measure: true });
  rep.fact(`served at ${srv.prefix}: every request same-origin and under the base; initial view ${(initial / 1024).toFixed(0)} KB, whole page ${(total / 1024).toFixed(0)} KB (gzip text; limit 400 KB and 1.5 MB, light, 1440)`);
  if (initial > 400 * 1024) rep.fail("size", `the first load downloaded ${(initial / 1024).toFixed(0)} KB, over 400 KB`);
  if (total > 1.5 * 1024 * 1024) rep.fail("size", `the whole page downloaded ${(total / 1024 / 1024).toFixed(2)} MB, over 1.5 MB`);
  if (process.env.SITE_ROOT_DIR) {
    const root = await serve(process.env.SITE_ROOT_DIR, "");
    const b2 = await launch();
    try {
      await visit(b2, root, "/", rep);
      rep.fact('served at "/" (an export built with an empty base path): every request same-origin, no 4xx, no console error');
    } finally {
      await b2.close();
      await root.close();
    }
  }
}
