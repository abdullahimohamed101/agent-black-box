import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";
import { enterReplay, openRun, scrub, seekTo } from "./replay";

// Phase 14 step 4 (fixtures mode): decisions on the run page. Run 20 waits for a person; run 21 holds an allowed read, a
// read denied by deny-env-read v3 and a push that was approved and then used. A denial is a policy outcome, not a failed run.
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
const WAITING = `/w/demo/projects/all/runs/${RUN(20)}`;
const DECISIONS = `/w/demo/projects/all/runs/${RUN(21)}`;
const SHOTS = "../../docs/screenshots/phase-14";
const APR1 = `apr_${String(1).padStart(26, "0")}`;

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}
const row = (page: Page, type: string, nth = 0) =>
  page
    .getByRole("option")
    .filter({
      has: page.locator(".tl-type", { hasText: new RegExp(`^${type.replace(/\./g, "\\.")}$`) }),
    })
    .nth(nth);
const noSideScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const)
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height } });

    test("a run waiting for a person: the headline, the real time left and a link to decide", async ({
      page,
    }) => {
      const problems: string[] = [];
      page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
      page.on("pageerror", (e) => problems.push(e.message));
      await page.goto(WAITING);
      await expect(page.getByTestId("headline")).toContainText(
        /^Waiting for approval of github\.push at step \d+\.$/,
      );
      await expect(page.getByTestId("headline-detail")).toContainText(/\d+ h( \d+ min)? left/);
      const banner = page.getByTestId("approval-banner");
      await expect(banner).toContainText("Waiting for a person to approve github.push");
      await expect(page.getByText("Awaiting approval").first()).toBeVisible();
      expect(await noSideScroll(page)).toBe(true);
      await axe(page);
      await banner.getByRole("link", { name: "Open Approvals" }).click();
      await expect(page).toHaveURL(new RegExp(`/w/demo/approvals\\?approval=${APR1}`));
      await expect(page.getByTestId("approval-detail")).toContainText("github.push");
      expect(problems.filter((p) => /418|hydrat/i.test(p))).toEqual([]);
    });

    test("decisions in the timeline: glyph and word, the policy's reason, the provenance marker; none is an error", async ({
      page,
    }) => {
      await page.goto(DECISIONS);
      await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
      await expect(page.getByTestId("headline")).toContainText(/^Completed in/);
      await expect(row(page, "policy.decision", 0)).toContainText("Allowed (default)");
      const denied = row(page, "policy.action.blocked");
      await expect(denied).toContainText("Denied by deny-env-read v3");
      await expect(denied).toContainText(
        "Environment files hold secrets, so agents do not read them.",
      );
      await expect(denied).toContainText("Recorded by the policy service");
      await expect(row(page, "approval.requested")).toContainText("Approval requested");
      await expect(row(page, "approval.granted")).toContainText("Approved");
      await expect(row(page, "approval.consumed")).toContainText("Approval used");
      await expect(row(page, "file.read")).not.toContainText("policy service");
      // not a failure: no error rows, no fault line, and nothing is drawn in the failure colour
      await expect(page.locator("[data-error], .tl-fault")).toHaveCount(0);
      const colors = await page
        .locator(".tl-pol")
        .evaluateAll((els) => els.map((e) => getComputedStyle(e).color));
      for (const c of colors) {
        const [r, g, b] = (c.match(/\d+/g) ?? []).map(Number) as [number, number, number];
        expect(r > 150 && g < 80 && b < 80, `a red-looking colour: ${c}`).toBe(false);
      }
      expect(await noSideScroll(page)).toBe(true);
      await axe(page);
    });

    test("the inspector explains the decision and says who wrote it", async ({ page }) => {
      await page.goto(DECISIONS);
      await row(page, "policy.action.blocked").click();
      const drawer = page.getByTestId("drawer-policy");
      await expect(drawer).toContainText("Denied by deny-env-read v3");
      await expect(drawer.getByTestId("provenance")).toHaveText(
        "Recorded by the policy service, not by the agent.",
      );
      await expect(page.getByRole("dialog")).not.toHaveAttribute("data-error", /.*/);
      await axe(page);
    });
  });

test("the class filter 'Approvals & policy' shows the decisions only", async ({ page }) => {
  await page.goto(DECISIONS);
  await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
  await page.getByRole("button", { name: /^Filter/ }).click();
  for (const name of ["Run & agent", "Files & git"]) await page.getByLabel(name).uncheck();
  await expect(page.getByLabel("Approvals & policy")).toBeChecked();
  const types = await page.locator(".tl-type").allTextContents();
  expect(types).toEqual([
    "policy.decision",
    "policy.action.blocked",
    "policy.decision",
    "approval.requested",
    "approval.granted",
    "approval.consumed",
  ]);
});

test("replay: the decisions appear as ordinary events as the playhead reaches them", async ({
  page,
}) => {
  await openRun(page, "/w/demo/projects/all", RUN(21));
  await enterReplay(page);
  await seekTo(page, 0);
  await expect(page.getByRole("option").filter({ hasText: "policy.action.blocked" })).toHaveCount(
    0,
  );
  const total = await scrub(page).getAttribute("max");
  await seekTo(page, Number(total));
  await expect(row(page, "policy.action.blocked")).toContainText("Denied by deny-env-read v3");
  await expect(row(page, "approval.consumed")).toContainText("Approval used");
  // the fault line logic is unchanged: a denial is not an error event
  await expect(page.locator(".tl-fault")).toHaveCount(0);
});

test("a viewer reads the same words; the time left needs approval rights and is not shown", async ({
  page,
}) => {
  const calls: string[] = [];
  page.on("request", (r) => calls.push(new URL(r.url()).pathname));
  await page.goto(`/w/viewer-only/projects/all/runs/${RUN(20)}`);
  await expect(page.getByTestId("headline")).toContainText(/^Waiting for approval of github\.push/);
  await expect(page.getByTestId("headline-detail")).not.toContainText("left");
  const banner = page.getByTestId("approval-banner");
  await expect(banner).toContainText("Someone with approval rights decides this request.");
  await expect(banner.getByRole("link")).toHaveCount(0);
  expect(calls.filter((p) => p.includes("/v1/approvals"))).toEqual([]);
  await page.goto(`/w/viewer-only/projects/all/runs/${RUN(21)}`);
  await expect(row(page, "policy.action.blocked")).toContainText("Denied by deny-env-read v3");
});

for (const scheme of ["light", "dark"] as const)
  test.describe(`${scheme} scheme`, () => {
    test.use({ colorScheme: scheme, viewport: { width: 1440, height: 900 } });
    test("axe on both runs and the open inspector", async ({ page }) => {
      await page.goto(WAITING);
      await expect(page.getByTestId("approval-banner")).toBeVisible();
      await axe(page);
      await page.goto(DECISIONS);
      await expect(row(page, "policy.action.blocked")).toBeVisible();
      await axe(page);
      await row(page, "approval.granted").click();
      await expect(page.getByTestId("drawer-policy")).toBeVisible();
      await axe(page);
    });
  });

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("nothing animates", async ({ page }) => {
    for (const url of [WAITING, DECISIONS]) {
      await page.goto(url);
      await page.waitForTimeout(300);
      const running = await page.evaluate(
        () => document.getAnimations().filter((a) => a.playState === "running").length,
      );
      expect(running, url).toBe(0);
    }
  });
});

for (const scheme of ["light", "dark"] as const)
  for (const [width, height] of [
    [1440, 900],
    [375, 812],
  ] as const)
    test(`screenshots: ${scheme} ${width}`, async ({ browser }) => {
      const ctx = await browser.newContext({ colorScheme: scheme, viewport: { width, height } });
      const page = await ctx.newPage();
      const shot = async (name: string) => {
        await settleAnimations(page);
        await page.screenshot({
          path: `${SHOTS}/${name}-${width}-${scheme}.png`,
          fullPage: width < 800,
        });
      };
      await page.goto(WAITING);
      await expect(page.getByTestId("approval-banner")).toBeVisible();
      await shot("run-waiting");
      await page.goto(DECISIONS);
      await expect(row(page, "policy.action.blocked")).toBeVisible();
      await shot("run-decisions");
      await row(page, "policy.action.blocked").click();
      await expect(page.getByTestId("drawer-policy")).toBeVisible();
      await shot("run-decision-inspector");
      await ctx.close();
    });
