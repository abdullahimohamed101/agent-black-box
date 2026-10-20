import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";

// Design pass 2 step 5a (fixtures build): the one top bar, the scope control, the Menu button, the redirects that replace the
// Overview and the project list, and the Runs home and the sign-in pages at 1440 and 375 in both colour schemes with axe,
// reduced motion and a clean console.
const BASE = "/w/demo/projects/all";

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
const watch = (page: Page) => {
  const problems: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(e.message));
  return problems;
};
const primary = (page: Page) => page.getByRole("navigation", { name: "Primary" });

test.describe("old routes redirect to Runs", () => {
  test("/w/{workspace} and /w/{workspace}/projects/{project} keep working", async ({ page }) => {
    await page.goto("/w/demo");
    await expect(page).toHaveURL(/\/w\/demo\/projects\/all\/runs$/);
    await expect(page.getByRole("heading", { name: "Runs", level: 1 })).toBeVisible();
    await page.goto("/w/demo/projects/demo");
    await expect(page).toHaveURL(/\/w\/demo\/projects\/demo\/runs$/);
    await expect(page.getByRole("heading", { name: "Runs", level: 1 })).toBeVisible();
    await page.goto("/");
    await expect(page.getByRole("link", { name: "Open your runs →" })).toHaveAttribute(
      "href",
      "/w/default/projects/all/runs",
    );
  });
});

test.describe("1440 px", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("one bar with the links, the scope control, search and the account menu; the current page is marked", async ({
    page,
  }) => {
    const problems = watch(page);
    await page.goto(`${BASE}/runs`);
    await expect(primary(page).getByRole("link")).toHaveText([
      "Runs",
      /^Review/,
      "Analytics",
      "Experiments",
    ]);
    await expect(primary(page).getByRole("link", { name: "Runs" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(page.getByRole("button", { name: "Menu" })).toBeHidden();
    await expect(page.getByRole("button", { name: /^demo \/ All projects/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Search runs", exact: true })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toHaveCount(0);
    await primary(page).getByRole("link", { name: "Analytics" }).click();
    await expect(page).toHaveURL(/\/analytics$/);
    await expect(primary(page).getByRole("link", { name: "Analytics" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(primary(page).getByRole("link", { name: "Runs" })).not.toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(problems).toEqual([]);
  });

  test("keyboard: skip link first, then the bar in reading order, then the page", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs`);
    const names: string[] = [];
    for (let i = 0; i < 9; i++) {
      await page.keyboard.press("Tab");
      names.push(
        await page.evaluate(() =>
          (
            document.activeElement?.getAttribute("aria-label") ??
            document.activeElement?.textContent ??
            ""
          ).trim(),
        ),
      );
    }
    expect(names.slice(0, 8)).toEqual([
      "Skip to content",
      "Agent BlackBox",
      "Runs",
      expect.stringMatching(/^Review/),
      "Analytics",
      "Experiments",
      expect.stringMatching(/^demo \/ All projects/),
      "Search runs",
    ] as unknown as string[]);
  });

  test("the skip link moves focus to the page", async ({ page }) => {
    await page.goto(`${BASE}/runs`);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await expect(page.locator("main#main")).toBeFocused();
  });

  test("the scope control lists projects as links into Runs, closes on Escape and gives focus back", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs`);
    const scope = page.getByRole("button", { name: /^demo \/ All projects/ });
    await scope.click();
    await expect(scope).toHaveAttribute("aria-expanded", "true");
    const projects = page.getByRole("list", { name: "Projects" });
    await expect(projects.getByRole("link", { name: "All projects" })).toHaveAttribute(
      "aria-current",
      "true",
    );
    await expect(projects.getByRole("link", { name: "Demo project" })).toHaveAttribute(
      "href",
      "/w/demo/projects/demo/runs",
    );
    await axe(page);
    await page.keyboard.press("Escape");
    await expect(projects).toHaveCount(0);
    await expect(scope).toBeFocused();
    await scope.click();
    await projects.getByRole("link", { name: "Demo project" }).click();
    await expect(page).toHaveURL(/\/projects\/demo\/runs$/);
    await expect(page.getByRole("button", { name: /^demo \/ Demo project/ })).toBeVisible();
  });

  test("the account menu holds no temporary links: Security findings is a tab of Review, Alerts a tab of Settings", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs`);
    await page.getByRole("button", { name: "Account" }).click();
    // fixtures have no person: the panel says so instead of opening empty (KI-279 closed in step 5c)
    await expect(page.locator("#person-panel")).toContainText("Fixture data");
    await expect(page.locator("#person-panel").getByRole("link")).toHaveCount(0);
    await axe(page);
    await page.keyboard.press("Escape");
    await expect(page.locator("#person-panel")).toHaveCount(0);
  });
});

test.describe("375 px", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("the top region is one band; the links live behind a Menu button that Escape closes", async ({
    page,
  }) => {
    const problems = watch(page);
    await page.goto(`${BASE}/runs`);
    const bar = (await page.locator("header.topbar").boundingBox())!;
    expect(bar.height).toBe(44);
    const title = (await page.getByRole("heading", { name: "Runs", level: 1 }).boundingBox())!;
    expect(title.y).toBeLessThan(44 + 40); // the page title follows the bar directly: no second band
    const menu = page.getByRole("button", { name: "Menu" });
    await expect(menu).toBeVisible();
    await expect(primary(page)).toBeHidden();
    await menu.click();
    await expect(menu).toHaveAttribute("aria-expanded", "true");
    await expect(primary(page).getByRole("link", { name: "Analytics" })).toBeVisible();
    // every item is a 44 px target
    const heights = await primary(page)
      .getByRole("link")
      .evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height));
    expect(Math.min(...heights)).toBeGreaterThanOrEqual(44);
    await axe(page);
    await page.keyboard.press("Escape");
    await expect(primary(page)).toBeHidden();
    await expect(menu).toBeFocused();
    await menu.click();
    await primary(page).getByRole("link", { name: "Analytics" }).click();
    await expect(page).toHaveURL(/\/analytics$/);
    await expect(primary(page)).toBeHidden(); // navigating closes the panel
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    expect(problems).toEqual([]);
  });

  test("the scope panel spans the width under the bar and nothing overflows", async ({ page }) => {
    await page.goto(`${BASE}/runs`);
    await page.getByRole("button", { name: /^demo \/ All projects/ }).click();
    const panel = (await page.locator("#scope-panel").boundingBox())!;
    expect(panel.x).toBeGreaterThanOrEqual(0);
    expect(panel.x + panel.width).toBeLessThanOrEqual(375);
    expect(panel.y).toBeGreaterThanOrEqual(44);
    await axe(page);
    expect(await overflow(page)).toBeLessThanOrEqual(0);
  });
});

for (const [w, h] of [
  [1440, 900],
  [375, 812],
] as const)
  for (const scheme of ["light", "dark"] as const)
    test.describe(`${w} px ${scheme}, reduced motion`, () => {
      test.use({
        viewport: { width: w, height: h },
        colorScheme: scheme,
        reducedMotion: "reduce",
      });

      test("Runs home: populated, filtered and zero results are accessible, quiet and fit the width", async ({
        page,
      }) => {
        const problems = watch(page);
        await page.goto(`${BASE}/runs`);
        await expect(page.getByTestId("week-sentence")).toBeVisible();
        await expect(page.locator("tbody tr").first()).toBeVisible();
        expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
        expect(await overflow(page)).toBeLessThanOrEqual(0);
        await axe(page);
        // the red words and dashes are the only red on the page: no row is tinted and no chip is filled
        const tinted = await page.evaluate(
          () =>
            [...document.querySelectorAll("tbody tr")].filter(
              (r) => getComputedStyle(r).backgroundColor !== "rgba(0, 0, 0, 0)",
            ).length,
        );
        expect(tinted).toBe(0);
        // filtered: a chip appears and the page still fits
        await page.goto(`${BASE}/runs?q=status%3Afailed`);
        await expect(page.getByRole("list", { name: "Search terms" })).toContainText(
          "status:failed",
        );
        expect(await overflow(page)).toBeLessThanOrEqual(0);
        await axe(page);
        // zero results: a sentence, one reset
        await page.goto(`${BASE}/runs?agent=nobody`);
        await expect(page.getByText("No runs match these filters")).toBeVisible();
        await expect(page.getByRole("button", { name: "Clear filters" })).toHaveCount(1);
        expect(await overflow(page)).toBeLessThanOrEqual(0);
        await axe(page);
        expect(problems).toEqual([]);
      });

      test("sign-in, invitation and not-found: the display headline, one action, axe clean", async ({
        page,
      }) => {
        for (const url of ["/login", "/login?error=no_access", "/invite", "/no-such-page"]) {
          await page.goto(url);
          await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
          expect(await overflow(page)).toBeLessThanOrEqual(0);
          await axe(page);
        }
        await page.goto("/login?error=no_access");
        await expect(page.locator("main [role=alert]")).toHaveText(
          "No access yet. Ask a workspace owner to invite you.",
        );
        const size = await page
          .getByRole("heading", { level: 1 })
          .evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
        expect(size).toBe(w === 375 ? 28 : 32);
      });
    });
