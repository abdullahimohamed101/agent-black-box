import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";

// Phase 14 step 4 (fixtures mode): the Approvals page. Every test that decides a request uses a request of its own, so
// the specs can run in parallel against the one fixture server (state is in memory and resets when it restarts).
const APR = (n: number) => `apr_${String(n).padStart(26, "0")}`;
const PAGE = "/w/demo/approvals";
const SHOTS = "../../docs/screenshots/phase-14";

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
const rows = (page: Page) => page.getByTestId("approval-row");
const detail = (page: Page) => page.getByTestId("approval-detail");

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const)
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height } });

    test("list: the rail count, the rows, the tabs as URL state and Load more", async ({
      page,
    }) => {
      const problems: string[] = [];
      page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
      page.on("pageerror", (e) => problems.push(e.message));
      await page.goto(PAGE);
      await expect(page.getByRole("heading", { name: "Approvals", level: 1 })).toBeVisible();
      await expect(rows(page).first()).toBeVisible();
      expect(await rows(page).count()).toBeGreaterThanOrEqual(1);
      const first = rows(page).first();
      await expect(first).toContainText(/Risk R\d|Unclassified/);
      await expect(first).toContainText("Waiting");
      await expect(first.getByTestId("time-left")).toHaveText(/\d+ (h|min|s)/);
      if (width > 800) {
        const count = page.getByTestId("approvals-count");
        await expect(count).toBeVisible();
        await expect(page.getByRole("link", { name: /^Approvals/ }).first()).toHaveAttribute(
          "aria-current",
          "page",
        );
      }
      expect(await noSideScroll(page)).toBe(true);
      // tabs are links that write the URL; back and forward restore it
      await page.getByRole("link", { name: "Decided" }).click();
      await expect(page).toHaveURL(/\?state=decided$/);
      await expect(rows(page)).toHaveCount(25);
      for (const state of await rows(page).evaluateAll((els) =>
        els.map((e) => e.getAttribute("data-state")),
      ))
        expect(state).not.toBe("REQUESTED");
      await page.getByRole("button", { name: "Load more" }).click();
      await expect.poll(() => rows(page).count()).toBeGreaterThan(25);
      await page.getByRole("link", { name: "All" }).click();
      await expect(page).toHaveURL(/\?state=all$/);
      await page.goBack();
      await expect(page).toHaveURL(/\?state=decided$/);
      await expect(page.getByRole("link", { name: "Decided" })).toHaveAttribute(
        "aria-current",
        "page",
      );
      await page.goBack();
      await expect(page).toHaveURL(/\/w\/demo\/approvals$/);
      await expect(page.getByRole("link", { name: "Pending" })).toHaveAttribute(
        "aria-current",
        "page",
      );
      expect(problems.filter((p) => /418|hydrat/i.test(p))).toEqual([]);
    });

    test("detail: the intent as the API returns it, and axe with a request open", async ({
      page,
    }) => {
      await page.goto(`${PAGE}?approval=${APR(3)}`);
      const d = detail(page);
      await expect(d.getByRole("heading", { level: 2, name: "database.execute" })).toBeFocused();
      await expect(d).toContainText("Approver needed");
      await expect(d).toContainText("Admin or above");
      await expect(d).toContainText("approve-db-write");
      await expect(d.locator("pre.apr-args")).toContainText(
        "ALTER TABLE orders ADD COLUMN note text",
      );
      await expect(d.getByRole("button", { name: "Approve" })).toBeVisible();
      expect(await noSideScroll(page)).toBe(true);
      if (width < 800) await expect(page.locator(".apr-list")).toBeHidden(); // the detail replaces the list
      await axe(page);
    });
  });

test("keyboard only: open a request, comment, approve with a confirm step", async ({ page }) => {
  await page.goto(PAGE);
  await expect(rows(page).first()).toBeVisible();
  const open = page.locator(`#apr-open-${APR(2)}`);
  await open.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`approval=${APR(2)}`));
  const d = detail(page);
  await expect(d.getByRole("heading", { level: 2 })).toBeFocused();
  const comment = d.getByLabel("Comment (optional)");
  await comment.focus();
  await page.keyboard.type("Reviewed the manifest.");
  await page.keyboard.press("Tab");
  await expect(d.getByRole("button", { name: "Approve" })).toBeFocused();
  await page.keyboard.press("Enter");
  // the confirm step has the focus; nothing was sent yet
  await expect(d.getByRole("button", { name: "Confirm approve" })).toBeFocused();
  await expect(d.getByTestId("decision-saved")).toHaveCount(0);
  await page.keyboard.press("Enter");
  await expect(d.getByTestId("decision-saved")).toHaveText("You approved this request.");
  await expect(d.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(d).toContainText("Reviewed the manifest.");
  // Escape closes the panel and the focus goes back to the row
  await page.keyboard.press("Escape");
  await expect(d).toHaveCount(0);
  await page.getByRole("link", { name: "Decided" }).click();
  await expect(page.locator(`#apr-open-${APR(2)}`)).toBeVisible();
});

test("deny with a comment, and cancel at the confirm step", async ({ page }) => {
  await page.goto(`${PAGE}?approval=${APR(3)}`);
  const d = detail(page);
  await d.getByLabel("Comment (optional)").fill("Not on a shared host.");
  await d.getByRole("button", { name: "Deny" }).click();
  await d.getByRole("button", { name: "Cancel" }).click();
  await expect(d.getByRole("button", { name: "Approve" })).toBeVisible();
  await d.getByRole("button", { name: "Deny" }).click();
  await d.getByRole("button", { name: "Confirm deny" }).click();
  await expect(d.getByTestId("decision-saved")).toHaveText("You denied this request.");
  await expect(d).toContainText("Not on a shared host.");
  await expect(d).toContainText("Denied");
});

for (const [n, sentence] of [
  [4, "You cannot decide a request made with your own API key."],
  [5, "Your role cannot decide this request."],
  [6, "Someone else decided first: it is now denied."],
] as const)
  test(`the API's refusal for request ${n} is shown as a fixed sentence`, async ({ page }) => {
    await page.goto(`${PAGE}?approval=${APR(n)}`);
    const d = detail(page);
    await d.getByRole("button", { name: "Approve" }).click();
    await d.getByRole("button", { name: "Confirm approve" }).click();
    await expect(d.getByTestId("decision-error")).toHaveText(sentence);
    await axe(page);
  });

test("a request that does not exist says so", async ({ page }) => {
  await page.goto(`${PAGE}?approval=${APR(999)}`);
  await expect(detail(page)).toContainText("This approval was not found.");
});

test("the pending list re-reads about every 10 s while the tab is visible", async ({ page }) => {
  await page.clock.install();
  let lists = 0;
  let counts = 0;
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (u.pathname === "/api/abb/v1/approvals" && u.searchParams.get("state") === "pending")
      lists++;
    if (u.pathname === "/api/abb/v1/approvals/pending-count") counts++;
  });
  await page.goto(PAGE);
  await expect(rows(page).first()).toBeVisible();
  const l0 = lists;
  const c0 = counts;
  await page.clock.runFor(11_000);
  await expect.poll(() => lists).toBeGreaterThan(l0);
  await page.clock.runFor(31_000);
  await expect.poll(() => counts).toBeGreaterThan(c0);
});

test("the empty states", async ({ page }) => {
  await page.route("**/api/abb/v1/approvals?*", (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
  await page.goto(PAGE);
  await expect(page.getByTestId("approvals-empty")).toContainText("Nothing is waiting for you");
  await axe(page);
  await page.goto(`${PAGE}?state=decided`);
  await expect(page.getByTestId("approvals-empty")).toContainText("No decided requests yet");
});

test("an error shows a retry", async ({ page }) => {
  let fail = true;
  await page.route("**/api/abb/v1/approvals?*", (route) =>
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
  await expect(page.getByRole("alert").filter({ hasText: "could not be reached" })).toBeVisible({
    timeout: 15_000,
  });
  fail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(rows(page).first()).toBeVisible();
});

test("a viewer sees 'Not available for your role' and no rail item", async ({ page }) => {
  await page.goto("/w/viewer-only/approvals");
  await expect(page.getByText("Not available for your role")).toBeVisible();
  await expect(page.getByRole("link", { name: /^Approvals/ })).toHaveCount(0);
  await page.goto("/w/viewer-only/settings/policies");
  await expect(page.getByText("Not available for your role")).toBeVisible();
});

for (const scheme of ["light", "dark"] as const)
  test.describe(`${scheme} scheme`, () => {
    test.use({ colorScheme: scheme, viewport: { width: 1440, height: 900 } });
    test("axe on the list, the decided tab and an open request", async ({ page }) => {
      await page.goto(PAGE);
      await expect(rows(page).first()).toBeVisible();
      await axe(page);
      await page.goto(`${PAGE}?state=decided`);
      await expect(rows(page).first()).toBeVisible();
      await axe(page);
      await page.goto(`${PAGE}?state=decided&approval=${APR(11)}`);
      await expect(detail(page)).toContainText("Release notes reviewed.");
      await axe(page);
    });
  });

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("nothing animates", async ({ page }) => {
    for (const url of [PAGE, `${PAGE}?approval=${APR(3)}`]) {
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
      await page.goto(PAGE);
      await expect(rows(page).first()).toBeVisible();
      await shot("approvals-pending");
      await page.goto(`${PAGE}?approval=${APR(1)}`);
      await expect(detail(page)).toContainText("Approver needed");
      await shot("approvals-detail");
      await page.goto(`${PAGE}?state=decided&approval=${APR(11)}`);
      await expect(detail(page)).toContainText("Release notes reviewed.");
      await shot("approvals-decided");
      await page.route("**/api/abb/v1/approvals?*", (route) =>
        route.fulfill({ json: { items: [], next_cursor: null } }),
      );
      await page.goto(PAGE);
      await expect(page.getByTestId("approvals-empty")).toBeVisible();
      await shot("approvals-empty");
      await ctx.close();
    });
