import { expect, test } from "@playwright/test";
import { openTab } from "./motion";

// Regressions found by the Phase 21 code review (docs/plans/completed/phase-21-code-review.md). Fixtures build.
const BASE = "/w/demo/projects/all";
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;

test.describe("R4 focus after expanding the folded Steps strip", () => {
  test.use({ viewport: { width: 1000, height: 800 } });

  test("Enter on the strip keeps focus on it and opens the steps, never <body>", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs/${RUN(12)}`);
    await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
    // below 1100 px the Steps column is a strip above the pane (design pass 2; it replaced the Context strip)
    const summary = page.locator("details.steps-strip > summary");
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("details.steps-strip")).toHaveAttribute("open", "");
    await expect(summary).toBeFocused();
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
          [...document.querySelectorAll(".run-head .fig dt, .run-head .fig dd")]
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
          return [...document.querySelectorAll(".run-head .fig")].filter(
            (el) => el.getBoundingClientRect().right > head.right + 1,
          ).length;
        });
        expect(out, `run ${n}`).toBe(0);
      }
    });
  }
});

test.describe("R7 the Steps column survives a click on the inspector", () => {
  test("1440 and 1280: the inspector is a column beside Steps; under 1280 it is an overlay and the pane keeps its width", async ({
    page,
  }) => {
    for (const [w, mode] of [
      [1440, "column"],
      [1280, "column"],
      [1100, "overlay"],
    ] as const) {
      await page.setViewportSize({ width: w, height: 900 });
      await page.goto(`${BASE}/runs/${RUN(12)}`);
      await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
      const before = (await page.locator(".pane").boundingBox())!.width;
      await page.getByRole("button", { name: "Go to first error" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.locator(".work")).toHaveAttribute("data-steps", "open");
      await expect(page.getByRole("complementary", { name: "Steps" })).toBeVisible();
      const pane = (await page.locator(".pane").boundingBox())!.width;
      if (mode === "overlay") expect(pane, `${w}`).toBe(before);
      else expect(pane, `${w}`).toBeGreaterThanOrEqual(w === 1440 ? 480 : 440);
    }
  });
});

test.describe("R8 the toolbar controls and the Filter popover are never cut in half", () => {
  // With the inspector only where it is a column (from 1280 px); below that it is an overlay that covers the tab row's tools by
  // design (03: "the pane behind stays; Esc or Back"), so there the toolbar is checked with the inspector closed.
  for (const [w, h, inspector] of [
    [1024, 640, false],
    [1280, 720, true],
    [1440, 900, false],
  ] as const) {
    test(`${w}x${h}${inspector ? " with the inspector" : ""}: every toolbar control is fully inside its row`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: w, height: h });
      await page.goto(`${BASE}/runs/${RUN(2)}`);
      await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
      if (inspector) {
        await page.getByRole("button", { name: "Go to first error" }).click();
        await expect(page.getByRole("dialog")).toBeVisible();
      }
      const clipped = await page.evaluate(() => {
        const toolbar = document.querySelector(".pane-head")!.getBoundingClientRect();
        return [...document.querySelectorAll(".pane-tools > *, .tl-tools > *")]
          .filter((c) => {
            const r = c.getBoundingClientRect();
            return r.right > toolbar.right + 0.5 || r.left < toolbar.left - 0.5;
          })
          .map((c) => c.textContent);
      });
      expect(clipped).toEqual([]);
      // the Filter popover holds every class and stays inside the window
      await page.getByRole("button", { name: /^Filter/ }).click();
      const panel = page.getByRole("group", { name: "Event classes" });
      await expect(panel.getByRole("checkbox")).toHaveCount(8);
      const box = (await panel.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(w);
      expect(box.y + box.height).toBeLessThanOrEqual(h);
      await page.keyboard.press("Escape");
      await expect(panel).toBeHidden();
    });
  }
});

test.describe("R9 experiment detail on a phone", () => {
  test.use({ viewport: { width: 375, height: 812 } });
  for (const n of [1, 2]) {
    test(`experiment ${n}: no horizontal scroll`, async ({ page }) => {
      await page.goto(`${BASE}/experiments/exp_${String(n).padStart(26, "0")}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      const over = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      expect(over).toBeLessThanOrEqual(0);
    });
  }
});

test.describe("R12 empty states are statements, not cards", () => {
  for (const [name, url] of [
    ["api keys", "/w/demo/settings/api-keys"],
    ["run evaluations", `${BASE}/runs/${RUN(2)}`],
  ] as const) {
    test(`${name}: no bordered box`, async ({ page }) => {
      await page.goto(url);
      if (name === "run evaluations") await openTab(page, "Evaluations");
      const empty = page.locator('[data-state="empty"]').first();
      await expect(empty).toBeVisible();
      const css = await empty.evaluate((el) => {
        const s = getComputedStyle(el);
        return [s.borderLeftWidth, s.borderRightWidth, s.borderBottomWidth, s.borderRadius];
      });
      expect(css).toEqual(["0px", "0px", "0px", "0px"]);
    });
  }
});
