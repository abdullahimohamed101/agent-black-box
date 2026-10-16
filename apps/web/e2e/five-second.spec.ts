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
        /^Failed at step \d+ after 6\.2 s: run_migration, ConnectionTimeout\./,
      );
      await inFirstViewport(page, page.getByTestId("headline"));
      await inFirstViewport(page, failure);

      // below 600 px the facts after the first error live in Details (one tap) so the headline and the line stay in the first screen
      if (width < 600) await page.getByRole("button", { name: /^Details/ }).click();
      const detail = page.getByTestId("headline-detail");
      await expect(detail).toContainText("2 retries");
      await expect(detail).toContainText(/\$0\.0167 spent after/);
      await expect(detail).toContainText("$0.0356 total");
      await inFirstViewport(page, detail);
      expect(await page.evaluate(() => window.scrollY)).toBe(0);
    });

    test("the headline is high on the page and the Go control is one tap away", async ({
      page,
    }) => {
      await page.goto(RUN);
      const headline = (await page.getByTestId("headline").boundingBox())!;
      // 03: the headline sits by about 330 px at 375 (title line, status, then the sentence); lower at no width than that
      expect(headline.y + headline.height).toBeLessThanOrEqual(330);
      const go = (await page.getByRole("button", { name: "Go to first error" }).boundingBox())!;
      if (width < 600) {
        expect(go.height).toBeGreaterThanOrEqual(44);
        expect(go.width).toBeGreaterThan(300); // full width on a phone
      }
    });

    test("the fault line marks the first error on the timeline", async ({ page }) => {
      await page.goto(RUN);
      const line = page.locator(".tl-fault");
      await expect(line).toContainText(/^First error, step \d+$/);
      await inFirstViewport(page, line.locator(".tl-fault-label"));
    });

    test("Go jumps to the first error", async ({ page }) => {
      await page.goto(RUN);
      await page.getByRole("button", { name: "Go to first error" }).click();
      await expect(page.getByRole("dialog")).toContainText("ConnectionTimeout");
      await expect(page.locator("[data-first-error]").first()).toBeVisible();
    });
  });
}
