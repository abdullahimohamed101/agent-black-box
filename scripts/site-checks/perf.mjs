// C5: LCP, CLS and long tasks under a throttled profile (no Lighthouse dependency): 375x812, CPU slowed 4x, 1.6 Mbps down,
// 750 kbps up, 150 ms RTT. Three runs, the median counts. Hydration: how long after LCP the Copy button is enabled and a gallery
// tab answers a click. Limits: LCP 2.0 s, CLS 0.02, long tasks 200 ms in total, interactive within 1.5 s of LCP.
const median = (a) => [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)];

export default async function perf({ browser, srv, rep }) {
  const runs = [];
  for (let i = 0; i < 3; i++) {
    const ctx = await browser.newContext({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    const page = await ctx.newPage();
    await page.addInitScript(() => {
      window.__m = { lcp: 0, lcpEl: "", cls: 0, long: 0 };
      new PerformanceObserver((l) => { for (const e of l.getEntries()) { window.__m.lcp = e.startTime; window.__m.lcpEl = (e.element?.tagName ?? "") + ":" + (e.element?.textContent ?? "").slice(0, 30); } }).observe({ type: "largest-contentful-paint", buffered: true });
      new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__m.cls += e.value; }).observe({ type: "layout-shift", buffered: true });
      new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__m.long += e.duration; }).observe({ type: "longtask", buffered: true });
    });
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 150, downloadThroughput: (1.6 * 1024 * 1024) / 8, uploadThroughput: (750 * 1024) / 8 });
    await page.goto(srv.url, { waitUntil: "load" });
    await page.waitForFunction(() => !document.querySelector("button.copy")?.disabled, null, { timeout: 20000 });
    const interactiveAt = await page.evaluate(() => performance.now());
    const lcpAtInteractive = await page.evaluate(() => window.__m.lcp);
    // A real interaction: a gallery tab answers a click (the island works), measured as the time from the click to the new panel.
    await page.locator("#screens").scrollIntoViewIfNeeded();
    const before = Date.now();
    await page.getByRole("tab").nth(2).click();
    await page.locator("#panel-what-it-cost:not([hidden])").waitFor();
    const tabMs = Date.now() - before;
    // Scroll to the end so layout shifts from lazy images and fonts are counted.
    for (let y = 0; y < 60; y++) { await page.mouse.wheel(0, 700); await page.waitForTimeout(40); }
    await page.waitForTimeout(500);
    const m = await page.evaluate(() => window.__m);
    runs.push({ ...m, hydrateAfterLcp: Math.max(0, interactiveAt - lcpAtInteractive), tabMs });
    await ctx.close();
  }
  const f = (k) => median(runs.map((r) => r[k]));
  const lcp = f("lcp"), cls = f("cls"), long = f("long"), hyd = f("hydrateAfterLcp");
  rep.fact(`median of 3 on 375x812, CPU x4, 1.6 Mbps, 150 ms RTT: LCP ${lcp.toFixed(0)} ms (limit 2000; element ${runs[0].lcpEl}), CLS ${cls.toFixed(4)} (limit 0.02), long tasks ${long.toFixed(0)} ms (limit 200), interactive ${hyd.toFixed(0)} ms after LCP (limit 1500), tab click to panel ${f("tabMs")} ms`);
  if (lcp > 2000) rep.fail("perf", `LCP ${lcp.toFixed(0)} ms is over 2000 ms`);
  if (cls > 0.02) rep.fail("perf", `CLS ${cls.toFixed(4)} is over 0.02`);
  if (long > 200) rep.fail("perf", `long tasks total ${long.toFixed(0)} ms, over 200 ms`);
  if (hyd > 1500) rep.fail("perf", `the islands were interactive ${hyd.toFixed(0)} ms after LCP, over 1500 ms`);
}
