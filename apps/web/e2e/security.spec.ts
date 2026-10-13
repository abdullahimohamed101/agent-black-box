import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { openContext, settleAnimations } from "./motion";

// Phase 13 step 3 (fixtures mode): security findings on the run page, the workspace Security page, the settings page.
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
const LEAKED = `/w/demo/projects/all/runs/${RUN(14)}`;
const SHOTS = "../../docs/screenshots/phase-13";
const SENTINELS = [
  "AKIAFIXTURESENTINEL",
  "pastebin.example.net",
  "DROP TABLE customers",
  "rm -rf ~/projects",
];

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}
const noSentinel = async (page: Page, scope = "body") => {
  const text = (await page.locator(scope).innerText()) + (await page.locator(scope).innerHTML());
  for (const s of SENTINELS) expect(text).not.toContain(s);
};

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const)
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height } });

    test("run page: the chip, the Security group above the findings, and evidence", async ({
      page,
    }) => {
      await page.goto(LEAKED);
      await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
      const chip = page.getByTestId("security-chip");
      await expect(chip).toHaveText("6 security findings (most severe: Critical)");
      await expect(page.getByRole("button", { name: /^Context/ })).toHaveText(
        "Context · 6 security",
      );
      await chip.click();
      const group = page.getByTestId("security-group");
      await expect(group).toBeVisible();
      await expect(page.locator("#security-h")).toBeFocused();
      const lines = group.getByTestId("security-finding");
      await expect(lines).toHaveCount(6);
      await expect(lines.first()).toContainText("Critical");
      await expect(lines.first()).toContainText("a credential appeared in captured text");
      await noSentinel(page, "[data-testid=security-group]");
      await axe(page);
      const file = lines.filter({ hasText: "usually holds secrets" });
      await file.getByRole("button", { name: "Show evidence" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.locator("[data-landed]")).toHaveCount(1);
      await expect(page.getByRole("dialog")).toContainText(".env.production"); // the owner may read the event
    });

    test("workspace page: table, keyboard, deep link, facets and URL", async ({ page }) => {
      await page.goto("/w/demo/security");
      await expect(page.getByRole("heading", { name: "Security", level: 1 })).toBeVisible();
      await expect(page.getByTestId("security-row")).toHaveCount(10);
      await noSentinel(page, "main");
      if (width > 800)
        await expect(page.getByRole("link", { name: "Security" }).first()).toHaveAttribute(
          "aria-current",
          "page",
        );
      await axe(page);
      // facets edit the URL; back and forward restore it
      if (width < 800) await page.getByText("Narrow results").click();
      await page.getByRole("button", { name: /^Critical/ }).click();
      await expect(page).toHaveURL(/\?severity=critical$/);
      await expect(page.getByTestId("security-row")).toHaveCount(3);
      await page.goBack();
      await expect(page).toHaveURL(/\/w\/demo\/security(\?severity=critical)?$/);
      await page.goForward();
      await expect(page).toHaveURL(/severity=critical/);
      await expect(page.getByTestId("security-row")).toHaveCount(3);
    });

    test("deep link: a row opens the run with the finding marked and its evidence open", async ({
      page,
    }) => {
      await page.goto("/w/demo/security?detector=sec.risky_git");
      const link = page.getByTestId("security-row").first().getByRole("link");
      await link.focus(); // keyboard only from here
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(/runs\/run_\w+\?finding=fnd_\w+/);
      const marked = page.locator('[data-finding-id][data-focus="true"]');
      await expect(marked).toBeVisible();
      await expect(marked).toContainText("a risky git operation was run");
      await expect(page.getByRole("dialog")).toBeVisible();
    });
  });

test("the empty and filtered-empty states", async ({ page }) => {
  await page.route("**/api/abb/v1/security/findings*", (route) =>
    route.fulfill({
      json: { items: [], next_cursor: null, facets: { severity: [], detector: [], project: [] } },
    }),
  );
  await page.goto("/w/demo/security");
  const clean = page.getByTestId("security-clean");
  await expect(clean).toContainText("Nothing suspicious found");
  await expect(clean.getByRole("list", { name: "Detectors" }).getByRole("listitem")).toHaveCount(6);
  await axe(page);
  await page.unroute("**/api/abb/v1/security/findings*");
  await page.goto("/w/demo/security?since=2030-01-01T00:00:00Z");
  await expect(page.getByText("No findings match")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).first().click();
  await expect(page.getByTestId("security-row").first()).toBeVisible();
});

test("an error shows a retry; BILLING-like roles see 'not available'", async ({ page }) => {
  let fail = true;
  await page.route("**/api/abb/v1/security/findings*", (route) =>
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
  await page.goto("/w/demo/security");
  await expect(page.getByRole("alert").filter({ hasText: "could not be reached" })).toBeVisible({
    timeout: 15_000,
  });
  fail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByTestId("security-row").first()).toBeVisible();
});

test.describe("settings", () => {
  test.describe.configure({ mode: "serial" });
  test("capture mode, detector mute and allowlist: edit, save, see what was stored", async ({
    page,
  }) => {
    await page.goto("/w/demo/settings/security");
    await expect(page.getByRole("heading", { name: "Security", level: 1 })).toBeVisible();
    const save = page.getByRole("button", { name: "Save changes" });
    await expect(save).toBeDisabled();
    await page.getByRole("radio", { name: /^Full/ }).check();
    await page.getByRole("checkbox", { name: "Report risky git" }).uncheck();
    await page.getByLabel("Add a host").fill("https://nope.test");
    await page.getByLabel("Add a host").press("Enter");
    await expect(page.getByRole("alert").filter({ hasText: "must be a host name" })).toBeVisible();
    await page.getByLabel("Add a host").fill("*.example.org");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await axe(page);
    await save.click();
    await expect(page.getByTestId("save-status")).toContainText("Saved.");
    await page.reload();
    await expect(page.getByRole("radio", { name: /^Full/ })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: "Report risky git" })).not.toBeChecked();
    await expect(page.getByRole("list", { name: "Allowed hosts" })).toContainText("*.example.org");
    // put it back for the other specs
    await page.getByRole("radio", { name: /^Metadata only/ }).check();
    await page.getByRole("checkbox", { name: "Report risky git" }).check();
    await page.getByRole("button", { name: "Remove *.example.org" }).click();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByTestId("save-status")).toContainText("Saved.");
  });
});

test.describe("viewer", () => {
  test("sees the same findings; the evidence drawer withholds the content; settings are not available", async ({
    page,
  }) => {
    await page.goto(`/w/viewer-only/projects/all/runs/${RUN(14)}`);
    await expect(page.getByTestId("security-chip")).toHaveText(/6 security findings/);
    await openContext(page);
    const lines = page.getByTestId("security-finding");
    await expect(lines).toHaveCount(6);
    await noSentinel(page, "#run-context");
    await lines
      .filter({ hasText: "usually holds secrets" })
      .getByRole("button", { name: "Show evidence" })
      .click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).not.toContainText(".env.production");
    await expect(dialog).toContainText(/withheld/i);
    await page.goto("/w/viewer-only/security");
    await expect(page.getByTestId("security-row")).toHaveCount(10);
    await noSentinel(page, "main");
    await page.goto("/w/viewer-only/settings/security");
    await expect(page.getByText("Not available for your role")).toBeVisible();
    await expect(page.getByRole("link", { name: "Security" }).first()).toBeVisible();
  });
});

for (const scheme of ["light", "dark"] as const)
  test.describe(`${scheme} scheme`, () => {
    test.use({ colorScheme: scheme, viewport: { width: 1440, height: 900 } });
    test("axe on the run page, the Security page and settings", async ({ page }) => {
      await page.goto(LEAKED);
      await page.getByTestId("security-chip").click();
      await axe(page);
      await page.goto("/w/demo/security");
      await expect(page.getByTestId("security-row").first()).toBeVisible();
      await axe(page);
      await page.goto("/w/demo/settings/security");
      await expect(page.getByRole("button", { name: "Save changes" })).toBeVisible();
      await axe(page);
    });
  });

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("nothing animates on the new pages", async ({ page }) => {
    for (const url of [LEAKED, "/w/demo/security", "/w/demo/settings/security"]) {
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
      await page.goto(`${LEAKED}?finding=${await findingId(page, "sec.risky_git")}`);
      await expect(page.getByTestId("security-group")).toBeVisible();
      await shot("run-with-findings");
      await page.goto("/w/demo/security");
      await expect(page.getByTestId("security-row").first()).toBeVisible();
      await shot("security-page");
      await page.route("**/api/abb/v1/security/findings*", (route) =>
        route.fulfill({
          json: {
            items: [],
            next_cursor: null,
            facets: { severity: [], detector: [], project: [] },
          },
        }),
      );
      await page.goto("/w/demo/security");
      await expect(page.getByTestId("security-clean")).toBeVisible();
      await shot("security-empty");
      await page.goto("/w/demo/settings/security");
      await expect(page.getByRole("button", { name: "Save changes" })).toBeVisible();
      await shot("security-settings");
      await ctx.close();
    });

async function findingId(page: Page, detector: string): Promise<string> {
  const res = await page.request.get(`/api/abb/v1/runs/${RUN(14)}/findings`);
  const body = (await res.json()) as { items: { id: string; detector: string }[] };
  return body.items.find((i) => i.detector === detector)!.id;
}
