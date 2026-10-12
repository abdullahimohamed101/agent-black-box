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

test.describe("R5 readouts at the smallest app window", () => {
  for (const [w, h] of [
    [1024, 640],
    [1280, 720],
  ]) {
    test(`${w}x${h}: no readout breaks inside a value or a label`, async ({ page }) => {
      await page.setViewportSize({ width: w!, height: h! });
      for (const n of [2, 7, 11]) {
        await page.goto(`${BASE}/runs/${RUN(n)}`);
        await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
        const broken = await page.evaluate(() =>
          [...document.querySelectorAll(".run-head .stat dt, .run-head .stat dd")]
            .filter((el) => {
              const r = document.createRange();
              r.selectNodeContents(el);
              const lines = new Set([...r.getClientRects()].map((q) => Math.round(q.top)));
              return lines.size > 1;
            })
            .map((el) => el.textContent),
        );
        expect(broken, `run ${n}`).toEqual([]);
        // the whole row stays inside the header
        const out = await page.evaluate(() => {
          const head = document.querySelector(".run-head")!.getBoundingClientRect();
          return [...document.querySelectorAll(".run-head .stat")].filter(
            (el) => el.getBoundingClientRect().right > head.right + 1,
          ).length;
        });
        expect(out, `run ${n}`).toBe(0);
      }
    });
  }
});

test.describe("R7 the context column survives a click on it", () => {
  test("1440: Show it opens the inspector and the column stays open; 1280: it folds", async ({
    page,
  }) => {
    for (const [w, state] of [
      [1440, "open"],
      [1280, "closed"],
    ] as const) {
      await page.setViewportSize({ width: w, height: 900 });
      await page.goto(`${BASE}/runs/${RUN(2)}`);
      await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
      await page.getByRole("button", { name: "Show it" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.locator(".work")).toHaveAttribute("data-context", state);
      const pane = await page.locator(".pane").boundingBox();
      expect(pane!.width, `${w}`).toBeGreaterThanOrEqual(w === 1440 ? 480 : 300);
    }
  });
});
