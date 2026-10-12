import { test, type Page } from "@playwright/test";

// Phase 21 screenshot set (fixtures build). `SHOTS_DIR=before|after` picks the folder under docs/screenshots/phase-21/.
// The same script runs against the pre-redesign tree and the final tree, so it only uses roles, test ids and URLs.
// Not an assertion suite: it never fails on how the page looks, only when a page cannot be reached.
const DIR = process.env.SHOTS_DIR ?? "after";
const OUT = `../../docs/screenshots/phase-21/${DIR}`;
const BASE = "/w/demo/projects/all";
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;

// Only runs when SHOTS_DIR is set, so the normal e2e run never rewrites committed screenshots.
test.skip(!process.env.SHOTS_DIR, "set SHOTS_DIR=before|after to take the screenshot set");
test.use({ viewport: { width: 1440, height: 900 } });

async function shot(page: Page, name: string) {
  // Let web fonts and the first data paint settle; nothing here animates.
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
}

test.describe("screenshot set", () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  });

  test("overview, runs list, search", async ({ page }) => {
    await page.goto(BASE);
    await page.getByText("Success rate").waitFor();
    await shot(page, "dashboard-1440");
    await page.goto(`${BASE}/runs`);
    await page.getByRole("table").waitFor();
    await shot(page, "runs-list-1440");
    const box = page.getByRole("searchbox", { name: "Search" });
    await box.fill("status:failed");
    await box.press("Enter");
    await page.getByRole("list", { name: "Search terms" }).waitFor();
    await shot(page, "runs-list-search-1440");
  });

  test("failed run, inspector, findings, multi-agent waterfall", async ({ page }) => {
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.getByTestId("headline").waitFor();
    await shot(page, "run-failed-1440");
    await page.getByRole("button", { name: "Show it" }).click();
    await page.getByRole("dialog").waitFor();
    await shot(page, "run-failed-inspector-1440");
    await page.goto(`${BASE}/runs/${RUN(7)}?view=waterfall`);
    await page.getByTestId("waterfall").waitFor();
    await shot(page, "run-multiagent-waterfall-1440");
    await page.goto(`${BASE}/runs/${RUN(8)}`);
    await page.getByTestId("findings").waitFor();
    await shot(page, "run-findings-loop-1440");
  });

  test("coding run: story, diff inspector, evidence", async ({ page }) => {
    await page.goto(`${BASE}/runs/${RUN(11)}`);
    await page.getByTestId("story").waitFor();
    await shot(page, "run-coding-story-1440");
    // the first story step that opens a diff; fall back to the first step so the shot still exists
    const steps = page.getByTestId("story").getByRole("button");
    const edit = steps.filter({ hasText: /edit|patch|file/i }).first();
    await ((await edit.count()) ? edit : steps.first()).click();
    await page.getByRole("dialog").waitFor();
    await shot(page, "run-coding-inspector-diff-1440");
    // the evidence view arrives in a later step; before it exists the URL param is ignored and the shot shows the default view
    await page.goto(`${BASE}/runs/${RUN(11)}?view=evidence`);
    await page.getByTestId("headline").waitFor();
    await shot(page, "run-evidence-1440");
  });

  test("compare, experiments, login", async ({ page }) => {
    await page.goto(`${BASE}/runs/compare?left=${RUN(11)}&right=${RUN(12)}`);
    await page.getByTestId("aligned-steps").waitFor();
    await shot(page, "compare-1440");
    await page.goto(`${BASE}/experiments`);
    await page.getByTestId("experiment-list").waitFor();
    await shot(page, "experiments-1440");
    await page.getByTestId("experiment-list").getByRole("link").first().click();
    await page.getByTestId("control-banner").waitFor();
    await shot(page, "experiment-detail-1440");
    await page.goto("/login");
    await shot(page, "login-1440");
  });

  test("viewer: withheld payload", async ({ page }) => {
    await page.goto(`/w/viewer-only/projects/all/runs/${RUN(7)}`);
    await page.getByTestId("headline").waitFor();
    await page.getByRole("button", { name: "Show it" }).click();
    await page.getByRole("dialog").waitFor();
    await shot(page, "viewer-payload-withheld-1440");
  });

  test("phone", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${BASE}/runs`);
    await page.getByRole("table").waitFor();
    await shot(page, "runs-list-375");
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.getByTestId("headline").waitFor();
    await shot(page, "run-375");
    await page.getByRole("button", { name: "Show it" }).click();
    await page.getByRole("dialog").waitFor();
    await shot(page, "run-375-inspector-open");
  });

  test("light scheme", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
    await page.goto(BASE);
    await page.getByText("Success rate").waitFor();
    await shot(page, "dashboard-1440-light");
    await page.goto(`${BASE}/runs`);
    await page.getByRole("table").waitFor();
    await shot(page, "runs-list-1440-light");
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.getByTestId("headline").waitFor();
    await shot(page, "run-failed-1440-light");
  });
});
