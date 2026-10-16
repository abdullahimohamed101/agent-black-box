import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { openTab, settleAnimations } from "./motion";

// Phase 22 D3 / acceptance 5, restated for design pass 2 (the Context column became the Findings tab): findings are not in the
// main view, and any of them is reachable, with its evidence shown, in two interactions from the run page: the Findings tab,
// then the finding. Fixtures build, the failed run that has findings.
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

    test("the main view has no findings; the Findings tab says how many there are", async ({
      page,
    }) => {
      await page.goto(FAILED_WITH_FINDINGS);
      await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
      await expect(page.getByTestId("findings")).toHaveCount(0);
      const tab = page.getByRole("tab", { name: "Findings" });
      await expect(tab).toHaveText("Findings2");
      await expect(tab).toHaveAttribute("aria-selected", "false");
    });

    test("two clicks: the Findings tab, then the finding shows its evidence", async ({ page }) => {
      await page.goto(FAILED_WITH_FINDINGS);
      await page.getByRole("tab", { name: "Findings" }).click(); // 1
      const chain = page.getByTestId("finding").filter({ hasText: "the run failed" });
      await expect(chain).toBeVisible();
      // one line: severity, title and the button share the head; the explanation is behind Details
      await expect(chain.locator(".finding-head").getByRole("button")).toHaveText("Show evidence");
      await expect(chain.getByLabel("Pattern")).toBeHidden();
      await axe(page);
      await chain.getByRole("button", { name: "Show evidence" }).click(); // 2
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.getByRole("tab", { name: "Timeline" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await expect(page.getByRole("option", { selected: true })).toBeAttached();
      await expect(page.locator("[data-landed]")).toHaveCount(1);
    });

    test("every finding is reachable the same way, the probable one included", async ({ page }) => {
      await page.goto(FAILED_WITH_FINDINGS);
      await openTab(page, "Findings");
      const findings = page.getByTestId("finding");
      await expect(findings).toHaveCount(2);
      for (let i = 0; i < 2; i++) {
        await findings.nth(i).getByRole("button", { name: "Show evidence" }).click();
        await expect(page.getByRole("dialog")).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await openTab(page, "Findings"); // evidence is shown in the timeline; the tab is one click back
        await expect(findings.nth(i)).toBeVisible();
      }
    });

    test("keyboard only: Enter on the Findings tab, Tab to a finding, Enter shows the evidence", async ({
      page,
    }) => {
      await page.goto(FAILED_WITH_FINDINGS);
      await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
      const tab = page.getByRole("tab", { name: "Findings" });
      await page.getByRole("tab", { name: "Timeline" }).focus();
      await page.keyboard.press("ArrowRight"); // Waterfall
      await page.keyboard.press("ArrowRight"); // Evidence
      await page.keyboard.press("ArrowRight"); // Findings
      await expect(tab).toHaveAttribute("aria-selected", "true");
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
