import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Phase 22 step 9 (D12): compare opens with the divergence line, analytics opens with where the money was wasted.
const BASE = "/w/demo/projects/all";
const VIEWER = "/w/viewer-only/projects/all";
const SHOTS = "../../docs/screenshots/phase-22/step-9";
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
const COMPARE = (b: string) => `${b}/runs/compare?left=${RUN(11)}&right=${RUN(12)}`;

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}
const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

for (const [w, h] of [
  [1440, 900],
  [375, 812],
] as const) {
  for (const scheme of ["light", "dark"] as const) {
    test.describe(`${w}px ${scheme}`, () => {
      test.use({ viewport: { width: w, height: h }, colorScheme: scheme });

      test("compare: divergence line first, a cut across the steps, deltas on one line", async ({
        page,
      }) => {
        await page.goto(COMPARE(BASE));
        const line = page.getByTestId("divergence-summary");
        await expect(line).toBeVisible();
        await expect(line).toContainText(/diverged at step \d+: A .+, B .+\./);
        const family = await line.evaluate((el) => getComputedStyle(el).fontFamily);
        expect(family).toMatch(/serif/i);
        // The line leads the page: nothing but the title and itself above the fold's first block.
        const box = await line.boundingBox();
        expect(box!.y).toBeLessThan(h / 2);
        await expect(page.getByTestId("compare-deltas").locator("[data-delta]")).toHaveCount(5);
        // The cut: one rule across the divergence row, drawn by CSS, and tinted rows after it.
        const rule = await page
          .locator("[data-divergence=true]")
          .evaluate((el) => getComputedStyle(el, "::before").height);
        expect(rule).toBe("2px");
        expect(await page.locator("[data-after-divergence=true]").count()).toBeGreaterThan(0);
        expect(await overflow(page)).toBeLessThanOrEqual(0);
        await axe(page);
        await page.screenshot({ path: `${SHOTS}/compare-${w}-${scheme}.png` });
      });

      test("analytics opens with where the money was wasted; the charts stay below", async ({
        page,
      }) => {
        await page.goto(`${BASE}/analytics`);
        const panel = page.getByTestId("waste-panel");
        await expect(panel).toBeVisible();
        await expect(panel.getByTestId("waste-line")).toContainText("wasted");
        await expect(panel.getByRole("list", { name: "Wasted spend by agent" })).toBeVisible();
        await expect(panel.getByRole("list", { name: "Wasted spend by model" })).toBeVisible();
        const panelY = (await panel.boundingBox())!.y;
        const costY = (await page.getByRole("heading", { name: "Cost", level: 2 }).boundingBox())!
          .y;
        expect(panelY).toBeLessThan(costY);
        expect(await overflow(page)).toBeLessThanOrEqual(0);
        await axe(page);
        await page.screenshot({ path: `${SHOTS}/analytics-${w}-${scheme}.png` });
      });
    });
  }
}

test.describe("viewers", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a viewer's divergence line carries no command or path", async ({ page }) => {
    await page.goto(COMPARE(VIEWER));
    const line = page.getByTestId("divergence-summary");
    await expect(line).toBeVisible();
    const text = (await line.innerText()).toLowerCase();
    expect(text).toMatch(/diverged at step \d+/);
    // The fixtures' content steps (a shell command, a file path) are named "withheld", never spelled out.
    expect(text).not.toMatch(/pytest|\.py|\/app\//);
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/compare-viewer-1440-light.png` });
  });

  test("a viewer sees the same wasted-spend figures (metadata only)", async ({ page }) => {
    await page.goto(`${VIEWER}/analytics`);
    await expect(page.getByTestId("waste-panel").getByTestId("waste-line")).toContainText("wasted");
  });
});
