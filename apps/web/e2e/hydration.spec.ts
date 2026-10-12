import { expect, test } from "@playwright/test";

// The run page must hydrate exactly what the server rendered (React #418). The layout's top bar used to start the run
// query before the page boundary hydrated, so the race was lost whenever the machine was busy; several parallel loads
// per run keep the window open. Fixtures build.
const BASE = "/w/demo/projects/all";
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;

for (let i = 1; i <= 6; i++) {
  test(`run pages hydrate without a mismatch (load ${i})`, async ({ page }) => {
    const problems: string[] = [];
    page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
    page.on("pageerror", (e) => problems.push(e.message));
    for (const n of [2, 7, 6]) {
      await page.goto(`${BASE}/runs/${RUN(n)}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
    }
    expect(problems.filter((p) => /418|hydrat/i.test(p))).toEqual([]);
    expect(problems).toEqual([]);
  });
}
