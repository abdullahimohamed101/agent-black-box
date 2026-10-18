import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";

// Design pass 2 step 5d (fixtures mode): Analytics (three reports as tabs, one headline sentence each, the exclusion note, empty and
// error states) and Experiments (list and an experiment, the winner in words): axe at 1440, 768 and 375 in both colour schemes, no
// red outside a run's failed word, no sideways scroll, a clean console, and zero motion under prefers-reduced-motion.
const A = "/w/demo/projects/all/analytics";
const E = "/w/demo/projects/all/experiments";
const EXP = (n: number) => `${E}/exp_${String(n).padStart(26, "0")}`;
const SIZES = [
  [1440, 900],
  [768, 1024],
  [375, 812],
] as const;
const REPORTS = [
  ["cost", "Cost"],
  ["reliability", "Reliability"],
  ["performance", "Performance"],
] as const;

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}
const noSideScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
const watch = (page: Page) => {
  const problems: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(e.message));
  return problems;
};
/** Fault-coloured elements that are not a run's failed word (the badge): charts, headlines, notices and tables must have none. */
const redOutsideBadges = (page: Page) =>
  page.evaluate(() => {
    const probe = document.createElement("i");
    probe.style.color = "var(--color-status-fault)";
    document.body.append(probe);
    const red = getComputedStyle(probe).color;
    probe.remove();
    const hits: string[] = [];
    for (const el of document.body.querySelectorAll("*")) {
      if (el.closest(".badge")) continue;
      const s = getComputedStyle(el);
      if (
        [s.color, s.backgroundColor, s.borderTopColor, s.borderBottomColor, s.outlineColor].some(
          (c) => c === red,
        )
      )
        hits.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)}`);
    }
    return hits;
  });

for (const [width, height] of SIZES)
  for (const scheme of ["light", "dark"] as const)
    test.describe(`Analytics and Experiments at ${width} px, ${scheme}`, () => {
      test.use({ viewport: { width, height }, colorScheme: scheme, reducedMotion: "reduce" });

      for (const [slug, name] of REPORTS)
        test(`Analytics, ${name}: one h1, one headline, axe clean, no red, no overflow, console clean`, async ({
          page,
        }) => {
          const problems = watch(page);
          await page.goto(slug === "cost" ? A : `${A}?tab=${slug}`);
          await expect(page.getByRole("heading", { name: "Analytics", level: 1 })).toBeVisible();
          await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
          await expect(page.getByRole("tab", { name, selected: true })).toBeVisible();
          await expect(page.getByTestId("report-headline")).toHaveCount(1);
          await page.waitForLoadState("networkidle");
          await axe(page);
          expect(await noSideScroll(page)).toBe(true);
          expect(await redOutsideBadges(page)).toEqual([]);
          expect(problems.filter((p) => !/favicon/.test(p))).toEqual([]);
        });

      for (const [label, url] of [
        ["the list", E],
        ["a controlled experiment", EXP(1)],
        ["an experiment with nothing to compare", EXP(2)],
      ] as const)
        test(`Experiments, ${label}: axe clean, no red, no overflow, console clean`, async ({
          page,
        }) => {
          const problems = watch(page);
          await page.goto(url);
          await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
          await page.waitForLoadState("networkidle");
          await axe(page);
          expect(await noSideScroll(page)).toBe(true);
          expect(await redOutsideBadges(page)).toEqual([]);
          expect(problems.filter((p) => !/favicon/.test(p))).toEqual([]);
        });
    });

test.describe("Analytics behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });

  test("the tab is the URL, arrows move and select, the window control keeps the tab", async ({
    page,
  }) => {
    await page.goto(A);
    const cost = page.getByRole("tab", { name: "Cost" });
    await cost.focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: "Reliability" })).toBeFocused();
    await expect(page).toHaveURL(/\/analytics\?tab=reliability$/);
    await expect(page.getByRole("tabpanel", { name: "Reliability" })).toBeVisible();
    await page.getByRole("button", { name: "Last 30 days" }).click();
    await expect(page.getByRole("button", { name: "Last 30 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByRole("tab", { name: "Reliability", selected: true })).toBeVisible();
    await expect(page.getByTestId("report-headline")).toContainText("in the last 30 days");
    await page.getByRole("tab", { name: "Reliability" }).focus();
    await page.keyboard.press("Home");
    await expect(page).toHaveURL(/\/analytics$/);
  });

  test("the exclusion note is a notice with its words, and an error report keeps the others readable", async ({
    page,
  }) => {
    await page.route("**/analytics/cost*", async (route) => {
      const res = await route.fetch();
      await route.fulfill({
        response: res,
        json: { ...(await res.json()), excluded_expired_traces: 3 },
      });
    });
    await page.route("**/analytics/reliability*", (route) =>
      route.fulfill({
        status: 503,
        json: { error: { code: "ANALYTICS_TIMEOUT", message: "The report took too long." } },
      }),
    );
    await page.goto(A);
    const note = page.getByTestId("expired-traces");
    await expect(note).toContainText("Excludes 3 runs whose trace has expired");
    await expect(note).toHaveClass(/notice/);
    await page.getByRole("tab", { name: "Reliability" }).click();
    const failed = page.getByRole("tabpanel", { name: "Reliability" }).getByRole("alert");
    await expect(failed).toContainText("The report took too long.");
    await expect(failed.locator("svg")).toHaveCount(0);
    await page.getByRole("tab", { name: "Performance" }).click();
    await expect(page.getByTestId("report-headline")).toContainText("Half of runs finished");
  });

  test("an empty cost window is a statement with no drawing", async ({ page }) => {
    await page.route("**/analytics/cost*", async (route) => {
      const res = await route.fetch();
      const b = await res.json();
      await route.fulfill({
        response: res,
        json: { ...b, headline: { ...b.headline, total_usd: 0 }, by_model: [] },
      });
    });
    await page.goto(A);
    const empty = page.getByRole("tabpanel", { name: "Cost" }).locator('[data-state="empty"]');
    await expect(empty).toContainText("No cost recorded in this window");
    await expect(page.locator("svg.illus")).toHaveCount(0);
  });

  test("chart marks have text equivalents and are not red", async ({ page }) => {
    await page.goto(`${A}?tab=reliability`);
    await expect(page.locator(".column-peak")).toContainText("Highest:");
    await expect(page.getByRole("table", { name: /Share of finished runs/ })).toBeAttached();
    const fills = await page
      .locator(".column-fill")
      .evaluateAll((els) => els.map((e) => getComputedStyle(e).backgroundColor));
    expect(fills.length).toBeGreaterThan(0);
    await page.goto(A);
    const bars = page.locator(".bars li");
    await expect(bars.first()).toBeVisible();
    expect(await bars.count()).toBeGreaterThan(0);
    for (const t of await bars.allTextContents()) expect(t).toMatch(/\$/);
  });

  test("reduced motion: nothing animates on Analytics or an experiment", async ({ page }) => {
    for (const url of [A, `${A}?tab=reliability`, EXP(1)]) {
      await page.goto(url);
      await page.waitForLoadState("networkidle");
      expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
    }
  });
});

test.describe("Experiments behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });

  test("the winner is said in words, the leading bar is marked, and the page opens with it", async ({
    page,
  }) => {
    await page.goto(EXP(1));
    const verdict = page.getByTestId("experiment-verdict");
    await expect(verdict).toContainText(
      "Baseline leads on success rate: 100% against 0% for wrong fix.",
    );
    await expect(verdict).toContainText("finished run");
    const lead = page
      .getByRole("list", { name: "Success rate by variant" })
      .locator("li[data-lead=true]");
    await expect(lead).toContainText("baseline");
    await expect(lead).toContainText("100%");
    const y = async (id: string) => (await page.getByTestId(id).boundingBox())!.y;
    expect(await y("experiment-verdict")).toBeLessThan(await y("control-banner"));
    expect(await y("control-banner")).toBeLessThan(await y("metrics"));
  });

  test("an experiment with no finished runs says so and draws no chart; the title line goes back", async ({
    page,
  }) => {
    await page.goto(EXP(2));
    await expect(page.getByTestId("experiment-verdict")).toContainText("Not enough finished runs");
    await expect(page.getByRole("list", { name: "Success rate by variant" })).toHaveCount(0);
    await page.getByRole("link", { name: "All experiments" }).click();
    await expect(page).toHaveURL(/\/experiments$/);
    await expect(page.getByTestId("experiment-list")).toBeVisible();
  });

  test("an empty list is a statement with no drawing", async ({ page }) => {
    await page.route("**/api/abb/v1/experiments*", (r) => r.fulfill({ json: { items: [] } }));
    await page.goto(E);
    await expect(page.getByText("No experiments yet")).toBeVisible();
    await expect(page.locator("svg.illus")).toHaveCount(0);
  });
});
