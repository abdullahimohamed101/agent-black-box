import { expect, type Page } from "@playwright/test";

// Shared by the run-page specs (Phase 22 steps 5 and 6, design pass 2 step 5b).

/** Open one of the run page's tabs, as a person would (design pass 2: Findings and Evaluations are tabs, not a column). */
export async function openTab(
  page: Page,
  name: "Timeline" | "Waterfall" | "Evidence" | "Findings" | "Evaluations",
) {
  const tab = page.getByRole("tab", { name, exact: true });
  if ((await tab.getAttribute("aria-selected")) !== "true") await tab.click();
  await expect(tab).toHaveAttribute("aria-selected", "true");
}

/**
 * Wait for every finite animation to end. The inspector fades in over 160 ms and axe, run right after a click, would
 * measure its half-transparent text; a person would see the finished page, so the check waits for it too.
 */
export const settleAnimations = (page: Page) =>
  page.waitForFunction(() => document.getAnimations().every((a) => a.playState === "finished"));
