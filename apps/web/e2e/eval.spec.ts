import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signIn, workspace } from "./session";

// Runs only through scripts/eval-e2e.sh: the scripted coding agent wrote a passing run and a wrong-fix run through the
// SDK into a real API; the owner's evaluators (rule, metric, the fake judge, a failing one) ran in the worker and an
// experiment was declared. The built web server on :3180 reads it through its proxy. The OWNER session sees everything;
// the VIEWER session sees scores, labels and structure but no explanation text and a metadata-only comparison.
const ids: Record<string, string> = JSON.parse(process.env.E2E_EVAL_IDS ?? "{}");
const viewerToken = process.env.E2E_VIEWER_SESSION_TOKEN;
test.skip(!ids["experiment"] || !viewerToken, "needs scripts/eval-e2e.sh");
test.use({ baseURL: "http://localhost:3180", viewport: { width: 1400, height: 1000 } });
const SHOTS = "../../docs/screenshots/phase-11";
const HOSTILE = "<img src=x onerror=";

const base = () => `/w/${workspace()}/projects/all`;
const compareUrl = () =>
  `${base()}/runs/compare?left=${ids["baseline_run"]}&right=${ids["wrong_run"]}`;
const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
};
const asViewer = async (context: Parameters<typeof signIn>[0], baseURL: string) => {
  await context.clearCookies();
  await signIn(context, baseURL, viewerToken);
};

test.describe("as the OWNER", () => {
  test.beforeEach(async ({ context, baseURL }) => signIn(context, baseURL!));

  test("the run page shows each kind of evaluation, the judge as an opinion, and the failed one", async ({
    page,
  }) => {
    const alerts: string[] = [];
    page.on("dialog", (d) => {
      alerts.push(d.message());
      void d.dismiss();
    });
    await page.goto(`${base()}/runs/${ids["wrong_run"]}`);
    const section = page.getByTestId("evaluations");
    const row = (name: string) =>
      section.getByTestId("evaluation").filter({ has: page.getByRole("heading", { name }) });
    await expect(row("tests-pass")).toHaveAttribute("data-state", "completed");
    await expect(row("tests-pass").getByTestId("evaluation-label")).toHaveText("fail");
    await expect(row("cost-under").getByTestId("evaluation-label")).toHaveText("pass");
    await expect(row("quality-judge").getByTestId("judge-badge")).toHaveText(
      "model judge, not ground truth",
    );
    await expect(row("quality-judge")).toContainText("fake-judge-1");
    // the run still says what it is: a failed evaluator never changes it
    await expect(row("broken-judge")).toHaveAttribute("data-state", "failed");
    await expect(row("broken-judge").getByTestId("evaluation-error")).toContainText(
      "no room for the run data",
    );
    await expect(page.locator('.badge[data-status="FAILED"]').first()).toBeVisible();
    await axe(page);
    await section.scrollIntoViewIfNeeded();
    await section.screenshot({ path: `${SHOTS}/e2e-evaluations-wrong-fix.png` });
    expect(alerts).toEqual([]);
  });

  test("records a human verdict in the browser; hostile text stays text", async ({ page }) => {
    await page.goto(`${base()}/runs/${ids["wrong_run"]}`);
    const form = page.getByRole("form", { name: "Record a verdict" });
    await form.getByLabel("bad").check();
    await form.getByLabel(/Explanation/).fill(`${HOSTILE}"alert(2)"> wrong file edited`);
    await form.getByRole("button", { name: "Save verdict" }).click();
    await expect(form.getByText("Verdict saved.")).toBeVisible();
    const verdict = page
      .getByTestId("evaluation")
      .filter({ has: page.getByRole("heading", { name: "reviewer" }) });
    await expect(verdict).toContainText("wrong file edited");
    await expect(verdict.getByTestId("explanation")).toContainText(HOSTILE);
    expect(await page.locator("[data-testid=evaluations] img").count()).toBe(0);
    // the baseline's verdict (recorded by the script) carries the same hostile text
    await page.goto(`${base()}/runs/${ids["baseline_run"]}`);
    await expect(page.getByTestId("explanation").first()).toContainText(HOSTILE);
    expect(await page.locator("[data-testid=evaluations] img").count()).toBe(0);
  });

  test("compare: the divergence is where the second edit goes to another file, the outcome is the second test run", async ({
    page,
  }) => {
    await page.goto(compareUrl());
    await expect(page.getByTestId("aligned-steps")).toBeVisible();
    await expect(page.getByTestId("metadata-notice")).toHaveCount(0);
    const divergence = page.locator("[data-testid=aligned-row][data-divergence=true]");
    await expect(divergence).toHaveCount(1);
    // the file.read that opens the second edit: the baseline reads app/session.py again, the wrong fix reads app/oauth.py
    await expect(divergence.locator("[data-side=left]")).toContainText("file.read");
    await expect(divergence.locator("[data-side=left]")).toContainText("app/session.py");
    await expect(divergence.locator("[data-side=right]")).toContainText("app/oauth.py");
    // the changed file.modified follows it
    const index = Number(await divergence.getAttribute("data-index"));
    const next = page.locator(`[data-testid=aligned-row][data-index="${index + 1}"]`);
    await expect(next).toHaveAttribute("data-relation", "changed");
    await expect(next.locator("[data-side=left]")).toContainText("file.modified");
    // the first outcome difference is the second test run (completed vs failed), later than the divergence
    const outcome = page.locator("[data-testid=aligned-row][data-outcome=true]");
    await expect(outcome).toHaveCount(1);
    await expect(outcome.locator("[data-side=left]")).toContainText("shell.command.completed");
    await expect(outcome.locator("[data-side=right]")).toContainText("shell.command.failed");
    expect(Number(await outcome.getAttribute("data-index"))).toBeGreaterThan(index);
    // the summary, scores and files
    await expect(page.getByTestId("compare-summary")).toContainText("Duration");
    await expect(page.getByTestId("compare-evaluations")).toContainText("tests-pass");
    await expect(page.getByTestId("compare-files")).toContainText("app/oauth.py");
    // Jump to divergence moves focus there; the live region says where
    await page.getByRole("button", { name: "Jump to divergence" }).click();
    await expect(divergence).toBeFocused();
    await expect(page.getByTestId("aligned-live")).toContainText("Divergence:");
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/e2e-compare-owner.png`, fullPage: true });
  });

  test("the experiment is not controlled (two settings differ); Evaluate all fills the metrics", async ({
    page,
  }) => {
    await page.goto(`${base()}/experiments`);
    const link = page.getByRole("link", { name: /Fix strategy/ });
    await expect(link).toContainText("<b>Fix strategy</b>");
    expect(await page.locator("table b").count()).toBe(0);
    await link.click();
    const banner = page.getByTestId("control-banner");
    await expect(banner).toHaveAttribute("data-control", "multiple");
    await expect(banner).toContainText("Not controlled");
    await expect(banner).toContainText("model, variant");
    await expect(page.getByTestId("warnings")).toContainText("More than one setting differs");
    await expect(page.getByTestId("variants")).toContainText("scripted-agent-2");
    await page.getByRole("button", { name: "Evaluate all" }).click();
    await expect(page.getByTestId("evaluate-result")).toContainText("Started 4 evaluation(s)");
    // the worker delays the batch about a second; the metrics read-model fills in as rows complete
    await expect(async () => {
      await page.reload();
      await expect(page.getByTestId("metrics")).toContainText("run-succeeded: pass rate");
      await expect(
        page
          .getByTestId("metrics")
          .getByRole("row", { name: /run-succeeded: completed \/ failed/ }),
      ).toContainText("1 / 0");
    }).toPass({ timeout: 60_000 });
    const metrics = page.getByTestId("metrics");
    await expect(metrics.getByRole("row", { name: /^Success rate/ })).toContainText("100%");
    await expect(metrics.getByRole("row", { name: /^Success rate/ })).toContainText("0%");
    await expect(metrics).toContainText("Cost, 95th percentile");
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/e2e-experiment-owner.png`, fullPage: true });
    // compare with the baseline run from the experiment
    await page.getByRole("link", { name: "Compare with baseline run" }).click();
    await expect(page.getByTestId("aligned-steps")).toBeVisible();
  });
});

test.describe("as the VIEWER", () => {
  test.beforeEach(async ({ context, baseURL }) => asViewer(context, baseURL!));

  test("sees scores and labels but no explanation text and no forms", async ({ page }) => {
    await page.goto(`${base()}/runs/${ids["baseline_run"]}`);
    const section = page.getByTestId("evaluations");
    await expect(section.getByTestId("evaluation").first()).toBeVisible();
    await expect(section.getByTestId("evaluation-label").first()).toBeVisible();
    for (const name of ["reviewer", "quality-judge"]) {
      const free = section
        .getByTestId("evaluation")
        .filter({ has: page.getByRole("heading", { name }) });
      await expect(free.getByTestId("explanation")).toHaveCount(0);
      await expect(free.getByTestId("explanation-withheld")).toBeVisible();
    }
    await expect(section.getByTestId("explanation-withheld").first()).toBeVisible();
    await expect(section).not.toContainText("onerror");
    await expect(section.getByRole("button", { name: "Run evaluator" })).toHaveCount(0);
    await expect(section.getByRole("form", { name: "Record a verdict" })).toHaveCount(0);
    await axe(page);
    await section.screenshot({ path: `${SHOTS}/e2e-evaluations-viewer.png` });
  });

  test("the comparison is metadata only: the divergence is the second test run, no paths or commands", async ({
    page,
  }) => {
    await page.goto(compareUrl());
    await expect(page.getByTestId("metadata-notice")).toContainText("metadata only");
    const divergence = page.locator("[data-testid=aligned-row][data-divergence=true]");
    await expect(divergence).toHaveCount(1);
    // on metadata the second test run is both the divergence and the first outcome difference
    await expect(divergence).toHaveAttribute("data-outcome", "true");
    await expect(divergence.locator("[data-side=left]")).toContainText("shell.command.completed");
    await expect(divergence.locator("[data-side=right]")).toContainText("shell.command.failed");
    await expect(divergence).toContainText("details withheld");
    const files = page.getByTestId("compare-files");
    await expect(files).toHaveAttribute("data-withheld", "true");
    await expect(files).toContainText("withheld for your role");
    const text = (await page.getByTestId("compare").innerText()).toLowerCase();
    for (const secret of ["app/session.py", "app/oauth.py", "unittest", "git push"])
      expect(text).not.toContain(secret);
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/e2e-compare-viewer.png`, fullPage: true });
  });

  test("reads the experiment but cannot change it", async ({ page }) => {
    await page.goto(`${base()}/experiments/${ids["experiment"]}`);
    await expect(page.getByTestId("control-banner")).toContainText("Not controlled");
    await expect(page.getByTestId("metrics")).toBeVisible();
    await expect(page.getByRole("form", { name: "Add a variant" })).toHaveCount(0);
    await expect(page.getByRole("form", { name: "Attach a run" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Evaluate all" })).toHaveCount(0);
    await axe(page);
  });
});
