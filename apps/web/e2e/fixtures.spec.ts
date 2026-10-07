import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const BASE = "/w/demo/projects/all";
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
const SHOTS = "../../docs/screenshots/phase-4";

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

test("dashboard summarises the project", async ({ page }) => {
  await page.goto(BASE);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText("Success rate")).toBeVisible();
  await expect(page.getByText("Running agents")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent failures" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Migrate billing schema" }).first()).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/dashboard.png`, fullPage: true });
});

test("runs list filters by status and keeps state in the URL", async ({ page }) => {
  await page.goto(`${BASE}/runs`);
  await expect(page.getByRole("table")).toBeVisible();
  const all = await page.locator("tbody tr").count();
  expect(all).toBe(6);
  await page.getByLabel("Failed").check();
  await expect(page).toHaveURL(/status=FAILED/);
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator("tbody tr").first()).toContainText("Failed");
  await page.getByLabel("Agent").fill("nobody");
  await expect(page.getByText("No runs match these filters")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(6);
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/runs-list.png`, fullPage: true });
});

test("failed run: headline, first error, drawer, keyboard navigation and focus return", async ({
  page,
}) => {
  await page.goto(`${BASE}/runs/${RUN(2)}`);
  await expect(page.getByTestId("headline")).toContainText("Failed after 2 retries");
  await expect(page.getByTestId("first-error")).toContainText("run_migration");
  await expect(page.getByTestId("progress")).toContainText("events shown");

  const timeline = page.getByRole("listbox", { name: /Run timeline/ });
  await timeline.focus();
  await page.keyboard.press("j");
  await expect(page.getByRole("option", { selected: true })).toContainText("run.started");
  await page.keyboard.press("k");
  await page.keyboard.press("e");
  const drawer = page.getByRole("dialog");
  await expect(drawer).toContainText("ConnectionTimeout");
  await expect(drawer.getByRole("heading", { name: /Error/ })).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Close details" })).toBeFocused();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/run-failed-drawer.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(timeline).toBeFocused();
});

test("filters and groups act on the timeline", async ({ page }) => {
  await page.goto(`${BASE}/runs/${RUN(2)}`);
  await expect(page.getByTestId("progress")).toContainText("events shown");
  const before = await page.getByRole("option").count();
  await page.getByLabel("Errors only").check();
  await expect(page.getByRole("option")).toHaveCount(4);
  await page.getByLabel("Errors only").uncheck();
  await page.getByRole("button", { name: "Collapse groups" }).click();
  expect(await page.getByRole("option").count()).toBeLessThan(before);
  await page.getByRole("button", { name: "Expand groups" }).click();
  expect(await page.getByRole("option").count()).toBe(before);
  // LLM drawer shows model fields and the lazily loaded payload.
  await page
    .getByRole("option", { name: /llm\.request\.completed/ })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toContainText("Model call");
  await expect(page.getByRole("dialog").getByLabel("Event payload")).toBeVisible();
});

test("successful and in-progress runs read at a glance", async ({ page }) => {
  await page.goto(`${BASE}/runs/${RUN(1)}`);
  await expect(page.getByTestId("headline")).toContainText("Succeeded");
  await expect(page.getByTestId("first-error")).toHaveCount(0);
  await page.screenshot({ path: `${SHOTS}/run-success.png`, fullPage: true });
  await page.goto(`${BASE}/runs/${RUN(4)}`);
  await expect(page.getByTestId("headline")).toContainText("In progress");
  await page.goto(`${BASE}/runs/${RUN(5)}`);
  await expect(page.getByTestId("headline")).toContainText("Waiting for approval");
});

test("unknown run shows a not-found state", async ({ page }) => {
  await page.goto(`${BASE}/runs/run_00000000000000000000000999`);
  await expect(page.locator('[data-state="error"]')).toContainText("Not found");
});

test("10,000-event run renders a bounded number of rows", async ({ page }) => {
  await page.goto(`${BASE}/runs/${RUN(6)}`);
  const progress = page.getByTestId("progress");
  await expect(progress).toContainText(/10,000 of 10,000 events shown/, { timeout: 45_000 });
  const rows = page.getByRole("option");
  expect(await rows.count()).toBeLessThan(80);
  const size = Number(await rows.first().getAttribute("aria-setsize"));
  expect(size).toBeGreaterThan(5_000);
  await page.getByRole("listbox").focus();
  await page.keyboard.press("End");
  await expect(page.getByRole("option", { selected: true })).toContainText("run.completed");
  expect(await rows.count()).toBeLessThan(80);
  await page.screenshot({ path: `${SHOTS}/run-stress-end.png` });
});

test("mobile layout has no horizontal page scroll", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  for (const path of [BASE, `${BASE}/runs`, `${BASE}/runs/${RUN(2)}`]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
  await page.screenshot({ path: `${SHOTS}/run-mobile.png`, fullPage: true });
});

test("security headers are set and the CSP breaks nothing", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(e.message));
  const res = await page.goto(`${BASE}/runs/${RUN(2)}`);
  const h = res!.headers();
  expect(h["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(h["content-security-policy"]).toContain("object-src 'none'");
  expect(h["x-content-type-options"]).toBe("nosniff");
  expect(h["referrer-policy"]).toBe("no-referrer");
  await expect(page.getByTestId("first-error")).toBeVisible();
  const api = await page.request.get("/api/abb/v1/runs");
  expect(api.headers()["cache-control"]).toContain("no-store");
  expect(api.headers()["x-content-type-options"]).toBe("nosniff");
  expect(problems).toEqual([]);
});
