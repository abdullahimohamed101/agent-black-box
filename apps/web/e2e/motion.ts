import { expect, type Page } from "@playwright/test";

// Shared by the run-page specs (Phase 22 steps 5 and 6).

/** The context column starts closed (D3): open it with the header button, as a person would. */
export async function openContext(page: Page) {
  const toggle = page.getByRole("button", { name: /^Context/ });
  if ((await toggle.getAttribute("aria-pressed")) !== "true") await toggle.click();
  await expect(page.locator(".work")).toHaveAttribute("data-context", "open");
}

/**
 * Wait for every finite animation to end. The inspector fades in over 160 ms and axe, run right after a click, would
 * measure its half-transparent text; a person would see the finished page, so the check waits for it too.
 */
export const settleAnimations = (page: Page) =>
  page.waitForFunction(() => document.getAnimations().every((a) => a.playState === "finished"));
