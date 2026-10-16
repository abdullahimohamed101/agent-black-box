// Design pass 2 step 5d: screenshots of Analytics (the three reports, empty, error, the excluded-traces note) and Experiments (list,
// empty, an experiment), light and dark, 1440 and 375 (768 for the main ones). Usage: node scripts/shots-step5d.mjs [baseUrl]
// [nameFilter]. Needs a running fixtures server (port 3110 by default).
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://localhost:3110";
const only = process.argv[3];
const OUT = "../../docs/screenshots/pass-2";
const A = "/w/demo/projects/all/analytics";
const E = "/w/demo/projects/all/experiments";
const EXP = (n) => `exp_${String(n).padStart(26, "0")}`;
/** Answer a request with the fixture body after `patch` has changed it. */
const patched = (pattern, patch) => (page) =>
  page.route(pattern, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    await route.fulfill({ response: res, json: patch(body) });
  });
const states = [
  ["analytics-cost", A, null, null],
  ["analytics-reliability", `${A}?tab=reliability`, null, null],
  ["analytics-performance", `${A}?tab=performance`, null, null],
  [
    "analytics-excluded",
    A,
    patched("**/analytics/cost*", (b) => ({ ...b, excluded_expired_traces: 3 })),
    null,
    [1440, 375],
  ],
  [
    "analytics-empty",
    A,
    patched("**/analytics/cost*", (b) => ({
      ...b,
      headline: { ...b.headline, total_usd: 0 },
      by_model: [],
    })),
    null,
    [1440, 375],
  ],
  [
    "analytics-error",
    `${A}?tab=reliability`,
    (page) =>
      page.route("**/analytics/reliability*", (route) =>
        route.fulfill({
          status: 503,
          json: { error: { code: "ANALYTICS_TIMEOUT", message: "The report took too long." } },
        }),
      ),
    async (p) => p.getByRole("tabpanel").getByRole("alert").waitFor({ timeout: 20000 }),
    [1440, 375],
  ],
  ["experiments", E, null, null],
  [
    "experiments-empty",
    E,
    (page) => page.route("**/api/abb/v1/experiments*", (r) => r.fulfill({ json: { items: [] } })),
    null,
    [1440, 375],
  ],
  ["experiment-controlled", `${E}/${EXP(1)}`, null, null],
  ["experiment-uncontrolled", `${E}/${EXP(2)}`, null, null, [1440, 375]],
];
const browser = await chromium.launch({ channel: "chrome" });
for (const scheme of ["light", "dark"]) {
  for (const [w, h] of [
    [1440, 900],
    [768, 1024],
    [375, 812],
  ]) {
    for (const [name, url, setup, act, widths] of states) {
      if (only && !name.includes(only)) continue;
      if (
        widths
          ? !widths.includes(w)
          : w === 768 && !/^(analytics-cost|experiment-controlled)$/.test(name)
      )
        continue;
      const ctx = await browser.newContext({
        viewport: { width: w, height: h },
        colorScheme: scheme,
        reducedMotion: "reduce",
      });
      const page = await ctx.newPage();
      if (setup) await setup(page);
      await page.goto(base + url);
      await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(400);
      if (act) {
        await act(page);
        await page.waitForTimeout(300);
      } else await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: `${OUT}/step5d-${name}-${w}-${scheme}.png`,
        fullPage: process.env.FULL === "1",
      });
      await ctx.close();
    }
  }
}
await browser.close();
