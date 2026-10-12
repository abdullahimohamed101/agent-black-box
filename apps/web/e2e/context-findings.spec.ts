import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { openContext, settleAnimations } from "./motion";

// Phase 22 D3 / acceptance 5: findings are not in the main view, and any of them is reachable, with its evidence shown,
// in two interactions from the run page: Context, then the finding. Fixtures build, the failed run that has findings.
const BASE = "/w/demo/projects/all";
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
const FAILED_WITH_FINDINGS = `${BASE}/runs/${RUN(10)}`;

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const)
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height } });

    test("the main view has no findings; the Context button says there are some", async ({
      page,
    }) => {
      await page.goto(FAILED_WITH_FINDINGS);
      await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
      await expect(page.getByTestId("findings")).toBeHidden();
      const toggle = page.getByRole("button", { name: /^Context/ });
      await expect(toggle).toHaveText("Context · 2 findings");
      await expect(toggle).toHaveAttribute("aria-pressed", "false");
    });

    test("two clicks: Context, then the finding shows its evidence", async ({ page }) => {
      await page.goto(FAILED_WITH_FINDINGS);
      await page.getByRole("button", { name: /^Context/ }).click(); // 1
      const chain = page.getByTestId("finding").filter({ hasText: "the run failed" });
      await expect(chain).toBeVisible();
      // one line: severity, title and the button share the head; the explanation is behind Details
      await expect(chain.locator(".finding-head").getByRole("button")).toHaveText("Show evidence");
      await expect(chain.getByLabel("Pattern")).toBeHidden();
      await axe(page);
      await chain.getByRole("button", { name: "Show evidence" }).click(); // 2
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.getByRole("option", { selected: true })).toBeAttached();
      await expect(page.locator("[data-landed]")).toHaveCount(1);
    });

    test("every finding is reachable the same way, the probable one included", async ({ page }) => {
      await page.goto(FAILED_WITH_FINDINGS);
      await openContext(page);
      const findings = page.getByTestId("finding");
      await expect(findings).toHaveCount(2);
      for (let i = 0; i < 2; i++) {
        await findings.nth(i).getByRole("button", { name: "Show evidence" }).click();
        await expect(page.getByRole("dialog")).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await expect(findings.nth(i)).toBeVisible(); // the column is still open (it only folds in a window under 1280)
      }
    });

    test("keyboard only: Enter on Context, Tab to a finding, Enter shows the evidence", async ({
      page,
    }) => {
      await page.goto(FAILED_WITH_FINDINGS);
      await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
      const toggle = page.getByRole("button", { name: /^Context/ });
      await toggle.focus();
      await page.keyboard.press("Enter");
      await expect(toggle).toHaveAttribute("aria-pressed", "true");
      let reached = false;
      for (let i = 0; i < 12 && !reached; i++) {
        await page.keyboard.press("Tab");
        reached = await page.evaluate(
          () =>
            document.activeElement?.textContent === "Show evidence" &&
            !!document.activeElement.closest("[data-testid=finding]"),
        );
      }
      expect(reached).toBe(true);
      await page.keyboard.press("Enter");
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.getByRole("option", { selected: true })).toBeAttached();
    });
  });
