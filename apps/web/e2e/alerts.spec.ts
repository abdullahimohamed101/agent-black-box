import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";
import { shotsDir } from "./shots-dir";

// Phase 17 step 3 (fixtures mode): the Alerts page. The fixture server keeps one in-memory store shared by parallel
// specs, so tests that write create their own uniquely named items and never depend on counts.
// Design pass 2 step 5c: Alerts is a tab of Settings (/settings/alerts); the old /alerts address redirects there and keeps ?tab=.
const PAGE = "/w/demo/settings/alerts";
const VIEWER = "/w/viewer-only/settings/alerts";
const SHOTS = shotsDir("phase-17");
const uniq = (s: string) =>
  `${s} ${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}
const noSideScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
const watch = (page: Page) => {
  const problems: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(e.message));
  return problems;
};

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const)
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height } });

    test("rules tab: table in words, Alerts in the settings section list, no console errors, accessible", async ({
      page,
    }) => {
      const problems = watch(page);
      await page.goto(PAGE);
      await expect(page.getByRole("heading", { name: "Alerts", level: 1 })).toBeVisible();
      // Alerts lives under Settings (Notify): the section list names it, the account menu no longer does (KI-279)
      const sections = page.getByRole("navigation", { name: "Settings sections" });
      if (width < 900) await sections.getByRole("button", { name: /^Section: Alerts/ }).click();
      await expect(sections.getByRole("link", { name: "Alerts" })).toHaveAttribute(
        "aria-current",
        "page",
      );
      await page.goto(PAGE);
      await page.getByRole("button", { name: "Account" }).click();
      await expect(page.locator("#person-panel")).toBeVisible();
      await expect(page.locator("#person-panel").getByRole("link", { name: "Alerts" })).toHaveCount(
        0,
      );
      await page.keyboard.press("Escape");
      await expect(page.getByTestId("rule-row").first()).toBeVisible();
      await expect(page.getByText("ending failed, timed out")).toBeVisible();
      expect(await noSideScroll(page)).toBe(true);
      await axe(page);
      expect(problems.filter((p) => !/favicon/.test(p))).toEqual([]);
    });

    test("the sub-tabs are the shared tab list: URL-addressable, arrow keys move and select", async ({
      page,
    }) => {
      await page.goto(`${PAGE}?tab=history`);
      await expect(page.getByRole("tab", { name: "History" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await expect(page.getByTestId("history-row").first()).toBeVisible();
      await page.getByRole("tab", { name: "History" }).focus();
      await page.keyboard.press("ArrowLeft");
      await expect(page).toHaveURL(/tab=destinations$/);
      await expect(page.getByTestId("destination-row").first()).toBeVisible();
      await axe(page);
    });

    test("destinations: health, send test, create shows the secret once", async ({ page }) => {
      const problems = watch(page);
      await page.goto(`${PAGE}?tab=destinations`);
      await expect(page.getByTestId("health").filter({ hasText: "Failing" })).toBeVisible();
      await page.getByRole("button", { name: "Send test to Team Slack" }).click();
      await expect(page.getByText(/Test failed\. The destination refused/)).toBeVisible();
      const name = uniq("Pager");
      await page.getByRole("button", { name: "Add a destination" }).click();
      await page.getByLabel("Name").fill(name);
      await page.getByLabel(/^Webhook URL/).fill("https://pager.example.com/hook/xyz");
      await page.getByRole("button", { name: "Add destination" }).click();
      const panel = page.getByTestId("secret-panel");
      await expect(panel).toBeVisible();
      const secret = await panel.getByTestId("signing-secret").innerText();
      expect(secret).toMatch(/^whsec_/);
      await axe(page);
      expect(
        await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage })),
      ).not.toContain(secret);
      expect(page.url()).not.toContain(secret);
      await panel.getByRole("checkbox").check();
      await panel.getByRole("button", { name: "Done" }).click();
      await expect(panel).toHaveCount(0);
      expect(await page.content()).not.toContain(secret);
      await page.reload();
      expect(await page.content()).not.toContain(secret);
      await expect(page.getByRole("row", { name: new RegExp(name) })).toBeVisible();
      expect(problems.filter((p) => !/favicon/.test(p))).toEqual([]);
    });

    test("rules: create with a preview line, then delete with a confirmation", async ({ page }) => {
      await page.goto(PAGE);
      const name = uniq("Spend");
      await page.getByRole("button", { name: "Add a rule" }).click();
      const form = page.getByRole("form", { name: "New rule" });
      await form.getByLabel("Tell me when").selectOption("spend_over");
      await form.getByLabel("Name").fill(name);
      await form.getByLabel(/Daily spend limit/).fill("25");
      await form.getByRole("checkbox", { name: /Ops webhook/ }).check();
      await axe(page);
      await form.getByRole("button", { name: "Create rule" }).click();
      await expect(page.getByTestId("rule-saved")).toContainText(name);
      await expect(page.getByTestId("preview")).toContainText("in the last 7 days");
      await expect(page.getByRole("row", { name: new RegExp(name) })).toContainText(
        "over $25.00 in a UTC day",
      );
      await page.getByRole("button", { name: `Delete ${name}` }).click();
      await page.getByRole("button", { name: `Delete ${name}` }).click();
      await expect(page.getByRole("row", { name: new RegExp(name) })).toHaveCount(0);
    });

    test("history: statuses in words, filters and links", async ({ page }) => {
      await page.goto(`${PAGE}?tab=history`);
      await expect(page.getByText("Retrying (attempt 3)")).toBeVisible();
      await expect(page.getByTestId("history-row").getByText("Gave up").first()).toBeVisible();
      await page.getByLabel("Status").selectOption("skipped");
      await expect(page.getByTestId("history-row")).toHaveCount(3);
      await expect(page.getByText(/cooldown was still running/)).toBeVisible();
      await page.getByLabel("Status").selectOption("");
      await expect(
        page.getByTestId("history-row").getByRole("link", { name: "Approval" }).first(),
      ).toHaveAttribute("href", /approval=apr_/);
      await axe(page);
    });

    test("a viewer sees rules and history but never destinations", async ({ page }) => {
      await page.goto(VIEWER);
      await expect(page.getByTestId("rule-row").first()).toBeVisible();
      await expect(page.getByRole("button", { name: "Add a rule" })).toHaveCount(0);
      await page.getByRole("tab", { name: "Destinations" }).click();
      await expect(page.getByTestId("destinations-forbidden")).toContainText("owners and admins");
      await page.getByRole("tab", { name: "History" }).click();
      await expect(page.getByText("Hidden for your role").first()).toBeVisible();
      await axe(page);
    });

    test("nothing animates", async ({ page }) => {
      await page.goto(PAGE);
      await expect(page.getByTestId("rule-row").first()).toBeVisible();
      await page.waitForTimeout(300);
      expect(
        await page.evaluate(
          () => document.getAnimations().filter((a) => a.playState === "running").length,
        ),
      ).toBe(0);
    });
  });

test("the old /alerts address redirects to the Alerts tab and keeps ?tab=", async ({ page }) => {
  await page.goto("/w/demo/alerts");
  await expect(page).toHaveURL(/\/w\/demo\/settings\/alerts$/);
  await expect(page.getByRole("heading", { name: "Alerts", level: 1 })).toBeVisible();
  await page.goto("/w/demo/alerts?tab=history");
  await expect(page).toHaveURL(/\/w\/demo\/settings\/alerts\?tab=history$/);
  await expect(page.getByRole("tab", { name: "History" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("history-row").first()).toBeVisible();
});

test("reduced motion: the page is complete and static", async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: "reduce" });
  const page = await ctx.newPage();
  await page.goto(PAGE);
  await expect(page.getByTestId("rule-row").first()).toBeVisible();
  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.getByTestId("history-row").first()).toBeVisible();
  expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
  await ctx.close();
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
        await page.screenshot({ path: `${SHOTS}/${name}-${width}-${scheme}.png`, fullPage: true });
      };
      await page.goto(PAGE);
      await expect(page.getByTestId("rule-row").first()).toBeVisible();
      await shot("alerts-rules");
      await page.goto(`${PAGE}?tab=destinations`);
      await expect(page.getByTestId("destination-row").first()).toBeVisible();
      await shot("alerts-destinations");
      await page.goto(`${PAGE}?tab=history`);
      await expect(page.getByTestId("history-row").first()).toBeVisible();
      await shot("alerts-history");
      await ctx.close();
    });
