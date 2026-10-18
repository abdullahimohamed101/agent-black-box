import { currentSettingsTab } from "./settings";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";

// Phase 19a step 3 (fixtures mode): the Quotas settings page. Figures come from src/fixtures/quotas.ts: agents inside the
// limit, runs per day near it, events per day at it, artifact storage inside it.
const SHOTS = "../../docs/screenshots/phase-19a";

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

/** Console errors and uncaught exceptions of the page: the pages must stay clean. */
function watchConsole(page: Page): string[] {
  const seen: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") seen.push(m.text());
  });
  page.on("pageerror", (e) => seen.push(e.message));
  return seen;
}

const item = (page: Page, quota: string) =>
  page.locator(`[data-testid=quota-item][data-quota=${quota}]`);

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const)
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height } });

    test("usage: figures, status in words, what is refused, and no console noise", async ({
      page,
    }) => {
      const errors = watchConsole(page);
      await page.goto("/w/demo/settings/quotas");
      await expect(page.getByRole("heading", { name: "Quotas", level: 1 })).toBeVisible();
      await expect(page.getByTestId("quota-item")).toHaveCount(4);
      await expect(item(page, "agents")).toContainText("Within limit");
      await expect(item(page, "runs_per_day")).toContainText("41,200 of 50,000 runs today (82%)");
      await expect(item(page, "runs_per_day")).toContainText("Near limit");
      await expect(item(page, "runs_per_day").getByTestId("quota-refusing")).toHaveCount(0);
      await expect(item(page, "events_per_day")).toContainText("Limit reached");
      await expect(item(page, "events_per_day").getByTestId("quota-refusing")).toContainText(
        "refused until midnight UTC",
      );
      await expect(item(page, "artifact_bytes")).toContainText("1.25 GiB of 5 GiB stored");
      // the tab is current in the settings section list (a folded list on a phone: its link is hidden but still current)
      await expect(currentSettingsTab(page)).toHaveText("Quotas");
      await axe(page);
      expect(errors).toEqual([]);
    });

    test("the page does not scroll sideways", async ({ page }) => {
      await page.goto("/w/demo/settings/quotas");
      await expect(page.getByTestId("quota-item")).toHaveCount(4);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
    });
  });

test.describe("overrides", () => {
  test.describe.configure({ mode: "serial" });

  test("change a limit from the keyboard, see what was stored, then put it back", async ({
    page,
  }) => {
    const errors = watchConsole(page);
    await page.goto("/w/demo/settings/quotas");
    const save = page.getByRole("button", { name: "Save limits" });
    await expect(save).toBeDisabled();
    const agents = page.getByLabel("Distinct agents");
    await expect(agents).toHaveAttribute("placeholder", "Default 500");
    // an invalid value is refused on the page, without a request, in words that do not echo it
    await agents.fill("lots");
    await save.focus();
    await page.keyboard.press("Enter");
    const alert = page.getByRole("alert").filter({ hasText: "whole number" });
    await expect(alert).toBeVisible();
    await expect(alert).not.toContainText("lots");
    await axe(page);
    await agents.fill("750");
    await save.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("save-status")).toContainText("Saved.");
    await expect(item(page, "agents")).toContainText("12 of 750 agents");
    await expect(item(page, "agents")).toContainText("Overridden for this project");
    await page.reload();
    await expect(page.getByLabel("Distinct agents")).toHaveValue("750");
    // back to the default for the other specs
    await page.getByLabel("Distinct agents").fill("");
    await page.getByRole("button", { name: "Save limits" }).click();
    await expect(page.getByTestId("save-status")).toContainText("Saved.");
    await expect(item(page, "agents")).toContainText("12 of 500 agents");
    expect(errors).toEqual([]);
  });
});

test.describe("viewer", () => {
  test("sees the usage but cannot change a limit", async ({ page }) => {
    await page.goto("/w/viewer-only/settings/quotas");
    await expect(page.getByTestId("quota-item")).toHaveCount(4);
    await expect(page.getByTestId("quota-readonly")).toContainText("Owners and admins");
    await expect(page.getByRole("button", { name: "Save limits" })).toHaveCount(0);
    await expect(
      page
        .getByRole("navigation", { name: "Settings sections" })
        .getByRole("link", { name: "Quotas" }),
    ).toBeVisible(); // a reader still has the tab (desktop width: the list is open)
    await axe(page);
  });
});

test("an error shows a retry", async ({ page }) => {
  let fail = true;
  await page.route("**/api/abb/v1/projects/*/quotas", (route) =>
    fail
      ? route.fulfill({
          status: 503,
          json: {
            error: {
              code: "API_UNREACHABLE",
              message: "The API could not be reached.",
              retryable: true,
              request_id: "r",
              details: {},
            },
          },
        })
      : route.continue(),
  );
  await page.goto("/w/demo/settings/quotas");
  await expect(page.getByRole("alert").filter({ hasText: "could not be reached" })).toBeVisible({
    timeout: 15_000,
  });
  fail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByTestId("quota-item")).toHaveCount(4);
});

for (const scheme of ["light", "dark"] as const)
  test.describe(`${scheme} scheme`, () => {
    test.use({ colorScheme: scheme, viewport: { width: 1440, height: 900 } });
    test("axe on the page and on its form", async ({ page }) => {
      await page.goto("/w/demo/settings/quotas");
      await expect(page.getByRole("button", { name: "Save limits" })).toBeVisible();
      await axe(page);
    });
  });

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("nothing animates on the page", async ({ page }) => {
    await page.goto("/w/demo/settings/quotas");
    await expect(page.getByTestId("quota-item")).toHaveCount(4);
    await page.waitForTimeout(300);
    const running = await page.evaluate(
      () => document.getAnimations().filter((a) => a.playState === "running").length,
    );
    expect(running).toBe(0);
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
      await page.goto("/w/demo/settings/quotas");
      await expect(page.getByTestId("quota-item")).toHaveCount(4);
      await settleAnimations(page);
      await page.screenshot({ path: `${SHOTS}/quotas-${width}-${scheme}.png`, fullPage: true });
      await ctx.close();
    });
