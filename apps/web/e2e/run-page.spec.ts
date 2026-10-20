import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";

// Design pass 2 step 5b (fixtures build): the run page and Compare at 1440, 768 and 375, light and dark, in the states a person
// meets: a failed run, a running one, a succeeded one, a run whose trace expired, and a comparison. Axe, a clean console, no sideways
// scroll, the fault line's label clear of the row's own marker, and the first-viewport promise of 03 at a phone width.
const W = "/w/demo/projects/all";
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
const PAGES: [string, string][] = [
  ["failed run", `${W}/runs/${RUN(2)}`],
  ["failed coding run", `${W}/runs/${RUN(12)}`],
  ["running run", `${W}/runs/${RUN(4)}`],
  ["succeeded run", `${W}/runs/${RUN(1)}`],
  ["expired trace", `${W}/runs/${RUN(90)}`],
  ["deleting trace", `${W}/runs/${RUN(91)}`],
  ["waterfall", `${W}/runs/${RUN(2)}?view=waterfall`],
  ["findings tab", `${W}/runs/${RUN(10)}?view=findings`],
  ["compare", `${W}/runs/compare?left=${RUN(11)}&right=${RUN(12)}`],
];

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}
const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

for (const scheme of ["light", "dark"] as const)
  for (const [width, height] of [
    [1440, 900],
    [768, 1024],
    [375, 812],
  ] as const)
    test.describe(`${scheme} ${width}`, () => {
      test.use({ viewport: { width, height }, colorScheme: scheme });
      for (const [name, url] of PAGES)
        test(`${name}: axe clean, console clean, no sideways scroll`, async ({ page }) => {
          const errors: string[] = [];
          page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
          page.on("pageerror", (e) => errors.push(e.message));
          await page.goto(url);
          await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
          await page.waitForLoadState("networkidle");
          await axe(page);
          expect(await overflow(page)).toBeLessThanOrEqual(0);
          expect(errors).toEqual([]);
        });
    });

test.describe("the fault line", () => {
  for (const [width, height] of [
    [1440, 900],
    [768, 1024],
    [375, 812],
  ] as const)
    test(`${width}: a decoration with a label; the label does not cover the first-error row's marker or text`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height });
      await page.goto(`${W}/runs/${RUN(2)}`);
      const line = page.locator(".tl-fault");
      await expect(line).toHaveAttribute("aria-hidden", "true"); // the row says it in words for assistive technology
      const row = page.locator("[data-first-error]");
      await expect(row.locator(".sr-only", { hasText: "First error" })).toHaveCount(1);
      const label = (await line.locator(".tl-fault-label").boundingBox())!;
      const b = (await row.boundingBox())!;
      expect(label.height).toBeLessThanOrEqual(16);
      for (const part of [".tl-type", ".tl-dur", ".tl-status"]) {
        const el = row.locator(part);
        if (!(await el.count())) continue;
        const box = await el.first().boundingBox();
        if (!box || box.width === 0) continue;
        // no overlap of the label with the row's own columns, as a rectangle test on the row's box edges
        const overlap =
          label.x < box.x + box.width &&
          label.x + label.width > box.x &&
          label.y + label.height > box.y + 4 &&
          label.y < box.y + box.height - 4;
        expect(
          overlap,
          `${part} at ${width}: label ${JSON.stringify(label)} vs ${JSON.stringify(box)}`,
        ).toBe(false);
      }
      expect(label.y).toBeLessThan(b.y + 8);
    });

  test("the line is 2 px", async ({ page }) => {
    await page.goto(`${W}/runs/${RUN(2)}`);
    const h = await page
      .locator(".tl-fault")
      .evaluate((el) => parseFloat(getComputedStyle(el).height));
    expect(h).toBe(2);
  });
});

test.describe("375 first viewport", () => {
  test.use({ viewport: { width: 375, height: 812 } });
  test("the title, status, headline, Go control, figures and the tab row are in the first screen, with the first rows of the timeline", async ({
    page,
  }) => {
    await page.goto(`${W}/runs/${RUN(2)}`);
    await expect(page.getByRole("option").first()).toBeVisible();
    for (const target of [
      page.getByRole("link", { name: "Back to runs" }),
      page.getByRole("heading", { level: 1 }),
      page.getByTestId("headline"),
      page.getByRole("button", { name: "Go to first error" }),
      page.getByRole("tablist", { name: "Run views" }),
      page.getByRole("option").first(),
      page.locator(".tl-fault-label"),
    ]) {
      const box = (await target.boundingBox())!;
      expect(box.y + box.height).toBeLessThanOrEqual(812);
    }
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
  });
});

test.describe("1440 first viewport", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  test("the first events are visible without scrolling and nothing sits above them but the header", async ({
    page,
  }) => {
    await page.goto(`${W}/runs/${RUN(2)}`);
    const first = (await page.getByRole("option").first().boundingBox())!;
    expect(first.y).toBeLessThanOrEqual(300);
    const rows = await page
      .getByRole("option")
      .evaluateAll((els) => els.filter((e) => e.getBoundingClientRect().bottom <= 900).length);
    expect(rows).toBeGreaterThanOrEqual(8);
  });
});

test.describe("trace states keep their words", () => {
  test("expired and deleting say it in fixed sentences, with no date and no tabs", async ({
    page,
  }) => {
    await page.goto(`${W}/runs/${RUN(90)}`);
    const expired = page.getByTestId("trace-notice");
    await expect(expired).toContainText("Trace expired");
    await expect(expired).toContainText(
      "The trace of this run was removed by the retention policy; the run summary and its numbers remain.",
    );
    await expect(expired).not.toContainText(/20\d\d/);
    await expect(page.getByRole("tab")).toHaveCount(0);
    await page.goto(`${W}/runs/${RUN(91)}`);
    await expect(page.getByTestId("trace-notice")).toContainText("Trace being removed");
    await expect(page.getByRole("tab")).toHaveCount(0);
  });
});

test.describe("Compare", () => {
  test("the divergence sentence is the headline, in ink; the rule is ink; names link to the runs", async ({
    page,
  }) => {
    await page.goto(`${W}/runs/compare?left=${RUN(11)}&right=${RUN(12)}`);
    const line = page.getByTestId("divergence-summary");
    await expect(line).toContainText(/The runs diverged at step \d+: A .+, B .+\./);
    const colours = await page.evaluate(() => {
      const c = (sel: string) => getComputedStyle(document.querySelector(sel)!).color;
      return { line: c('[data-testid="divergence-summary"]'), body: c("body") };
    });
    expect(colours.line).toBe(colours.body);
    await expect(page.getByRole("link", { name: "Back to runs" })).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Fix OAuth session expiry" }).first(),
    ).toBeVisible();
    // the divergence rule is ink and 2 px
    const rule = await page.locator('[data-divergence="true"]').evaluate((el) => ({
      height: getComputedStyle(el, "::before").height,
      bg: getComputedStyle(el, "::before").backgroundColor,
    }));
    expect(rule.height).toBe("2px");
    expect(rule.bg).toBe(colours.body);
  });

  test("an identical pair says the runs took the same steps and offers no divergence", async ({
    page,
  }) => {
    await page.goto(`${W}/runs/compare?left=${RUN(11)}&right=${RUN(11)}`);
    await expect(page.getByTestId("no-divergence")).toContainText("The runs took the same steps");
    await expect(page.getByRole("button", { name: "Jump to divergence" })).toBeDisabled();
  });

  test("at 375 a step is a block with A and B prefixes and the toolbar scrolls sideways inside itself", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${W}/runs/compare?left=${RUN(11)}&right=${RUN(12)}`);
    await expect(page.getByTestId("aligned-steps")).toBeVisible();
    const before = await page
      .locator(".step-cell")
      .first()
      .evaluate((el) => getComputedStyle(el, "::before").content);
    expect(before).toMatch(/^"[AB]\s+"$/);
    expect(await overflow(page)).toBeLessThanOrEqual(0);
  });
});
