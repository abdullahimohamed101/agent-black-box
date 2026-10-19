import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { signIn, workspace } from "./session";
import { shotsDir } from "./shots-dir";

// Runs only through scripts/analytics-e2e.sh: a real API + worker, the built web server on :3150 and runs written by
// scripts/analytics_driver.py with known costs (the expected figures are in the file named by E2E_ANALYTICS_EXPECT).
const expectFile = process.env.E2E_ANALYTICS_EXPECT;
test.skip(!expectFile, "needs scripts/analytics-e2e.sh");
test.use({ baseURL: "http://localhost:3150" });
test.beforeEach(async ({ context, baseURL }) => signIn(context, baseURL!));
const SHOTS = shotsDir("phase-7");
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

const usd = (n: number) => `$${n.toFixed(2)}`;
const PROJECT = "all"; // `all` is the route form of "no project filter"
const base = `/w/${workspace()}/projects/${PROJECT}`;

test("the Runs home states the whole-window aggregates from the server in one sentence", async ({
  page,
}) => {
  const w = want();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(base);
  await expect(page.getByRole("heading", { name: "Runs", level: 1 })).toBeVisible();
  const sentence = page.getByTestId("week-sentence");
  await expect(sentence).toContainText(`Last 7 days: ${w.runs} runs`);
  await expect(sentence).toContainText("50% succeeded");
  await expect(sentence).toContainText(`${usd(w.total_usd)} spent`);
  await expect(page.getByRole("link", { name: "Analytics" }).first()).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/dashboard.png`, fullPage: true });
  expect(errors).toEqual([]);
});

test("analytics page: cost, retries, reliability and performance", async ({ page }) => {
  const w = want();
  await page.goto(`${base}/analytics`);
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
  // Step 5d: the three reports are tabs and each opens with one headline sentence (the figure tiles are gone), so the figures that
  // were read from tiles are read from the sentence. Same numbers, same expectations. UNVERIFIED here: needs the real-API script.
  const cost = page.getByRole("tabpanel", { name: "Cost" });
  await expect(cost.getByTestId("report-headline")).toContainText(`${usd(w.total_usd)} spent`);
  await expect(cost.getByTestId("report-headline")).toContainText("$2.25 per successful run");
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

  await page.getByRole("tab", { name: "Reliability" }).click();
  const reliability = page.getByRole("tabpanel", { name: "Reliability" });
  await expect(reliability.getByTestId("report-headline")).toContainText(
    "25% failed or were blocked and 25% timed out",
  );
  await expect(
    reliability.getByRole("table", { name: "Tool success rate and latency" }),
  ).toContainText("git");
  const retry = reliability.getByRole("table", { name: "Runs with the most retries" });
  await expect(retry).toContainText("$1.50");

  await page.getByRole("tab", { name: "Performance" }).click();
  const perf = page.getByRole("tabpanel", { name: "Performance" });
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
  const cost = page.getByRole("tabpanel", { name: "Cost" });
  await expect(cost.getByRole("list", { name: "Cost by model" })).toContainText(w.hostile);
  await page.getByRole("tab", { name: "Reliability" }).click();
  await expect(
    page
      .getByRole("tabpanel", { name: "Reliability" })
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
