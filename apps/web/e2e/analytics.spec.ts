import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Runs only through scripts/analytics-e2e.sh: a real API + worker, the built web server on :3150 and runs written by
// scripts/analytics_driver.py with known costs (the expected figures are in the file named by E2E_ANALYTICS_EXPECT).
const expectFile = process.env.E2E_ANALYTICS_EXPECT;
test.skip(!expectFile, "needs scripts/analytics-e2e.sh");
test.use({ baseURL: "http://localhost:3150" });
const SHOTS = "../../docs/screenshots/phase-7";
const want = () =>
  JSON.parse(readFileSync(expectFile!, "utf8")) as {
    runs: number;
    success: number;
    total_usd: number;
    retry_usd: number;
    hostile: string;
  };

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

/** One headline figure tile, found by its label (tables have headers with the same words). */
const stat = (scope: Page | Locator, label: string) =>
  scope.locator(".stat").filter({ hasText: new RegExp(`^${label}`) });
const usd = (n: number) => `$${n.toFixed(2)}`;
const PROJECT = "all"; // the web key is workspace-wide (KI-027: no project lookup, `all` = no filter)
const base = `/w/e2e/projects/${PROJECT}`;

test("dashboard shows whole-window aggregates from the server", async ({ page }) => {
  const w = want();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(base);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText(`All ${w.runs} runs started in the last 7 days`)).toBeVisible();
  await expect(stat(page, "Success rate")).toContainText("50%");
  await expect(stat(page, "Cost")).toContainText(usd(w.total_usd));
  await expect(page.getByRole("heading", { name: "Recent failures" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Cost and reliability analytics/ })).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/dashboard.png`, fullPage: true });
  expect(errors).toEqual([]);
});

test("analytics page: cost, retries, reliability and performance", async ({ page }) => {
  const w = want();
  await page.goto(`${base}/analytics`);
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
  const cost = page.getByRole("region", { name: "Cost" });
  await expect(stat(cost, "Total")).toContainText(usd(w.total_usd));
  await expect(stat(cost, "Per successful run")).toContainText("$2.25");
  await expect(cost.getByText(/19% of cost came from retries/)).toBeVisible();
  await expect(
    cost.getByText(/Initial attempts \$6\.25, retries \$1\.50 across 1 runs/),
  ).toBeVisible();
  await expect(cost.getByRole("list", { name: "Cost by agent" })).toContainText("coding-agent");
  await expect(cost.getByRole("list", { name: "Cost by model" })).toContainText("model-x");
  await expect(cost.getByRole("list", { name: "Cost by source" })).toContainText(
    "Computed from tokens and prices",
  );
  await expect(cost.getByRole("table", { name: "Most expensive runs" })).toBeVisible();

  const reliability = page.getByRole("region", { name: "Reliability" });
  await expect(stat(reliability, "Failure rate")).toContainText("25%");
  await expect(stat(reliability, "Timeout rate")).toContainText("25%");
  await expect(
    reliability.getByRole("table", { name: "Tool success rate and latency" }),
  ).toContainText("git");
  const retry = reliability.getByRole("table", { name: "Runs with the most retries" });
  await expect(retry).toContainText("$1.50");

  const perf = page.getByRole("region", { name: "Performance" });
  await expect(
    perf.getByRole("table", { name: "Slowest operations by p95 duration" }),
  ).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/analytics.png`, fullPage: true });
});

test("hostile telemetry names render as text and never run", async ({ page }) => {
  const w = want();
  let alerted = false;
  page.on("dialog", async (d) => {
    alerted = true;
    await d.dismiss();
  });
  await page.goto(`${base}/analytics`);
  const cost = page.getByRole("region", { name: "Cost" });
  await expect(cost.getByRole("list", { name: "Cost by model" })).toContainText(w.hostile);
  await expect(
    page
      .getByRole("region", { name: "Reliability" })
      .getByRole("table", { name: "Tool success rate and latency" }),
  ).toContainText(w.hostile);
  expect(await page.locator("main img, main script").count()).toBe(0);
  expect(
    await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned),
  ).toBeUndefined();
  expect(alerted).toBe(false);
  await page.screenshot({ path: `${SHOTS}/analytics-hostile.png`, fullPage: true });
});

test("window switch refetches and the layout holds on a phone", async ({ page }) => {
  await page.goto(`${base}/analytics`);
  const requests: string[] = [];
  page.on("request", (r) => requests.push(r.url()));
  await page.getByRole("button", { name: "Today" }).click();
  await expect(page.getByRole("button", { name: "Today" })).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => requests.some((u) => u.includes("/analytics/cost"))).toBe(true);
  await page.setViewportSize({ width: 375, height: 800 });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
  await page.screenshot({ path: `${SHOTS}/analytics-mobile.png`, fullPage: true });
});
