import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";

// Phase 19b step 3 (fixtures mode): a run page in each trace state, the runs list marker and the honest words for a run
// that is gone. The two runs come from src/fixtures/traceStateRuns.ts: 90 is expired, 91 is being deleted.
const SHOTS = "../../docs/screenshots/phase-19b";
const run = (n: number) => `/w/demo/projects/all/runs/run_${String(n).padStart(26, "0")}`;
const EXPIRED = run(90);
const DELETING = run(91);
const AVAILABLE = run(1);

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

/** The page's alerts, without Next's empty route announcer. */
const alerts = (page: Page) => page.locator("[role=alert]:not(#__next-route-announcer__)");

function watchConsole(page: Page): string[] {
  const seen: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") seen.push(m.text());
  });
  page.on("pageerror", (e) => seen.push(e.message));
  return seen;
}

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const)
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height } });

    test("an expired run: the notice, the numbers of the record, no tabs, nothing of the trace", async ({
      page,
    }) => {
      const errors = watchConsole(page);
      const asked: string[] = [];
      page.on("request", (r) => asked.push(new URL(r.url()).pathname));
      await page.goto(EXPIRED);
      await expect(
        page.getByRole("heading", { level: 1, name: "Nightly revenue report" }),
      ).toBeVisible();
      const notice = page.getByTestId("trace-notice");
      await expect(notice).toHaveAttribute("data-state", "expired");
      await expect(notice).toContainText(
        "The trace of this run was removed by the retention policy; the run summary and its numbers remain.",
      );
      await expect(page.getByLabel("Run summary")).toContainText("Tool calls");
      await expect(page.getByRole("tab")).toHaveCount(0);
      await expect(page.getByRole("button", { name: /Replay/ })).toHaveCount(0);
      await expect(page.getByText("This run has no events yet.")).toHaveCount(0);
      await expect(alerts(page)).toHaveCount(0);
      await page.waitForTimeout(300);
      expect(asked.filter((p) => /\/(findings|evaluations|spans|stream)$/.test(p))).toEqual([]);
      await axe(page);
      expect(errors).toEqual([]);
    });

    test("a deleting run: the same notice, in amber words", async ({ page }) => {
      await page.goto(DELETING);
      const notice = page.getByTestId("trace-notice");
      await expect(notice).toHaveAttribute("data-state", "deleting");
      await expect(notice).toContainText("The retention policy is removing the trace of this run.");
      await expect(page.getByRole("tab")).toHaveCount(0);
      await axe(page);
    });

    test("an available run has no notice", async ({ page }) => {
      await page.goto(AVAILABLE);
      await expect(page.getByTestId("progress")).toBeVisible();
      await expect(page.getByTestId("trace-notice")).toHaveCount(0);
    });

    test("the runs list marks them and keeps them openable", async ({ page }) => {
      await page.goto("/w/demo/projects/all/runs");
      await page.getByPlaceholder("e.g. coding-agent").fill("report-agent");
      const markers = page.getByTestId("trace-marker");
      await expect(markers).toHaveCount(2);
      await expect(markers.first()).toContainText(/Trace expired|Trace being removed/);
      await axe(page);
      await page.getByRole("link", { name: "Nightly revenue report" }).click();
      await expect(page.getByTestId("trace-notice")).toBeVisible();
    });
  });

test("a run that is gone reads as such, without a retry", async ({ page }) => {
  await page.goto(run(999));
  const alert = alerts(page);
  await expect(alert).toContainText("This run no longer exists");
  await expect(alert).toContainText("retention policy");
  await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
  await axe(page);
});

test("comparing an expired run says so in words", async ({ page }) => {
  await page.goto(
    `/w/demo/projects/all/runs/compare?left=run_${String(90).padStart(26, "0")}&right=run_${String(1).padStart(26, "0")}`,
  );
  await expect(page.getByTestId("trace-expired")).toContainText("Trace expired");
  await expect(alerts(page)).toHaveCount(0);
  await axe(page);
});

for (const scheme of ["light", "dark"] as const)
  test.describe(`${scheme} scheme`, () => {
    test.use({ colorScheme: scheme, viewport: { width: 1440, height: 900 } });
    test("axe on the expired and the deleting run", async ({ page }) => {
      await page.goto(EXPIRED);
      await expect(page.getByTestId("trace-notice")).toBeVisible();
      await axe(page);
      await page.goto(DELETING);
      await expect(page.getByTestId("trace-notice")).toBeVisible();
      await axe(page);
    });
  });

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("nothing animates on an expired run", async ({ page }) => {
    await page.goto(EXPIRED);
    await expect(page.getByTestId("trace-notice")).toBeVisible();
    await page.waitForTimeout(300);
    const running = await page.evaluate(
      () => document.getAnimations().filter((a) => a.playState === "running").length,
    );
    expect(running).toBe(0);
  });
});

test.describe("keyboard", () => {
  test("the runs list marker's run opens from the keyboard", async ({ page }) => {
    await page.goto("/w/demo/projects/all/runs");
    await page.getByPlaceholder("e.g. coding-agent").fill("report-agent");
    await expect(page.getByTestId("trace-marker")).toHaveCount(2);
    await page.getByRole("link", { name: "Nightly revenue report" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("trace-notice")).toBeVisible();
  });
});

for (const scheme of ["light", "dark"] as const)
  for (const [width, height] of [
    [1440, 900],
    [375, 812],
  ] as const)
    test(`screenshot: ${scheme} ${width}`, async ({ browser }) => {
      const ctx = await browser.newContext({ colorScheme: scheme, viewport: { width, height } });
      const page = await ctx.newPage();
      await page.goto(EXPIRED);
      await expect(page.getByTestId("trace-notice")).toBeVisible();
      await settleAnimations(page);
      await page.screenshot({
        path: `${SHOTS}/run-trace-expired-${width}-${scheme}.png`,
        fullPage: true,
      });
      await page.goto(DELETING);
      await expect(page.getByTestId("trace-notice")).toBeVisible();
      await settleAnimations(page);
      await page.screenshot({
        path: `${SHOTS}/run-trace-deleting-${width}-${scheme}.png`,
        fullPage: true,
      });
      await page.goto("/w/demo/projects/all/runs");
      await page.getByPlaceholder("e.g. coding-agent").fill("report-agent");
      await expect(page.getByTestId("trace-marker")).toHaveCount(2);
      await settleAnimations(page);
      await page.screenshot({ path: `${SHOTS}/runs-list-markers-${width}-${scheme}.png` });
      await ctx.close();
    });
