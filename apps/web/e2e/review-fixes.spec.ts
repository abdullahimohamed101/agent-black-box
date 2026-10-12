import { expect, test } from "@playwright/test";
import { openContext } from "./motion";

// Regressions found by the Phase 21 code review (docs/plans/completed/phase-21-code-review.md). Fixtures build.
const BASE = "/w/demo/projects/all";
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;

test.describe("R4 focus after expanding the folded context", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Enter on the strip lands focus in the context column, not <body>", async ({ page }) => {
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
    // closed by default (Phase 22 D3): the strip is already there
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
  test("1440 and 1280: once open, Show it opens the inspector and the column stays open (KI-126); under 1280 it folds", async ({
    page,
  }) => {
    for (const [w, state] of [
      [1440, "open"],
      [1280, "open"],
      [1100, "closed"],
    ] as const) {
      await page.setViewportSize({ width: w, height: 900 });
      await page.goto(`${BASE}/runs/${RUN(2)}`);
      await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
      await openContext(page);
      await page.getByRole("button", { name: "Show it" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.locator(".work")).toHaveAttribute("data-context", state);
      const pane = await page.locator(".pane").boundingBox();
      expect(pane!.width, `${w}`).toBeGreaterThanOrEqual(w === 1440 ? 480 : w === 1280 ? 440 : 300);
    }
  });
});

test.describe("R8 the toolbar controls and the Filter popover are never cut in half", () => {
  for (const [w, h, inspector] of [
    [1024, 640, true],
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
        await page.getByRole("button", { name: "Show it" }).click();
        await expect(page.getByRole("dialog")).toBeVisible();
      }
      const clipped = await page.evaluate(() => {
        const toolbar = document.querySelector(".pane .toolbar")!.getBoundingClientRect();
        return [...document.querySelectorAll(".pane .toolbar > *")]
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
      if (name === "run evaluations") await openContext(page); // the context column starts closed (Phase 22 D3)
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
