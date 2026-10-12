import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { openContext, settleAnimations } from "./motion";
import { signIn, workspace } from "./session";

// Runs only through scripts/findings-e2e.sh: the ten pathological runs went in through the real ingestion path, the
// worker derived their findings, and the built web server on :3105 reads them through its proxy. An OWNER session and a
// VIEWER session (two different people) come from the script; no key is ever given to the web.
const runs: Record<string, string> = JSON.parse(process.env.E2E_FINDINGS_RUNS ?? "{}");
const viewerToken = process.env.E2E_VIEWER_SESSION_TOKEN;
test.skip(!runs.looping || !viewerToken, "needs scripts/findings-e2e.sh");
test.use({ baseURL: "http://localhost:3105", viewport: { width: 1400, height: 1000 } });
test.beforeEach(async ({ context, baseURL }) => signIn(context, baseURL!));
const SHOTS = "../../docs/screenshots/phase-10";

const open = async (page: Page, name: string) => {
  await page.goto(`/w/${workspace()}/projects/all/runs/${runs[name]}`);
  await openContext(page); // findings live in the context panel, closed by default (Phase 22 D3)
  await expect(page.getByTestId("findings")).toBeVisible();
  await expect(page.getByTestId("findings-state")).toContainText("Analysis is current");
};
const axe = async (page: Page) => {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
};

test("a looping run lists its probable loops, and Show evidence selects the cited event", async ({
  page,
}) => {
  await open(page, "looping");
  const panel = page.getByTestId("findings");
  const loops = panel
    .getByTestId("finding")
    .filter({ hasText: "Probable: the agent may be looping" });
  await expect(loops.first()).toContainText("Probable");
  await loops.first().getByText("Details", { exact: true }).click(); // the explanation sits behind Details
  await expect(loops.first().getByLabel("Pattern")).toContainText("tool:search");
  await expect(panel.getByTestId("finding").filter({ hasText: "Observed" }).first()).toBeVisible();
  await panel.getByText(/^Health:/).click(); // the dimensions sit behind a disclosure
  await expect(panel.getByTestId("health")).toContainText("Loops");
  await loops.first().getByRole("button", { name: "Show evidence" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("option", { selected: true })).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/e2e-looping.png`, fullPage: true });
});

test("a tool-failure cascade is observed, not probable, and links to the failing calls", async ({
  page,
}) => {
  await open(page, "cascade");
  const cascade = page.getByTestId("finding").filter({ hasText: "Observed" }).first();
  await expect(cascade).toHaveAttribute("data-type", "tool_failure_cascade");
  await expect(cascade).not.toContainText("Probable:");
  await cascade.getByRole("button", { name: "Show evidence" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/e2e-cascade.png`, fullPage: true });
});

test("a failed run shows the failure chain as linked events and a probable cause", async ({
  page,
}) => {
  await open(page, "failing");
  const chain = page.getByTestId("finding").filter({ hasText: "the run failed" });
  await expect(chain).toHaveAttribute("data-type", "failure_chain");
  await chain.getByText("Details", { exact: true }).click();
  await expect(chain).toContainText("Observed");
  expect(await chain.getByRole("listitem").count()).toBeGreaterThanOrEqual(3);
  await expect(page.locator('[data-type="probable_cause"]')).toContainText("Probable:");
  await chain.getByRole("listitem").first().getByRole("button").click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/e2e-failing-chain.png`, fullPage: true });
});

test("the healthy run has no findings and says the analysis is current", async ({ page }) => {
  await open(page, "healthy");
  await expect(page.getByTestId("findings-empty")).toContainText("Nothing wrong found");
  await expect(page.getByTestId("finding")).toHaveCount(0);
});

test("the runs list shows a findings column as text", async ({ page }) => {
  await page.goto(`/w/${workspace()}/projects/all/runs`);
  await expect(page.getByRole("table")).toBeVisible();
  const row = (name: string) =>
    page.locator("tbody tr", { has: page.locator(`a[href*="${runs[name]}"]`) });
  await expect(row("looping").getByTestId("findings-cell")).not.toContainText("No findings");
  await expect(row("looping").getByTestId("findings-cell")).toContainText(/critical|warning|info/);
  await expect(row("healthy").getByTestId("findings-cell")).toContainText("No findings");
  await page.screenshot({ path: `${SHOTS}/e2e-runs-list.png`, fullPage: true });
  // Phase 16 search sits beside the findings column: a search keeps the column on every result row
  await page.getByRole("searchbox", { name: "Search" }).fill("status:failed");
  await page.getByRole("searchbox", { name: "Search" }).press("Enter");
  await expect(page.getByRole("list", { name: "Search terms" })).toContainText("status:failed");
  await expect(row("failing").getByTestId("findings-cell")).toContainText(/critical|warning|info/);
  await expect(row("healthy")).toHaveCount(0);
});

test("a VIEWER sees the same findings as the owner and no withheld content", async ({
  page,
  browser,
  baseURL,
}) => {
  await open(page, "failing");
  const ownerText = await page.getByTestId("findings").innerText();
  const context = await browser.newContext({ baseURL, viewport: { width: 1400, height: 1000 } });
  try {
    await signIn(context, baseURL!, viewerToken);
    const viewer = await context.newPage();
    await open(viewer, "failing");
    expect(await viewer.getByTestId("findings").innerText()).toEqual(ownerText);
    // the failing command is shell content: a person without payload.read must not see it anywhere on the page
    expect(await viewer.locator("body").innerText()).not.toContain("pytest -q");
    await viewer.screenshot({ path: `${SHOTS}/e2e-viewer.png`, fullPage: true });
  } finally {
    await context.close();
  }
});
