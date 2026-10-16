import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";

// Phase 19b step 3 (fixtures mode): the Retention settings tab. Values come from src/fixtures/retention.ts: the default
// periods (30/90/365/365 days), a dry-run installation, four reports. Edits keep in-memory state on the fixture server, so
// the mutating tests are serial and put the defaults back.
const SHOTS = "../../docs/screenshots/phase-19b";
const PAGE = "/w/demo/settings/retention";

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

const tier = (page: Page, name: string) =>
  page.locator(`[data-testid=retention-tier][data-tier=${name}]`);

/** Make the policy answer with another installation mode (the fixture server is always in dry run). */
async function withMode(page: Page, mode: "off" | "dry_run" | "enforce") {
  await page.route("**/api/abb/v1/workspaces/*/retention", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, unknown>;
    return route.fulfill({ response, json: { ...body, mode } });
  });
}

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const)
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height } });

    test("the four tiers, the mode banner and the last passes, and no console noise", async ({
      page,
    }) => {
      const errors = watchConsole(page);
      await page.goto(PAGE);
      await expect(page.getByRole("heading", { name: "Retention", level: 1 })).toBeVisible();
      await expect(page.getByTestId("retention-tier")).toHaveCount(4);
      await expect(page.getByTestId("retention-mode")).toContainText(
        "Retention is in dry-run mode: nothing is being deleted",
      );
      await expect(tier(page, "artifacts").getByTestId("tier-value")).toHaveText(
        "30 days (installation default)",
      );
      await expect(tier(page, "run")).toContainText("The run summary and its numbers");
      await expect(page.getByTestId("retention-pass")).toHaveCount(4);
      await expect(page.getByTestId("retention-pass").first()).toContainText("Dry run");
      await expect(page.getByRole("link", { name: "Retention" }).first()).toHaveAttribute(
        "aria-current",
        "page",
      );
      await axe(page);
      expect(errors).toEqual([]);
    });

    test("the page does not scroll sideways", async ({ page }) => {
      await page.goto(PAGE);
      await expect(page.getByTestId("retention-pass")).toHaveCount(4);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
    });
  });

test.describe("editing", () => {
  test.describe.configure({ mode: "serial" });

  test("preview, validate, shorten with a confirm, save from the keyboard, then put it back", async ({
    page,
  }) => {
    const errors = watchConsole(page);
    await page.goto(PAGE);
    await expect(page.getByTestId("retention-tier")).toHaveCount(4);
    const save = page.getByRole("button", { name: "Save periods" });
    await expect(save).toBeDisabled();

    // an invalid value is refused on the page, in words that do not echo it
    await page.getByLabel("Artifacts period").selectOption("days");
    await page.getByLabel("Artifacts days").fill("3");
    await save.focus();
    await page.keyboard.press("Enter");
    const alert = alerts(page).filter({ hasText: "at least 7 days" });
    await expect(alert).toBeVisible();
    await expect(alert).not.toContainText("3 ");
    await axe(page);

    // preview the proposed policy: the API measures it, nothing is saved
    await page.getByLabel("Artifacts days").fill("14");
    await page.getByRole("button", { name: /Preview/ }).click();
    const preview = page.getByTestId("retention-preview");
    await expect(preview).toContainText("This would remove 226 runs");
    await expect(preview).toContainText("dry-run mode, so nothing is deleted yet");
    await axe(page);

    // shortening asks first; Cancel changes nothing
    await save.focus();
    await page.keyboard.press("Enter");
    const confirm = page.getByTestId("retention-confirm");
    await expect(confirm).toBeFocused();
    await expect(confirm).toContainText("Artifacts: from 30 days to 14 days");
    await expect(confirm).toContainText("deleted permanently and cannot be restored");
    await axe(page);
    await confirm.getByRole("button", { name: "Cancel" }).click();
    await expect(tier(page, "artifacts").getByTestId("tier-value")).toContainText("30 days");

    await save.focus();
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Save and shorten" }).click();
    await expect(page.getByTestId("save-status")).toContainText("Saved.");
    await expect(tier(page, "artifacts").getByTestId("tier-value")).toHaveText(
      "14 days (set for this workspace)",
    );
    await page.reload();
    await expect(page.getByLabel("Artifacts days")).toHaveValue("14");

    // back to the default (a longer period than 14: no question)
    await page.getByLabel("Artifacts period").selectOption("default");
    await page.getByRole("button", { name: "Save periods" }).click();
    await expect(tier(page, "artifacts").getByTestId("tier-value")).toHaveText(
      "30 days (installation default)",
    );
    expect(errors).toEqual([]);
  });
});

test.describe("modes", () => {
  for (const [mode, text] of [
    ["enforce", "Retention is enforcing"],
    ["off", "Retention is off"],
  ] as const)
    test(`the banner says ${mode}`, async ({ page }) => {
      await withMode(page, mode);
      await page.goto(PAGE);
      await expect(page.getByTestId("retention-mode")).toContainText(text);
      await axe(page);
    });
});

test.describe("viewer", () => {
  test("sees the periods and the passes but cannot change anything", async ({ page }) => {
    await page.goto("/w/viewer-only/settings/retention");
    await expect(page.getByTestId("retention-tier")).toHaveCount(4);
    await expect(page.getByTestId("retention-readonly")).toContainText("Owners and admins");
    await expect(page.getByRole("button", { name: "Save periods" })).toHaveCount(0);
    await expect(
      page.getByRole("form", { name: "Retention periods" }).getByRole("combobox"),
    ).toHaveCount(0);
    await axe(page);
  });
});

test("an error shows a retry", async ({ page }) => {
  let fail = true;
  await page.route("**/api/abb/v1/workspaces/*/retention", (route) =>
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
  await page.goto(PAGE);
  await expect(alerts(page).filter({ hasText: "could not be reached" })).toBeVisible({
    timeout: 15_000,
  });
  fail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByTestId("retention-tier")).toHaveCount(4);
});

test("a rate limit on save says how long to wait", async ({ page }) => {
  await page.route("**/api/abb/v1/workspaces/*/retention", (route) =>
    route.request().method() === "PUT"
      ? route.fulfill({
          status: 429,
          headers: { "retry-after": "9" },
          json: {
            error: {
              code: "RATE_LIMITED",
              message: "Slow down.",
              retryable: true,
              request_id: "r",
              details: {},
            },
          },
        })
      : route.continue(),
  );
  await page.goto(PAGE);
  await page.getByLabel("Audit log period").selectOption("forever");
  await page.getByRole("button", { name: "Save periods" }).click();
  await expect(alerts(page)).toContainText("Try again in 9 seconds");
});

for (const scheme of ["light", "dark"] as const)
  test.describe(`${scheme} scheme`, () => {
    test.use({ colorScheme: scheme, viewport: { width: 1440, height: 900 } });
    test("axe on the page, with a preview open", async ({ page }) => {
      await page.goto(PAGE);
      await page.getByRole("button", { name: /Preview/ }).click();
      await expect(page.getByTestId("retention-preview")).toBeVisible();
      await axe(page);
    });
  });

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("nothing animates on the page", async ({ page }) => {
    await page.goto(PAGE);
    await expect(page.getByTestId("retention-tier")).toHaveCount(4);
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
      await page.goto(PAGE);
      await expect(page.getByTestId("retention-pass")).toHaveCount(4);
      await page.getByRole("button", { name: /Preview/ }).click();
      await expect(page.getByTestId("retention-preview")).toBeVisible();
      await settleAnimations(page);
      await page.screenshot({ path: `${SHOTS}/retention-${width}-${scheme}.png`, fullPage: true });
      await ctx.close();
    });
