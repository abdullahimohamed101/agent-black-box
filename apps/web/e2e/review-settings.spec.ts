import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";

// Design pass 2 step 5c (fixtures mode): Review (Approvals and Security findings as two tabs), THE ONE settings pattern on every
// tab (Alerts included), the people who may not change things, and the red rule: nothing on these screens is red. The fixture
// workspaces are "demo" (an owner) and "viewer-only" (a viewer).
const APR = (n: number) => `apr_${String(n).padStart(26, "0")}`;
const SIZES = [
  [1440, 900],
  [768, 1024],
  [375, 812],
] as const;
const SETTINGS = [
  ["members", "Members"],
  ["sessions", "Sessions"],
  ["api-keys", "API keys"],
  ["policies", "Policies"],
  ["security", "Capture and detectors"],
  ["quotas", "Quotas"],
  ["retention", "Retention"],
  ["pricing", "Pricing"],
  ["alerts", "Alerts"],
  ["audit", "Audit log"],
] as const;

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
/** Elements that paint text, a background, a border or an outline in the fault colour: on these screens there must be none. */
const redElements = (page: Page) =>
  page.evaluate(() => {
    const probe = document.createElement("i");
    probe.style.color = "var(--color-status-fault)";
    document.body.append(probe);
    const red = getComputedStyle(probe).color;
    probe.remove();
    const hits: string[] = [];
    for (const el of document.body.querySelectorAll("*")) {
      const s = getComputedStyle(el);
      if (
        [s.color, s.backgroundColor, s.borderTopColor, s.borderBottomColor, s.outlineColor].some(
          (c) => c === red,
        )
      )
        hits.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)}`);
    }
    return hits;
  });

for (const [width, height] of SIZES)
  test.describe(`Review at ${width} px`, () => {
    test.use({ viewport: { width, height } });

    test("two tabs, the open route's selected, arrow keys move and the URL follows", async ({
      page,
    }) => {
      const problems = watch(page);
      await page.goto("/w/demo/approvals");
      await expect(page.getByRole("heading", { name: "Review", level: 1 })).toBeVisible();
      const tabs = page.getByRole("tablist", { name: "Review sections" }).getByRole("tab");
      await expect(tabs).toHaveText(["Approvals", "Security findings"]);
      await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
      await expect(page.getByTestId("approval-row").first()).toBeVisible();
      await tabs.nth(0).focus();
      await page.keyboard.press("ArrowRight");
      await expect(page).toHaveURL(/\/w\/demo\/security$/);
      await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
      await expect(tabs.nth(1)).toBeFocused(); // the frame is shared: focus stays on the tab list
      await expect(page.getByTestId("security-row").first()).toBeVisible();
      await page.keyboard.press("Home");
      await expect(page).toHaveURL(/\/w\/demo\/approvals$/);
      await page.keyboard.press("End");
      await expect(page).toHaveURL(/\/w\/demo\/security$/);
      await page.goBack();
      await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
      expect(await noSideScroll(page)).toBe(true);
      expect(problems.filter((p) => !/favicon/.test(p))).toEqual([]);
    });

    test("deep links keep working: an approval opens its panel on Approvals, a filtered findings list on Security findings", async ({
      page,
    }) => {
      await page.goto(`/w/demo/approvals?approval=${APR(3)}`);
      await expect(page.getByRole("tab", { name: "Approvals" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await expect(page.getByTestId("approval-detail")).toContainText("database.execute");
      await page.goto("/w/demo/security?severity=critical");
      await expect(page.getByRole("tab", { name: "Security findings" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      const rows = page.getByTestId("security-row");
      await expect(rows.first()).toBeVisible();
      for (const sev of await rows.evaluateAll((els) =>
        els.map((e) => e.getAttribute("data-severity")),
      ))
        expect(sev).toBe("critical");
    });

    test("security findings: severity is a glyph and a word on every row, most severe first inside a run", async ({
      page,
    }) => {
      await page.goto("/w/demo/security");
      const rows = page.getByTestId("security-row");
      await expect(rows.first()).toBeVisible();
      const rank = ["critical", "high", "medium", "warning", "info"];
      const seen = await rows.evaluateAll((els) =>
        els.map((e) => ({
          sev: e.getAttribute("data-severity")!,
          word: e.querySelector(".sec-sev-word")?.textContent ?? "",
          glyph: !!e.querySelector(".sec-sev svg[aria-hidden='true']"),
          run: e.querySelector("a")?.getAttribute("href")?.split("?")[0] ?? "",
        })),
      );
      for (const r of seen) {
        expect(r.word.toLowerCase()).toBe(r.sev);
        expect(r.glyph).toBe(true);
      }
      const byRun = new Map<string, number[]>();
      for (const r of seen) byRun.set(r.run, [...(byRun.get(r.run) ?? []), rank.indexOf(r.sev)]);
      for (const [run, order] of byRun)
        expect(order, run).toEqual([...order].sort((a, b) => a - b));
      await expect(page.getByRole("columnheader", { name: "Project" })).toHaveCount(0);
    });

    for (const scheme of ["light", "dark"] as const)
      test(`axe, no red and no overflow on both tabs and an open request (${scheme})`, async ({
        browser,
      }) => {
        const ctx = await browser.newContext({
          viewport: { width, height },
          colorScheme: scheme,
          reducedMotion: "reduce",
        });
        const page = await ctx.newPage();
        const problems = watch(page);
        for (const url of [
          "/w/demo/approvals",
          `/w/demo/approvals?approval=${APR(3)}`,
          "/w/demo/approvals?state=decided",
          "/w/demo/security",
        ]) {
          await page.goto(url);
          await expect(page.getByRole("heading", { name: "Review", level: 1 })).toBeVisible();
          await page.waitForLoadState("networkidle");
          await axe(page);
          expect(await noSideScroll(page), url).toBe(true);
          // an approval that waits, is denied or has expired is not a failed run: none of this is red (the One Red Line)
          expect(await redElements(page), url).toEqual([]);
        }
        expect(problems.filter((p) => !/favicon/.test(p))).toEqual([]);
        await ctx.close();
      });
  });

test("a viewer sees Security findings only: no Approvals tab, and the approvals route says so", async ({
  page,
}) => {
  await page.goto("/w/viewer-only/security");
  await expect(page.getByRole("tab")).toHaveText(["Security findings"]);
  await page.goto("/w/viewer-only/approvals");
  await expect(page.getByText("Not available for your role")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Approvals" })).toHaveCount(0);
});

test("an empty approvals list is one sentence, no drawing", async ({ page }) => {
  await page.route("**/api/abb/v1/approvals?*", (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
  await page.goto("/w/demo/approvals");
  const empty = page.getByTestId("approvals-empty");
  await expect(empty).toContainText("Nothing is waiting for you");
  await expect(empty.locator("svg")).toHaveCount(0);
});

for (const [width, height] of SIZES)
  for (const scheme of ["light", "dark"] as const)
    test.describe(`Settings at ${width} px, ${scheme}`, () => {
      test.use({ viewport: { width, height }, colorScheme: scheme, reducedMotion: "reduce" });

      for (const [slug, name] of SETTINGS)
        test(`${name}: the title line, one h1, axe clean, no red, no overflow, console clean`, async ({
          page,
        }) => {
          const problems = watch(page);
          await page.goto(`/w/demo/settings/${slug}`);
          await expect(page.getByRole("heading", { name, level: 1, exact: true })).toBeVisible();
          await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
          await expect(page.locator(".title-line .title-back")).toHaveText("Settings");
          await page.waitForLoadState("networkidle");
          await axe(page);
          expect(await noSideScroll(page)).toBe(true);
          expect(await redElements(page)).toEqual([]);
          expect(problems.filter((p) => !/favicon/.test(p))).toEqual([]);
        });
    });

test.describe("the settings list at 1440 px", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  test("five groups, the open tab current, every tab one click away", async ({ page }) => {
    await page.goto("/w/demo/settings/quotas");
    const nav = page.getByRole("navigation", { name: "Settings sections" });
    await expect(nav.getByRole("button", { name: /^Section:/ })).toBeHidden();
    await expect(nav.getByRole("list")).toHaveCount(5);
    await expect(nav.getByRole("link")).toHaveText(SETTINGS_ORDER);
    await expect(nav.getByRole("link", { name: "Quotas" })).toHaveAttribute("aria-current", "page");
    await nav.getByRole("link", { name: "Audit log" }).click();
    await expect(page).toHaveURL(/\/settings\/audit$/);
    await expect(page.getByRole("heading", { name: "Audit log", level: 1 })).toBeVisible();
  });
});
const SETTINGS_ORDER = [
  "Members",
  "Sessions",
  "API keys",
  "Policies",
  "Capture and detectors",
  "Quotas",
  "Retention",
  "Pricing",
  "Alerts",
  "Audit log",
];

test.describe("the settings list at 375 px", () => {
  test.use({ viewport: { width: 375, height: 812 } });
  test("the list fits: one button names the tab, opens the groups, Escape closes it with focus back", async ({
    page,
  }) => {
    await page.goto("/w/demo/settings/quotas");
    const nav = page.getByRole("navigation", { name: "Settings sections" });
    const toggle = nav.getByRole("button", { name: /^Section: Quotas/ });
    await expect(toggle).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
    await expect(nav.getByRole("link", { name: "Members" })).toBeHidden();
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(nav.getByRole("link")).toHaveCount(10);
    for (const link of await nav.getByRole("link").all()) await expect(link).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
    await axe(page);
    await page.keyboard.press("Escape");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(toggle).toBeFocused();
    await toggle.click();
    await nav.getByRole("link", { name: "Members" }).click();
    await expect(page).toHaveURL(/\/settings\/members$/);
    await expect(page.getByRole("heading", { name: "Members", level: 1 })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Section: Members/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  test("a table row is a block of labelled lines (Members)", async ({ page }) => {
    await page.goto("/w/demo/settings/members");
    const cell = page.getByRole("cell").filter({ hasText: "2026-10-07" }).first();
    await expect(cell).toHaveAttribute("data-label", "Joined");
    await expect(cell).toHaveCSS("display", "grid");
  });
});

test.describe("destructive actions are two steps in place", () => {
  test("Remove a member asks first; Escape and Cancel put the first button back", async ({
    page,
  }) => {
    await page.goto("/w/demo/settings/members");
    const row = page.getByRole("row").filter({ hasText: "dev@local.test" });
    await row.getByRole("button", { name: "Remove" }).click();
    const confirm = row.getByRole("button", { name: "Confirm remove" });
    await expect(confirm).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(row.getByRole("button", { name: "Remove" })).toBeFocused();
    await row.getByRole("button", { name: "Remove" }).click();
    await row.getByRole("button", { name: "Cancel" }).click();
    await expect(row.getByRole("button", { name: "Remove" })).toBeVisible();
    await expect(row.getByRole("button", { name: "Confirm remove" })).toHaveCount(0);
  });
});

test.describe("a role that can read but not change gets one sentence, not a disabled form", () => {
  for (const slug of ["members", "pricing", "quotas", "retention"])
    test(`viewer: ${slug}`, async ({ page }) => {
      await page.goto(`/w/viewer-only/settings/${slug}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(
        page
          .locator("main")
          .getByRole("note")
          .filter({ hasText: /Owners and admins|Your role can read/ }),
      ).toHaveCount(1);
      await expect(page.locator("main button:disabled, main input:disabled")).toHaveCount(0);
      await axe(page);
    });
  test("viewer: tabs the role cannot read are absent from the list, and typing the address says so", async ({
    page,
  }) => {
    await page.goto("/w/viewer-only/settings/members");
    const nav = page.getByRole("navigation", { name: "Settings sections" });
    for (const gone of ["API keys", "Policies", "Capture and detectors", "Audit log"])
      await expect(nav.getByRole("link", { name: gone })).toHaveCount(0);
    for (const slug of ["api-keys", "audit"]) {
      await page.goto(`/w/viewer-only/settings/${slug}`);
      await expect(page.getByText("Not available for your role")).toBeVisible();
    }
  });
});

test.describe("Alerts, under Settings", () => {
  test("rules, destinations and history are tabs inside the Alerts tab; the old route lands on it", async ({
    page,
  }) => {
    await page.goto("/w/demo/alerts?tab=destinations");
    await expect(page).toHaveURL(/\/w\/demo\/settings\/alerts\?tab=destinations$/);
    const nav = page.getByRole("navigation", { name: "Settings sections" });
    await expect(nav.getByRole("link", { name: "Alerts" })).toHaveAttribute("aria-current", "page");
    const tabs = page.getByRole("tablist", { name: "Alert sections" }).getByRole("tab");
    await expect(tabs).toHaveText(["Rules", "Destinations", "History"]);
    await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("destination-row").first()).toBeVisible();
  });
});
