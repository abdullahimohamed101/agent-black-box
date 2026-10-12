import { expect, test } from "@playwright/test";

// Regressions found by the Phase 21 code review (docs/plans/active/phase-21-code-review.md). Fixtures build.
const BASE = "/w/demo/projects/all";
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;

test.describe("R4 focus after expanding the folded context", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Enter on the strip lands focus in the context column, not <body>", async ({ page }) => {
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
    await page.getByRole("button", { name: "Context" }).click(); // fold
    const strip = page.getByRole("button", { name: "Show run context" });
    await strip.focus();
    await page.keyboard.press("Enter");
    const column = page.getByRole("complementary", { name: "Run context" });
    await expect(column).toBeFocused();
    expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe("BODY");
  });
});
