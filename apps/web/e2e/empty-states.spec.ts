import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { shotsDir } from "./shots-dir";

// Phase 22 step 8 (D9): the first-run setup guide, the arrival switch, and the trace illustrations on empty/not-found states.
const BASE = "/w/demo/projects/all";
const SHOTS = shotsDir("phase-22/step-8");
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}
const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

/** The runs list answers "no runs" until `release()`, then falls through to the fixture API. */
async function emptyUntil(page: Page) {
  let released = false;
  await page.route("**/api/abb/v1/runs?*", (route) =>
    released
      ? route.fallback()
      : route.fulfill({ json: { items: [], next_cursor: null, facets: null } }),
  );
  return () => {
    released = true;
  };
}

for (const [w, h] of [
  [1440, 900],
  [375, 812],
] as const) {
  for (const scheme of ["light", "dark"] as const) {
    test.describe(`${w}px ${scheme}`, () => {
      test.use({ viewport: { width: w, height: h }, colorScheme: scheme });

      test("first-run guide: three steps, copy confirms, axe clean, no overflow", async ({
        page,
      }) => {
        await page
          .context()
          .grantPermissions(["clipboard-read", "clipboard-write"])
          .catch(() => {});
        await emptyUntil(page);
        await page.goto(`${BASE}/runs`);
        const guide = page.getByTestId("setup-guide");
        await expect(guide).toBeVisible();
        await expect(guide.getByRole("heading", { level: 2, name: "No runs yet" })).toBeVisible();
        await expect(guide.getByRole("heading", { level: 3 })).toHaveText([
          "Create an API key",
          "Install the SDK",
          "Record a run",
        ]);
        await expect(guide.getByTestId("setup-waiting")).toHaveText("Waiting for your first event");
        // The headline is the display step in Plex Sans (design pass 2: the serif is gone from the app).
        const family = await guide
          .getByRole("heading", { level: 2 })
          .evaluate((el) => getComputedStyle(el).fontFamily);
        expect(family).toContain("IBMPlexSans");
        expect(family).not.toMatch(/IBMPlexSerif|Plex Serif/);
        await expect(guide.getByRole("heading", { level: 2 })).toHaveCSS("font-size", "32px");
        const btn = guide.getByRole("button", { name: "Copy the example code" });
        await btn.click();
        await expect(btn).toHaveText("Copied");
        await expect(guide.getByRole("status").filter({ hasText: "Copied" })).toHaveCount(1);
        expect(await overflow(page)).toBeLessThanOrEqual(0);
        await axe(page);
        await page.screenshot({ path: `${SHOTS}/first-run-${w}-${scheme}.png`, fullPage: true });
      });

      test("no results is a sentence with no drawing, the 404 keeps its motif; axe clean", async ({
        page,
      }) => {
        await page.goto(`${BASE}/runs?agent=nobody`);
        const empty = page.locator('[data-state="empty"]').first();
        await expect(empty).toContainText("No runs match these filters");
        await expect(empty.locator("svg.illus")).toHaveCount(0); // ADR-166: drawings only on first-run and not-found
        await axe(page);
        await page.screenshot({ path: `${SHOTS}/no-results-${w}-${scheme}.png` });

        await page.goto("/this-page-does-not-exist");
        await expect(page.getByRole("heading", { level: 1 })).toHaveText("This page isn't here");
        await expect(page.locator("svg.illus")).toHaveCount(1);
        expect(await overflow(page)).toBeLessThanOrEqual(0);
        await axe(page);
        await page.screenshot({ path: `${SHOTS}/not-found-${w}-${scheme}.png` });
      });
    });
  }
}

test.describe("arrival", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the first run switches the guide to an offer to open it (no new load: the list's own poll)", async ({
    page,
  }) => {
    test.setTimeout(60_000);
    const release = await emptyUntil(page);
    await page.goto(`${BASE}/runs`);
    await expect(page.getByTestId("setup-waiting")).toBeVisible();
    let runsRequests = 0;
    page.on("request", (r) => {
      if (/\/api\/abb\/v1\/runs\?/.test(r.url())) runsRequests++;
    });
    release();
    const arrived = page.getByTestId("setup-arrived");
    await expect(arrived).toBeVisible({ timeout: 30_000 });
    await expect(arrived).toContainText("Your first run arrived");
    expect(runsRequests).toBeLessThanOrEqual(2); // one poll, not a second poller
    await page.screenshot({ path: `${SHOTS}/first-run-arrived-1440-light.png` });
    await arrived.getByRole("link", { name: "Open the run" }).click();
    await expect(page).toHaveURL(/\/runs\/run_/);
  });
});

test.describe("other empty states", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("top-bar search with no match is a sentence; a run without evaluations says so without a drawing", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs`);
    const combo = page.getByRole("combobox", { name: "Search all runs" });
    await expect(async () => {
      await page.keyboard.press("/");
      await expect(combo).toBeFocused({ timeout: 400 });
    }).toPass();
    await combo.fill("zzzznomatch");
    const note = page.locator(".topbar-note-empty");
    await expect(note).toContainText("No runs match this search.");
    await expect(note.locator("svg.illus")).toHaveCount(0);
    await page.screenshot({ path: `${SHOTS}/search-no-results-1440-light.png` });

    await page.goto(`${BASE}/runs/${RUN(2)}`);
    // Evaluations is a tab now; a routine empty state is a statement with no drawing (ADR-166: drawings stay on the first-run
    // guide and not-found only).
    await page.getByRole("tab", { name: "Evaluations" }).click();
    const ev = page.locator('[data-state="empty"]').first();
    await expect(ev).toBeVisible();
    await expect(ev).toContainText("No evaluations yet");
    await expect(ev.locator("svg.illus")).toHaveCount(0);
  });
});
