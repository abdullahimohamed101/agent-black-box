import type { Page } from "@playwright/test";

/**
 * The settings tab that is current in the section list (design pass 2 step 5c). A folded list on a phone still holds the link,
 * hidden, so a role query would not see it: this reads the attribute, which is what a screen reader announces once it opens.
 */
export const currentSettingsTab = (page: Page) =>
  page.getByRole("navigation", { name: "Settings sections" }).locator('a[aria-current="page"]');
