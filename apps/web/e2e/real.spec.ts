import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { signIn, workspace, workspaceHeader } from "./session";

// Runs only through scripts/e2e-web-real.sh, which ingests the runs over HTTP into a real API + worker.
const failed = process.env.E2E_REAL_FAILED_RUN;
const ok = process.env.E2E_REAL_OK_RUN;
test.skip(!failed || !ok, "needs scripts/e2e-web-real.sh");
test.use({ baseURL: "http://localhost:3102" });
test.beforeEach(async ({ context, baseURL }) => signIn(context, baseURL!));
const SHOTS = "../../docs/screenshots/phase-4";

test("a really ingested failed run explains itself", async ({ page }) => {
  await page.goto(`/w/${workspace()}/projects/all/runs/${failed}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("fails");
  await expect(page.getByTestId("headline")).toContainText("Failed after 1 retry");
  await expect(page.getByTestId("first-error")).toContainText("run_tests · TestFailure");
  await expect(page.getByTestId("progress")).toContainText("events shown");
  const types = await page.locator(".tl-type").allTextContents();
  expect(types.slice(0, 4)).toEqual([
    "run.started",
    "agent.started",
    "file.read",
    "llm.request.completed",
  ]);
  await page.getByRole("button", { name: "Show it" }).click();
  await expect(page.getByRole("dialog")).toContainText("TestFailure");
  const bad = (
    await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze()
  ).violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad).toEqual([]);
  await page.screenshot({ path: `${SHOTS}/real-run-failed.png`, fullPage: true });
});

test("the real project lists both runs and the proxy exposes no credentials", async ({ page }) => {
  await page.goto(`/w/${workspace()}/projects/all/runs`);
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await expect(page.getByText("Real: fix login bug (passes)")).toBeVisible();
  await page.getByLabel("Failed").check();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.goto(`/w/${workspace()}/projects/all/runs/${ok}`);
  await expect(page.getByTestId("headline")).toContainText("Succeeded");
  const html = await page.content();
  expect(html).not.toContain("abb_live_");
  const r = await page.request.get("/api/abb/v1/runs", { headers: await workspaceHeader(page) });
  expect(r.status()).toBe(200);
  expect((await r.text()).includes("abb_live_")).toBe(false);
  expect((await page.request.post("/api/abb/v1/events/batch", { data: {} })).status()).toBe(405);
  expect((await page.request.get("/api/abb/v1/keys")).status()).toBe(404);
  await page.screenshot({ path: `${SHOTS}/real-runs-list.png`, fullPage: true });
});
