import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Phase 22 step 7: the runs list shows which runs need you, facets and the top-bar search edit one query string.
const BASE = "/w/demo/projects/all";
const SHOTS = "../../docs/screenshots/phase-22/step-7";

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}
const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
const rows = (page: Page) => page.locator("tbody tr:not(.row-detail)");
// `/` does nothing until the page has hydrated, so a script that presses it straight after goto retries until it lands.
async function pressSlash(page: Page) {
  const combo = page.getByRole("combobox", { name: "Search all runs" });
  await expect(async () => {
    await page.keyboard.press("/");
    await expect(combo).toBeFocused({ timeout: 400 });
  }).toPass();
}
const NEEDS = '[data-status="FAILED"], [data-status="TIMED_OUT"], [data-status="BLOCKED"]';

test.describe("failure lines", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("failed, timed-out and blocked runs get a second line; clean runs stay one quiet line", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs`);
    await expect(rows(page)).toHaveCount(12);
    const needing = rows(page).filter({ has: page.locator(NEEDS) });
    const count = await needing.count();
    expect(count).toBeGreaterThanOrEqual(3);
    await expect(page.getByTestId("failure-line")).toHaveCount(count);
    for (const line of await page.getByTestId("failure-line").allInnerTexts())
      expect(line).toMatch(/^(Failed|Timed out|Blocked)( at step \d+)?/);
    await expect(needing.first().getByTestId("failure-line")).toContainText("Failed at step");
    // the line is above the fold of its row: clean rows are at most 34 px, failed ones at most two lines
    const clean = rows(page).filter({ hasNot: page.locator(NEEDS) });
    const heights = await clean.evaluateAll((els) =>
      els.map((e) => Math.round(e.getBoundingClientRect().height)),
    );
    expect(Math.max(...heights)).toBeLessThanOrEqual(34);
    const failedHeights = await needing.evaluateAll((els) =>
      els.map((e) => Math.round(e.getBoundingClientRect().height)),
    );
    expect(Math.max(...failedHeights)).toBeLessThanOrEqual(70);
    // events and errors are not columns; they live in the row detail
    await expect(page.getByRole("columnheader", { name: "Events" })).toHaveCount(0);
    await expect(page.getByRole("columnheader", { name: "Errors" })).toHaveCount(0);
    const more = needing.first().getByRole("button", { name: /^Show details of/ });
    await more.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("row-detail")).toContainText("Errors");
    await axe(page);
  });

  test("Needs attention is one click away and keeps the failures when everything else is paged", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs`);
    await expect(rows(page)).toHaveCount(12);
    const expected = await rows(page)
      .filter({ has: page.locator(NEEDS) })
      .count();
    const button = page.getByRole("button", { name: "Needs attention" });
    await expect(button).toHaveAttribute("aria-pressed", "false");
    await button.click();
    await expect(button).toHaveAttribute("aria-pressed", "true");
    await expect(page).toHaveURL(
      /status=FAILED%2CTIMED_OUT%2CBLOCKED|status=FAILED,TIMED_OUT,BLOCKED/,
    );
    expect(expected).toBeGreaterThanOrEqual(3);
    await expect(rows(page)).toHaveCount(expected);
    await expect(page.getByTestId("failure-line")).toHaveCount(expected);
    await button.click();
    await expect(rows(page)).toHaveCount(12);
    await expect(page).not.toHaveURL(/status=/);
  });
});

test.describe("facet column", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a facet edits the query string and the URL; clicking it again removes the term", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs`);
    const region = page.getByRole("region", { name: "Narrow the results" });
    await expect(region).toBeVisible();
    const box = page.getByRole("searchbox", { name: "Search" });
    await region.getByRole("button", { name: "Failed" }).click();
    await expect(box).toHaveValue("status:failed");
    await expect(page).toHaveURL(/q=status%3Afailed/);
    const on = region.getByRole("button", { name: /^Failed\s*\(\d+\)/ });
    await expect(on).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("list", { name: "Search terms" })).toContainText("status:failed");
    // a second facet joins the first; both stay in the one string
    await region.getByRole("button", { name: "Last 24 hours" }).click();
    await expect(box).toHaveValue(/^status:failed started:>=-24h$/);
    await region.getByRole("button", { name: "Last 24 hours" }).click();
    await expect(box).toHaveValue("status:failed");
    await on.click();
    await expect(box).toHaveValue("");
    await expect(page).not.toHaveURL(/q=/);
    await expect(rows(page)).toHaveCount(12);
    // a shared link carries the facet state
    await page.goto(`${BASE}/runs?q=status%3Afailed`);
    await expect(box).toHaveValue("status:failed");
    await expect(
      page
        .getByRole("region", { name: "Narrow the results" })
        .getByRole("button", { name: /^Failed\s*\(\d+\)/ }),
    ).toHaveAttribute("aria-pressed", "true");
    await axe(page);
  });

  test("a viewer's facet column never shows content and the refusal is the existing one", async ({
    page,
  }) => {
    await page.goto(`/w/viewer-only/projects/all/runs?q=status%3Afailed`);
    await expect(page.getByRole("region", { name: "Narrow the results" })).toBeVisible();
    await expect(rows(page).first()).toBeVisible();
    await axe(page);
    await page.goto(`/w/viewer-only/projects/all/runs?q=command%3Acurl`);
    await expect(page.locator(".search-problem")).toContainText("Your role cannot search");
    await expect(page.getByText("No runs match")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Narrow the results" })).toHaveCount(0);
  });
});

test.describe("compare mode", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("tick boxes appear only after Compare is pressed, from the keyboard", async ({ page }) => {
    await page.goto(`${BASE}/runs`);
    await expect(page.getByRole("checkbox", { name: /^Compare / })).toHaveCount(0);
    await expect(page.getByTestId("compare-bar")).toHaveCount(0);
    const toggle = page.getByRole("button", { name: "Compare", exact: true });
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    const picks = page.getByRole("checkbox", { name: /^Compare / });
    await expect(picks).toHaveCount(12);
    await picks.nth(0).check();
    await picks.nth(1).check();
    await expect(page.getByTestId("compare-bar")).toContainText("2 of 2 runs picked.");
    await expect(picks.nth(2)).toBeDisabled();
    await expect(page.getByTestId("compare-link")).toHaveAttribute(
      "href",
      /\/runs\/compare\?left=run_\w+&right=run_\w+$/,
    );
    await axe(page);
    await page.keyboard.press("Enter"); // focus is still on a tick box, not the toggle: press the toggle again
    await toggle.press("Enter");
    await expect(picks).toHaveCount(0);
    await expect(page.getByTestId("compare-bar")).toHaveCount(0);
  });
});

test.describe("top-bar search", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("opens with /, lists matches, and works from the keyboard alone", async ({ page }) => {
    await page.goto(`${BASE}`);
    const combo = page.getByRole("combobox", { name: "Search all runs" });
    await expect(page.locator(".topbar-search-hint")).toBeVisible();
    await pressSlash(page);
    await expect(combo).toBeFocused();
    await page.keyboard.type("status:failed");
    const list = page.getByRole("listbox", { name: "Matching runs" });
    await expect(list.getByRole("option")).toHaveCount(4); // three failed runs and "Show all"
    expect(await list.getByRole("option").count()).toBeLessThanOrEqual(6);
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/topbar-search-open-1440-light.png` });
    await page.keyboard.press("ArrowDown");
    await expect(list.getByRole("option").first()).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Escape");
    await expect(list).toBeHidden();
    await expect(combo).toHaveValue("status:failed");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/runs\/run_\w+$/);
    // `/` does nothing while typing in a field
    await page.goto(`${BASE}/runs`);
    const box = page.getByRole("searchbox", { name: "Search" });
    await box.focus();
    await page.keyboard.type("a/b");
    await expect(box).toHaveValue("a/b");
    await expect(combo).not.toBeFocused();
  });

  test("Enter goes to the runs list with ?q=, also from the runs list itself", async ({ page }) => {
    await page.goto(`${BASE}`);
    await pressSlash(page);
    await page.keyboard.type("tool:run_migration");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/runs\?q=tool%3Arun_migration/);
    await expect(page.getByRole("searchbox", { name: "Search" })).toHaveValue("tool:run_migration");
    await expect(rows(page)).toHaveCount(1);
    // from the list: the page adopts the new query
    await page.keyboard.press("Escape");
    await pressSlash(page);
    await page.keyboard.type("status:failed");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/q=status%3Afailed/);
    await expect(page.getByRole("searchbox", { name: "Search" })).toHaveValue("status:failed");
    await expect(rows(page)).toHaveCount(3);
  });

  test("says when nothing matches, explains a bad query, and refuses a viewer's content field", async ({
    page,
  }) => {
    await page.goto(`${BASE}`);
    const combo = page.getByRole("combobox", { name: "Search all runs" });
    await pressSlash(page);
    await page.keyboard.type("tool:nothing-called-this");
    await expect(page.getByText("No runs match this search.")).toBeVisible();
    await combo.fill("bogus:1");
    await expect(page.locator(".topbar-note")).toContainText("could not be understood");
    await page.goto(`/w/viewer-only/projects/all`);
    await pressSlash(page);
    await page.keyboard.type("command:curl");
    await expect(page.locator(".topbar-note")).toContainText("Your role cannot search command:");
    await expect(page.getByText("No runs match this search.")).toHaveCount(0);
    await axe(page);
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("search collapses to an icon, facets fold, nothing overflows", async ({ page }) => {
    await page.goto(`${BASE}/runs`);
    await expect(rows(page)).toHaveCount(12);
    const icon = page.getByRole("button", { name: "Search runs" });
    await expect(icon).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Search all runs" })).toBeHidden();
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/runs-list-375-light.png`, fullPage: true });
    // the facet column is folded under one summary and opens on demand
    const facets = page.getByRole("region", { name: "Narrow the results" });
    await expect(facets.getByRole("button", { name: "Failed" })).toBeHidden();
    await facets.getByText("Narrow results").click();
    await expect(facets.getByRole("button", { name: "Failed" })).toBeVisible();
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `${SHOTS}/facets-375-light.png`, fullPage: true });
    await facets.getByText("Narrow results").click();
    // "Needs attention" stays on the surface
    await expect(page.getByRole("button", { name: "Needs attention" })).toBeVisible();
    await icon.click();
    const combo = page.getByRole("combobox", { name: "Search all runs" });
    await expect(combo).toBeFocused();
    await page.keyboard.type("status:failed");
    await expect(page.getByRole("option").first()).toBeVisible();
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/topbar-search-open-375-light.png` });
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
    await expect(combo).toBeHidden();
  });
});

for (const [scheme, width, height] of [
  ["light", 1440, 900],
  ["dark", 1440, 900],
  ["dark", 375, 812],
] as const) {
  test(`screenshots: ${scheme} ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
    await page.goto(`${BASE}/runs`);
    await expect(rows(page)).toHaveCount(12);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `${SHOTS}/runs-list-${width}-${scheme}.png`, fullPage: true });
    await page.getByRole("button", { name: "Needs attention" }).click();
    await expect(page.getByTestId("failure-line").first()).toBeVisible();
    await page.screenshot({
      path: `${SHOTS}/needs-attention-${width}-${scheme}.png`,
      fullPage: true,
    });
    await page.goto(`${BASE}/runs?q=status%3Afailed`);
    await expect(page.getByRole("list", { name: "Search terms" })).toBeVisible();
    if (width < 700) await page.getByText("Narrow results").click();
    await page.screenshot({ path: `${SHOTS}/facets-${width}-${scheme}.png`, fullPage: true });
    if (width >= 700) {
      await pressSlash(page);
      await page.keyboard.type("status:failed");
      await expect(page.getByRole("option").first()).toBeVisible();
      await page.screenshot({ path: `${SHOTS}/topbar-search-open-${width}-${scheme}.png` });
    }
  });
}
