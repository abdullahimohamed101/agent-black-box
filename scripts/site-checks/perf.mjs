// C5: LCP, CLS and long tasks under a throttled profile (no Lighthouse dependency): 375x812, CPU slowed 4x, 1.6 Mbps down,
// 750 kbps up, 150 ms RTT, served gzip-compressed as Pages serves it. Five runs, the median counts. LCP and long tasks are read at
// the moment the islands answer (the Copy button is enabled), i.e. before the scroll and the gallery click that follow; CLS is
// read after scrolling the whole page. Limits: LCP 2.0 s, CLS 0.02, long tasks 200 ms in total, interactive within 1.5 s of LCP.
// Timing is ADVISORY by default: LCP, long tasks and time-to-interactive depend on the machine (shared CI runners under CPU x4 are
// noisy), so they are printed and never fail. SITE_PERF_STRICT=1 (local use, on a quiet machine) gates them at the limits above, repeating
// a failing attempt up to three times. CLS does not depend on speed, so it is a gate in both modes.
const median = (a) => [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)];
const LIMITS = { lcp: 2000, cls: 0.02, long: 200, hyd: 1500 };
const STRICT = process.env.SITE_PERF_STRICT === "1";

async function attempt(browser, srv) {
  const runs = [];
  for (let i = 0; i < 5; i++) {
    const ctx = await browser.newContext({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    const page = await ctx.newPage();
    await page.addInitScript(() => {
      window.__m = { lcp: 0, lcpEl: "", cls: 0, long: 0 };
      new PerformanceObserver((l) => { for (const e of l.getEntries()) { window.__m.lcp = e.startTime; window.__m.lcpEl = (e.element?.tagName ?? "") + ":" + (e.element?.textContent ?? "").slice(0, 30).replace(/\s+/g, " "); } }).observe({ type: "largest-contentful-paint", buffered: true });
      new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__m.cls += e.value; }).observe({ type: "layout-shift", buffered: true });
      new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__m.long += e.duration; }).observe({ type: "longtask", buffered: true });
    });
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 150, downloadThroughput: (1.6 * 1024 * 1024) / 8, uploadThroughput: (750 * 1024) / 8 });
    await page.goto(srv.url, { waitUntil: "load" });
    await page.waitForFunction(() => !document.querySelector("button.copy")?.disabled, null, { timeout: 30000 });
    const at = await page.evaluate(() => ({ now: performance.now(), ...window.__m }));
    // A real interaction: a gallery tab answers a click, then the whole page is scrolled so layout shifts are counted.
    await page.locator("#screens").scrollIntoViewIfNeeded();
    const t = Date.now();
    await page.getByRole("tab").nth(2).click();
    await page.locator("#panel-what-it-cost:not([hidden])").waitFor();
    const tabMs = Date.now() - t;
    for (let y = 0; y < 60; y++) { await page.mouse.wheel(0, 700); await page.waitForTimeout(40); }
    await page.waitForTimeout(500);
    const end = await page.evaluate(() => window.__m);
    runs.push({ lcp: at.lcp, lcpEl: at.lcpEl, long: at.long, longAll: end.long, cls: end.cls, hyd: Math.max(0, at.now - at.lcp), tabMs });
    await ctx.close();
  }
  const f = (k) => median(runs.map((r) => r[k]));
  const r = { lcp: f("lcp"), cls: f("cls"), long: f("long"), longAll: f("longAll"), hyd: f("hyd"), tabMs: f("tabMs"), el: runs[0].lcpEl };
  r.line = `LCP ${r.lcp.toFixed(0)} ms (limit ${LIMITS.lcp}; ${r.el}), CLS ${r.cls.toFixed(4)} (limit ${LIMITS.cls}), long tasks ${r.long.toFixed(0)} ms until interactive (limit ${LIMITS.long}; ${r.longAll.toFixed(0)} ms with the later scroll and click), interactive ${r.hyd.toFixed(0)} ms after LCP (limit ${LIMITS.hyd}), tab click to panel ${r.tabMs} ms`;
  r.bad = [r.lcp > LIMITS.lcp && "LCP", r.cls > LIMITS.cls && "CLS", r.long > LIMITS.long && "long tasks", r.hyd > LIMITS.hyd && "interactive"].filter(Boolean);
  return r;
}

export default async function perf({ browser, srv, rep }) {
  let last;
  for (let n = 1; n <= (STRICT ? 3 : 1); n++) {
    last = await attempt(browser, srv);
    rep.fact(`attempt ${n}, median of 5 on 375x812, CPU x4, 1.6 Mbps, 150 ms RTT: ${last.line}`);
    if (!last.bad.length) return;
  }
  if (STRICT) return rep.fail("perf", `over the limit in all three attempts: ${last.bad.join(", ")}`);
  if (last.bad.includes("CLS")) return rep.fail("perf", `layout shift ${last.cls.toFixed(4)} is over ${LIMITS.cls}`);
  rep.fact(`ADVISORY (not a gate; SITE_PERF_STRICT=1 gates it): over the limit on this machine: ${last.bad.join(", ")}`);
}
