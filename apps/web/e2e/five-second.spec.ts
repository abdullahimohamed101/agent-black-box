import { expect, test, type Locator, type Page } from "@playwright/test";

// Phase 22 acceptance 1, the five-second test: an engineer whose agent just failed opens the run page and, without
// scrolling or clicking, sees where it went wrong, how many retries that caused and what they cost. Fixtures build, failed run.
const RUN = `/w/demo/projects/all/runs/run_${String(2).padStart(26, "0")}`;

/** The element's box lies inside the viewport the page opened with: no scroll was needed to see it. */
async function inFirstViewport(page: Page, el: Locator) {
  const box = await el.boundingBox();
  const vp = page.viewportSize()!;
  expect(box, "has a box").not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(vp.width);
  expect(box!.y + box!.height).toBeLessThanOrEqual(vp.height);
}

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const) {
  test.describe(`${width}x${height}`, () => {
    test.use({ viewport: { width, height } });

    test("the failure line, the retries and the cost after it are in the first viewport", async ({
      page,
    }) => {
      await page.goto(RUN);
      const failure = page.getByTestId("first-error");
      await expect(failure).toContainText(
        /^Failed at step \d+ \(\+6\.2 s\): run_migration, ConnectionTimeout/,
      );
      await inFirstViewport(page, page.getByTestId("headline"));
      await inFirstViewport(page, failure);

      const detail = page.getByTestId("headline-detail");
      await expect(detail).toContainText("2 retries");
      await expect(detail).toContainText(/\$0\.0167 spent after/);
      await expect(detail).toContainText("$0.0356 total");
      await inFirstViewport(page, detail);
      expect(await page.evaluate(() => window.scrollY)).toBe(0);
    });

    test("the fault line marks the first error on the timeline", async ({ page }) => {
      await page.goto(RUN);
      const line = page.locator(".tl-fault");
      await expect(line).toContainText(/^first error · step \d+$/);
      await inFirstViewport(page, line.locator(".tl-fault-label"));
    });

    test("Go jumps to the first error", async ({ page }) => {
      await page.goto(RUN);
      await page.getByRole("button", { name: "Show it" }).click();
      await expect(page.getByRole("dialog")).toContainText("ConnectionTimeout");
      await expect(page.locator("[data-first-error]").first()).toBeVisible();
    });
  });
}
